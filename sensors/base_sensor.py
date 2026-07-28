"""
BaseSensor — base class with two nested data structures.

Two independent channels:
  nmea: NMEAData — live telemetry from PGN 127505
  ble:  BLEData  — sensor config & measurements via Bluetooth

Channels are completely isolated. One class handles formatting & export.
"""
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
from gobius_parsers import FLUID_TYPES


@dataclass
class NMEAData:
    """NMEA 2000 channel — PGN 127505 live data."""
    fill_level_pct: Optional[float] = None
    capacity_l: Optional[float] = None
    calculated_l: Optional[float] = None
    fluid_type_code: Optional[int] = None
    fluid_type_name: Optional[str] = None
    src: Optional[int] = None
    last_update: float = 0.0

    @property
    def age_sec(self) -> Optional[float]:
        if self.last_update > 0:
            return round(time.time() - self.last_update, 1)
        return None


@dataclass
class BLEData:
    """BLE channel — Gobius C GATT characteristics."""
    # 0xFFE8 Status
    temp_c: Optional[int] = None
    voltage_v: Optional[float] = None
    uptime_s: Optional[int] = None
    current_range: Optional[int] = None
    mac_address: str = ""
    state_str: Optional[str] = None
    status_bits_str: Optional[str] = None
    error_code: Optional[int] = None
    measuring: Optional[int] = None

    # 0xFFE9 Measurement
    fill_pct: Optional[float] = None
    fill_permille: Optional[int] = None
    level_valid: Optional[int] = None
    distance_mm: Optional[int] = None
    inclination_deg: Optional[int] = None

    # 0xFFE6 User Config (tank geometry)
    distance_empty_mm: Optional[int] = None
    distance_full_mm: Optional[int] = None

    # 0xFFF2 N2K Config
    volume_l: Optional[float] = None
    fluid_type_code: Optional[int] = None
    fluid_type_name: Optional[str] = None

    # 0xFFF3 N2K Status
    n2k_state: Optional[int] = None
    n2k_src: Optional[int] = None

    # Computed from geometry
    computed_fill_pct: Optional[float] = None
    computed_volume_l: Optional[float] = None

    # Device info
    serial_number: str = ""
    last_update: float = 0.0
    firmware: str = ""

    @property
    def tank_depth_mm(self) -> Optional[int]:
        if self.distance_empty_mm is not None and self.distance_full_mm is not None:
            return self.distance_empty_mm + self.distance_full_mm
        return None


