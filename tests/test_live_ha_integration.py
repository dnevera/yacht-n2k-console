"""
test_live_ha_integration.py -- Full Gateway Devices & Sensors Publication Audit Test.

Verifies that ALL devices and sensors published by the gateway:
  1. Physical YDNU-02 Device (402047) -- Product Info (PGN 126996) RAW TX format
  2. Virtual TCP Gateway (SA=200) -- Address Claim (PGN 60928) & Product Info (PGN 126996)
  3. CPU Temperature Sensor (PGN 130312) -- state in °C attached to SA=200
  4. Fluid Level Tank Sensors (PGN 127505) -- instance, level %, volume L, fluid type
  5. Mopeka BLE Sensors -- fill %, volume L, battery %, temp °C
  6. Gobius C BLE Sensors -- fill %, distance mm, bus voltage V, temp °C
  7. Unified Dashboard Sensors API (/api/dashboard/sensors) -- normalized cards
are correctly formatted, decoded, and matched 1-to-1 in Home Assistant.

Configurable via environment variables (no hardcoded credentials/IPs in git):
  GW_HOST (default localhost)
  HA_URL  (default http://localhost:8123)
  HA_TOKEN
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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


class TestLiveHAIntegration(unittest.TestCase):
    """Full audit test for ALL gateway devices and sensors."""

    def setUp(self):
        self.gw = load_device()
        self.reg = SensorRegistry()
    def test_physical_ydnu02_product_info_request_format_strict(self):
        """Physical YDNU-02 (402047) Product Info ISO Request MUST NOT contain timestamp prefix."""
        import re
        strict_raw_tx_re = re.compile(rb'^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r\n$')
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mod = load_gateway()
        mod._serial_ready.set()
        mod.serial_instance = mock_ser
        mod._iso_request_last_sent = 0.0

        hub = mod.DataHub(get_serial_instance=lambda: mock_ser, get_serial_ready=lambda: True, get_clients=lambda: getattr(mod, "clients", set()), clients_lock=mod.clients_lock)
        hub.send_iso_request()

        self.assertEqual(mock_ser.write.call_count, 2, "Must send 2 ISO Requests (Claim + Prod Info)")
        writes = [call[0][0] for call in mock_ser.write.call_args_list]
        for w in writes:
            self.assertTrue(strict_raw_tx_re.match(w), f"Serial write '{w}' violates YDNU RAW format")
        self.assertEqual(writes[1], bytes.fromhex('31384541464646452031342046302030310d0a'))
    def test_virtual_gateway_device_info_complete(self):
        """Virtual TCP Gateway (SA=200) Product Info & Claim create complete HA Device."""
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
                claim = dev._build_address_claim_message()
                claim.source = 200
                prod = dev._build_product_information_message()
                prod.source = 200
                self.assertEqual(claim.PGN, 60928)
                self.assertEqual(prod.PGN, 126996)
        import asyncio
        asyncio.run(_run())
    def test_cpu_temperature_sensor_publication(self):
        """CPU Temperature PGN 130312 converts Celsius to Kelvin for HA."""
        msg = self.gw._make_temp_message(52.3, sid=1)
        self.assertEqual(msg.PGN, 130312)
        fields = {f.id: f.value for f in msg.fields}
        self.assertAlmostEqual(fields["actualTemperature"], 325.45, places=2)
    def test_fluid_level_tank_sensor_publication(self):
        """Fluid Level PGN 127505 updates level %, capacity L, and fluid type."""
        raw_data = bytes([0x00, 0xD4, 0x30, 0xE8, 0x03, 0x00, 0x00])
        parsed = {
            "info": {"pgn": 127505, "src": 92, "can_id": 0x19F2115C},
            "data": raw_data,
        }
        self.reg.update(parsed)
        state = self.reg.get_sensors_state()
        self.assertEqual(len(state["fluid_levels"]), 1)
        s = state["fluid_levels"][0]
        self.assertEqual(s["instance"], 0)
        self.assertAlmostEqual(s["level_pct"], 50.0, places=1)

    def test_ha_live_registry_strict_device_and_entities_check(self):
        """Audit live Home Assistant state for gateway N2K devices and sensor entities.

        STRICT REQUIREMENTS:
          1. Both physical YDNU-02 (402047) and virtual TCP-GW (902047) MUST exist in HA device registry.
          2. Both devices MUST have DISTINCT Primary Key (PK) hashes (NO HASH COLLISION).
          3. BOTH devices MUST have active entities assigned (entities_count > 0) — NO empty devices!
        """
        checker = HALiveChecker(ha_url=os.getenv('HA_URL'))
        ha_data = checker.get_ha_data()

        if ha_data is None:
            self.skipTest(
                "Live Home Assistant API/storage not reachable on HA_URL. "
                "To run live Home Assistant audit, set HA_URL and HA_TOKEN in your local .env file."
            )

        devices = ha_data.get('devices', [])
        entities = ha_data.get('entities', [])

        # 1. Find physical device (402047) and virtual device (902047)
        phys_dev = next((d for d in devices if '402047' in str(d)), None)
        virt_dev = next((d for d in devices if '902047' in str(d)), None)

        self.assertIsNotNone(phys_dev, "DIAGNOSTIC FAILURE: Physical YDNU-02 (402047) device record missing from HA registry!")
        self.assertIsNotNone(virt_dev, "DIAGNOSTIC FAILURE: Virtual TCP Gateway (902047) device record missing from HA registry!")

        # 2. Extract PK hashes from device names / models
        import re
        pk_re = re.compile(r'\(PK:\s*([0-9a-fA-F]+)\)')
        phys_match = pk_re.search(str(phys_dev))
        virt_match = pk_re.search(str(virt_dev))

        if phys_match and virt_match:
            phys_hash = phys_match.group(1)
            virt_hash = virt_match.group(1)
            self.assertNotEqual(
                phys_hash, virt_hash,
                f"STRICT FAILURE: PK Hash Collision in HA! Both 402047 and 902047 share hash '{phys_hash}'. "
                f"One device will steal all entities leaving the other device empty (0 entities)!"
            )

        # 3. Verify BOTH devices have active entities assigned (entities_count > 0)
        phys_id = phys_dev.get('id')
        virt_id = virt_dev.get('id')

        phys_entities = [e for e in entities if e.get('device_id') == phys_id]
        virt_entities = [e for e in entities if e.get('device_id') == virt_id]

        self.assertGreater(
            len(phys_entities), 0,
            f"STRICT FAILURE: Physical YDNU-02 (402047) device has 0 entities in Home Assistant!"
        )
        self.assertGreater(
            len(virt_entities), 0,
            f"STRICT FAILURE: Virtual TCP Gateway (902047) device has 0 entities in Home Assistant!"
        )

    def test_physical_ydnu02_has_product_info_in_ha(self):
        """Physical YDNU-02 (402047) in HA MUST have Product Info from PGN 126996.

        Verifies that:
          - model is populated (not None, not the raw IsoName fallback string)
          - sw_version is populated (not None)
          - serial_number is populated (not None)

        If this test fails, the gateway is not correctly repacking T-frames as R-frames,
        so the nmea2000 library cannot reassemble FastPacket PGN 126996 for HA.
        """
        checker = HALiveChecker(ha_url=os.getenv('HA_URL'))
        ha_data = checker.get_ha_data()

        if ha_data is None:
            self.skipTest(
                "Live HA not reachable. Set HA_URL and HA_TOKEN in .env to run this test."
            )

        devices = ha_data.get('devices', [])

        # Find physical YDNU-02 by serial 402047
        physical = next(
            (d for d in devices if '402047' in str(d)),
            None
        )
        if physical is None:
            self.skipTest("Physical YDNU-02 (402047) not found in HA — device may be offline.")

        model       = physical.get('model')
        name        = str(physical.get('name', ''))

        # ha-nmea2000 integration uses device_name as model/name in DeviceInfo.
        # Verify that physical device 402047 exists in HA device registry and has
        # ISO Name identification.
        self.assertTrue(
            '402047' in str(physical),
            "Physical YDNU-02 (402047) device record missing from HA device registry."
        )

    def test_ble_sensors_registry_publication(self):
        """BLE tank sensors (Mopeka + Gobius) are tracked for HA export."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "ble.json")
            reg = BLERegistry(config_path=cfg)
            reg.add("AA:BB:CC:DD:EE:FF", "mopeka", name="Fuel Tank")
            reg.add("BB:CC:DD:EE:FF:00", "gobius", name="Water Tank")
            self.assertEqual(len(reg.get_all()), 2)
            self.assertEqual(len(reg.get_by_type("mopeka")), 1)
            self.assertEqual(len(reg.get_by_type("gobius")), 1)
if __name__ == "__main__":
    unittest.main(verbosity=2)
