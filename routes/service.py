import asyncio
import json
import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from models import CmdRequest
from routes import get_device_mgr, get_mopeka_scanner, get_gobius_poller

router = APIRouter()

# --- Global I/O pause/resume state (persisted to disk) ---
_IO_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "io_state.json")


def _load_io_state() -> bool:
    try:
        with open(_IO_STATE_FILE) as f:
            return json.load(f).get("paused", False)
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _save_io_state(paused: bool):
    with open(_IO_STATE_FILE, "w") as f:
        json.dump({"paused": paused}, f)


_io_paused = _load_io_state()


def is_io_paused() -> bool:
    return _io_paused


def io_paused_response():
    """Return 503 JSON when IO is paused."""
    return JSONResponse(
        status_code=503,
        content={"error": "I/O is stopped. Resume from Dashboard first."}
    )


@router.post("/io/pause")
async def api_io_pause():
    """Pause all I/O: serial port + BLE (Gobius + Mopeka). Service stays running."""
    global _io_paused
    dm = get_device_mgr()
    mopeka = get_mopeka_scanner()
    gobius = get_gobius_poller()

    # 1. Stop bus worker (releases serial port)
    dm.stop_bus_worker()
    dm._state = "STOPPED"

    # 2. Stop Gobius BLE poller (disconnects GATT)
    if gobius:
        await gobius.stop()

    # 3. Stop Mopeka scanner (stops BLE scanning)
    if mopeka:
        await mopeka.stop()

    _io_paused = True
    _save_io_state(True)
    print("[IO] All I/O paused — serial and BLE released")
    return _build_io_state()


@router.post("/io/resume")
async def api_io_resume():
    """Resume all I/O: serial port + BLE."""
    global _io_paused
    _io_paused = False
    _save_io_state(False)

    dm = get_device_mgr()
    mopeka = get_mopeka_scanner()
    gobius = get_gobius_poller()

    # 1. Start Mopeka scanner
    if mopeka:
        await mopeka.start()

    # 2. Start Gobius BLE poller
    if gobius:
        await gobius.start()

    # 3. Start bus worker (opens serial port)
    dm.start_bus_worker()

    print("[IO] All I/O resumed — serial and BLE active")
    return _build_io_state()


@router.get("/io/state")
async def api_io_state():
    """Current I/O state with per-service details."""
    return _build_io_state()


def _build_io_state() -> dict:
    dm = get_device_mgr()
    mopeka = get_mopeka_scanner()
    gobius = get_gobius_poller()

    # Serial: use actual device manager state
    serial_state = dm.get_state() if dm else "UNKNOWN"
    if _io_paused and serial_state not in ("STOPPED",):
        serial_state = "STOPPED"

    # Gobius: check actual running + connected
    if gobius:
        if not gobius._running:
            gobius_state = "STOPPED"
        elif gobius.connected:
            gobius_state = "CONNECTED"
        else:
            gobius_state = "CONNECTING"
    else:
        gobius_state = "N/A"

    # Mopeka: check actual _running flag
    if mopeka:
        mopeka_state = "SCANNING" if getattr(mopeka, '_running', False) else "STOPPED"
    else:
        mopeka_state = "N/A"

    return {
        "paused": _io_paused,
        "port": dm.get_port() if dm else "--",
        "serial": serial_state,
        "gobius": gobius_state,
        "mopeka": mopeka_state,
    }

@router.get("/filters")
async def api_filters():
    if _io_paused: return io_paused_response()
    return await asyncio.to_thread(get_device_mgr().get_filters)


@router.get("/settings")
async def api_settings():
    if _io_paused: return io_paused_response()
    return await asyncio.to_thread(get_device_mgr().get_settings)


@router.get("/diag/{scope}")
async def api_diag(scope: str):
    if _io_paused: return io_paused_response()
    return await asyncio.to_thread(get_device_mgr().get_diag, scope)


@router.post("/service/cmd")
async def api_service_cmd(req: CmdRequest):
    if _io_paused: return io_paused_response()
    return await asyncio.to_thread(get_device_mgr().send_service_cmd, req.cmd)


@router.post("/service/enter")
async def api_service_enter():
    if _io_paused: return io_paused_response()
    return await asyncio.to_thread(get_device_mgr().enter_service)


@router.post("/service/exit")
async def api_service_exit():
    if _io_paused: return io_paused_response()
    return await asyncio.to_thread(get_device_mgr().exit_service)


@router.get("/service/state")
async def api_service_state():
    return {"state": "STOPPED" if _io_paused else get_device_mgr().get_state()}
