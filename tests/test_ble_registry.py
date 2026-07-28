"""Tests for BLE Registry — add / remove / migrate / filter."""

import json
import os
import tempfile
import threading
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ble_registry import BLERegistry


class TestBLERegistry(unittest.TestCase):
    """Core registry CRUD operations."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self._tmpfile.close()
        os.unlink(self._tmpfile.name)  # start clean
        self.reg = BLERegistry(config_path=self._tmpfile.name)

    def tearDown(self):
        if os.path.exists(self._tmpfile.name):
            os.unlink(self._tmpfile.name)
        migrated = self._tmpfile.name + '.migrated'
        if os.path.exists(migrated):
            os.unlink(migrated)

    def test_empty_registry(self):
        self.assertEqual(self.reg.get_all(), [])

    def test_add_sensor(self):
        result = self.reg.add("AA:BB:CC:DD:EE:FF", "gobius", "Fresh Water")
        self.assertEqual(result["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(result["type"], "gobius")
        self.assertEqual(result["name"], "Fresh Water")
        self.assertEqual(len(self.reg.get_all()), 1)

    def test_add_with_extra_config(self):
        self.reg.add("AA:BB:CC:DD:EE:FF", "mopeka", "Fuel",
                     tank_depth_mm=500, capacity_l=20)
        sensor = self.reg.get("AA:BB:CC:DD:EE:FF")
        self.assertEqual(sensor["tank_depth_mm"], 500)
        self.assertEqual(sensor["capacity_l"], 20)

    def test_get_by_type(self):
        self.reg.add("AA:AA:AA:AA:AA:AA", "gobius", "Tank A")
        self.reg.add("BB:BB:BB:BB:BB:BB", "mopeka", "Tank B")
        self.reg.add("CC:CC:CC:CC:CC:CC", "gobius", "Tank C")

        gobius = self.reg.get_by_type("gobius")
        mopeka = self.reg.get_by_type("mopeka")
        self.assertEqual(len(gobius), 2)
        self.assertEqual(len(mopeka), 1)

    def test_is_registered(self):
        self.reg.add("AA:BB:CC:DD:EE:FF", "gobius", "Test")
        self.assertTrue(self.reg.is_registered("AA:BB:CC:DD:EE:FF"))
        self.assertFalse(self.reg.is_registered("11:22:33:44:55:66"))

    def test_update_sensor(self):
        self.reg.add("AA:BB:CC:DD:EE:FF", "mopeka", "Old Name")
        self.reg.update("AA:BB:CC:DD:EE:FF", {"name": "New Name", "capacity_l": 50})
        sensor = self.reg.get("AA:BB:CC:DD:EE:FF")
        self.assertEqual(sensor["name"], "New Name")
        self.assertEqual(sensor["capacity_l"], 50)
        self.assertEqual(sensor["type"], "mopeka")  # type preserved

    def test_remove_sensor(self):
        self.reg.add("AA:BB:CC:DD:EE:FF", "gobius", "Test")
        self.assertTrue(self.reg.remove("AA:BB:CC:DD:EE:FF"))
        self.assertFalse(self.reg.is_registered("AA:BB:CC:DD:EE:FF"))
        self.assertEqual(len(self.reg.get_all()), 0)

    def test_remove_nonexistent(self):
        self.assertFalse(self.reg.remove("11:22:33:44:55:66"))

    def test_persistence(self):
        """Registry survives reload from disk."""
        self.reg.add("AA:BB:CC:DD:EE:FF", "gobius", "Persistent")
        # Reload from same file
        reg2 = BLERegistry(config_path=self._tmpfile.name)
        self.assertEqual(len(reg2.get_all()), 1)
        sensor = reg2.get("AA:BB:CC:DD:EE:FF")
        self.assertEqual(sensor["name"], "Persistent")
        self.assertEqual(sensor["type"], "gobius")

    def test_json_format(self):
        """Verify saved JSON structure."""
        self.reg.add("AA:BB:CC:DD:EE:FF", "gobius", "Test")
        with open(self._tmpfile.name) as f:
            data = json.load(f)
        self.assertIn("sensors", data)
        self.assertIn("AA:BB:CC:DD:EE:FF", data["sensors"])
        self.assertEqual(data["sensors"]["AA:BB:CC:DD:EE:FF"]["type"], "gobius")


class TestBLERegistryMigration(unittest.TestCase):
    """Migration from mopeka_config.json."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, 'ble_sensors.json')
        self._old_config = os.path.join(self._tmpdir, 'mopeka_config.json')

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir)

    def _write_old_config(self, data):
        with open(self._old_config, 'w') as f:
            json.dump(data, f)

    def test_migration_imports_sensors(self):
        """Old mopeka_config.json sensors are migrated."""
        self._write_old_config({
            "sensors": {
                "F1:FD:CB:6C:B2:CC": {
                    "name": "Water Tank",
                    "tank_depth_mm": 500,
                    "capacity_l": 20,
                    "fluid_type": "Fresh Water"
                }
            }
        })
        # BLERegistry looks for mopeka_config.json in same dir as itself
        # We need to trick it — pass config_path in same dir as old config
        reg = BLERegistry.__new__(BLERegistry)
        reg._config_path = self._config_path
        reg._lock = threading.Lock()
        reg._sensors = {}
        reg._load()
        # Manual migration call (normally called in __init__)
        # Patch the path lookup
        original_abspath = os.path.abspath
        import ble_registry
        old_dirname = ble_registry.os.path.dirname
        ble_registry.os.path.dirname = lambda x: self._tmpdir
        try:
            reg._migrate_mopeka_config()
        finally:
            ble_registry.os.path.dirname = old_dirname

        sensor = reg.get("F1:FD:CB:6C:B2:CC")
        self.assertIsNotNone(sensor)
        self.assertEqual(sensor["type"], "mopeka")
        self.assertEqual(sensor["name"], "Water Tank")
        self.assertEqual(sensor["tank_depth_mm"], 500)

    def test_migration_renames_old_file(self):
        """After migration, old file is renamed to .migrated."""
        self._write_old_config({"sensors": {"AA:BB:CC:DD:EE:FF": {"name": "X"}}})
        reg = BLERegistry.__new__(BLERegistry)
        reg._config_path = self._config_path
        reg._lock = threading.Lock()
        reg._sensors = {}
        reg._load()
        import ble_registry
        old_dirname = ble_registry.os.path.dirname
        ble_registry.os.path.dirname = lambda x: self._tmpdir
        try:
            reg._migrate_mopeka_config()
        finally:
            ble_registry.os.path.dirname = old_dirname

        self.assertFalse(os.path.exists(self._old_config))
        self.assertTrue(os.path.exists(self._old_config + '.migrated'))

    def test_no_migration_without_old_file(self):
        """No error when mopeka_config.json doesn't exist."""
        reg = BLERegistry(config_path=self._config_path)
        self.assertEqual(len(reg.get_all()), 0)


