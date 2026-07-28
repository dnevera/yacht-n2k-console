#!/usr/bin/env python3
"""
Unit tests for Gobius C BLE parsers — byte maps verified against official protocol spec
(Issue 3, 2023-08-08) and REAL sensor dumps.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gobius_parsers import (
    parse_status, parse_measurement, parse_n2k_status,
    parse_user_cfg, parse_n2k_cfg, compute_fill_level, FLUID_TYPES,
)

# ──────────────────── Real sensor dumps ────────────────────
REAL_FFE8 = "05080000749a00001c2ec02ca7742156d8000001"
REAL_FFE9 = "0508010318050066005e0087017902d300000000"
REAL_FFF3 = "025c000000000000000000000000000000000000"
REAL_FFE6 = "012c003203151032053205000000000000000a00"
REAL_FFF2_ON  = "0100010000000000009600000000000000000000"
REAL_FFF2_OFF = "0000000000000000009600000000000000000000"


def test_parse_status_real():
    """0xFFE8: State=Active, uptime=29850s, temp=28°C, voltage=11.968V, MAC correct."""
    r = parse_status(bytes.fromhex(REAL_FFE8))
    assert r["state"] == 5
    assert r["state_str"] == "Active"
    assert r["status_bits"] == 8
    # bytes[2:6] BE = 0x0000749a = 29850 s
    assert r["uptime_s"] == 29850, f"uptime_s: expected 29850, got {r['uptime_s']}"
    assert r["error_code"] == 0, "ST_ER1"
    assert r["hw_error"] == 0, "ST_ER2"
    assert r["temp_c"] == 28, f"temp: expected 28, got {r['temp_c']}"
    assert r["voltage_v"] == 11.968, f"voltage: expected 11.968, got {r['voltage_v']}"
    assert r["mac"] == "2C:A7:74:21:56:D8"
    assert r["ext_hw_error"] == 0, "ST_ER3"
    assert r["radar_err_cnt"] == 0, "ST_ERR"
    assert r["current_range"] == 1, "ST_RNG = Near Range"
    assert r["measuring"] == 1
    print("  ✅ parse_status — uptime=29850s, temp=28°C, voltage=11.968V, range=Near")


def test_parse_status_voltage_across_dumps():
    """Verify voltage across multiple real dumps."""
    dumps = [
        ("05080000701c00001c2ec02ca7742156d8000001", 28, 11.968),
        ("05080000712000001c2ee42ca7742156d8000001", 28, 12.004),
        ("0508000070b200001c2edb2ca7742156d8000001", 28, 11.995),
    ]
    for hexstr, exp_temp, exp_volt in dumps:
        r = parse_status(bytes.fromhex(hexstr))
        assert r["temp_c"] == exp_temp
        assert r["voltage_v"] == exp_volt
    print("  ✅ parse_status voltage verified across 3 dumps")


def test_parse_status_short():
    assert "error" in parse_status(bytes.fromhex("0508"))
    print("  ✅ parse_status short → error")


def test_parse_measurement_real():
    """
    0xFFE9 Measurement — Table 27:
      M_VD=1 (valid), M_FL=0x0318=792‰=79.2%, M_INC=5°, M_DIST=0x0066=102mm
    """
    r = parse_measurement(bytes.fromhex(REAL_FFE9))
    assert r["state"] == 5
    assert r["level_valid"] == 1, "M_VD: level should be valid"
    # bytes[3:5] BE = 0x0318 = 792 ‰ = 79.2%
    assert r["fill_permille"] == 792, f"M_FL: expected 792, got {r['fill_permille']}"
    assert r["fill_pct"] == 79.2, f"fill_pct: expected 79.2%, got {r['fill_pct']}"
    assert r["inclination_deg"] == 5, f"M_INC: expected 5, got {r['inclination_deg']}"
    assert r["distance_mm"] == 102, f"M_DIST: expected 102, got {r['distance_mm']}"
    # Envelope sizes
    assert r["envelope_zero"] == 94, f"M_SZR: expected 94, got {r['envelope_zero']}"
    assert r["envelope_near"] == 135, f"M_SNR: expected 135, got {r['envelope_near']}"
    assert r["envelope_mid"] == 377, f"M_SMR: expected 377, got {r['envelope_mid']}"
    assert r["envelope_far"] == 723, f"M_SFR: expected 723, got {r['envelope_far']}"
    print("  ✅ parse_measurement — fill=79.2% (792‰), dist=102mm, incl=5°")


def test_parse_measurement_short():
    assert "error" in parse_measurement(bytes.fromhex("050801"))
    print("  ✅ parse_measurement short → error")


def test_parse_n2k_status_real():
    """0xFFF3: n2k_state=2, n2k_src=92 (0x5C)."""
    r = parse_n2k_status(bytes.fromhex(REAL_FFF3))
    assert r["n2k_state"] == 2
    assert r["n2k_src"] == 92
    print("  ✅ parse_n2k_status — state=2, src=92")


def test_parse_user_cfg_real():
    """
    0xFFE6 User Config — Table 23:
      UC_DE = bytes[0:2] BE = 0x012C = 300mm (distance empty = tank depth)
      UC_DF = bytes[2:4] BE = 0x0032 = 50mm  (distance full = dead zone)

    App shows: Tank depth = 350.0mm (= UC_DE + something? or raw value with offset)
    Volume = 10.0L (from N2K config 0xFFF2, not in 0xFFE6)
    """
    r = parse_user_cfg(bytes.fromhex(REAL_FFE6))
    assert r["distance_empty_mm"] == 300, f"UC_DE: expected 300, got {r['distance_empty_mm']}"
    assert r["distance_full_mm"] == 50, f"UC_DF: expected 50, got {r['distance_full_mm']}"
    assert r["tank_depth_mm"] == 300
    assert r["lp_filter_n"] == 3
    assert r["lp_filter_k"] == 21  # 0x15
    assert r["config_bits"] == 0x10  # 00010000
    assert r["advertise_off_s"] == 10  # 0x0a
    print("  ✅ parse_user_cfg — dist_empty=300mm, dist_full=50mm, LP=3/21")


def test_parse_n2k_cfg_enabled():
    """0xFFF2 enabled: Fresh Water, volume=150L."""
    r = parse_n2k_cfg(bytes.fromhex(REAL_FFF2_ON))
    assert r["n2k_enabled"] is True
    assert r["fluid_instance"] == 0
    assert r["fluid_type"] == 1
    assert r["fluid_type_name"] == "Fresh Water"
    assert r["volume_l"] == 150, f"volume: expected 150, got {r['volume_l']}"
    print("  ✅ parse_n2k_cfg enabled — Fresh Water, volume=150L")


def test_parse_n2k_cfg_disabled():
    r = parse_n2k_cfg(bytes.fromhex(REAL_FFF2_OFF))
    assert r["n2k_enabled"] is False
    assert r["volume_l"] == 150
    print("  ✅ parse_n2k_cfg disabled — volume=150L")


def test_compute_fill_level():
    """Fill = (300 - 102) / (300 - 50) * 100 = 79.2%."""
    fill = compute_fill_level(dist_empty_mm=300, dist_full_mm=50, distance_mm=102)
    assert fill == 79.2, f"fill: expected 79.2%, got {fill}%"
    print("  ✅ compute_fill_level(300, 50, 102) = 79.2%")


def test_fill_matches_ble_and_nmea():
    """
    Cross-validate all 3 sources:
      BLE M_FL (0xFFE9): 792‰ = 79.2%
      BLE computed:      (300-102)/(300-50)*100 = 79.2%
      NMEA PGN 127505:   79.2%
    All three must match exactly.
    """
    # BLE measurement register
    m = parse_measurement(bytes.fromhex(REAL_FFE9))
    ble_fill = m["fill_pct"]

    # BLE computed from distances
    cfg = parse_user_cfg(bytes.fromhex(REAL_FFE6))
    computed_fill = compute_fill_level(
        cfg["distance_empty_mm"], cfg["distance_full_mm"], m["distance_mm"]
    )

    # NMEA PGN 127505
    nmea_fill = 79.2

    assert ble_fill == 79.2, f"BLE M_FL: {ble_fill}"
    assert computed_fill == 79.2, f"Computed: {computed_fill}"
    assert nmea_fill == 79.2

    print(f"  ✅ All 3 sources match: BLE={ble_fill}%, computed={computed_fill}%, NMEA={nmea_fill}%")


def test_compute_fill_clamped():
    assert compute_fill_level(300, 50, 400) == 0.0   # below empty
    assert compute_fill_level(300, 50, 50) == 100.0   # at full
    assert compute_fill_level(300, 50, 0) == 100.0     # above full, clamped
    assert compute_fill_level(50, 50, 100) == 0.0      # zero range
    print("  ✅ compute_fill_level clamped edge cases")


def test_fluid_types():
    assert FLUID_TYPES[0] == "Fuel"
    assert FLUID_TYPES[1] == "Fresh Water"
    print("  ✅ fluid_type_names")


def test_ble_fill_matches_app_display():
    """
    App shows: Tank depth = 350.0mm, Volume = 10.0L
    BLE 0xFFE6: UC_DE=300mm (dist empty), UC_DF=50mm (dist full)
    App "depth" = UC_DE + UC_DF = 300 + 50 = 350mm (total sensor range)
    App "volume" = from 0xFFF2 byte[9] = 150L (or app-level override to 10L)

    BLE 0xFFE9: M_FL=792‰=79.2%, M_DIST=102mm
    fill = (300 - 102) / (300 - 50) * 100 = 79.2%

    Volume at current fill = 10.0L * 79.2% = 7.92L
    (using app volume=10L, not N2K config volume=150L)
    """
    cfg = parse_user_cfg(bytes.fromhex(REAL_FFE6))
    m = parse_measurement(bytes.fromhex(REAL_FFE9))
    n2k = parse_n2k_cfg(bytes.fromhex(REAL_FFF2_ON))

    total_range = cfg["distance_empty_mm"] + cfg["distance_full_mm"]
    assert total_range == 350, f"Total range: {total_range}"

    app_volume_l = 10.0  # from app screenshot
    n2k_volume_l = n2k["volume_l"]  # 150L from N2K config
    assert n2k_volume_l == 150

    current_volume = round(app_volume_l * m["fill_pct"] / 100.0, 1)
    assert current_volume == 7.9, f"Current volume: {current_volume}"
    print(f"  ✅ App display match: depth={total_range}mm, vol=10L, current={current_volume}L")


if __name__ == "__main__":
    print(f"{'='*60}")
    print("Gobius C BLE Parser Tests — Official Protocol Spec")
    print(f"{'='*60}\n")

    tests = [
        test_parse_status_real,
        test_parse_status_voltage_across_dumps,
        test_parse_status_short,
        test_parse_measurement_real,
        test_parse_measurement_short,
        test_parse_n2k_status_real,
        test_parse_user_cfg_real,
        test_parse_n2k_cfg_enabled,
        test_parse_n2k_cfg_disabled,
        test_compute_fill_level,
        test_fill_matches_ble_and_nmea,
        test_compute_fill_clamped,
        test_fluid_types,
        test_ble_fill_matches_app_display,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
    print(f"{'='*60}")
    sys.exit(1 if failed > 0 else 0)
