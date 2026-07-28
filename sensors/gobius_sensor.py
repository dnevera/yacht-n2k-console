"""
GobiusCSensor — Gobius C radar tank sensor (BLE + NMEA 2000).

Data is stored in two nested structures:
  self.nmea: NMEAData — PGN 127505 live telemetry (fill, capacity)
  self.ble:  BLEData  — BLE config & measurements (geometry, volume, temp)

Channels are isolated. NMEA does NOT depend on BLE. BLE does NOT depend on NMEA.
All parsers live in gobius_parsers.py — no parsing logic here, only state management.
"""
from typing import Dict, Any, Optional
from sensors.base_sensor import BaseSensor
from gobius_parsers import (
    parse_status, parse_measurement, parse_n2k_status,
    parse_user_cfg, parse_n2k_cfg, compute_fill_level, FLUID_TYPES,
)


class GobiusCSensor(BaseSensor):
    """
    Gobius C sensor state object.
    NMEA 2000 (self.nmea) = sole authority for fill_level_pct & capacity_l.
    BLE (self.ble) = sensor config, telemetry, geometry.
    """

    def __init__(self, instance: int = 0, name: str = "Fresh Water"):
        super().__init__(instance=instance, name=name)
        self.comment: str = "Main Tank"

    # ─── Compat properties (tests/code access these directly) ───

    @property
    def fill_level_pct(self): return self.nmea.fill_level_pct
    @property
    def capacity_l(self): return self.nmea.capacity_l
    @property
    def calculated_l(self): return self.nmea.calculated_l
    @property
    def n2k_src(self): return self.nmea.src

    @property
    def voltage_v(self): return self.ble.voltage_v
    @property
    def temp_c(self): return self.ble.temp_c
    @property
    def uptime_s(self): return self.ble.uptime_s
    @property
    def current_range(self): return self.ble.current_range
    @property
    def mac_address(self): return self.ble.mac_address

    @property
    def ble_fill_pct(self): return self.ble.fill_pct
    @property
    def distance_mm(self): return self.ble.distance_mm
    @property
    def inclination_deg(self): return self.ble.inclination_deg

    @property
    def distance_empty_mm(self): return self.ble.distance_empty_mm
    @property
    def distance_full_mm(self): return self.ble.distance_full_mm
    @property
    def tank_depth_mm(self): return self.ble.tank_depth_mm

    @property
    def volume_l(self): return self.ble.volume_l
    @property
    def ble_n2k_state(self): return self.ble.n2k_state
    @property
    def ble_n2k_src(self): return self.ble.n2k_src
    @property
    def computed_fill_pct(self): return self.ble.computed_fill_pct
    @property
    def computed_volume_l(self): return self.ble.computed_volume_l
    @property
    def serial_number(self): return self.ble.serial_number
    @property
    def firmware(self): return self.ble.firmware

    # ─── BLE update methods (write to self.ble) ───

    def update_from_ble_status(self, raw: bytes):
        """0xFFE8 Status → ble.temp_c, voltage_v, mac, uptime, range, state, bits, error."""
        p = parse_status(raw)
        if "error" in p:
            return
        b = self.ble
        b.temp_c = p["temp_c"]
        b.voltage_v = p["voltage_v"]
        b.uptime_s = p["uptime_s"]
        b.current_range = p["current_range"]
        b.mac_address = p.get("mac", "")
        b.state_str = p.get("state_str")
        b.status_bits_str = p.get("status_bits_str")
        b.error_code = p.get("error_code")
        b.measuring = p.get("measuring")
        b.last_update = __import__('time').time()

    def update_from_ble_measurement(self, raw: bytes):
        """0xFFE9 Measurement → ble.fill_pct, distance_mm, inclination."""
        p = parse_measurement(raw)
        if "error" in p:
            return
        b = self.ble
        b.fill_pct = p["fill_pct"]
        b.fill_permille = p["fill_permille"]
        b.level_valid = p["level_valid"]
        b.distance_mm = p["distance_mm"]
        b.inclination_deg = p["inclination_deg"]
        self._recompute_from_geometry()

    # backward compat alias
    update_from_ble_radar = update_from_ble_measurement

    def update_from_ble_n2k_status(self, raw: bytes):
        """0xFFF3 N2K Status → ble.n2k_state, n2k_src."""
        p = parse_n2k_status(raw)
        if "error" in p:
            return
        self.ble.n2k_state = p["n2k_state"]
        self.ble.n2k_src = p["n2k_src"]

    def update_from_ble_user_cfg(self, raw: bytes):
        """0xFFE6 User Config → ble.distance_empty/full (geometry)."""
        p = parse_user_cfg(raw)
        if "error" in p:
            return
        self.ble.distance_empty_mm = p["distance_empty_mm"]
        self.ble.distance_full_mm = p["distance_full_mm"]
        self._recompute_from_geometry()

    def update_from_ble_n2k_cfg(self, raw: bytes):
        """0xFFF2 N2K Config → ble.volume_l, ble.fluid_type (BLE channel only)."""
        p = parse_n2k_cfg(raw)
        if "error" in p:
            return
        if p.get("volume_l"):
            self.ble.volume_l = float(p["volume_l"])
        if p.get("fluid_type") is not None:
            self.ble.fluid_type_code = p["fluid_type"]
            self.ble.fluid_type_name = p.get("fluid_type_name", FLUID_TYPES.get(p["fluid_type"], "--"))
        self._recompute_from_geometry()
        self._recalculate_nmea()

    def update_from_ble_device_info(self, info: Dict[str, Any]):
        """Device metadata from BLE GATT Device Information service."""
        if info.get("serial"):
            self.ble.serial_number = str(info["serial"])
        if info.get("firmware"):
            self.ble.firmware = str(info["firmware"])
        if info.get("info1"):
            self.name = info["info1"]
        if info.get("info2"):
            self.comment = info["info2"]

    # ─── Internal computation ───

    def _recompute_from_geometry(self):
        """Cross-validate: compute fill% from BLE geometry."""
        b = self.ble
        if (b.distance_empty_mm and b.distance_full_mm is not None
                and b.distance_mm is not None):
            b.computed_fill_pct = compute_fill_level(
                b.distance_empty_mm, b.distance_full_mm, b.distance_mm
            )
            if b.volume_l and b.volume_l > 0 and b.computed_fill_pct is not None:
                b.computed_volume_l = round(
                    (b.computed_fill_pct / 100.0) * b.volume_l, 1
                )
