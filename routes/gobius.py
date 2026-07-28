"""Gobius C BLE routes — read/write sensor config via poller's persistent connection.

All BLE operations go through GobiusBLEPoller which owns the single GATT connection.
No direct BLE connect/disconnect in routes — only poller.read_char/write_char.

Writable characteristics (from Gobius C protocol Issue 3, 2023-08-08):
  0xFFE6 (User Config, R/W, 20 bytes): tank geometry, outputs, filtering
  0xFFE7 (Command, W, 3 bytes): calibrate, initialize, advertise mode
  0xFFF2 (N2K Config, R/W, 20 bytes): NMEA 2000 volume, fluid type, instance
  0xFFEB/0xFFEC (Info 1-2, R/W, 20 bytes): user labels (ASCII)
"""

import asyncio
from fastapi import APIRouter, HTTPException
from routes import get_device_mgr, get_gobius_poller, get_ble_registry
from gobius_parsers import parse_user_cfg, parse_n2k_cfg

router = APIRouter()

# GATT UUIDs
UUID_USER_CFG   = "0000ffe6-0000-1000-8000-00805f9b34fb"
UUID_N2K_CFG    = "0000fff2-0000-1000-8000-00805f9b34fb"
UUID_COMMAND    = "0000ffe7-0000-1000-8000-00805f9b34fb"
UUID_INFO1      = "0000ffeb-0000-1000-8000-00805f9b34fb"
UUID_INFO2      = "0000ffec-0000-1000-8000-00805f9b34fb"

# FFE7 Command codes
COMMANDS = {
    "initialize": b"i",
    "calibrate":  b"c",
    "stop":       b"a",
    "start":      b"b",
    "adv_normal": b"n",
    "adv_off":    b"o",
    "write_info": b"w",
    "secure":     b"s",
    "unsecure":   b"u",
}


def _get_poller():
    """Get poller or raise 503."""
    poller = get_gobius_poller()
    if not poller or not poller.connected:
        raise HTTPException(503, "Gobius BLE not connected")
    return poller


def _get_gobius_addr():
    """Get first registered Gobius MAC from BLE registry."""
    reg = get_ble_registry()
    if reg:
        sensors = reg.get_by_type("gobius")
        if sensors:
            return sensors[0]["mac"]
    return None


def _get_active_gobius_sensor():
    dev_mgr = get_device_mgr()
    if not dev_mgr or not dev_mgr.sensors:
        return None
    return next(iter(dev_mgr.sensors.values()), None)

@router.get("/gobius/live")
async def gobius_live():
    """Return all BLE sensor data from poller cache — for auto-refresh."""
    poller = get_gobius_poller()
    sensor = _get_active_gobius_sensor()

    if not sensor:
        return {"connected": False, "error": "No sensor data yet"}

    b = sensor.ble
    n = sensor.nmea
    return {
        "connected": poller.connected if poller else False,
        "age_sec": poller.age_sec if poller else None,
        "address": b.mac_address or _get_gobius_addr() or "",
        "device": {
            "serial": b.serial_number,
            "firmware": b.firmware,
        },
        "status": {
            "temp_c": b.temp_c,
            "voltage_v": b.voltage_v,
            "uptime_s": b.uptime_s,
            "current_range": b.current_range,
            "mac": b.mac_address,
            "state_str": b.state_str,
            "status_bits_str": b.status_bits_str,
            "error_code": b.error_code,
            "measuring": b.measuring,
        },
        "measurement": {
            "fill_pct": b.fill_pct,
            "distance_mm": b.distance_mm,
            "inclination_deg": b.inclination_deg,
        },
        "n2k_status": {
            "n2k_state": b.n2k_state,
            "n2k_src": b.n2k_src,
        },
        "unified_sensor": {
            "fill_level_pct": n.fill_level_pct,
            "capacity_l": n.capacity_l,
            "calculated_l": n.calculated_l,
        },
    }


# ─── REFRESH (full re-read via poller — returns configs for input fields) ───

@router.get("/gobius/status")
async def gobius_refresh():
    """Trigger full BLE re-read via poller. Returns all data including configs."""
    poller = get_gobius_poller()
    addr = _get_gobius_addr()

    if not poller or not poller.connected:
        return {
            "connected": False,
            "error": "Gobius BLE not connected",
            "address": addr or "(not configured)",
        }

    try:
        ok = await poller.do_full_read()
        if not ok:
            return {
                "connected": False,
                "error": "Failed reading BLE GATT data",
                "address": addr or "(not configured)",
            }

        sensor = get_device_mgr().sensors.get(0)
        if not sensor:
            return {"connected": False, "error": "No sensor after read"}

        b = sensor.ble
        n = sensor.nmea

        # Get stored parsed config dicts from poller's full read
        n2k_cfg = getattr(sensor, '_ble_n2k_config', {})
        user_cfg = getattr(sensor, '_ble_user_config', {})

        return {
            "connected": True,
            "address": b.mac_address or addr or "",
            "device": {
                "serial": b.serial_number,
                "firmware": b.firmware,
                "info1": sensor.name,
                "info2": sensor.comment,
            },
            "status": {
                "temp_c": b.temp_c,
                "voltage_v": b.voltage_v,
                "uptime_s": b.uptime_s,
                "current_range": b.current_range,
                "mac": b.mac_address,
                "state_str": b.state_str,
                "status_bits_str": b.status_bits_str,
                "error_code": b.error_code,
                "measuring": b.measuring,
            },
            "measurement": {
                "fill_pct": b.fill_pct,
                "distance_mm": b.distance_mm,
                "inclination_deg": b.inclination_deg,
            },
            "n2k_status": {
                "n2k_state": b.n2k_state,
                "n2k_src": b.n2k_src,
            },
            "n2k_config": n2k_cfg,
            "user_config": user_cfg,
            "unified_sensor": sensor.to_dict(),
        }

    except Exception as e:
        return {
            "connected": False,
            "error": f"Failed reading BLE GATT data: {e}",
            "address": addr or "(not configured)",
        }


