import struct

HARDWARE_TYPES = {
    0x03: 'Pro Check',
    0x04: 'Pro 200',
    0x05: 'Pro Check Universal',
    0x08: 'Water',
    0x0A: 'Pro 200 Water',
    0x0C: 'Pro+ Water'
}

QUALITY_LABELS = ['None', 'Low', 'Medium', 'High']


def parse_advertisement(payload: bytes) -> dict:
    """Parse Mopeka Pro advertisement (10 bytes)."""
    if len(payload) < 10:
        return {"error": "Payload too short"}

    # Byte 0
    hw_byte = payload[0]
    hardware_id = hw_byte & 0x7F
    extended_range = bool(hw_byte & 0x80)
    
    hardware_name = HARDWARE_TYPES.get(hardware_id, f"Unknown (0x{hardware_id:02X})")

    # Byte 1
    batt_raw = payload[1] & 0x7F
    voltage_v = batt_raw / 32.0
    
    battery_pct_raw = (voltage_v - 2.2) / (3.0 - 2.2) * 100.0
    battery_pct = max(0.0, min(100.0, battery_pct_raw))

    # Byte 2
    temp_raw = payload[2] & 0x7F
    temp_c = temp_raw - 40
    sync_pressed = bool(payload[2] & 0x80)

    # Byte 3-4 (Little Endian)
    level_word = struct.unpack('<H', payload[3:5])[0]
    tof_us = level_word & 0x3FFF
    if extended_range:
        tof_us *= 4
    
    quality_stars = (level_word >> 14) & 0x03
    quality_label = QUALITY_LABELS[quality_stars]

    # Calculate distance based on hw_id
    if hardware_id in (0x04, 0x0A):
        # Top-down
        v_air = 331.3 + 0.606 * temp_c
        distance_mm = (tof_us * v_air) / 2000.0
    else:
        # Bottom-up
        distance_mm = tof_us * (0.575 - 0.0017 * temp_c)

    # Byte 5-6
    accel_x = payload[5]
    accel_y = payload[6]

    # Byte 7-9
    mac_suffix = payload[7:10]

    return {
        "hardware_id": hardware_id,
        "hardware_name": hardware_name,
        "extended_range": extended_range,
        "voltage_v": round(voltage_v, 2),
        "battery_pct": round(battery_pct, 1),
        "temp_c": temp_c,
        "sync_pressed": sync_pressed,
        "tof_us": tof_us,
        "distance_mm": round(distance_mm, 1),
        "quality_stars": quality_stars,
        "quality_label": quality_label,
        "accel_x": accel_x,
        "accel_y": accel_y,
        "mac_suffix_hex": mac_suffix.hex().upper()
    }


def compute_fill_level(tank_depth_mm: float, air_gap_mm: float) -> float:
    """Compute fill % for top-down sensor (air gap)."""
    if not tank_depth_mm or tank_depth_mm <= 0:
        return 0.0
    fill_mm = tank_depth_mm - air_gap_mm
    pct = (fill_mm / tank_depth_mm) * 100.0
    return max(0.0, min(100.0, round(pct, 1)))
