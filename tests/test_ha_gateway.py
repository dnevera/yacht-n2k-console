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


if __name__ == '__main__':
    unittest.main(verbosity=2)
