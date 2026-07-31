"""
test_ha_gateway.py -- HA Entity & Device Publication Audit Tests.
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from ble_registry import BLERegistry
from device_manager.manager import get_app_version
from device_manager.sensor_registry import SensorRegistry
from gw_test_helpers import load_device, load_gateway, VALID_LINE, ISO_CLAIM_LINE
from ydnu02.pgn_decoder import N2KPGNDecoder


class TestHAISOAddressClaim(unittest.TestCase):
    """Verify PGN 60928 Address Claim decoding for HA device discovery."""

    def setUp(self):
        self.reg = SensorRegistry()

    def test_address_claim_creates_bus_device(self):
        """Receiving PGN 60928 registers a new device in bus devices map."""
        parsed = {
            'info': {'pgn': 60928, 'src': 92, 'can_id': 0x18EEFF5C},
            'data': bytes.fromhex('3930A05C7421A72C'),
        }
        self.reg.update(parsed)
        devices = self.reg.get_bus_devices()
        self.assertIn(92, devices)
        self.assertEqual(devices[92]['src'], 92)

    def test_address_claim_extracts_unique_id(self):
        """ISO Claim must populate unique_id for device identification."""
        parsed = {
            'info': {'pgn': 60928, 'src': 92, 'can_id': 0x18EEFF5C},
            'data': bytes.fromhex('3930A05C7421A72C'),
        }
        self.reg.update(parsed)
        dev = self.reg.get_bus_devices()[92]
        self.assertIn('unique_id', dev)
        self.assertIsNotNone(dev['unique_id'])

    def test_virtual_gateway_sa200_claim_tracked(self):
        """Virtual gateway address claim (SA=200) is tracked in bus devices."""
        parsed = {
            'info': {'pgn': 60928, 'src': 200, 'can_id': 0x18EEFFC8},
            'data': bytes.fromhex('3930A05C7421A72C'),
        }
        self.reg.update(parsed)
        devices = self.reg.get_bus_devices()
        self.assertIn(200, devices)
        self.assertEqual(devices[200]['src'], 200)


class TestHAProductInformation(unittest.TestCase):
    """Verify PGN 126996 Product Info parsing (model, firmware, serial)."""

    def setUp(self):
        self.reg = SensorRegistry()

    def test_product_info_decoder_crash_safety(self):
        """Corrupt PGN 126996 frame must not crash SensorRegistry."""
        parsed = {
            'info': {'pgn': 126996, 'src': 92, 'can_id': 0x18F0145C},
            'data': bytes([255] * 8),
        }
        try:
            self.reg.update(parsed)
        except Exception as exc:
            self.fail(f'update() raised on corrupt frame: {exc}')

    def test_active_pgns_tracked_per_source(self):
        """Active PGN list must track PGN 126996 for the emitting SA."""
        parsed = {
            'info': {'pgn': 126996, 'src': 92, 'can_id': 0x18F0145C},
            'data': bytes([0] * 8),
        }
        self.reg.update(parsed)
        dev = self.reg.get_bus_devices()[92]
        self.assertIn(126996, dev['active_pgns'])


class TestHAISORequestOnboarding(unittest.TestCase):
    """Verify ISO Requests (PGN 59904) trigger physical bus discovery."""

    def setUp(self):
        self.mod = load_gateway()
        self.mod._serial_ready.set()
        self.mock_ser = MagicMock()
        self.mock_ser.is_open = True
        self.mod.serial_instance = self.mock_ser
        self.mod._iso_request_last_sent = 0.0

    def test_iso_request_sends_claim_and_product_info_requests(self):
        """send_iso_request() writes PGN 60928 and PGN 126996 requests to serial."""
        hub = self.mod.DataHub(get_serial_instance=lambda: self.mock_ser, get_serial_ready=lambda: True, get_clients=lambda: getattr(self.mod, "clients", set()), clients_lock=self.mod.clients_lock); hub.send_iso_request()
        self.assertEqual(self.mock_ser.write.call_count, 2)
        writes = [call[0][0] for call in self.mock_ser.write.call_args_list]
        self.assertTrue(any(b'00 EE 00' in w or b'18EAFFFE' in w for w in writes))
        self.assertTrue(any(b'14 F0 01' in w or b'18EAFFFE' in w for w in writes))

    def test_iso_request_broadcasts_to_tcp_clients(self):
        """ISO Requests must be broadcast to connected TCP clients (HA)."""
        received = []
        class FakeConn:
            def sendall(self, data):
                received.append(data)
        self.mod.clients = {FakeConn()}
        hub = self.mod.DataHub(get_serial_instance=lambda: self.mock_ser, get_serial_ready=lambda: True, get_clients=lambda: getattr(self.mod, "clients", set()), clients_lock=self.mod.clients_lock); hub.send_iso_request()
        self.assertGreaterEqual(len(received), 1)
        self.assertTrue(any(b'18EAFFFE' in line for line in received))


class TestHATelemetryPGNs(unittest.TestCase):
    """Verify CPU Temperature and Fluid Level PGN telemetry formatting."""

    def setUp(self):
        self.dev = load_device()
        self.reg = SensorRegistry()

    def test_cpu_temp_message_pgn_130312(self):
        """CPU temp message PGN must be 130312."""
        msg = self.dev._make_temp_message(52.3, sid=1)
        self.assertEqual(msg.PGN, 130312)

    def test_cpu_temp_kelvin_conversion(self):
        """Celsius to Kelvin conversion for HA temperature sensor."""
        msg = self.dev._make_temp_message(50.0, sid=1)
        fields = {f.id: f.value for f in msg.fields}
        self.assertAlmostEqual(fields['actualTemperature'], 323.15, places=2)

    def test_fluid_level_pgn127505_parsing(self):
        """PGN 127505 Fluid Level updates sensor state in registry."""
        raw_data = bytes([0x00, 0xD4, 0x30, 0xE8, 0x03, 0x00, 0x00])
        parsed = {
            'info': {'pgn': 127505, 'src': 92, 'can_id': 0x19F2115C},
            'data': raw_data,
        }
        self.reg.update(parsed)
        state = self.reg.get_sensors_state()
        self.assertEqual(state['status'], 'ok')
        self.assertEqual(len(state['fluid_levels']), 1)
        sensor = state['fluid_levels'][0]
        self.assertEqual(sensor['instance'], 0)
        self.assertAlmostEqual(sensor['level_pct'], 50.0, places=1)


class TestHAVirtualGatewayIdentity(unittest.TestCase):
    """Virtual gateway N2K identity (SA=200, PC Gateway) per N2K spec."""

    def setUp(self):
        self.dev = load_device()

    def test_virtual_device_preferred_sa_is_200(self):
        """Virtual gateway SA must be 200."""
        self.assertEqual(self.dev.GW_PREFERRED_SA, 200)

    def test_virtual_device_class_internetwork(self):
        """Device class 25 = Internetwork Device."""
        self.assertEqual(self.dev.GW_DEVICE_CLASS, 25)

    def test_virtual_device_function_pc_gateway(self):
        """Device function 130 = PC Gateway."""
        self.assertEqual(self.dev.GW_DEVICE_FUNCTION, 130)

    def test_virtual_device_model_id(self):
        """Model ID string must be YDNU-02 TCP-GW."""
        self.assertEqual(self.dev.GW_MODEL_ID, 'YDNU-02 TCP-GW')


class TestHABLERegistry(unittest.TestCase):
    """BLE tank level sensor registry for HA."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._config = os.path.join(self._tmp, 'ble_registry.json')
        self.reg = BLERegistry(config_path=self._config)
        self.reg.add('AA:BB:CC:DD:EE:FF', 'mopeka', name='Fuel Tank')
        self.reg.add('BB:CC:DD:EE:FF:00', 'gobius', name='Water Tank')

    def test_registry_returns_all_registered_sensors(self):
        """get_all() returns all registered sensors."""
        self.assertEqual(len(self.reg.get_all()), 2)

    def test_get_by_type_mopeka(self):
        """get_by_type('mopeka') returns Mopeka sensors."""
        sensors = self.reg.get_by_type('mopeka')
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0]['type'], 'mopeka')

    def test_get_by_type_gobius(self):
        """get_by_type('gobius') returns Gobius sensors."""
        sensors = self.reg.get_by_type('gobius')
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0]['type'], 'gobius')

    def test_app_version_semver(self):
        """get_app_version() returns semver string."""
        v = get_app_version()
        self.assertTrue(len(v) > 0)


