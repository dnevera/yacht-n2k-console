"""Unit tests for ha/ais/custom_components/ais_targets/ais_bus.py.

Home Assistant is not installed in this project's venv, and ais_bus.py only
needs `homeassistant.util.dt.utcnow`, so we inject a tiny stub for that plus a
stub parent package (`ais_targets`) whose real `const` submodule (pure, no HA
imports) is loaded from disk. The module is then loaded in isolation via
importlib — no HA runtime required.

These tests lock down the two failure-prone parts of the direct-gateway
decoder: CAN-ID/PGN parsing and the MMSI grouping + unit conversions
(rad → deg, m/s → knots) in `_ingest`.
"""
import importlib.util
import math
import os
import sys
import types
from datetime import datetime, timezone

import pytest

AIS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ha",
    "ais",
    "custom_components",
    "ais_targets",
)


@pytest.fixture()
def ais_bus():
    """Load ais_bus.py in isolation with stubbed HA + package context."""
    # Stub homeassistant.util.dt.utcnow used by ais_bus.
    ha = types.ModuleType("homeassistant")
    ha_util = types.ModuleType("homeassistant.util")
    ha_dt = types.ModuleType("homeassistant.util.dt")
    ha_dt.utcnow = lambda: datetime.now(timezone.utc)
    ha_util.dt = ha_dt
    ha.util = ha_util

    # Stub parent package so `from .const import ...` resolves.
    pkg = types.ModuleType("ais_targets")
    pkg.__path__ = [AIS_DIR]

    saved = {k: sys.modules.get(k) for k in (
        "homeassistant", "homeassistant.util", "homeassistant.util.dt",
        "ais_targets", "ais_targets.const", "ais_targets.ais_bus",
    )}
    sys.modules.update({
        "homeassistant": ha,
        "homeassistant.util": ha_util,
        "homeassistant.util.dt": ha_dt,
        "ais_targets": pkg,
    })

    # Load the real (pure) const module as ais_targets.const.
    const_spec = importlib.util.spec_from_file_location(
        "ais_targets.const", os.path.join(AIS_DIR, "const.py")
    )
    const_mod = importlib.util.module_from_spec(const_spec)
    sys.modules["ais_targets.const"] = const_mod
    const_spec.loader.exec_module(const_mod)

    spec = importlib.util.spec_from_file_location(
        "ais_targets.ais_bus", os.path.join(AIS_DIR, "ais_bus.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ais_targets.ais_bus"] = mod
    spec.loader.exec_module(mod)

    try:
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class _Field:
    def __init__(self, fid, value):
        self.id = fid
        self.value = value


class _Msg:
    def __init__(self, pgn, fields):
        self.PGN = pgn
        self.fields = fields


def test_parse_can_id_matches_project_decoder(ais_bus):
    # PDU2 (broadcast) and PDU1 (addressed) forms.
    assert ais_bus.parse_can_id("09F80115") == (0x09F80115, 129025, 0x15)
    _cid, pgn, src = ais_bus.parse_can_id("18EEFF40")
    assert (pgn, src) == (60928, 0x40)


def test_clean_and_num_helpers(ais_bus):
    assert ais_bus._clean_str("SEA BREEZE@@@") == "SEA BREEZE"
    assert ais_bus._clean_str("\x00\x00") is None
    assert ais_bus._clean_str(123) is None
    assert ais_bus._num("6.4") == 6.4
    assert ais_bus._num(None) is None
    assert ais_bus._num("n/a") is None


def test_ingest_position_report_groups_and_converts(ais_bus):
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    # 129039 Class B position: userId=MMSI, lat/lon deg, sog m/s, cog/heading rad.
    msg = _Msg(
        129039,
        [
            _Field("userId", 244660123),
            _Field("latitude", 42.4312),
            _Field("longitude", 18.6021),
            _Field("sog", 3.2),                 # m/s
            _Field("cog", math.radians(187.0)),  # rad
            _Field("heading", math.radians(185.0)),
            _Field("navStatus", "Under way using engine"),
        ],
    )
    client._ingest(msg)
    snap = client.snapshot()
    assert 244660123 in snap
    t = snap[244660123]
    assert t.latitude == pytest.approx(42.4312, abs=1e-6)
    assert t.longitude == pytest.approx(18.6021, abs=1e-6)
    assert t.sog == pytest.approx(6.2, abs=0.1)      # 3.2 m/s ≈ 6.2 kn
    assert t.cog == pytest.approx(187.0, abs=0.1)
    assert t.heading == pytest.approx(185.0, abs=0.1)
    assert t.nav_status == "Under way using engine"
    assert t.has_position is True
    assert t.last_seen is not None


def test_ingest_static_merges_into_same_mmsi(ais_bus):
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    client._ingest(_Msg(129038, [
        _Field("userId", 111), _Field("latitude", 1.0), _Field("longitude", 2.0),
    ]))
    # 129794 static/voyage data for the same MMSI.
    client._ingest(_Msg(129794, [
        _Field("userId", 111),
        _Field("name", "MSC ARMONIA@@"),
        _Field("callsign", "ZA1234 "),
        _Field("typeOfShip", "Passenger ship"),
        _Field("length", 275.0),
        _Field("beam", 32.0),
        _Field("destination", "BUDVA"),
    ]))
    t = client.snapshot()[111]
    assert t.vessel_name == "MSC ARMONIA"
    assert t.callsign == "ZA1234"
    assert t.ship_type == "Passenger ship"
    assert t.length == 275.0
    assert t.beam == 32.0
    assert t.destination == "BUDVA"
    # position from the earlier 129038 is preserved
    assert t.has_position is True


def test_ingest_ignores_invalid_mmsi(ais_bus):
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    client._ingest(_Msg(129039, [_Field("userId", 0), _Field("latitude", 1.0)]))
    client._ingest(_Msg(129039, [_Field("userId", None), _Field("latitude", 1.0)]))
    assert client.snapshot() == {}


def test_drop_removes_target(ais_bus):
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    client._ingest(_Msg(129039, [
        _Field("userId", 555), _Field("latitude", 1.0), _Field("longitude", 2.0),
    ]))
    assert 555 in client.snapshot()
    client.drop(555)
    assert 555 not in client.snapshot()


def test_static_data_survives_target_expiry(ais_bus):
    """Identity fields must be replayed onto a re-appearing MMSI.

    129809/129810/129794 are broadcast only every few minutes, so dropping the
    cached name/callsign/type/size on expiry left the dashboard table full of
    nameless "AIS <mmsi>" rows for minutes at a time.
    """
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    client._ingest(_Msg(129809, [_Field("userId", 777), _Field("name", "M.Y. TROY")]))
    client._ingest(_Msg(129810, [
        _Field("userId", 777), _Field("callsign", "MTOJ5"),
        _Field("typeOfShip", "Pleasure"), _Field("length", 24.0),
        _Field("beam", 5.0),
    ]))
    client._ingest(_Msg(129039, [
        _Field("userId", 777), _Field("latitude", 42.4), _Field("longitude", 18.6),
    ]))
    client.drop(777)
    assert 777 not in client.snapshot()

    # Vessel comes back into range: only a position report arrives.
    client._ingest(_Msg(129039, [
        _Field("userId", 777), _Field("latitude", 42.5), _Field("longitude", 18.7),
    ]))
    t = client.snapshot()[777]
    assert t.vessel_name == "M.Y. TROY"
    assert t.callsign == "MTOJ5"
    assert t.ship_type == "Pleasure"
    assert t.length == 24.0
    assert t.beam == 5.0


def test_own_position_comes_from_bus_gnss_pgns(ais_bus):
    """Our own position is decoded off the bus, not taken from an HA sensor.

    129029 (full GNSS fix) outranks 129025 (rapid update), and a partial fix
    with a zero coordinate is rejected outright — such a value once made every
    target read ~1500 km away.
    """
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    assert client.own_position is None

    client._ingest_own_position(
        _Msg(129025, [_Field("latitude", 42.4346), _Field("longitude", 18.6032)]),
        129025,
    )
    assert client.own_position == (42.4346, 18.6032)

    client._ingest_own_position(
        _Msg(129029, [_Field("latitude", 42.4345), _Field("longitude", 18.6030)]),
        129029,
    )
    assert client.own_position == (42.4345, 18.603)

    # Once the full fix is known, the rapid update must not flip the origin.
    client._ingest_own_position(
        _Msg(129025, [_Field("latitude", 10.0), _Field("longitude", 20.0)]),
        129025,
    )
    assert client.own_position == (42.4345, 18.603)


def test_own_position_rejects_null_island_fix(ais_bus):
    client = ais_bus.AisBusClient("127.0.0.1", 4001)
    client._ingest_own_position(
        _Msg(129029, [_Field("latitude", 42.4346), _Field("longitude", 0.0)]),
        129029,
    )
    assert client.own_position is None
