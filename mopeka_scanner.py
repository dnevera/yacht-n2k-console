"""
Mopeka BLE passive scanner.

Listens for BLE advertisements from Mopeka Pro 200 sensors.
Only processes advertisements from sensors registered in BLERegistry.
"""

import os
from typing import Dict, Optional

try:
    from bleak import BleakScanner
except ImportError:
    BleakScanner = None

from sensors.mopeka_sensor import MopekaSensor


class MopekaScanner:
    """Background scanner for Mopeka BLE advertisements."""

    def __init__(self, registry=None):
        self.sensors: Dict[str, MopekaSensor] = {}  # mac -> sensor
        self._scanner: Optional[BleakScanner] = None
        self._running = False
        self._registry = registry
        self._init_from_registry()

    def _init_from_registry(self):
        """Pre-populate sensors dict from registry."""
        if not self._registry:
            return
        for entry in self._registry.get_by_type("mopeka"):
            mac = entry["mac"]
            sensor = MopekaSensor(mac=mac, name=entry.get("name", "Mopeka Sensor"))
            sensor.tank_depth_mm = entry.get("tank_depth_mm", 0.0)
            sensor.capacity_l = entry.get("capacity_l", 0.0)
            sensor.fluid_type = entry.get("fluid_type", "")
            self.sensors[mac] = sensor

    async def start(self):
        """Start passive BLE scanning in background."""
        if BleakScanner is None:
            print("[Mopeka] bleak library not installed, scanner disabled.")
            return

        if self._running:
            return

        self._scanner = BleakScanner(detection_callback=self._detection_callback)
        try:
            await self._scanner.start()
            self._running = True
            print("[Mopeka] BLE scanner started.")
        except Exception as e:
            print(f"[Mopeka] Failed to start scanner: {e}")

    async def stop(self):
        """Stop BLE scanning."""
        if self._scanner and self._running:
            await self._scanner.stop()
            self._running = False
            print("[Mopeka] BLE scanner stopped.")

    def _detection_callback(self, device, advertisement_data):
        """Called for each BLE advertisement. Only process registered MACs."""
        if 0x0059 not in advertisement_data.manufacturer_data:
            return

        mac = device.address

        # Only process registered sensors
        if self._registry and not self._registry.is_registered(mac):
            return

        payload = advertisement_data.manufacturer_data[0x0059]
        rssi = advertisement_data.rssi

        if mac not in self.sensors:
            # Sensor is registered but not yet in memory
            entry = self._registry.get(mac) if self._registry else {}
            self.sensors[mac] = MopekaSensor(
                mac=mac,
                name=(entry or {}).get("name", "Mopeka Sensor"),
            )
            s = self.sensors[mac]
            s.tank_depth_mm = (entry or {}).get("tank_depth_mm", 0.0)
            s.capacity_l = (entry or {}).get("capacity_l", 0.0)
            s.fluid_type = (entry or {}).get("fluid_type", "")

        self.sensors[mac].update_from_advertisement(payload, rssi)

    def get_sensors(self) -> list:
        """Return all sensors as list of dicts."""
        return [s.to_dict() for s in self.sensors.values()]

    def get_sensor(self, mac: str) -> Optional[dict]:
        """Return specific sensor by MAC."""
        if mac in self.sensors:
            return self.sensors[mac].to_dict()
        return None

    def update_config(self, mac: str, config: dict):
        """Update tank config and persist to registry."""
        if mac not in self.sensors:
            self.sensors[mac] = MopekaSensor(mac=mac)

        sensor = self.sensors[mac]
        if "name" in config:
            sensor.name = config["name"]
        if "tank_depth_mm" in config:
            sensor.tank_depth_mm = float(config["tank_depth_mm"])
        if "capacity_l" in config:
            sensor.capacity_l = float(config["capacity_l"])
        if "fluid_type" in config:
            sensor.fluid_type = config["fluid_type"]

        # Re-compute immediately if we have a valid past reading
        if sensor.adv.distance_mm is not None and sensor.tank_depth_mm > 0:
            from mopeka_parsers import compute_fill_level
            sensor.fill_level_pct = compute_fill_level(sensor.tank_depth_mm, sensor.adv.distance_mm)
            if sensor.capacity_l > 0:
                sensor.calculated_l = round((sensor.fill_level_pct / 100.0) * sensor.capacity_l, 1)

        # Persist to registry
        if self._registry:
            self._registry.update(mac, {
                "name": sensor.name,
                "tank_depth_mm": sensor.tank_depth_mm,
                "capacity_l": sensor.capacity_l,
                "fluid_type": sensor.fluid_type,
            })
