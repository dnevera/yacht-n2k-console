"""
Gobius C BLE data parsers — byte maps from official protocol document:
  "GOBIUS C (Continuous measurement) BLUETOOTH PROTOCOL & FUNCTIONAL DESCRIPTION"
  Issue 3, 2023-08-08, Anders Remar, Gobius Sensor Technology AB

NOTE: All multi-byte data is Big-Endian (MSB first) per spec section 8.2.4.
"""

# Standard NMEA 2000 / Gobius fluid types
FLUID_TYPES = {
    0: "Fuel",
    1: "Fresh Water",
    2: "Gray Water",
    3: "Live Well",
    4: "Oil",
    5: "Black Water",
    6: "Gasoline",
}


def parse_status(data: bytes) -> dict:
    """
    Parse 20-byte Status register (0xFFE8) — Table 26.

    Byte map (official):
      byte[0]:    ST_ST — State (0x05 = Active)
      byte[1]:    ST_SB — Status bits
      bytes[2:6]: ST_T  — Time since power-on [s] (uint32 BE)
      byte[6]:    ST_ER1 — General error code
      byte[7]:    ST_ER2 — Hardware error code
      byte[8]:    ST_T   — Processor temperature °C (int8 signed)
      bytes[9:11]: ST_V  — Supply voltage [mV] (uint16 BE)
      bytes[11:17]: ST_ID — Sensor ID (MAC BLE address)
      byte[17]:   ST_ER3 — Extended HW error
      byte[18]:   ST_ERR — Radar comm error counter
      byte[19]:   ST_RNG — Current measurement range (0=Zero, 1=Near, 2=Mid, 3=Far)
    """
    if len(data) < 20:
        return {"raw": data.hex(), "error": "too short"}

    state = data[0]
    status_bits = data[1]
    uptime_s = int.from_bytes(data[2:6], "big")
    error_code = data[6]
    hw_error = data[7]
    temp_c = int.from_bytes(data[8:9], byteorder="big", signed=True)
    voltage_mv = int.from_bytes(data[9:11], "big")
    voltage_v = round(voltage_mv / 1000.0, 3)
    mac = ":".join(f"{b:02X}" for b in data[11:17])
    ext_hw_error = data[17]
    radar_err_cnt = data[18]
    current_range = data[19]

    STATE_NAMES = {
        0: "Start-Up", 1: "Self-Test", 2: "Uninit", 3: "Uncalibrated",
        4: "Calibration", 5: "Active", 6: "Error", 7: "Production-Test", 8: "HW-Test",
    }

    return {
        "state": state,
        "state_str": STATE_NAMES.get(state, f"Unknown({state})"),
        "status_bits": status_bits,
        "status_bits_str": f"{status_bits:08b}",
        "uptime_s": uptime_s,
        "error_code": error_code,
        "hw_error": hw_error,
        "temp_c": temp_c,
        "voltage_v": voltage_v,
        "mac": mac,
        "ext_hw_error": ext_hw_error,
        "radar_err_cnt": radar_err_cnt,
        "current_range": current_range,
        "measuring": 1 if state == 5 else 0,
        "raw": data.hex(),
    }


def parse_measurement(data: bytes) -> dict:
    """
    Parse 20-byte Measurement register (0xFFE9) — Table 27.

    Byte map (official):
      byte[0]:    M_ST   — State (copy from Status)
      byte[1]:    M_SB   — Status bits
      byte[2]:    M_VD   — Level validity (0=invalid, 1=valid)
      bytes[3:5]: M_FL   — Fill level in ‰ [0-1000] (uint16 BE)
      byte[5]:    M_INC  — Sensor inclination [0-90°] (uint8)
      bytes[6:8]: M_DIST — Distance from sensor [mm] (uint16 BE)
      bytes[8:10]:  M_SZR — Envelope size Zero Range
      bytes[10:12]: M_SNR — Envelope size Near Range
      bytes[12:14]: M_SMR — Envelope size Mid Range
      bytes[14:16]: M_SFR — Envelope size Far Range
      bytes[16:20]: Reserved
    """
    if len(data) < 8:
        return {"raw": data.hex(), "error": "too short"}

    level_valid = data[2]
    fill_permille = int.from_bytes(data[3:5], "big")  # 0-1000 ‰
    fill_pct = round(fill_permille / 10.0, 1)          # → 0-100 %
    inclination_deg = data[5]
    distance_mm = int.from_bytes(data[6:8], "big")

    return {
        "state": data[0],
        "status_bits": data[1],
        "level_valid": level_valid,
        "fill_permille": fill_permille,
        "fill_pct": fill_pct,
        "inclination_deg": inclination_deg,
        "distance_mm": distance_mm,
        "envelope_zero": int.from_bytes(data[8:10], "big") if len(data) >= 10 else None,
        "envelope_near": int.from_bytes(data[10:12], "big") if len(data) >= 12 else None,
        "envelope_mid": int.from_bytes(data[12:14], "big") if len(data) >= 14 else None,
        "envelope_far": int.from_bytes(data[14:16], "big") if len(data) >= 16 else None,
        "raw": data.hex(),
    }

