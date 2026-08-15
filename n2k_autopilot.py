"""
n2k_autopilot.py — Raymarine Evolution autopilot frame decoder (READ ONLY).

Decodes the autopilot state the course computer broadcasts on SeaTalkNG
(physically NMEA 2000): pilot mode, locked heading, wind datum, rudder angle.

Nothing in this module writes to the bus. Sending commands to a device that
steers the boat is deliberately out of scope — see
specs/active/008-autopilot-control.md.

!! The proprietary byte layouts below are REVERSE-ENGINEERED by the community
!! (canboat PGN database, SignalK @signalk/raymarine-autopilot). Raymarine has
!! never documented them, and they may differ per EV firmware. Verify against
!! the p70 display with scripts/sniff_autopilot.py before trusting them.
"""

from typing import Any, Dict, Optional

# Raymarine, Marine industry group — first two bytes of every proprietary frame,
# packed little-endian as (industry_group << 13) | (reserved << 11) | mfg_code.
RAYMARINE_MFG_CODE = 1851
MARINE_INDUSTRY_GROUP = 4

PROPRIETARY_PGN = 126720          # fast-packet carrier for the subtypes below
PGN_PILOT_MODE = 65379            # Seatalk: Pilot Mode
PGN_PILOT_LOCKED_HEADING = 65360  # Seatalk: Pilot Locked Heading
PGN_PILOT_WIND_DATUM = 65345      # Seatalk: Pilot Wind Datum
PGN_HEADING_TRACK_CONTROL = 127237
PGN_RUDDER = 127245

AUTOPILOT_PGNS = frozenset({
    PROPRIETARY_PGN,
    PGN_PILOT_MODE,
    PGN_PILOT_LOCKED_HEADING,
    PGN_PILOT_WIND_DATUM,
    PGN_HEADING_TRACK_CONTROL,
    PGN_RUDDER,
})

# (mode, sub_mode) -> mode name. Reverse-engineered, see module docstring.
PILOT_MODES: Dict[tuple, str] = {
    (0x0000, 0x0000): "standby",
    (0x0040, 0x0000): "auto",
    (0x0100, 0x0001): "wind",
    (0x0180, 0x0001): "track",
}

_NA_16 = 0xFFFF          # NMEA 2000 "data not available" for a 16-bit field
_RAD_TO_DEG = 57.29577951308232
_ANGLE_RESOLUTION = 0.0001   # rad per LSB, standard N2K angle resolution


def _u16(data: bytes, offset: int) -> Optional[int]:
    """Little-endian uint16 at offset, or None if truncated / not available."""
    if len(data) < offset + 2:
        return None
    raw = data[offset] | (data[offset + 1] << 8)
    return None if raw == _NA_16 else raw


def _i16(data: bytes, offset: int) -> Optional[int]:
    raw = _u16(data, offset)
    if raw is None:
        return None
    return raw - 0x10000 if raw >= 0x8000 else raw


def _angle_deg(raw: Optional[int]) -> Optional[float]:
    """Raw 0.0001 rad units -> degrees, normalised to [0, 360)."""
    if raw is None:
        return None
    return round((raw * _ANGLE_RESOLUTION * _RAD_TO_DEG) % 360.0, 1)


def _signed_angle_deg(raw: Optional[int]) -> Optional[float]:
    """Raw 0.0001 rad units -> degrees, kept signed (rudder, wind datum)."""
    if raw is None:
        return None
    return round(raw * _ANGLE_RESOLUTION * _RAD_TO_DEG, 1)


def is_raymarine_proprietary(data: bytes) -> bool:
    """True if the payload starts with Raymarine's manufacturer/industry header."""
    if not data or len(data) < 2:
        return False
    header = data[0] | (data[1] << 8)
    return (header & 0x07FF) == RAYMARINE_MFG_CODE and (header >> 13) == MARINE_INDUSTRY_GROUP


def _decode_pilot_mode(data: bytes, offset: int) -> Optional[Dict[str, Any]]:
    mode = _u16(data, offset)
    sub_mode = _u16(data, offset + 2)
    if mode is None or sub_mode is None:
        return None
    return {
        "kind": "pilot_mode",
        "mode": PILOT_MODES.get((mode, sub_mode), "unknown"),
        "mode_raw": mode,
        "sub_mode_raw": sub_mode,
    }


