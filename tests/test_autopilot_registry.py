"""
Autopilot state tracking inside SensorRegistry (read only).

Checks that live bus frames end up in the snapshot returned by
get_autopilot_state(), and that adding the autopilot branch did not break the
existing PGN dispatch (60928 / 126996 fast-packet / 127505).
"""

import n2k_autopilot as ap
from device_manager.sensor_registry import SensorRegistry

RAY_HEADER = bytes([0x3B, 0x9F])


def _u16le(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def _frame(pgn: int, data: bytes, src: int = 204):
    return {"info": {"pgn": pgn, "src": src}, "data": data}


def _pilot_mode(mode: int, sub_mode: int) -> bytes:
    return RAY_HEADER + _u16le(mode) + _u16le(sub_mode) + b"\xff\xff"


def test_standby_then_auto_then_heading_change():
    reg = SensorRegistry()
    assert reg.get_autopilot_state()["mode"] == "unknown"
    assert reg.get_autopilot_state()["age_sec"] is None

    reg.update(_frame(ap.PGN_PILOT_MODE, _pilot_mode(0x0000, 0x0000)))
    assert reg.get_autopilot_state()["mode"] == "standby"

    reg.update(_frame(ap.PGN_PILOT_MODE, _pilot_mode(0x0040, 0x0000)))
    reg.update(_frame(ap.PGN_PILOT_LOCKED_HEADING,
                      RAY_HEADER + b"\x00" + _u16le(0xFFFF) + _u16le(32708)))

    snap = reg.get_autopilot_state()
    assert snap["mode"] == "auto"
    assert snap["locked_heading_deg"] == 187.4
    assert snap["heading_reference"] == "magnetic"
    assert snap["src"] == 204
    assert snap["age_sec"] is not None

    # A new locked heading replaces the old one; the mode is untouched.
    reg.update(_frame(ap.PGN_PILOT_LOCKED_HEADING,
                      RAY_HEADER + b"\x00" + _u16le(0xFFFF) + _u16le(34454)))
    snap = reg.get_autopilot_state()
    assert snap["mode"] == "auto"
    assert snap["locked_heading_deg"] == 197.4


def test_age_grows_while_autopilot_is_silent():
    reg = SensorRegistry()
    reg.update(_frame(ap.PGN_PILOT_MODE, _pilot_mode(0x0040, 0x0000)))
    reg.autopilot.last_update -= 5.0
    snap = reg.get_autopilot_state()
    assert snap["mode"] == "auto"          # last known value stays
    assert snap["age_sec"] >= 5.0


def test_rudder_angle_from_standard_pgn():
    reg = SensorRegistry()
    reg.update(_frame(ap.PGN_RUDDER,
                      bytes([0, 0xFF]) + _u16le(0xFFFF) + _u16le((-366) & 0xFFFF)))
    assert reg.get_autopilot_state()["rudder_angle_deg"] == -2.1


def test_foreign_manufacturer_leaves_state_alone():
    reg = SensorRegistry()
    reg.update(_frame(ap.PGN_PILOT_MODE, bytes([0x89, 0x9F]) + _u16le(0x0040) + _u16le(0)))
    assert reg.get_autopilot_state()["mode"] == "unknown"
    assert reg.get_autopilot_state()["last_update"] is None


def test_endpoint_empty_state_is_not_an_error():
    import asyncio

    import routes.n2k as n2k_routes

    class _Mgr:
        def __init__(self, reg):
            self._reg = reg

        def get_autopilot_state(self):
            return self._reg.get_autopilot_state()

    reg = SensorRegistry()
    original = n2k_routes.get_device_mgr
    n2k_routes.get_device_mgr = lambda: _Mgr(reg)
    try:
        body = asyncio.run(n2k_routes.autopilot_state())
        assert body["status"] == "ok"
        assert body["autopilot"]["mode"] == "unknown"
        assert body["autopilot"]["age_sec"] is None

        reg.update(_frame(ap.PGN_PILOT_MODE, _pilot_mode(0x0100, 0x0001)))
        body = asyncio.run(n2k_routes.autopilot_state())
        assert body["autopilot"]["mode"] == "wind"
        assert body["autopilot"]["src"] == 204
    finally:
        n2k_routes.get_device_mgr = original


def test_endpoint_without_device_manager_is_503():
    import asyncio

    from fastapi import HTTPException
    import routes.n2k as n2k_routes

    original = n2k_routes.get_device_mgr
    n2k_routes.get_device_mgr = lambda: None
    try:
        try:
            asyncio.run(n2k_routes.autopilot_state())
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("expected HTTP 503 without a device manager")
    finally:
        n2k_routes.get_device_mgr = original


def test_fluid_level_dispatch_still_works():
    reg = SensorRegistry()
    # PGN 127505: instance 0, type 0, level 50% (12500 * 0.004)
    data = bytes([0x00]) + _u16le(12500) + (100000).to_bytes(4, "little") + b"\xff"
    reg.update(_frame(127505, data, src=35))
    state = reg.get_sensors_state()
    assert state["count"] == 1
    assert state["fluid_levels"][0]["nmea"]["fill_level_pct"] == 50.0
