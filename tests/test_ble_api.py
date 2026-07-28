"""
Integration tests for BLE sensor management — full API flow.

Tests the complete chain:
  BLE API → Registry → Gobius routes (MAC lookup)
                     → Mopeka scanner (sensor sync)

Run against live server: python3 test_ble_api.py [base_url]
Run locally: python3 -m unittest tests/test_ble_api.py -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ble_registry import BLERegistry
from mopeka_scanner import MopekaScanner


# ── Offline integration tests (no BLE hardware needed) ──

class TestRegistryToGobiusRoutes(unittest.TestCase):
    """Verify gobius routes read MAC from registry."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self._tmpfile.close()
        os.unlink(self._tmpfile.name)
        self.reg = BLERegistry(config_path=self._tmpfile.name)

    def tearDown(self):
        if os.path.exists(self._tmpfile.name):
            os.unlink(self._tmpfile.name)

    def test_no_gobius_returns_none(self):
        """_get_gobius_addr() returns None when no Gobius registered."""
        # Simulate what gobius.py does
        sensors = self.reg.get_by_type("gobius")
        self.assertEqual(sensors, [])

    def test_gobius_addr_from_registry(self):
        """After adding Gobius, get_by_type returns its MAC."""
        self.reg.add("2C:A7:74:21:56:D8", "gobius", "Fresh Water")
        sensors = self.reg.get_by_type("gobius")
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0]["mac"], "2C:A7:74:21:56:D8")

    def test_multiple_gobius_returns_first(self):
        """With multiple Gobius, first one is used."""
        self.reg.add("AA:AA:AA:AA:AA:AA", "gobius", "Tank A")
        self.reg.add("BB:BB:BB:BB:BB:BB", "gobius", "Tank B")
        sensors = self.reg.get_by_type("gobius")
        self.assertEqual(len(sensors), 2)
        # First added = first in list
        self.assertEqual(sensors[0]["mac"], "AA:AA:AA:AA:AA:AA")

    def test_remove_gobius_clears_addr(self):
        """After removing Gobius, get_by_type is empty."""
        self.reg.add("2C:A7:74:21:56:D8", "gobius", "Test")
        self.reg.remove("2C:A7:74:21:56:D8")
        self.assertEqual(self.reg.get_by_type("gobius"), [])


class TestRegistryToMopekaScanner(unittest.TestCase):
    """Verify MopekaScanner reads from registry and filters by registered MACs."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self._tmpfile.close()
        os.unlink(self._tmpfile.name)
        self.reg = BLERegistry(config_path=self._tmpfile.name)

    def tearDown(self):
        if os.path.exists(self._tmpfile.name):
            os.unlink(self._tmpfile.name)

    def test_scanner_init_from_registry(self):
        """Scanner pre-populates sensors from registry."""
        self.reg.add("F1:FD:CB:6C:B2:CC", "mopeka", "Water Tank",
                     tank_depth_mm=500, capacity_l=20, fluid_type="Fresh Water")
        scanner = MopekaScanner(registry=self.reg)
        self.assertIn("F1:FD:CB:6C:B2:CC", scanner.sensors)
        sensor = scanner.sensors["F1:FD:CB:6C:B2:CC"]
        self.assertEqual(sensor.name, "Water Tank")
        self.assertEqual(sensor.tank_depth_mm, 500)
        self.assertEqual(sensor.capacity_l, 20)

    def test_scanner_empty_without_registered(self):
        """Scanner has no sensors if registry has none."""
        scanner = MopekaScanner(registry=self.reg)
        self.assertEqual(scanner.sensors, {})

    def test_scanner_ignores_gobius(self):
        """Scanner doesn't load Gobius sensors from registry."""
        self.reg.add("AA:BB:CC:DD:EE:FF", "gobius", "Not a Mopeka")
        scanner = MopekaScanner(registry=self.reg)
        self.assertEqual(scanner.sensors, {})

    def test_scanner_get_sensors_returns_dicts(self):
        """get_sensors() returns list of dicts."""
        self.reg.add("F1:FD:CB:6C:B2:CC", "mopeka", "Tank A")
        scanner = MopekaScanner(registry=self.reg)
        result = scanner.get_sensors()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["mac_address"], "F1:FD:CB:6C:B2:CC")
        self.assertEqual(result[0]["name"], "Tank A")
        self.assertEqual(result[0]["source"], "OFFLINE")

    def test_scanner_update_config_persists_to_registry(self):
        """update_config() writes back to registry."""
        self.reg.add("F1:FD:CB:6C:B2:CC", "mopeka", "Old Name",
                     tank_depth_mm=100, capacity_l=10)
        scanner = MopekaScanner(registry=self.reg)
        scanner.update_config("F1:FD:CB:6C:B2:CC", {
            "name": "New Name", "tank_depth_mm": 999, "capacity_l": 50
        })
        # Check scanner state
        self.assertEqual(scanner.sensors["F1:FD:CB:6C:B2:CC"].name, "New Name")
        self.assertEqual(scanner.sensors["F1:FD:CB:6C:B2:CC"].tank_depth_mm, 999)
        # Check registry persistence
        sensor = self.reg.get("F1:FD:CB:6C:B2:CC")
        self.assertEqual(sensor["name"], "New Name")
        self.assertEqual(sensor["tank_depth_mm"], 999)
        self.assertEqual(sensor["capacity_l"], 50)