class TestHADeviceRegistryNamingAndPK(unittest.TestCase):
    """Verify HA device naming and PK hash registration logic for physical (SA=64) and virtual (SA=200) devices."""

    def setUp(self):
        self.mod = load_gateway()
        self.hub = self.mod.DataHub()

    def test_dual_device_pre_registration_distinct_unique_ids(self):
        """Both physical (SA=64) and virtual (SA=200) devices are pre-registered with distinct unique IDs."""
        devices = self.hub.device_registry.get_all_devices()
        self.assertIn(64, devices)
        self.assertIn(200, devices)
        self.assertEqual(devices[64].unique_id, 402047)
        self.assertEqual(devices[200].unique_id, 902047)
        self.assertEqual(devices[64].mfg_code, 717)
        self.assertEqual(devices[200].mfg_code, 2047)

    def test_announce_all_devices_emits_both_sa64_and_sa200_frames(self):
        """announce_all_devices() outputs ISO Claim + Product Info for both SA=64 and SA=200."""
        received = []
        class FakeConn:
            def sendall(self, data):
                received.append(data)
        self.hub.clients.add(FakeConn())
        self.hub.announce_all_devices()

        self.assertTrue(any(b'18EEFF40' in line for line in received), "SA=64 ISO Claim missing")
        self.assertTrue(any(b'19F01440' in line for line in received), "SA=64 Product Info missing")
        self.assertTrue(any(b'18EEFFC8' in line for line in received), "SA=200 ISO Claim missing")
        self.assertTrue(any(b'19F014C8' in line for line in received), "SA=200 Product Info missing")

    def test_pk_hash_uniqueness_per_device_source(self):
        """PGN 126996 messages from SA=64 and SA=200 MUST generate distinct PK hashes."""
        from nmea2000.decoder import NMEA2000Decoder as LibDecoder

        # FastPacket PGN 126996 for SA=64 (physical YDNU-02)
        lines_sa64 = [
            '00:00:00.000 R 18EEFF40 7F 22 A6 59 00 82 32 C0',
            '00:00:00.000 R 19F01440 C0 86 15 05 83 19 59 44',
            '00:00:00.000 R 19F01440 C1 4E 55 2D 30 32 20 20',
            '00:00:00.000 R 19F01440 C2 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 C3 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 C4 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 C5 20 20 31 2E 37 35 20',
            '00:00:00.000 R 19F01440 C6 30 37 2F 30 38 2F 32',
            '00:00:00.000 R 19F01440 C7 30 32 35 20 20 20 20',
            '00:00:00.000 R 19F01440 C8 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 C9 20 20 20 20 20 20 4E',
            '00:00:00.000 R 19F01440 CA 4D 45 41 20 32 30 30',
            '00:00:00.000 R 19F01440 CB 30 20 55 53 42 20 47',
            '00:00:00.000 R 19F01440 CC 61 74 65 77 61 79 20',
            '00:00:00.000 R 19F01440 CD 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 CE 20 20 20 30 30 34 30',
            '00:00:00.000 R 19F01440 CF 32 30 34 37 20 20 20',
            '00:00:00.000 R 19F01440 D0 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 D1 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 D2 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F01440 D3 01 01',
        ]
        # FastPacket PGN 126996 for SA=200 (virtual TCP-GW)
        lines_sa200 = [
            '00:00:00.000 R 18EEFFC8 7F 22 A6 59 00 82 33 C0',
            '00:00:00.000 R 19F014C8 C0 86 15 05 83 19 59 44',
            '00:00:00.000 R 19F014C8 C1 4E 55 2D 30 32 20 20',
            '00:00:00.000 R 19F014C8 C2 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 C3 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 C4 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 C5 20 20 30 2E 32 2E 30',
            '00:00:00.000 R 19F014C8 C6 30 37 2F 30 38 2F 32',
            '00:00:00.000 R 19F014C8 C7 30 32 35 20 20 20 20',
            '00:00:00.000 R 19F014C8 C8 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 C9 20 20 20 20 20 20 4E',
            '00:00:00.000 R 19F014C8 CA 4D 45 41 20 32 30 30',
            '00:00:00.000 R 19F014C8 CB 30 20 55 53 42 20 47',
            '00:00:00.000 R 19F014C8 CC 61 74 65 77 61 79 20',
            '00:00:00.000 R 19F014C8 CD 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 CE 20 20 20 30 30 34 30',
            '00:00:00.000 R 19F014C8 CF 32 30 34 37 20 20 20',
            '00:00:00.000 R 19F014C8 D0 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 D1 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 D2 20 20 20 20 20 20 20',
            '00:00:00.000 R 19F014C8 D3 01 01',
        ]

        lib_dec64 = LibDecoder(build_network_map=True)
        lib_msg64 = None
        for line in lines_sa64:
            m = lib_dec64.decode(line)
            if m and m.PGN == 126996:
                lib_msg64 = m

        lib_dec200 = LibDecoder(build_network_map=True)
        lib_msg200 = None
        for line in lines_sa200:
            m = lib_dec200.decode(line)
            if m and m.PGN == 126996:
                lib_msg200 = m

        self.assertIsNotNone(lib_msg64, "SA=64 PGN 126996 message failed to decode")
        self.assertIsNotNone(lib_msg200, "SA=200 PGN 126996 message failed to decode")

        hash64 = getattr(lib_msg64, 'hash', None)
        hash200 = getattr(lib_msg200, 'hash', None)

        # COLLISION VERIFICATION ASSERTION:
        # PK hashes MUST NOT be identical between physical (SA=64) and virtual (SA=200) devices!
        self.assertNotEqual(hash64, hash200, f"PK Hash Collision detected! Both SA=64 and SA=200 share hash '{hash64}'")
        self.assertNotEqual(hash64, '818d9516db08fd90ffd1967e3c403bed', "Hash collapsed to default productInformation static MD5")

    def test_each_device_receives_independent_entities(self):
        """Both physical (SA=64) and virtual (SA=200) devices MUST have complete distinct product info."""
        dev64 = self.hub.device_registry.get_device(64)
        dev200 = self.hub.device_registry.get_device(200)

        self.assertIsNotNone(dev64)
        self.assertIsNotNone(dev200)

        self.assertTrue(dev64.is_complete, "Physical device SA=64 product info incomplete")
        self.assertTrue(dev200.is_complete, "Virtual device SA=200 product info incomplete")

        self.assertNotEqual(dev64.model_serial, dev200.model_serial)
        self.assertEqual(dev64.model_serial, "00402047")
        self.assertEqual(dev200.model_serial, "SW-GW-00902047")


if __name__ == '__main__':
    unittest.main(verbosity=2)