class TestBLERegistryMultipleGobius(unittest.TestCase):
    """Support for multiple Gobius sensors."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self._tmpfile.close()
        os.unlink(self._tmpfile.name)
        self.reg = BLERegistry(config_path=self._tmpfile.name)

    def tearDown(self):
        if os.path.exists(self._tmpfile.name):
            os.unlink(self._tmpfile.name)

    def test_multiple_gobius(self):
        self.reg.add("AA:AA:AA:AA:AA:AA", "gobius", "Fresh Water")
        self.reg.add("BB:BB:BB:BB:BB:BB", "gobius", "Waste Water")
        self.reg.add("CC:CC:CC:CC:CC:CC", "gobius", "Fuel")

        gobius = self.reg.get_by_type("gobius")
        self.assertEqual(len(gobius), 3)
        names = [s["name"] for s in gobius]
        self.assertIn("Fresh Water", names)
        self.assertIn("Waste Water", names)
        self.assertIn("Fuel", names)

    def test_remove_one_keeps_others(self):
        self.reg.add("AA:AA:AA:AA:AA:AA", "gobius", "Tank A")
        self.reg.add("BB:BB:BB:BB:BB:BB", "gobius", "Tank B")

        self.reg.remove("AA:AA:AA:AA:AA:AA")
        self.assertEqual(len(self.reg.get_by_type("gobius")), 1)
        self.assertEqual(self.reg.get_by_type("gobius")[0]["name"], "Tank B")


if __name__ == '__main__':
    unittest.main()