class TestAddRemoveFlow(unittest.TestCase):
    """Full add → verify → remove → verify flow."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self._tmpfile.close()
        os.unlink(self._tmpfile.name)
        self.reg = BLERegistry(config_path=self._tmpfile.name)

    def tearDown(self):
        if os.path.exists(self._tmpfile.name):
            os.unlink(self._tmpfile.name)

    def test_full_gobius_lifecycle(self):
        """Add gobius → visible in get_by_type → remove → gone."""
        # Add
        self.reg.add("2C:A7:74:21:56:D8", "gobius", "Fresh Water")
        self.assertEqual(len(self.reg.get_by_type("gobius")), 1)
        # Visible
        self.assertTrue(self.reg.is_registered("2C:A7:74:21:56:D8"))
        # Remove
        self.reg.remove("2C:A7:74:21:56:D8")
        self.assertEqual(len(self.reg.get_by_type("gobius")), 0)
        self.assertFalse(self.reg.is_registered("2C:A7:74:21:56:D8"))

    def test_full_mopeka_lifecycle(self):
        """Add mopeka → scanner has it → update config → persisted → remove → scanner empty."""
        # Add
        self.reg.add("F1:FD:CB:6C:B2:CC", "mopeka", "Fuel Tank",
                     tank_depth_mm=300, capacity_l=15)
        scanner = MopekaScanner(registry=self.reg)
        # Scanner has it
        self.assertIn("F1:FD:CB:6C:B2:CC", scanner.sensors)
        self.assertEqual(scanner.sensors["F1:FD:CB:6C:B2:CC"].tank_depth_mm, 300)
        # Update
        scanner.update_config("F1:FD:CB:6C:B2:CC", {"capacity_l": 99})
        self.assertEqual(self.reg.get("F1:FD:CB:6C:B2:CC")["capacity_l"], 99)
        # Remove from registry
        self.reg.remove("F1:FD:CB:6C:B2:CC")
        # New scanner instance sees nothing
        scanner2 = MopekaScanner(registry=self.reg)
        self.assertEqual(scanner2.sensors, {})

    def test_mixed_sensors_isolation(self):
        """Gobius and Mopeka don't interfere."""
        self.reg.add("AA:AA:AA:AA:AA:AA", "gobius", "G1")
        self.reg.add("BB:BB:BB:BB:BB:BB", "mopeka", "M1")
        self.reg.add("CC:CC:CC:CC:CC:CC", "gobius", "G2")

        scanner = MopekaScanner(registry=self.reg)
        # Scanner only has mopeka
        self.assertEqual(len(scanner.sensors), 1)
        self.assertIn("BB:BB:BB:BB:BB:BB", scanner.sensors)
        # Registry has all 3
        self.assertEqual(len(self.reg.get_all()), 3)
        self.assertEqual(len(self.reg.get_by_type("gobius")), 2)
        self.assertEqual(len(self.reg.get_by_type("mopeka")), 1)
        # Remove gobius doesn't affect mopeka
        self.reg.remove("AA:AA:AA:AA:AA:AA")
        self.assertEqual(len(self.reg.get_by_type("mopeka")), 1)


if __name__ == '__main__':
    unittest.main()
