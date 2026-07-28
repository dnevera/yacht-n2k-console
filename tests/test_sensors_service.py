#!/usr/bin/env python3
"""
GobiusCSensor integration tests — NMEA ↔ BLE.

NMEA = live data, self-contained. No BLE dependency.
BLE = sensor configuration (read/write).

Test data uses REAL hex dumps from hardware.
PGN 127505 broadcasts capacity=150L (current sensor config).
BLE FFF2 reads volume=150L (same config). They match.
If user wants 10L — WRITE via BLE to FFF2, sensor reconfigures.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sensors.gobius_sensor import GobiusCSensor
from gobius_parsers import compute_fill_level

try:
    from ydnu02 import N2KPGNDecoder
except ImportError:
    N2KPGNDecoder = None


# ──────────── Real sensor hex dumps ────────────
REAL_FFE8 = bytes.fromhex("05080000749a00001c2ec02ca7742156d8000001")
REAL_FFE9 = bytes.fromhex("0508010318050066005e0087017902d300000000")
REAL_FFF3 = bytes.fromhex("025c000000000000000000000000000000000000")
REAL_FFE6 = bytes.fromhex("012c003203151032053205000000000000000a00")
REAL_FFF2 = bytes.fromhex("0100010000000000009600000000000000000000")

# PGN 127505: instance=0, type=0, level=79.2%, capacity=150L
# CAN data: 00 58 4D DC 05 00 00 FF → cap bytes DC050000 LE = 1500 → 150.0L
PGN_127505_LINE = "02:51:52.952 R 19F2115C 00 58 4D DC 05 00 00 FF"
NMEA_CAPACITY_L = 150.0
NMEA_FILL_PCT = 79.2
NMEA_CALCULATED_L = 118.8  # 79.2% × 150L


class FakeSensorManager:
    """Minimal manager for tests — parses PGN 127505 into sensors."""
    def __init__(self):
        self.sensors = []

    def _update_sensor_state(self, parsed):
        if not parsed:
            return
        info = parsed.get("info", {})
        pgn = info.get("pgn") or parsed.get("pgn")
        if pgn != 127505:
            return
        src = info.get("src", 0)
        raw_bytes = parsed.get("data", b"")

        if len(raw_bytes) >= 7:
            instance = raw_bytes[0] & 0x0F
            level_raw = (raw_bytes[1] | (raw_bytes[2] << 8))
            level_pct = round(level_raw * 0.004, 1) if level_raw != 0xFFFF else None
            capacity_raw = int.from_bytes(raw_bytes[3:7], "little")
            capacity_l = round(capacity_raw * 0.1, 1) if capacity_raw != 0xFFFFFFFF else None
            type_code = (raw_bytes[0] >> 4) & 0x0F

            sensor = None
            for s in self.sensors:
                if s.instance == instance:
                    sensor = s
                    break
            if sensor is None:
                sensor = GobiusCSensor(instance=instance)
                self.sensors.append(sensor)
            sensor.update_from_nmea127505({
                "instance": instance, "level_pct": level_pct,
                "capacity_l": capacity_l, "type_code": type_code, "src": src,
            })


class TestSensorsService(unittest.TestCase):

    def setUp(self):
        self.mgr = FakeSensorManager()

    # ─── 1. NMEA self-contained (no BLE dependency) ───

    def test_nmea_standalone(self):
        """NMEA gives fill, capacity, calculated_l — no BLE needed."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": NMEA_FILL_PCT,
            "capacity_l": NMEA_CAPACITY_L, "type_code": 0, "src": 92,
        })
        d = sensor.to_dict()
        self.assertEqual(d["fill_level_pct"], 79.2)
        self.assertEqual(d["capacity_l"], 150.0, "PGN capacity")
        self.assertEqual(d["calculated_l"], 118.8, "79.2% × 150L")
        self.assertEqual(d["n2k_src"], 92)
        # BLE fields must be None — NMEA doesn't touch them
        self.assertIsNone(d["ble_fill_pct"])
        self.assertIsNone(d["volume_l"])
        self.assertIsNone(d["distance_mm"])

    def test_nmea_pgn_parsing(self):
        """Parse real PGN 127505 CAN line."""
        if N2KPGNDecoder is None:
            self.skipTest("ydnu02 not available")
        self.mgr._update_sensor_state(N2KPGNDecoder.parse_raw_line(PGN_127505_LINE))
        self.assertEqual(len(self.mgr.sensors), 1)
        s = self.mgr.sensors[0]
        self.assertEqual(s.fill_level_pct, 79.2)
        self.assertEqual(s.capacity_l, 150.0, "PGN raw DC050000 = 1500 × 0.1 = 150L")
        self.assertEqual(s.calculated_l, 118.8)
        self.assertEqual(s.n2k_src, 92)

    # ─── 2. NMEA does NOT depend on BLE ───

    def test_nmea_unaffected_by_ble(self):
        """Adding BLE data must NOT change NMEA-derived values."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": 79.2,
            "capacity_l": 150.0, "type_code": 0, "src": 92,
        })
        nmea_cap = sensor.capacity_l
        nmea_calc = sensor.calculated_l
        nmea_fill = sensor.fill_level_pct

        # Add all BLE data
        sensor.update_from_ble_status(REAL_FFE8)
        sensor.update_from_ble_measurement(REAL_FFE9)
        sensor.update_from_ble_n2k_status(REAL_FFF3)
        sensor.update_from_ble_user_cfg(REAL_FFE6)
        sensor.update_from_ble_n2k_cfg(REAL_FFF2)

        # NMEA values unchanged
        self.assertEqual(sensor.capacity_l, nmea_cap, "BLE must not change NMEA capacity")
        self.assertEqual(sensor.calculated_l, nmea_calc, "BLE must not change NMEA calc")
        self.assertEqual(sensor.fill_level_pct, nmea_fill, "BLE must not change NMEA fill")

    def test_ble_fill_does_not_override_nmea(self):
        """BLE M_FL goes to ble_fill_pct, NOT fill_level_pct."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": 79.2, "capacity_l": 150.0,
            "type_code": 0, "src": 92,
        })
        sensor.update_from_ble_measurement(REAL_FFE9)
        self.assertEqual(sensor.fill_level_pct, 79.2, "NMEA stays")
        self.assertEqual(sensor.ble_fill_pct, 79.2, "BLE stored separately")

    # ─── 3. BLE config data (read from sensor) ───

    def test_ble_status(self):
        """0xFFE8: temp, voltage, MAC."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_ble_status(REAL_FFE8)
        self.assertEqual(sensor.temp_c, 28)
        self.assertEqual(sensor.voltage_v, 11.968)
        self.assertEqual(sensor.mac_address, "2C:A7:74:21:56:D8")

    def test_ble_measurement(self):
        """0xFFE9: fill (M_FL), distance, inclination."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_ble_measurement(REAL_FFE9)
        self.assertEqual(sensor.ble_fill_pct, 79.2)
        self.assertEqual(sensor.distance_mm, 102)
        self.assertEqual(sensor.inclination_deg, 5)

    def test_ble_n2k_config(self):
        """0xFFF2: volume=150L, fluid_type=Fresh Water."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_ble_n2k_cfg(REAL_FFF2)
        self.assertEqual(sensor.volume_l, 150.0, "FFF2 byte[9]=0x96=150")
        self.assertEqual(sensor.fluid_type_name, "Fresh Water")

    def test_ble_geometry(self):
        """0xFFE6: dist_empty=300mm, dist_full=50mm, tank_depth=350mm."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_ble_user_cfg(REAL_FFE6)
        self.assertEqual(sensor.distance_empty_mm, 300)
        self.assertEqual(sensor.distance_full_mm, 50)
        self.assertEqual(sensor.tank_depth_mm, 350)

    def test_ble_n2k_status(self):
        """0xFFF3: n2k_state=2, src=92."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_ble_n2k_status(REAL_FFF3)
        self.assertEqual(sensor.ble_n2k_state, 2)
        self.assertEqual(sensor.ble_n2k_src, 92)

    # ─── 4. Cross-validation (BLE ↔ NMEA, BLE ↔ geometry) ───

    def test_ble_fill_matches_nmea_fill(self):
        """BLE M_FL (79.2%) should match NMEA PGN 127505 (79.2%)."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": 79.2, "capacity_l": 150.0,
            "type_code": 0, "src": 92,
        })
        sensor.update_from_ble_measurement(REAL_FFE9)
        self.assertEqual(sensor.ble_fill_pct, sensor.fill_level_pct)

    def test_ble_volume_matches_nmea_capacity(self):
        """BLE FFF2 volume (150L) should match NMEA PGN capacity (150L)."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": 79.2, "capacity_l": 150.0,
            "type_code": 0, "src": 92,
        })
        sensor.update_from_ble_n2k_cfg(REAL_FFF2)
        self.assertEqual(sensor.volume_l, sensor.capacity_l,
                         "BLE volume must match PGN capacity")

    def test_n2k_src_cross_match(self):
        """NMEA CAN src (92) matches BLE FFF3 src (92)."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": 79.2, "capacity_l": 150.0,
            "type_code": 0, "src": 92,
        })
        sensor.update_from_ble_n2k_status(REAL_FFF3)
        self.assertEqual(sensor.n2k_src, sensor.ble_n2k_src)

    def test_geometry_fill_matches_measured(self):
        """Computed fill from geometry (300-102)/(300-50) = 79.2% matches BLE M_FL."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_ble_user_cfg(REAL_FFE6)
        sensor.update_from_ble_n2k_cfg(REAL_FFF2)
        sensor.update_from_ble_measurement(REAL_FFE9)
        self.assertEqual(sensor.computed_fill_pct, 79.2)
        self.assertEqual(sensor.computed_fill_pct, sensor.ble_fill_pct,
                         "Geometry-computed fill must match BLE measured fill")
        self.assertEqual(sensor.computed_volume_l, 118.8, "79.2% × 150L")

    # ─── 5. Multiple instances ───

    def test_multiple_sensor_instances(self):
        """Two independent tanks on the same bus."""
        s0 = GobiusCSensor(instance=0, name="Fresh Water")
        s1 = GobiusCSensor(instance=1, name="Gray Water")
        s0.update_from_nmea127505({"instance": 0, "level_pct": 79.2,
                                    "capacity_l": 150.0, "type_code": 0, "src": 92})
        s1.update_from_nmea127505({"instance": 1, "level_pct": 45.0,
                                    "capacity_l": 50.0, "type_code": 2, "src": 93})
        self.assertEqual(s0.calculated_l, 118.8)
        self.assertEqual(s1.calculated_l, 22.5)

    # ─── 6. Full export dict ───

    def test_full_export(self):
        """All fields present in to_dict after full NMEA + BLE."""
        sensor = GobiusCSensor(instance=0)
        sensor.update_from_nmea127505({
            "instance": 0, "level_pct": 79.2, "capacity_l": 150.0,
            "type_code": 0, "src": 92,
        })
        sensor.update_from_ble_status(REAL_FFE8)
        sensor.update_from_ble_measurement(REAL_FFE9)
        sensor.update_from_ble_n2k_status(REAL_FFF3)
        sensor.update_from_ble_user_cfg(REAL_FFE6)
        sensor.update_from_ble_n2k_cfg(REAL_FFF2)
        sensor.update_from_ble_device_info({
            "serial": "697207", "firmware": "4.1.0",
            "info1": "Fresh Water", "info2": "Main Tank",
        })

        d = sensor.to_dict()
        # NMEA (self-contained)
        self.assertEqual(d["fill_level_pct"], 79.2)
        self.assertEqual(d["capacity_l"], 150.0, "PGN capacity, no BLE mixing")
        self.assertEqual(d["calculated_l"], 118.8)
        self.assertEqual(d["n2k_src"], 92)
        # BLE config
        self.assertEqual(d["volume_l"], 150.0)
        self.assertEqual(d["temp_c"], 28)
        self.assertEqual(d["voltage_v"], 11.968)
        self.assertEqual(d["mac_address"], "2C:A7:74:21:56:D8")
        self.assertEqual(d["ble_fill_pct"], 79.2)
        self.assertEqual(d["distance_mm"], 102)
        self.assertEqual(d["distance_empty_mm"], 300)
        self.assertEqual(d["distance_full_mm"], 50)
        self.assertEqual(d["tank_depth_mm"], 350)
        self.assertEqual(d["ble_n2k_state"], 2)
        self.assertEqual(d["ble_n2k_src"], 92)
        self.assertEqual(d["computed_fill_pct"], 79.2)
        self.assertEqual(d["computed_volume_l"], 118.8)
        self.assertEqual(d["serial_number"], "697207")
        self.assertEqual(d["firmware"], "4.1.0")


if __name__ == "__main__":
    unittest.main()
