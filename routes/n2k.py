"""
routes/n2k.py — Universal NMEA 2000 command endpoint.

Sends PGN 126208 Group Function commands to any device on the CAN bus.
Safety guard: for Gobius sensors (dual-channel BLE+N2K), checks N2K broadcast
state before sending — command is useless if N2K broadcasting is off.
"""

from fastapi import APIRouter, HTTPException
from routes import get_device_mgr

router = APIRouter()


@router.get("/n2k/autopilot")
async def autopilot_state():
    """Read-only snapshot of the Raymarine Evolution autopilot state.

    Never sends anything to the bus. With no autopilot traffic the answer is
    still HTTP 200 with mode "unknown" and age_sec null.
    See specs/active/008-autopilot-control.md.
    """
    dev_mgr = get_device_mgr()
    if not dev_mgr:
        raise HTTPException(503, "Device manager not running")

    return {"status": "ok", "autopilot": dev_mgr.get_autopilot_state()}


@router.post("/n2k/command")
async def n2k_command(body: dict):
    """
    Send PGN 126208 (Group Function Command) to a target NMEA 2000 device.

    Body:
        target_address (int): SRC address of target device
        target_pgn (int): PGN to configure (e.g. 127505 for Fluid Level)
        fields (dict): Field values keyed by field name
            For PGN 127505: instance, fluid_type, capacity
    """
    dev_mgr = get_device_mgr()
    if not dev_mgr:
        raise HTTPException(503, "Device manager not running")

    target_address = int(body.get("target_address", 255))
    target_pgn = int(body.get("target_pgn", 127505))
    fields = body.get("fields", {})

    # Safety guard for dual-channel Gobius: if target is a known Gobius sensor
    # with N2K broadcasting disabled, PGN 126208 won't be received.
    # n2k_state and n2k_src live in sensor.ble (read via BLE GATT 0xFFF3).
    dev_mgr_sensors = dev_mgr.sensors if hasattr(dev_mgr, 'sensors') else {}
    for sensor in dev_mgr_sensors.values():
        if hasattr(sensor, 'nmea') and sensor.nmea.src == target_address:
            if hasattr(sensor, 'ble') and sensor.ble.n2k_state == 0:
                raise HTTPException(
                    400,
                    f"NMEA 2000 broadcasting is disabled on sensor SRC {target_address}. "
                    "Enable N2K via BLE first (Gobius C → N2K Config → Enable)."
                )

    from n2k_command_builder import build_pgn_126208_command
    cmd = build_pgn_126208_command(
        target_address=target_address,
        target_pgn=target_pgn,
        instance=int(fields.get("instance", 0)),
        fluid_type_code=int(fields["fluid_type"]) if "fluid_type" in fields else None,
        capacity_l=float(fields["capacity"]) if "capacity" in fields else None,
    )

    dev_mgr.send_raw_command(cmd["hex_str"])

    return {
        "status": "ok",
        "message": f"PGN 126208 command sent to SRC {target_address} (target PGN {target_pgn})",
        "command": cmd["params"],
        "hex": cmd["hex_str"],
    }
