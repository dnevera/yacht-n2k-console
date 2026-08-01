"""Tracks N2K device state and sensor readings from live bus traffic.

Maintains per-SA device info (ISO Claims, Product Info) and per-instance
sensor readings (fluid levels, temperatures).
"""

import threading
from typing import Dict, Any, Optional
from ydnu02 import N2KPGNDecoder
from sensors import GobiusCSensor


class SensorRegistry:
    """Thread-safe sensor state and N2K bus device tracker.

    PGN dispatch:
      60928  → ISO Address Claim → device identity cache
      126996 → Product Information → device model/version (fast-packet reassembly)
      127505 → Fluid Level → GobiusCSensor update

    Locking:
        Receives external lock reference (owned by DeviceManager facade).
    """

    def __init__(self, lock: Optional[threading.Lock] = None):
        self._lock = lock or threading.Lock()
        self.sensors: Dict[int, GobiusCSensor] = {}
        self.discovered_bus_devices: Dict[int, Dict[str, Any]] = {}

    def update(self, parsed: Dict[str, Any]) -> None:
        """Process a decoded NMEA frame and update internal state.

        Called from _bus_worker inner loop on every valid frame.
        """
        info = parsed.get("info", {})
        pgn = info.get("pgn")
        src = info.get("src")
        data = parsed.get("data", b"")

        with self._lock:
            # ── Track all CAN-bus devices by source address ───────────────────
            # 254 (0xFE) is Cannot Claim / Null Address; 255 (0xFF) is Broadcast.
            # Neither represents a valid physical bus device.
            if src is not None and src < 254:
                if src not in self.discovered_bus_devices:
                    self.discovered_bus_devices[src] = {
                        "src": src,
                        "claimed": False,     # True only after PGN 60928 ISO Address Claim received
                        "manufacturer": "",   # filled by ISO Claim (PGN 60928)
                        "mfg_code": 0,        # filled by ISO Claim — raw manufacturer_code int
                        "model": "",          # filled by Product Info (PGN 126996)
                        "serial": "",         # filled by Product Info
                        "firmware": "",       # filled by Product Info
                        "device_class": "",
                        "device_class_int": 0,  # raw int device class
                        "device_function": 0,   # raw int device function
                        "industry_group": 4,    # Marine Industry (default)
                        "unique_id": 0,
                        "function_name": "",
                        "device_class_name": "",
                        "active_pgns": [],
                    }
                if pgn and pgn not in self.discovered_bus_devices[src]["active_pgns"]:
                    self.discovered_bus_devices[src]["active_pgns"].append(pgn)

                # ── PGN 60928 (Address Claim) — single-frame, parse directly ──
                if pgn == 60928:
                    dev_info = N2KPGNDecoder.parse_device_info(parsed)
                    if dev_info:
                        new_uid = dev_info.get("unique_id")
                        # If device claimed a new address, purge stale old entry for same unique_id
                        if new_uid:
                            stale_srcs = [
                                old_src for old_src, old_dev in self.discovered_bus_devices.items()
                                if old_src != src and old_dev.get("unique_id") == new_uid
                            ]
                            for stale in stale_srcs:
                                del self.discovered_bus_devices[stale]

                        dev = self.discovered_bus_devices[src]
                        dev["claimed"] = True  # ISO Address Claim received — device is fully identified
                        for key in ("manufacturer", "function_name", "device_class_name",
                                    "model_version", "unique_id"):
                            if key in dev_info and dev_info[key]:
                                dev[key] = dev_info[key]
                        if "device_class" in dev_info and dev_info["device_class"]:
                            dev["device_class"] = dev_info.get("device_class_name",
                                                               str(dev_info["device_class"]))
                        # Raw integer fields needed for N2KDevice synthesis
                        if "mfg_code" in dev_info and dev_info["mfg_code"]:
                            try:
                                dev["mfg_code"] = int(dev_info["mfg_code"])
                            except (ValueError, TypeError):
                                pass
                        if "device_class" in dev_info and dev_info["device_class"]:
                            try:
                                dev["device_class_int"] = int(dev_info["device_class"])
                            except (ValueError, TypeError):
                                pass
                        if "function" in dev_info and dev_info["function"]:
                            try:
                                dev["device_function"] = int(dev_info["function"])
                            except (ValueError, TypeError):
                                pass

                # ── All frames → library decoder (handles fast-packet reassembly) ──
                lib_msg = N2KPGNDecoder.feed_to_lib(parsed)
                if lib_msg is not None and lib_msg.PGN == 126996:
                    fields = {f.id: f for f in lib_msg.fields}
                    dev = self.discovered_bus_devices.get(lib_msg.source)
                    if dev is None and lib_msg.source is not None:
                        # Guard: never create entries for null/broadcast addresses via PGN 126996
                        if lib_msg.source >= 254:
                            dev = None
                        else:
                            dev = self.discovered_bus_devices.setdefault(
                                lib_msg.source, {"src": lib_msg.source, "claimed": False,
                                                 "active_pgns": []}
                            )
                    if dev is not None:
                        for field_id, attr in (
                            ("modelId",            "model"),
                            ("softwareVersionCode", "firmware"),
                            ("modelSerialCode",     "serial"),
                            ("modelVersion",        "model_version"),
                        ):
                            fld = fields.get(field_id)
                            if fld and fld.value:
                                val_str = str(fld.value).strip()
                                # Guard against empty/dummy strings overwriting valid data
                                if val_str:
                                    dev[attr] = val_str

            # ── PGN 127505: Fluid Level ───────────────────────────────────────
            if pgn == 127505 and len(data) >= 5:
                instance  = data[0] & 0x0F
                type_code = (data[0] >> 4) & 0x0F
                raw_level = data[1] | (data[2] << 8)
                level_pct = round(raw_level * 0.004, 1) if raw_level <= 25000 else None

                capacity_l = None
                if len(data) >= 7:
                    raw_cap = int.from_bytes(data[3:7], 'little')
                    if raw_cap != 0xFFFFFFFF:
                        capacity_l = round(raw_cap * 0.1, 1)

                if instance not in self.sensors:
                    self.sensors[instance] = GobiusCSensor(instance=instance,
                                                           name=f"Tank {instance}")

                self.sensors[instance].update_from_nmea127505({
                    "instance":   instance,
                    "type_code":  type_code,
                    "level_pct":  level_pct,
                    "capacity_l": capacity_l,
                    "src":        src,
                })

    def get_sensors_state(self) -> Dict[str, Any]:
        """Non-blocking snapshot of all known sensors (thread-safe)."""
        with self._lock:
            fluid_levels = [sensor.to_dict() for sensor in self.sensors.values()]
        return {
            "status": "ok",
            "fluid_levels": fluid_levels,
            "count": len(fluid_levels)
        }

    def get_bus_devices(self) -> Dict[int, Dict[str, Any]]:
        """Return cached bus device info keyed by Source Address."""
        with self._lock:
            return dict(self.discovered_bus_devices)
