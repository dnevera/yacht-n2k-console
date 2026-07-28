import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from sensors.base_sensor import BaseSensor
from mopeka_parsers import parse_advertisement, compute_fill_level


@dataclass
class MopekaAdvData:
    """Mopeka BLE Advertisement telemetry data."""
    voltage_v: Optional[float] = None
    battery_pct: Optional[float] = None
    temp_c: Optional[int] = None
    tof_us: Optional[int] = None
    distance_mm: Optional[float] = None
    quality_stars: Optional[int] = None
    quality_label: Optional[str] = None
    hardware_id: Optional[int] = None
    hardware_name: Optional[str] = None
    sync_pressed: Optional[bool] = None
    accel_x: Optional[int] = None
    accel_y: Optional[int] = None
    rssi: Optional[int] = None
    mac_address: str = ""
    last_seen: float = 0.0


class MopekaSensor(BaseSensor):
    """Mopeka Pro BLE Sensor representing passive telemetry."""

    def __init__(self, mac: str, name: str = "Mopeka Sensor"):
        super().__init__(instance=0, name=name)
        self.adv = MopekaAdvData(mac_address=mac)
        
        # Local config
        self.tank_depth_mm: float = 0.0
        self.capacity_l: float = 0.0
        self.fluid_type: str = ""
        
        # Computed
        self.fill_level_pct: Optional[float] = None
        self.calculated_l: Optional[float] = None

    @property
    def sensor_type(self) -> str:
        return self.adv.hardware_name or "Mopeka Sensor"

    def update_from_advertisement(self, payload: bytes, rssi: int):
        """Parse 10-byte manufacturer data payload and update internal state."""
        parsed = parse_advertisement(payload)
        if "error" in parsed:
            return
            
        a = self.adv
        a.hardware_id = parsed["hardware_id"]
        a.hardware_name = parsed["hardware_name"]
        a.voltage_v = parsed["voltage_v"]
        a.battery_pct = parsed["battery_pct"]
        a.temp_c = parsed["temp_c"]
        a.sync_pressed = parsed["sync_pressed"]
        a.tof_us = parsed["tof_us"]
        a.distance_mm = parsed["distance_mm"]
        a.quality_stars = parsed["quality_stars"]
        a.quality_label = parsed["quality_label"]
        a.accel_x = parsed["accel_x"]
        a.accel_y = parsed["accel_y"]
        a.rssi = rssi
        a.last_seen = time.time()
        
        # Compute fill level and volume based on config
        if self.tank_depth_mm > 0 and a.distance_mm is not None:
            self.fill_level_pct = compute_fill_level(self.tank_depth_mm, a.distance_mm)
            if self.capacity_l > 0:
                self.calculated_l = round((self.fill_level_pct / 100.0) * self.capacity_l, 1)

    def to_dict(self) -> Dict[str, Any]:
        """Override BaseSensor dict for flat Mopeka API format."""
        age = round(time.time() - self.adv.last_seen, 1) if self.adv.last_seen > 0 else None
        
        return {
            "mac_address": self.adv.mac_address,
            "name": self.name,
            "sensor_type": self.sensor_type,
            "fluid_type": self.fluid_type,
            
            # Config
            "tank_depth_mm": self.tank_depth_mm,
            "capacity_l": self.capacity_l,
            
            # Computed
            "fill_level_pct": self.fill_level_pct,
            "calculated_l": self.calculated_l,
            
            # Telemetry
            "distance_mm": self.adv.distance_mm,
            "temp_c": self.adv.temp_c,
            "voltage_v": self.adv.voltage_v,
            "battery_pct": self.adv.battery_pct,
            "quality_stars": self.adv.quality_stars,
            "quality_label": self.adv.quality_label,
            "sync_pressed": self.adv.sync_pressed,
            "rssi": self.adv.rssi,
            "age_sec": age,
            "source": "BLE Advertisement" if age is not None else "OFFLINE"
        }