# ─── WRITE: N2K Config (0xFFF2) ───

@router.post("/gobius/n2k")
async def gobius_write_n2k(body: dict):
    """Write NMEA 2000 Config to Gobius C (0xFFF2) via poller."""
    poller = _get_poller()
    try:
        poller.pause()
        old = bytearray(await poller.read_char(UUID_N2K_CFG))
        if "enabled" in body:
            old[0] = 0x01 if body["enabled"] else 0x00
        if "fluid_instance" in body:
            old[1] = int(body["fluid_instance"]) & 0x0F
        if "fluid_type" in body:
            old[2] = int(body["fluid_type"]) & 0xFF
        if "volume_l" in body:
            vol = max(1, min(255, int(body["volume_l"])))
            old[9] = vol

        await poller.write_char(UUID_N2K_CFG, bytes(old))
        await asyncio.sleep(0.5)

        verify = await poller.read_char(UUID_N2K_CFG)
        saved = verify == bytes(old)

        # Physical sensor data (fluid_type, capacity) lives on the sensor,
        # NOT in our app registry. Registry only stores mac/type/name.

        # Re-read everything after config change
        await poller.do_full_read()

        return {
            "status": "ok" if saved else "verify_failed",
            "message": "N2K config saved" if saved else "Write failed — verify mismatch",
            "config": parse_n2k_cfg(verify),
        }
    finally:
        poller.resume()


# ─── WRITE: User Config (0xFFE6) ───

@router.post("/gobius/user_config")
async def gobius_write_user_config(body: dict):
    """Write User Config to Gobius C (0xFFE6, 20 bytes) via poller."""
    poller = _get_poller()
    try:
        poller.pause()
        old = bytearray(await poller.read_char(UUID_USER_CFG))

        if "distance_empty_mm" in body:
            val = max(20, min(2000, int(body["distance_empty_mm"])))
            old[0:2] = val.to_bytes(2, "big")
        if "distance_full_mm" in body:
            val = max(20, min(2000, int(body["distance_full_mm"])))
            old[2:4] = val.to_bytes(2, "big")
        if "lp_filter_n" in body:
            old[4] = max(0, min(100, int(body["lp_filter_n"])))
        if "lp_filter_k" in body:
            old[5] = max(1, min(100, int(body["lp_filter_k"])))
        if "config_bits" in body:
            old[6] = int(body["config_bits"]) & 0xFF
        if "out1_threshold" in body:
            old[7] = max(0, min(100, int(body["out1_threshold"])))
        if "out1_hysteresis" in body:
            old[8] = max(0, min(100, int(body["out1_hysteresis"])))
        if "out2_threshold" in body:
            old[9] = max(0, min(100, int(body["out2_threshold"])))
        if "out2_hysteresis" in body:
            old[10] = max(0, min(100, int(body["out2_hysteresis"])))
        if "advertise_off_s" in body:
            old[18] = max(10, min(255, int(body["advertise_off_s"])))

        await poller.write_char(UUID_USER_CFG, bytes(old))
        await asyncio.sleep(0.5)

        verify = await poller.read_char(UUID_USER_CFG)
        saved = verify == bytes(old)

        # Re-read everything after config change
        await poller.do_full_read()

        return {
            "status": "ok" if saved else "verify_failed",
            "message": "User config saved" if saved else "Write failed — verify mismatch",
            "config": parse_user_cfg(verify),
        }
    finally:
        poller.resume()


# ─── WRITE: Command (0xFFE7) ───

@router.post("/gobius/command")
async def gobius_send_command(body: dict):
    """Send command to Gobius C (0xFFE7) via poller."""
    cmd_name = body.get("command", "").lower()
    if cmd_name not in COMMANDS:
        raise HTTPException(400, f"Unknown command: {cmd_name}. Valid: {list(COMMANDS.keys())}")

    poller = _get_poller()
    try:
        poller.pause()
        cmd_bytes = bytearray(3)
        cmd_bytes[0] = COMMANDS[cmd_name][0]
        if "param" in body:
            param = int(body["param"])
            cmd_bytes[1:3] = param.to_bytes(2, "big")

        await poller.write_char(UUID_COMMAND, bytes(cmd_bytes))
        await asyncio.sleep(1.0)

        return {
            "status": "ok",
            "message": f"Command '{cmd_name}' sent",
            "command_hex": cmd_bytes.hex(),
        }
    finally:
        poller.resume()


# ─── WRITE: Info 1-2 (0xFFEB, 0xFFEC) ───

@router.post("/gobius/info")
async def gobius_write_info(body: dict):
    """Write Info fields (tank name, comment) via poller."""
    poller = _get_poller()
    try:
        poller.pause()
        result = {}
        for key, uuid in [("info1", UUID_INFO1), ("info2", UUID_INFO2)]:
            if key in body:
                val = str(body[key])[:20].ljust(20)
                await poller.write_char(uuid, val.encode("utf-8"))
                result[key] = val.strip()

        return {"status": "ok", "message": "Info saved", **result}
    finally:
        poller.resume()


# ─── WRITE: NMEA 2000 Command PGN 126208 (CAN Bus) ───

# N2K command endpoint moved to routes/n2k.py — POST /api/n2k/command

