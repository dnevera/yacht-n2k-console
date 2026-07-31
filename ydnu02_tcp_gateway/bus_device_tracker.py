"""bus_device_tracker.py — Lightweight N2K bus device tracker for the TCP gateway.

Decodes PGN 60928 (ISO Claim) and PGN 126996 (Product Info, FastPacket) from
broadcast R-frames using the nmea2000 library. Stores per-SA device info with
raw integer N2K NAME fields so ydnu02_gateway_device._replay_iso_presence() can
synthesize proper PGN 60928 + PGN 126996 frames for HA.

No dependency on ydnu02-web / DeviceManager — self-contained within the
ydnu02_tcp_gateway package.
"""

import threading
from typing import Dict, Any, Optional

from nmea2000.decoder import NMEA2000Decoder


class BusDeviceTracker:
    """Thread-safe tracker of physical N2K bus devices.

    Populated by serial_reader via update(line_bytes) on every broadcast frame.
    Read by ydnu02_gateway_device via get_devices() during ISO replay.

    Device dict fields (per SA):
        src             (int)  Source Address
        unique_id       (int)  21-bit unique number from ISO Name
        mfg_code        (int)  Manufacturer code (raw int)
        device_class    (int)  Device class (raw int)
        device_function (int)  Device function (raw int)
        industry_group  (int)  Industry group (raw int, 4=Marine)
        model           (str)  Model ID from PGN 126996
        firmware        (str)  Software version from PGN 126996
        serial          (str)  Serial code from PGN 126996
        model_version   (str)  Model version from PGN 126996
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._decoder = NMEA2000Decoder()
        self._devices: Dict[int, Dict[str, Any]] = {}

    def update(self, line: bytes) -> None:
        """Feed one broadcast R-frame line and update internal device state."""
        try:
            text = line.decode("ascii", errors="replace").rstrip("\r\n")
        except Exception:
            return

        try:
            msg = self._decoder.decode(text)
        except Exception:
            return

        if msg is None:
            return

        sa = msg.source
        if sa is None:
            return

        with self._lock:
            dev = self._devices.setdefault(sa, {
                "src": sa,
                "unique_id": 0,
                "mfg_code": 0,
                "device_class": 0,
                "device_function": 0,
                "industry_group": 4,
                "model": "",
                "firmware": "",
                "serial": "",
                "model_version": "",
            })

            if msg.PGN == 60928:
                fields = {f.id: f.value for f in msg.fields}
                uid = fields.get("uniqueNumber")
                if uid is not None:
                    dev["unique_id"] = int(uid)
                mfg = fields.get("manufacturerCode")
                if isinstance(mfg, (int, float)):
                    dev["mfg_code"] = int(mfg)
                dev["device_function"] = _coerce_int(fields.get("deviceFunction"), 130)
                dev["industry_group"]  = _coerce_int(fields.get("industryGroup"), 4)
                dev["device_class"]    = _coerce_int(fields.get("deviceClass"), 25)

            elif msg.PGN == 126996:
                fields = {f.id: f.value for f in msg.fields}
                for field_id, attr in (
                    ("modelId",            "model"),
                    ("softwareVersionCode", "firmware"),
                    ("modelSerialCode",    "serial"),
                    ("modelVersion",       "model_version"),
                ):
                    val = fields.get(field_id)
                    if val:
                        dev[attr] = str(val).strip()

    def get_devices(self) -> Dict[int, Dict[str, Any]]:
        """Return a shallow copy of all tracked devices keyed by SA."""
        with self._lock:
            return {sa: dict(d) for sa, d in self._devices.items()}

    def get_device(self, sa: int) -> Optional[Dict[str, Any]]:
        """Return device dict for a specific SA, or None if not tracked."""
        with self._lock:
            d = self._devices.get(sa)
            return dict(d) if d else None


def _coerce_int(value: Any, default: int) -> int:
    """Convert nmea2000 field value (int, float, or string) to int."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default
