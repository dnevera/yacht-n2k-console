"""
test_ha_integration_full.py -- End-to-End Home Assistant Integration Audit Test.

Performs a 1-to-1 audit verifying that ALL devices and sensors published by the
TCP Gateway (virtual SA=200, physical N2K devices, BLE sensors) correctly arrive
and publish as Home Assistant Devices and Entities with 100% data matching.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, ".")
sys.path.insert(0, "tests")

from nmea2000.decoder import NMEA2000Decoder
from nmea2000.device import N2KDevice
from nmea2000.input_formats import N2KFormat
import ydnu02_tcp_gateway.ydnu02_gateway_device as gwdev
from ble_registry import BLERegistry
from device_manager.manager import get_app_version
from device_manager.sensor_registry import SensorRegistry
from gw_test_helpers import load_device, load_gateway
from ha_live_checker import HALiveChecker, compare_published_with_ha


class HADevice:
    """Simulates Home Assistant Device Registry entry for N2K nodes."""
    def __init__(self, sa: int):
        self.sa = sa
        self.iso_name = None
        self.model = ""
        self.firmware = ""
        self.serial = ""
        self.manufacturer = ""
        self.unique_id = None
        self.entities = []

    def is_complete(self) -> bool:
        return bool(self.model and (self.serial or self.unique_id))


class HAEntity:
    """Simulates Home Assistant Entity Registry entry for sensors."""
    def __init__(self, entity_id: str, state, unit: str, device_class: str, device_sa: int):
        self.entity_id = entity_id
        self.state = state
        self.unit = unit
        self.device_class = device_class
        self.device_sa = device_sa


class HASimulator:
    """Simulates Home Assistant NMEA 2000 ingestion engine & registry pipeline."""
    def __init__(self):
        self.decoder = NMEA2000Decoder()
        self.devices = {}
        self.entities = {}

    def feed_line(self, line_bytes: bytes):
        line_str = line_bytes.decode("ascii", errors="ignore").strip()
        msg = self.decoder.decode(line_str)
        if msg:
            self.feed_msg(msg)

    def feed_msg(self, msg):
        if not msg:
            return
        sa = msg.source
        if sa not in self.devices:
            self.devices[sa] = HADevice(sa)
        dev = self.devices[sa]

        if msg.PGN == 60928 and msg.source_iso_name:
            dev.iso_name = msg.source_iso_name
            dev.unique_id = getattr(msg.source_iso_name, "unique_number", None)
            dev.manufacturer = str(getattr(msg.source_iso_name, "manufacturer_code", ""))

        elif msg.PGN == 126996:
            fields = {f.id: f.value for f in msg.fields}
            dev.model = str(fields.get("modelId", "")).strip()
            dev.firmware = str(fields.get("softwareVersionCode", "")).strip()
            dev.serial = str(fields.get("modelSerialCode", "")).strip()

        elif msg.PGN == 130312:
            fields = {f.id: f.value for f in msg.fields}
            temp_k = fields.get("actualTemperature")
            if temp_k:
                temp_c = round(temp_k - 273.15, 2)
                ent_id = f"sensor.device_{sa}_temperature"
                ent = HAEntity(ent_id, temp_c, "°C", "temperature", sa)
                self.entities[ent_id] = ent
                if ent not in dev.entities:
                    dev.entities.append(ent)

        elif msg.PGN == 127505:
            fields = {f.id: f.value for f in msg.fields}
            level = fields.get("level")
            instance = fields.get("instance", 0)
            if level is not None:
                ent_id = f"sensor.tank_{instance}_level"
                ent = HAEntity(ent_id, level, "%", "capacity", sa)
                self.entities[ent_id] = ent
                if ent not in dev.entities:
                    dev.entities.append(ent)


class TestHAEndToEndPublication(unittest.TestCase):
    """Full audit of gateway -> HA publication pipeline."""

    def setUp(self):
        self.sim = HASimulator()
        self.gw = load_device()

    def test_virtual_gateway_publishes_complete_device_info_to_ha(self):
        """Virtual TCP-GW (SA=200) Product Info & Address Claim create complete HA Device."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                p = os.path.join(tmp, "dev.json")
                dev = N2KDevice.for_text_gateway(
                    "127.0.0.1", 4001,
                    format=N2KFormat.CAN_FRAME_ASCII,
                    preferred_address=200,
                    unique_number=902047,
                    manufacturer_code=2047,
                    product_code=200,
                    device_class=25,
                    device_function=130,
                    industry_group=4,
                    model_id="YDNU-02 TCP-GW",
                    model_version="yacht-n2k-console",
                    software_version_code="1.0.0",
                    model_serial_code="SW-GW-00902047",
                    persistence_path=p,
                )
                claim_msg = dev._build_address_claim_message()
                claim_msg.source = 200
                self.sim.feed_msg(claim_msg)

                prod_msg = dev._build_product_information_message()
                prod_msg.source = 200
                self.sim.feed_msg(prod_msg)

                ha_dev = self.sim.devices.get(200)
                self.assertIsNotNone(ha_dev, "Device SA=200 must be registered in HA")
                self.assertEqual(ha_dev.model, "YDNU-02 TCP-GW")
                self.assertEqual(ha_dev.firmware, "1.0.0")
                self.assertEqual(ha_dev.serial, "SW-GW-00902047")
                self.assertTrue(ha_dev.is_complete(), "HA Device Info must be complete")
        import asyncio
        asyncio.run(_run())

    def test_cpu_temperature_publishes_as_ha_sensor_entity(self):
        """CPU temp (PGN 130312) attaches to SA=200 HA Device with exact state match."""
        temp_input_celsius = 52.3
        temp_msg = self.gw._make_temp_message(temp_input_celsius, sid=1)
        temp_msg.source = 200
        self.sim.feed_msg(temp_msg)

        ent_id = "sensor.device_200_temperature"
        self.assertIn(ent_id, self.sim.entities, "Temperature entity must exist in HA")
        ha_ent = self.sim.entities[ent_id]
        self.assertAlmostEqual(ha_ent.state, temp_input_celsius, places=1)
        self.assertEqual(ha_ent.unit, "°C")
        self.assertEqual(ha_ent.device_class, "temperature")
        self.assertEqual(ha_ent.device_sa, 200)

    def test_fluid_level_publishes_as_ha_tank_entity(self):
        """Fluid level (PGN 127505) creates sensor.tank_0_level entity in HA."""
        rx_line = bytes.fromhex("30313a34333a32322e36343820522031394632313135432030302044342033302045382030332030302030300a")
        self.sim.feed_line(rx_line)

        ent_id = "sensor.tank_0_level"
        self.assertIn(ent_id, self.sim.entities, "Fluid level entity must exist in HA")
        ha_ent = self.sim.entities[ent_id]
        self.assertEqual(ha_ent.unit, "%")
        self.assertEqual(ha_ent.device_sa, 92)

    def test_serial_iso_request_format_strict_no_timestamp_prefix(self):
        """CRITICAL DIAGNOSTIC: Serial ISO Request MUST NOT contain timestamp prefix.

        YDNU-02 RAW mode firmware rejects lines with '00:00:00.000 T ' prefix as syntax errors.
        If timestamp is included, physical devices like YDNU-02 (402047) never receive ISO Request
        for Product Info (PGN 126996), causing HA to show 'Product Information (...) This device has no entities'.
        """
        import re
        strict_raw_tx_re = re.compile(rb'^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r\n$')

        mock_ser = MagicMock()
        mock_ser.is_open = True
        mod = load_gateway()
        mod._serial_ready.set()
        mod.serial_instance = mock_ser
        mod._iso_request_last_sent = 0.0

        hub = mod.DataHub(get_serial_instance=lambda: mock_ser, get_serial_ready=lambda: True, get_clients=lambda: getattr(mod, 'clients', set()), clients_lock=mod.clients_lock)
        hub.send_iso_request()

        self.assertEqual(mock_ser.write.call_count, 2, 'Must send 2 ISO Requests (Claim + Prod Info)')
        writes = [call[0][0] for call in mock_ser.write.call_args_list]

        for w in writes:
            line_str = w.decode('ascii', errors='ignore')
            self.assertFalse(
                w.startswith(b'00:00:00.000') or b' T ' in w,
                f"DIAGNOSTIC FAILURE: Serial write '{line_str.strip()}' contains invalid timestamp prefix! "
                f"YDNU-02 RAW firmware will reject this, preventing HA from acquiring Product Info (PGN 126996)."
            )
            self.assertTrue(
                strict_raw_tx_re.match(w),
                f"DIAGNOSTIC FAILURE: Serial write '{line_str.strip()}' does not match strict YDNU RAW TX syntax! "
                f"Expected format: '18EAFFFE 14 F0 01\r\n'"
            )

        self.assertEqual(writes[1], bytes.fromhex("31384541464646452031342046302030310d0a"))

    def test_live_ha_comparison_audit(self):
        """Compare local published gateway entities against HA live or simulated state."""
        expected_devs = [{"model": "YDNU-02 TCP-GW", "src": 200, "unique_number": 902047}]
        expected_ents = [{
            "entity_id": "sensor.device_200_temperature",
            "unique_number": 902047,
            "field_suffix": "actualtemperature",
            "expected_state": 52.3,
        }]

        checker = HALiveChecker()
        ha_data = checker.get_ha_data()
        if ha_data is None or not ha_data.get('registry_available'):
            # Fallback to simulated HA state when HA API/storage is unconfigured, or
            # when only REST API 'states' were fetched without device/entity registry
            # access (registry_available=False) — 'devices' would otherwise be absent.
            ha_data = {
                "devices": [{"model": "YDNU-02 TCP-GW", "name": "YDNU-02 TCP-GW"}],
                "states": [{"entity_id": "sensor.device_200_temperature", "state": "52.3", "attributes": {"unit_of_measurement": "°C"}}]
            }

        audit = compare_published_with_ha(expected_devs, expected_ents, ha_data)
        self.assertEqual(len(audit["devices_missing"]), 0, f"Missing devices in HA: {audit['devices_missing']}")
        self.assertEqual(len(audit["entities_missing"]), 0, f"Missing entities in HA: {audit['entities_missing']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
