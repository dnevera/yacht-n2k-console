"""
Unified BLE Sensor Registry.

Single JSON file (ble_sensors.json) stores all BLE sensors — Gobius, Mopeka, future types.
Replaces hardcoded GOBIUS_ADDR and mopeka_config.json.
"""

import json
import os
import threading
from typing import Dict, List, Optional


class BLERegistry:
    """Thread-safe registry for BLE sensors persisted to ble_sensors.json."""

    def __init__(self, config_path=None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ble_sensors.json')
        self._config_path = config_path
        self._lock = threading.Lock()
        self._sensors: Dict[str, dict] = {}  # mac -> {type, name, ...extra config}
        self._load()
        self._migrate_mopeka_config()

    # ── Public API ──

    def get_all(self) -> List[dict]:
        """Return all sensors as list of dicts with mac included."""
        with self._lock:
            return [{"mac": mac, **cfg} for mac, cfg in self._sensors.items()]

    def get_by_type(self, sensor_type: str) -> List[dict]:
        """Return sensors filtered by type (e.g. 'gobius', 'mopeka')."""
        with self._lock:
            return [
                {"mac": mac, **cfg}
                for mac, cfg in self._sensors.items()
                if cfg.get("type") == sensor_type
            ]

    def get(self, mac: str) -> Optional[dict]:
        """Return single sensor config or None."""
        with self._lock:
            cfg = self._sensors.get(mac)
            if cfg:
                return {"mac": mac, **cfg}
            return None

    def is_registered(self, mac: str) -> bool:
        """Check if MAC is in registry."""
        with self._lock:
            return mac in self._sensors

    def add(self, mac: str, sensor_type: str, name: str = "", **extra) -> dict:
        """Add a sensor to registry and save."""
        with self._lock:
            entry = {"type": sensor_type, "name": name, **extra}
            self._sensors[mac] = entry
            self._save()
            return {"mac": mac, **entry}

    def update(self, mac: str, config: dict):
        """Update sensor config fields and save."""
        with self._lock:
            if mac not in self._sensors:
                return
            self._sensors[mac].update(config)
            self._save()

    def remove(self, mac: str) -> bool:
        """Remove sensor from registry. Returns True if existed."""
        with self._lock:
            if mac in self._sensors:
                del self._sensors[mac]
                self._save()
                return True
            return False

    # ── Persistence ──

    def _load(self):
        if not os.path.exists(self._config_path):
            return
        try:
            with open(self._config_path, 'r') as f:
                data = json.load(f)
            self._sensors = data.get("sensors", {})
        except Exception as e:
            print(f"[BLERegistry] Error loading {self._config_path}: {e}")

    def _save(self):
        try:
            with open(self._config_path, 'w') as f:
                json.dump({"sensors": self._sensors}, f, indent=2)
        except Exception as e:
            print(f"[BLERegistry] Error saving {self._config_path}: {e}")

    # ── Migration ──

    def _migrate_mopeka_config(self):
        """Auto-migrate mopeka_config.json → ble_sensors.json (one-time)."""
        old_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mopeka_config.json')
        if not os.path.exists(old_path):
            return

        try:
            with open(old_path, 'r') as f:
                old_data = json.load(f)

            migrated = 0
            for mac, cfg in old_data.get("sensors", {}).items():
                if mac not in self._sensors:
                    self._sensors[mac] = {
                        "type": "mopeka",
                        "name": cfg.get("name", "Mopeka Sensor"),
                        "tank_depth_mm": cfg.get("tank_depth_mm", 0),
                        "capacity_l": cfg.get("capacity_l", 0),
                        "fluid_type": cfg.get("fluid_type", ""),
                    }
                    migrated += 1

            if migrated > 0:
                self._save()
                print(f"[BLERegistry] Migrated {migrated} sensor(s) from mopeka_config.json")

            # Rename old file so migration doesn't repeat
            os.rename(old_path, old_path + '.migrated')
            print(f"[BLERegistry] Renamed {old_path} → .migrated")

        except Exception as e:
            print(f"[BLERegistry] Migration error: {e}")