def _decode_locked_heading(data: bytes, offset: int) -> Optional[Dict[str, Any]]:
    # byte layout: <reserved:8> <heading true:16> <heading magnetic:16>
    if len(data) < offset + 3:
        return None
    heading_true = _angle_deg(_u16(data, offset + 1))
    heading_magnetic = _angle_deg(_u16(data, offset + 3))
    if heading_magnetic is not None:
        heading, reference = heading_magnetic, "magnetic"
    elif heading_true is not None:
        heading, reference = heading_true, "true"
    else:
        heading, reference = None, None
    return {
        "kind": "locked_heading",
        "locked_heading_deg": heading,
        "heading_reference": reference,
    }


def _decode_wind_datum(data: bytes, offset: int) -> Optional[Dict[str, Any]]:
    # byte layout: <wind datum:16> <rotation offset:16>
    if len(data) < offset + 2:
        return None
    return {
        "kind": "wind_datum",
        "wind_datum_deg": _signed_angle_deg(_i16(data, offset)),
    }


_SUBTYPE_DECODERS = {
    PGN_PILOT_MODE: _decode_pilot_mode,
    PGN_PILOT_LOCKED_HEADING: _decode_locked_heading,
    PGN_PILOT_WIND_DATUM: _decode_wind_datum,
}


def decode_proprietary(pgn: int, data: bytes) -> Optional[Dict[str, Any]]:
    """Decode a single-frame Raymarine proprietary PGN (65345/65360/65379).

    Returns None for a non-Raymarine frame, an unknown PGN or a truncated
    payload — a bad frame must never raise inside the bus worker loop.
    """
    decoder = _SUBTYPE_DECODERS.get(pgn)
    if decoder is None or not is_raymarine_proprietary(data):
        return None
    return decoder(data, 2)


def decode_126720(data: bytes) -> Optional[Dict[str, Any]]:
    """Decode a fast-packet PGN 126720 frame carrying an autopilot subtype.

    Layout: <mfg+industry:16> <proprietary id:16> <subtype payload>.
    The payload is expected already reassembled (see the single feed_to_lib
    call in device_manager/sensor_registry.py — never reassemble separately).
    """
    if not is_raymarine_proprietary(data):
        return None
    proprietary_id = _u16(data, 2)
    decoder = _SUBTYPE_DECODERS.get(proprietary_id)
    if decoder is None:
        return None
    return decoder(data, 4)


def decode_127237(data: bytes) -> Optional[Dict[str, Any]]:
    """PGN 127237 Heading/Track Control — heading to steer and commanded rudder.

    Layout (standard N2K): byte 0-2 mode/reference bit fields, then
    <commanded rudder angle:16> <heading to steer:16> ...
    """
    if len(data) < 5:
        return None
    return {
        "kind": "heading_track_control",
        "commanded_rudder_deg": _signed_angle_deg(_i16(data, 3)),
        "heading_to_steer_deg": _angle_deg(_u16(data, 5)),
    }


def decode_127245(data: bytes) -> Optional[Dict[str, Any]]:
    """PGN 127245 Rudder — <instance:8> <direction order:8> <angle order:16> <position:16>."""
    if len(data) < 6:
        return None
    return {
        "kind": "rudder",
        "instance": data[0],
        "rudder_angle_deg": _signed_angle_deg(_i16(data, 4)),
    }


def decode_frame(pgn: int, data: bytes) -> Optional[Dict[str, Any]]:
    """Single entry point: dispatch any autopilot-related PGN to its decoder."""
    if not data:
        return None
    if pgn == PROPRIETARY_PGN:
        return decode_126720(data)
    if pgn in _SUBTYPE_DECODERS:
        return decode_proprietary(pgn, data)
    if pgn == PGN_HEADING_TRACK_CONTROL:
        return decode_127237(data)
    if pgn == PGN_RUDDER:
        return decode_127245(data)
    return None
