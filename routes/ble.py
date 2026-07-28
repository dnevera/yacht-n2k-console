"""
Unified BLE sensor management routes — scan, add, remove.

Shared by Gobius, Mopeka, and future sensor types.
"""

import asyncio
from fastapi import APIRouter, HTTPException

router = APIRouter()

# Known manufacturer IDs → sensor type
_MFR_TYPE_MAP = {
    0x0F53: "gobius",   # Gobius Sensor Tech
    0x0059: "mopeka",   # Nordic (Mopeka uses Nordic chipset)
}


def _get_registry():
    from routes import get_ble_registry
    reg = get_ble_registry()
    if not reg:
        raise HTTPException(500, "BLE registry not available")
    return reg


@router.get("/ble/sensors")
async def ble_sensors(type: str = None):
    """List all registered BLE sensors, optionally filtered by type."""
    reg = _get_registry()
    if type:
        return {"sensors": reg.get_by_type(type)}
    return {"sensors": reg.get_all()}


@router.post("/ble/sensors")
async def ble_add_sensor(body: dict):
    """Add a BLE sensor to registry.

    Body: {mac, type, name, ...extra config}
    """
    mac = body.get("mac", "").upper()
    sensor_type = body.get("type", "")
    name = body.get("name", "")

    if not mac or not sensor_type:
        raise HTTPException(400, "mac and type are required")

    reg = _get_registry()
    if reg.is_registered(mac):
        raise HTTPException(409, f"Sensor {mac} already registered")

    # Pass through any extra config (tank_depth_mm, capacity_l, etc.)
    extra = {k: v for k, v in body.items() if k not in ("mac", "type", "name")}
    sensor = reg.add(mac, sensor_type, name, **extra)

    # If Mopeka, register in scanner too
    if sensor_type == "mopeka":
        _sync_mopeka_to_scanner(mac, name, extra)

    return {"status": "ok", "sensor": sensor}


@router.delete("/ble/sensors/{mac}")
async def ble_remove_sensor(mac: str):
    """Remove a BLE sensor from registry."""
    reg = _get_registry()
    sensor = reg.get(mac)
    if not sensor:
        raise HTTPException(404, f"Sensor {mac} not found")

    sensor_type = sensor.get("type")
    reg.remove(mac)

    # If Mopeka, remove from scanner
    if sensor_type == "mopeka":
        from routes import get_mopeka_scanner
        scanner = get_mopeka_scanner()
        if scanner and mac in scanner.sensors:
            del scanner.sensors[mac]

    return {"status": "ok"}


@router.put("/ble/sensors/{mac}")
async def ble_update_sensor(mac: str, body: dict):
    """Update sensor config (name, tank_depth_mm, etc.)."""
    reg = _get_registry()
    if not reg.is_registered(mac):
        raise HTTPException(404, f"Sensor {mac} not found")

    reg.update(mac, body)

    # Sync to Mopeka scanner if applicable
    sensor = reg.get(mac)
    if sensor and sensor.get("type") == "mopeka":
        _sync_mopeka_to_scanner(mac, body.get("name"), body)

    return {"status": "ok", "sensor": reg.get(mac)}


@router.get("/ble/scan")
async def ble_scan(duration: float = 10.0):
    """Scan for BLE devices. Returns list with type identification."""
    try:
        from bleak import BleakScanner
    except ImportError:
        raise HTTPException(500, "bleak not installed")

    duration = min(max(duration, 3.0), 30.0)

    # Pause Mopeka background scanner to free BLE adapter
    from routes import get_mopeka_scanner
    scanner = get_mopeka_scanner()
    if scanner:
        await scanner.stop()

    try:
        results = await BleakScanner.discover(timeout=duration, return_adv=True)
    finally:
        # Always restart Mopeka scanner
        if scanner:
            await scanner.start()
    reg = _get_registry()
    found = []

    for dev, adv in results.values():
        sensor_type = None
        for mfr_id, stype in _MFR_TYPE_MAP.items():
            if mfr_id in adv.manufacturer_data:
                sensor_type = stype
                break

        if sensor_type is None:
            continue  # Skip unknown BLE devices

        found.append({
            "mac": dev.address,
            "name": dev.name or "",
            "type": sensor_type,
            "rssi": adv.rssi,
            "registered": reg.is_registered(dev.address),
        })

    # Sort: unregistered first, then by RSSI
    found.sort(key=lambda x: (x["registered"], x["rssi"]))
    return {"devices": found, "duration": duration}


def _sync_mopeka_to_scanner(mac, name, config):
    """Push config to live MopekaScanner instance."""
    from routes import get_mopeka_scanner
    scanner = get_mopeka_scanner()
    if not scanner:
        return
    from sensors.mopeka_sensor import MopekaSensor
    if mac not in scanner.sensors:
        scanner.sensors[mac] = MopekaSensor(mac=mac, name=name or "Mopeka Sensor")
    sensor = scanner.sensors[mac]
    if name:
        sensor.name = name
    if config.get("tank_depth_mm"):
        sensor.tank_depth_mm = float(config["tank_depth_mm"])
    if config.get("capacity_l"):
        sensor.capacity_l = float(config["capacity_l"])
    if config.get("fluid_type"):
        sensor.fluid_type = config["fluid_type"]
