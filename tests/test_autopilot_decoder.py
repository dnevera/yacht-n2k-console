"""
Unit tests for n2k_autopilot — Raymarine autopilot frame decoding (read only).

The byte layouts are reverse-engineered (canboat / SignalK), so these frames
are hand-built from the documented field order; the on-boat sniffer
(scripts/sniff_autopilot.py) is what confirms them against the p70 display.
"""

import n2k_autopilot as ap

RAY_HEADER = bytes([0x3B, 0x9F])          # mfg 1851 + industry group 4
OTHER_HEADER = bytes([0x89, 0x9F])        # some other manufacturer, marine group


def _u16le(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def _pilot_mode_frame(mode: int, sub_mode: int) -> bytes:
    return RAY_HEADER + _u16le(mode) + _u16le(sub_mode) + b"\xff\xff"


def test_header_detection():
    assert ap.is_raymarine_proprietary(RAY_HEADER + b"\x00\x00")
    assert not ap.is_raymarine_proprietary(OTHER_HEADER + b"\x00\x00")
    assert not ap.is_raymarine_proprietary(b"")
    assert not ap.is_raymarine_proprietary(b"\x3b")


def test_pilot_mode_all_modes():
    expected = {
        (0x0000, 0x0000): "standby",
        (0x0040, 0x0000): "auto",
        (0x0100, 0x0001): "wind",
        (0x0180, 0x0001): "track",
    }
    for (mode, sub_mode), name in expected.items():
        decoded = ap.decode_proprietary(ap.PGN_PILOT_MODE, _pilot_mode_frame(mode, sub_mode))
        assert decoded["kind"] == "pilot_mode"
        assert decoded["mode"] == name


def test_pilot_mode_unknown_pair_is_unknown_not_an_error():
    decoded = ap.decode_proprietary(ap.PGN_PILOT_MODE, _pilot_mode_frame(0x0777, 0x0009))
    assert decoded["mode"] == "unknown"
    assert decoded["mode_raw"] == 0x0777


def test_locked_heading_magnetic():
    raw = 32708                       # 0.0001 rad units -> 187.4 deg
    data = RAY_HEADER + b"\x00" + _u16le(0xFFFF) + _u16le(raw)
    decoded = ap.decode_proprietary(ap.PGN_PILOT_LOCKED_HEADING, data)
    assert decoded["kind"] == "locked_heading"
    assert decoded["locked_heading_deg"] == 187.4
    assert decoded["heading_reference"] == "magnetic"


def test_locked_heading_falls_back_to_true():
    data = RAY_HEADER + b"\x00" + _u16le(32708) + _u16le(0xFFFF)
    decoded = ap.decode_proprietary(ap.PGN_PILOT_LOCKED_HEADING, data)
    assert decoded["locked_heading_deg"] == 187.4
    assert decoded["heading_reference"] == "true"


def test_locked_heading_no_data_stays_none():
    data = RAY_HEADER + b"\x00" + _u16le(0xFFFF) + _u16le(0xFFFF)
    decoded = ap.decode_proprietary(ap.PGN_PILOT_LOCKED_HEADING, data)
    assert decoded["locked_heading_deg"] is None
    assert decoded["heading_reference"] is None


def test_wind_datum_keeps_sign():
    data = RAY_HEADER + _u16le((-6981) & 0xFFFF) + _u16le(0)
    decoded = ap.decode_proprietary(ap.PGN_PILOT_WIND_DATUM, data)
    assert decoded["kind"] == "wind_datum"
    assert decoded["wind_datum_deg"] == -40.0


def test_foreign_manufacturer_is_ignored():
    data = OTHER_HEADER + _u16le(0x0040) + _u16le(0x0000)
    assert ap.decode_proprietary(ap.PGN_PILOT_MODE, data) is None
    assert ap.decode_126720(data) is None


def test_unknown_proprietary_id_is_ignored():
    assert ap.decode_proprietary(65299, RAY_HEADER + b"\x00\x00\x00\x00") is None
    assert ap.decode_126720(RAY_HEADER + _u16le(65299) + b"\x00\x00") is None


def test_truncated_payload_never_raises():
    for length in range(0, 8):
        frame = (RAY_HEADER + _u16le(ap.PGN_PILOT_LOCKED_HEADING) + b"\x00" * 8)[:length]
        assert ap.decode_126720(frame) is None or isinstance(ap.decode_126720(frame), dict)
    assert ap.decode_proprietary(ap.PGN_PILOT_MODE, RAY_HEADER + b"\x00") is None
    assert ap.decode_127237(b"\x00\x00") is None
    assert ap.decode_127245(b"\x00\x00") is None


def test_126720_wraps_the_same_subtypes():
    inner = _u16le(0x0040) + _u16le(0x0000)
    decoded = ap.decode_126720(RAY_HEADER + _u16le(ap.PGN_PILOT_MODE) + inner)
    assert decoded["mode"] == "auto"


def test_127245_rudder_angle_signed():
    data = bytes([0, 0xFF]) + _u16le(0xFFFF) + _u16le((-366) & 0xFFFF)
    decoded = ap.decode_127245(data)
    assert decoded["kind"] == "rudder"
    assert decoded["instance"] == 0
    assert decoded["rudder_angle_deg"] == -2.1


def test_127237_heading_to_steer():
    data = bytes([0x00, 0x00, 0x00]) + _u16le(0) + _u16le(32708)
    decoded = ap.decode_127237(data)
    assert decoded["commanded_rudder_deg"] == 0.0
    assert decoded["heading_to_steer_deg"] == 187.4


def test_decode_frame_dispatch():
    assert ap.decode_frame(ap.PGN_PILOT_MODE, _pilot_mode_frame(0, 0))["mode"] == "standby"
    assert ap.decode_frame(127505, b"\x00" * 8) is None
    assert ap.decode_frame(ap.PGN_RUDDER, b"") is None
