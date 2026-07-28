"""
tests/test_n2k_commands.py — Unit tests for PGN 126208 commands & 3-layer data model.
"""
import unittest
from n2k_command_builder import build_pgn_126208_command, build_iso_request_frame
from sensors.base_sensor import BaseSensor


class TestN2KCommands(unittest.TestCase):

    def test_build_iso_request_frame(self):
        """Test PGN 59904 ISO Request payload generation."""
        hex_60928 = build_iso_request_frame(60928)
        self.assertEqual(hex_60928, "00 EE 00")

        hex_126996 = build_iso_request_frame(126996)
        self.assertEqual(hex_126996, "14 F0 01")

    def test_build_pgn_126208_command(self):
        """Test PGN 126208 Group Function Command generation."""
        cmd = build_pgn_126208_command(
            target_address=92,
            instance=1,
            fluid_type_code=1,  # Fresh Water
            capacity_l=200.0,
        )

        self.assertEqual(cmd["pgn"], 126208)
        self.assertEqual(cmd["dst"], 92)
        self.assertEqual(cmd["params"]["target_pgn"], 127505)
        self.assertEqual(cmd["params"]["instance"], 1)
        self.assertEqual(cmd["params"]["fluid_type_code"], 1)
        self.assertEqual(cmd["params"]["capacity_l"], 200.0)
        self.assertTrue(len(cmd["bytes"]) > 0)

    def test_base_sensor_3_layer_non_interference(self):
        """Test 3-layer data model ensuring BLE does NOT overwrite NMEA raw data."""
        sensor = BaseSensor(instance=0, name="Fresh Water Tank")

        # Populate NMEA channel (PGN 127505) with Fresh Water (code 1)
        sensor.update_from_nmea127505({
            "level_pct": 75.5,
            "capacity_l": 200.0,
            "type_code": 1,
            "src": 92,
        })

        # Simulate Gobius C buggy BLE reading (sending fluid_type_code 0 Fuel)
        sensor.ble.fluid_type_code = 0
        sensor.ble.fluid_type_name = "Fuel"
        sensor.ble.fill_pct = 75.5

        data = sensor.to_dict()

        # 1. Layer 1 (nmea_raw) must contain untouched NMEA data (Fresh Water code 1)
        self.assertEqual(data["nmea_raw"]["fluid_type_code"], 1)
        self.assertEqual(data["nmea_raw"]["fluid_type_name"], "Fresh Water")

        # 2. Layer 2 (ble_raw) must contain raw BLE data
        self.assertEqual(data["ble_raw"]["n2k_config"]["fluid_type_code"], 0)

        # 3. Layer 3 (service_registry) contains user identity
        self.assertEqual(data["service_registry"]["instance"], 0)
        self.assertEqual(data["service_registry"]["custom_name"], "Fresh Water Tank")

        # 4. Layer 4 (display) fill_level_pct MUST be 75.5 strictly from raw physical sensors
        self.assertEqual(data["display"]["fill_level_pct"], 75.5)
        self.assertEqual(data["display"]["fluid_type_name"], "Fresh Water")


if __name__ == "__main__":
    unittest.main()

