import asyncio
from fastapi import APIRouter, HTTPException
from routes import get_device_mgr
from routes.service import is_io_paused, io_paused_response

router = APIRouter()


@router.get("/info")
async def api_info(force: bool = False):
    if is_io_paused():
        return {"state": "offline", "error": "I/O is stopped. Resume from Dashboard first."}
    try:
        mgr = get_device_mgr()
        info = await asyncio.to_thread(mgr.get_info, force)
        if isinstance(info, dict):
            info["app_version"] = mgr.get_app_version()
        return info
    except Exception as e:
        return {"state": "offline", "error": str(e)}


@router.get("/version")
async def api_version():
    """Get console application version from VERSION file."""
    mgr = get_device_mgr()
    return {"version": mgr.get_app_version() if mgr else "0.0.0"}


@router.post("/mode/{mode}")
async def api_mode(mode: str):
    if is_io_paused(): return io_paused_response()
    if mode not in ("auto", "0183", "raw", "n2k"):
        raise HTTPException(400, f"Invalid mode: {mode}")
    mgr = get_device_mgr()
    result = await asyncio.to_thread(mgr.set_mode, mode)
    mgr._info_cache = None
    return result


@router.post("/silent/{state}")
async def api_silent(state: str):
    if is_io_paused(): return io_paused_response()
    if state not in ("on", "off"):
        raise HTTPException(400, f"Invalid state: {state}")
    mgr = get_device_mgr()
    result = await asyncio.to_thread(mgr.set_silent, state)
    mgr._info_cache = None
    return result


@router.get("/sensors")
async def api_sensors():
    """Get instant live sensor state parsed from NMEA 2000 bus (0ms response)."""
    return get_device_mgr().get_sensors_state()


@router.get("/errors")
async def api_get_errors(limit: int = 100, src: int = None):
    """Get recorded CAN bus error events."""
    return get_device_mgr().get_error_log(limit=limit, src=src)


@router.delete("/errors")
async def api_clear_errors():
    """Clear recorded CAN bus error events history."""
    return get_device_mgr().clear_error_log()


@router.get("/dashboard/sensors")
async def api_dashboard_sensors():
    """Unified sensor list for dashboard — merges registry + live data.

    Returns normalized cards with `channels` array for ALL registered sensors.
    Each channel has: name, age_sec, live (bool), fields (list of [label, value, accent?]).
    """
    from routes import get_ble_registry, get_mopeka_scanner

    registry = get_ble_registry()
    if not registry:
        return {"sensors": []}

    # Collect live data sources
    nmea_items = get_device_mgr().get_sensors_state().get("fluid_levels", [])
    mopeka_scanner = get_mopeka_scanner()
    mopeka_live = {}
    if mopeka_scanner:
        for s in mopeka_scanner.get_sensors():
            mopeka_live[s["mac_address"]] = s

    def _fmt(val, unit, decimals=1):
        """Format value with unit, return None if val is None."""
        if val is None:
            return None
        if isinstance(val, float):
            return f"{val:.{decimals}f} {unit}".strip()
        return f"{val} {unit}".strip()

    result = []
    for entry in registry.get_all():
        mac = entry["mac"]
        sensor_type = entry.get("type", "unknown")
        channels = []
        online = False
        age_sec = None

        if sensor_type == "gobius":
            # NMEA 2000 channel (live data)
            for nmea in nmea_items:
                nmea_age = nmea.get("age_sec")
                fields = []
                fill = nmea.get("fill_level_pct") or nmea.get("level_pct")
                cap = nmea.get("capacity_l")
                vol = nmea.get("calculated_l")
                ft = nmea.get("nmea", {}).get("fluid_type_name")

                if fill is not None: fields.append(["Fill", _fmt(fill, "%"), True])
                if vol is not None: fields.append(["Volume", _fmt(vol, "L"), True])
                if cap is not None: fields.append(["Capacity", _fmt(cap, "L")])
                if ft: fields.append(["Fluid Type", ft])

                channels.append({
                    "name": "NMEA 2000",
                    "age_sec": nmea_age,
                    "live": nmea_age is not None and nmea_age < 30,
                    "fields": fields,
                })
                online = True
                age_sec = nmea_age
                break

            # BLE channel (live poller — persistent GATT connection)
            gobius_sensor = get_device_mgr().sensors.get(0)
            if gobius_sensor and gobius_sensor.ble.voltage_v is not None:
                b = gobius_sensor.ble
                ble_fields = []
                if b.voltage_v is not None: ble_fields.append(["Bus Voltage", _fmt(b.voltage_v, "V", 2)])
                if b.temp_c is not None: ble_fields.append(["Temp", _fmt(b.temp_c, "°C", 0), True])
                if b.distance_mm is not None: ble_fields.append(["Distance", _fmt(b.distance_mm, "mm", 0)])
                if b.fill_pct is not None: ble_fields.append(["Fill (radar)", _fmt(b.fill_pct, "%")])
                if ble_fields:
                    from routes import get_gobius_poller
                    poller = get_gobius_poller()
                    ble_age = poller.age_sec if poller else None
                    channels.append({
                        "name": "BLE",
                        "age_sec": ble_age,
                        "live": poller is not None and poller.connected and ble_age is not None and ble_age < 30,
                        "fields": ble_fields,
                    })

        elif sensor_type == "mopeka":
            live = mopeka_live.get(mac)
            if live:
                mop_age = live.get("age_sec")
                fields = []
                fill = live.get("fill_level_pct")
                vol = live.get("calculated_l")
                cap = live.get("capacity_l")
                temp = live.get("temp_c")
                batt_pct = live.get("battery_pct")
                batt_v = live.get("voltage_v")

                if fill is not None: fields.append(["Fill", _fmt(fill, "%"), True])
                if vol is not None: fields.append(["Volume", _fmt(vol, "L"), True])
                if cap is not None: fields.append(["Capacity", _fmt(cap, "L")])
                if temp is not None: fields.append(["Temp", _fmt(temp, "°C"), True])
                if batt_pct is not None:
                    fields.append(["Battery", _fmt(batt_pct, "%", 0), True])
                elif batt_v is not None:
                    fields.append(["Battery", _fmt(batt_v, "V", 2), True])

                channels.append({
                    "name": "BLE",
                    "age_sec": mop_age,
                    "live": live.get("source") != "OFFLINE",
                    "fields": fields,
                })
                online = live.get("source") != "OFFLINE"
                age_sec = mop_age

        # Registry channel (user config / service registry settings)
        reg_fields = []
        if entry.get("fluid_type"):
            reg_fields.append(["Fluid Type", entry["fluid_type"]])
        if entry.get("capacity_l") is not None:
            reg_fields.append(["Capacity", _fmt(entry["capacity_l"], "L")])
        if entry.get("tank_depth_mm") is not None:
            reg_fields.append(["Tank Depth", _fmt(entry["tank_depth_mm"], "mm", 0)])

        if reg_fields:
            channels.append({
                "name": "Registry",
                "age_sec": None,
                "live": False,
                "fields": reg_fields,
            })

        card = {
            "mac": mac,
            "name": entry.get("name", "Sensor"),
            "type": sensor_type,
            "online": online,
            "age_sec": age_sec,
            "fluid_type": entry.get("fluid_type") or entry.get("fluid_type_name") or "",
            "channels": channels,
        }
        result.append(card)

    return {"sensors": result}