# Keep legacy name for backward compatibility
parse_radar = parse_measurement


def parse_n2k_status(data: bytes) -> dict:
    """
    Parse N2K Status register (0xFFF3) — not in base Gobius C spec,
    added by N2K firmware extension. 20 bytes.

    byte[0]: N2K State
    byte[1]: N2K Source Address
    bytes[2:20]: Padding
    """
    if len(data) < 2:
        return {"raw": data.hex(), "error": "too short"}
    return {
        "n2k_state": data[0],
        "n2k_src": data[1],
        "raw": data.hex(),
    }


def parse_user_cfg(data: bytes) -> dict:
    """
    Parse 20-byte User Config register (0xFFE6) — Table 23.

    Byte map (official, all multi-byte = Big-Endian):
      bytes[0:2]:  UC_DE  — Distance for tank EMPTY indication [mm] (uint16 BE, 20-2000)
      bytes[2:4]:  UC_DF  — Distance for tank FULL indication [mm]  (uint16 BE, 20-2000)
      byte[4]:     UC_LPN — Low pass filter size (0=disable, 0-100)
      byte[5]:     UC_LPK — Low pass filter threshold [1-100] %
      byte[6]:     UC_BITS — Config bits (see Table 17)
      byte[7]:     UC_O1T — Output 1 threshold level %
      byte[8]:     UC_O1H — Output 1 hysteresis %
      byte[9]:     UC_O2T — Output 2 threshold %
      byte[10]:    UC_O2H — Output 2 hysteresis %
      byte[11]:    UC_R0  — Resistive Ω at 0%
      byte[12]:    UC_R25 — Resistive Ω at 25%
      byte[13]:    UC_R50 — Resistive Ω at 50%
      byte[14]:    UC_R75 — Resistive Ω at 75%
      byte[15]:    UC_R100 — Resistive Ω at 100%
      byte[16]:    UC_VE  — Voltage empty (unit 25mV)
      byte[17]:    UC_VF  — Voltage full (unit 25mV)
      byte[18]:    UC_AOF — Advertise Off time [10-255 s]
      byte[19]:    Reserved

    Tank depth (as shown in app) = UC_DE (distance empty) in mm.
    Volume (liters) is stored elsewhere (N2K config or app-level).
    """
    if len(data) < 4:
        return {"raw": data.hex(), "error": "too short"}

    dist_empty_mm = int.from_bytes(data[0:2], "big")  # = tank depth in mm
    dist_full_mm = int.from_bytes(data[2:4], "big")    # = dead zone at top

    result = {
        "distance_empty_mm": dist_empty_mm,
        "distance_full_mm": dist_full_mm,
        "tank_depth_mm": dist_empty_mm,  # app shows this as "Tank depth (mm)"
        "raw": data.hex(),
    }

    if len(data) >= 7:
        result["lp_filter_n"] = data[4]
        result["lp_filter_k"] = data[5]
        result["config_bits"] = data[6]
        result["config_bits_str"] = f"{data[6]:08b}"

    if len(data) >= 19:
        result["out1_threshold"] = data[7]
        result["out1_hysteresis"] = data[8]
        result["out2_threshold"] = data[9]
        result["out2_hysteresis"] = data[10]
        result["advertise_off_s"] = data[18]

    return result


def parse_n2k_cfg(data: bytes) -> dict:
    """Parse NMEA 2000 Config register (0xFFF2) — N2K extension, 20 bytes."""
    if len(data) < 10:
        return {"raw": data.hex(), "error": "too short"}
    enabled = data[0]
    instance = data[1]
    fluid_type = data[2]
    # Volume (liters) is at byte[9] — single byte, max 255L
    volume_l = data[9]
    return {
        "n2k_enabled": bool(enabled),
        "fluid_instance": instance,
        "fluid_type": fluid_type,
        "fluid_type_name": FLUID_TYPES.get(fluid_type, f"Unknown({fluid_type})"),
        "volume_l": volume_l,
        "raw": data.hex(),
    }


def compute_fill_level(dist_empty_mm: int, dist_full_mm: int, distance_mm: int) -> float:
    """
    Compute fill level from User Config distances and current measurement.

    dist_empty_mm: UC_DE — distance when tank is EMPTY (= sensor to bottom)
    dist_full_mm:  UC_DF — distance when tank is FULL (= dead zone)
    distance_mm:   M_DIST — current measured distance

    fill% = (dist_empty - distance) / (dist_empty - dist_full) * 100

    Note: the sensor already computes this in M_FL (0xFFE9 bytes[3:5]),
    so this function is mainly for cross-validation.
    """
    measurable_range = dist_empty_mm - dist_full_mm
    if measurable_range <= 0:
        return 0.0
    fill = ((dist_empty_mm - distance_mm) / measurable_range) * 100.0
    return round(max(0.0, min(100.0, fill)), 1)
