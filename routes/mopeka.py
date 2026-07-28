from fastapi import APIRouter, HTTPException
from routes import get_mopeka_scanner

router = APIRouter()


@router.get("/mopeka/sensors")
async def mopeka_sensors():
    """Get all Mopeka sensors."""
    scanner = get_mopeka_scanner()
    if not scanner:
        return {"sensors": []}
    return {"sensors": scanner.get_sensors()}


@router.get("/mopeka/sensor/{mac}")
async def mopeka_sensor(mac: str):
    """Get a specific Mopeka sensor by MAC."""
    scanner = get_mopeka_scanner()
    if not scanner:
        raise HTTPException(404, "Mopeka scanner not available")
    
    sensor = scanner.get_sensor(mac)
    if not sensor:
        raise HTTPException(404, f"Sensor {mac} not found")
    return sensor


@router.post("/mopeka/config/{mac}")
async def mopeka_config(mac: str, body: dict):
    """Update Mopeka sensor configuration (tank depth, capacity, etc.)."""
    scanner = get_mopeka_scanner()
    if not scanner:
        raise HTTPException(404, "Mopeka scanner not available")
        
    scanner.update_config(mac, body)
    
    sensor = scanner.get_sensor(mac)
    return {"status": "ok", "sensor": sensor}


@router.delete("/mopeka/sensor/{mac}")
async def mopeka_delete(mac: str):
    """Delete a Mopeka sensor from configuration."""
    scanner = get_mopeka_scanner()
    if not scanner:
        raise HTTPException(404, "Mopeka scanner not available")
        
    if mac in scanner.sensors:
        del scanner.sensors[mac]
        scanner._save_config()
        return {"status": "ok"}
    raise HTTPException(404, f"Sensor {mac} not found")