class BaseSensor:
    """Tank sensor with two isolated data channels (NMEA + BLE)."""

    def __init__(self, instance: int = 0, name: str = ""):
        self.instance: int = instance
        self.name: str = name
        self.comment: str = ""
        self.nmea = NMEAData()
        self.ble = BLEData()

    @property
    def fluid_type_code(self) -> Optional[int]:
        """NMEA 2000 PGN fluid_type_code with fallback to BLE."""
        if self.nmea.fluid_type_code is not None:
            return self.nmea.fluid_type_code
        return self.ble.fluid_type_code

    @property
    def fluid_type_name(self) -> str:
        """NMEA 2000 PGN fluid_type_name with fallback to BLE."""
        if self.nmea.fluid_type_name is not None:
            return self.nmea.fluid_type_name
        if self.ble.fluid_type_name is not None:
            return self.ble.fluid_type_name
        return "--"

    def update_from_nmea127505(self, pgn_data: Dict[str, Any]):
        """NMEA 2000 (PGN 127505) → self.nmea only."""
        n = self.nmea
        if "level_pct" in pgn_data and pgn_data["level_pct"] is not None:
            n.fill_level_pct = pgn_data["level_pct"]
        if "capacity_l" in pgn_data and pgn_data["capacity_l"] is not None:
            n.capacity_l = pgn_data["capacity_l"]
        if "type_code" in pgn_data:
            n.fluid_type_code = pgn_data["type_code"]
            n.fluid_type_name = FLUID_TYPES.get(pgn_data["type_code"], f"Type_{pgn_data['type_code']}")
        if "src" in pgn_data:
            n.src = pgn_data["src"]
        n.last_update = time.time()
        self._recalculate_nmea()

    def _recalculate_nmea(self):
        """Calculate volume from NMEA fill% × NMEA capacity."""
        n = self.nmea
        if n.fill_level_pct is not None and n.capacity_l and n.capacity_l > 0:
            n.calculated_l = round((n.fill_level_pct / 100.0) * n.capacity_l, 1)

    def to_dict(self) -> Dict[str, Any]:
        """
        Export 3 non-interfering layers:
          1. nmea_raw: PGN 127505 raw bytes from CAN bus
          2. ble_raw:  GATT raw bytes from Bluetooth
          3. service_registry: Local service overrides (ble_registry.json)
          4. display: Dashboard view (fill% strictly from physical sensors)
        """
        n = self.nmea
        b = self.ble

        # Layer 1: NMEA RAW
        nmea_raw = asdict(n)

        # Layer 2: BLE RAW
        ble_raw = {
            "status": {
                "temp_c": b.temp_c,
                "voltage_v": b.voltage_v,
                "uptime_s": b.uptime_s,
                "current_range": b.current_range,
                "mac_address": b.mac_address,
            },
            "measurement": {
                "fill_pct": b.fill_pct,
                "distance_mm": b.distance_mm,
                "inclination_deg": b.inclination_deg,
                "level_valid": b.level_valid,
            },
            "geometry": {
                "distance_empty_mm": b.distance_empty_mm,
                "distance_full_mm": b.distance_full_mm,
                "tank_depth_mm": b.tank_depth_mm,
            },
            "n2k_config": {
                "volume_l": b.volume_l,
                "fluid_type_code": b.fluid_type_code,
                "fluid_type_name": b.fluid_type_name,
            },
            "n2k_status": {
                "state": b.n2k_state,
                "src": b.n2k_src,
            },
            "device": {
                "serial_number": b.serial_number,
                "firmware": b.firmware,
            },
        }

        # Layer 3: Service Registry Overrides
        service_registry = {
            "custom_name": self.name,
            "comment": self.comment,
            "instance": self.instance,
        }

        # Layer 4: Display View (Fill level strictly from raw physical sensors)
        resolved_fill_pct = n.fill_level_pct if n.fill_level_pct is not None else b.fill_pct
        resolved_vol = n.calculated_l if n.calculated_l is not None else b.computed_volume_l

        display = {
            "name": self.name or f"Tank {self.instance}",
            "fluid_type_name": n.fluid_type_name or b.fluid_type_name or "Tank",
            "fill_level_pct": resolved_fill_pct,
            "volume_l": resolved_vol,
            "n2k_active": b.n2k_state == 1 or n.age_sec is not None,
            "source": "NMEA 2000" if n.age_sec is not None else ("BLE" if b.fill_pct is not None else "OFFLINE"),
        }

        return {
            # Core identity
            "instance": self.instance,
            "name": self.name,
            "comment": self.comment,
            "sensor_type": "Gobius C",

            # 3 Isolated Layers
            "nmea_raw": nmea_raw,
            "ble_raw": ble_raw,
            "service_registry": service_registry,
            "display": display,

            # Legacy backward compatibility fields for existing UI / HA
            "fluid_type_code": self.fluid_type_code,
            "fluid_type_name": self.fluid_type_name,
            "type_name": self.fluid_type_name,
            "nmea": nmea_raw,
            "fill_level_pct": n.fill_level_pct,
            "level_pct": n.fill_level_pct,
            "capacity_l": n.capacity_l,
            "calculated_l": n.calculated_l,
            "n2k_src": n.src,
            "src": n.src,
            "age_sec": n.age_sec,
            "source": display["source"],

            "temp_c": b.temp_c,
            "voltage_v": b.voltage_v,
            "mac_address": b.mac_address,
            "ble_fill_pct": b.fill_pct,
            "distance_mm": b.distance_mm,
            "distance_empty_mm": b.distance_empty_mm,
            "distance_full_mm": b.distance_full_mm,
            "tank_depth_mm": b.tank_depth_mm,
            "volume_l": b.volume_l,
            "ble_n2k_state": b.n2k_state,
            "ble_n2k_src": b.n2k_src,
            "computed_fill_pct": b.computed_fill_pct,
            "computed_volume_l": b.computed_volume_l,
            "serial_number": b.serial_number,
            "firmware": b.firmware,
        }

