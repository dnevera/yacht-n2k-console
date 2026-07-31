"""Service tab API routes: I/O pause, service mode, diagnostics, gateway settings."""

import asyncio
import json
import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from models import CmdRequest
from routes import get_device_mgr, get_mopeka_scanner, get_gobius_poller

from ydnu02_tcp_gateway.gateway_settings import GatewaySettings

router = APIRouter()

# --- Global I/O pause/resume state (persisted to disk) ---
_IO_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "io_state.json")


def _load_io_state() -> bool:
    """Load persisted I/O pause state from disk."""
    try:
        with open(_IO_STATE_FILE) as f:
            return json.load(f).get("paused", False)
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _save_io_state(paused: bool):
    """Persist I/O pause state to disk."""
    with open(_IO_STATE_FILE, "w") as f:
        json.dump({"paused": paused}, f)


_io_paused = _load_io_state()


def is_io_paused() -> bool:
    """Return whether I/O is currently paused."""
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
    """Construct current I/O state summary for serial and BLE devices."""
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
    """Retrieve current N2K filter configurations."""
    if _io_paused: return io_paused_response()
    try:
        return await asyncio.to_thread(get_device_mgr().get_filters)
    except Exception as e:
        return {"filters": {}, "error": str(e)}


@router.get("/settings")
async def api_settings():
    """Retrieve current device settings."""
    if _io_paused: return io_paused_response()
    try:
        return await asyncio.to_thread(get_device_mgr().get_settings)
    except Exception as e:
        return {"settings": {}, "error": str(e)}


@router.get("/diag/{scope}")
async def api_diag(scope: str):
    """Retrieve diagnostic data for the specified scope."""
    if _io_paused: return io_paused_response()
    try:
        return await asyncio.to_thread(get_device_mgr().get_diag, scope)
    except Exception as e:
        return {"data": f"[Error] {e}"}


@router.post("/service/cmd")
async def api_service_cmd(req: CmdRequest):
    """Execute a service command on the device manager."""
    if _io_paused: return io_paused_response()
    try:
        return await asyncio.to_thread(get_device_mgr().send_service_cmd, req.cmd)
    except Exception as e:
        # Return JSON with error text so it displays in the terminal UI
        # (prevents HTTP 500 plain text which breaks JS JSON.parse)
        return {"response": f"[Error] {e}"}


@router.post("/service/enter")
async def api_service_enter():
    """Enter device service mode."""
    if _io_paused: return io_paused_response()
    try:
        return await asyncio.to_thread(get_device_mgr().enter_service)
    except Exception as e:
        return {"state": "error", "error": str(e)}


@router.post("/service/exit")
async def api_service_exit():
    """Exit device service mode."""
    if _io_paused: return io_paused_response()
    try:
        return await asyncio.to_thread(get_device_mgr().exit_service)
    except Exception as e:
        return {"state": "error", "error": str(e)}


@router.get("/service/state")
async def api_service_state():
    """Get the current service mode state."""
    return {"state": "STOPPED" if _io_paused else get_device_mgr().get_state()}


# ===========================================================================
#  GATEWAY SETTINGS — KI-001 ISO Replay workaround
# ===========================================================================

@router.get("/gw-settings")
async def api_gw_settings_get():
    """Return current GatewaySettings as JSON.

    Response::

        {
          "ha_iso_replay_enabled":    true,
          "ha_iso_replay_interval_s": 60.0
        }

    Skill — read via curl::

        curl -s http://localhost:8080/api/gw-settings | python3 -m json.tool
    """
    return GatewaySettings.instance().to_dict()


@router.post("/gw-settings")
async def api_gw_settings_post(body: dict):
    """Update GatewaySettings. Accepts a partial dict — only provided keys are updated.

    Changes take effect within ~3s (next GW_TEMP_INTERVAL_S loop iteration)
    without restarting the daemon.

    Request body (all fields optional)::

        {
          "ha_iso_replay_enabled":    true,
          "ha_iso_replay_interval_s": 60.0   // range: 5–3600 seconds
        }

    Skill — disable ISO replay::

        curl -X POST http://localhost:8080/api/gw-settings \\
             -H 'Content-Type: application/json' \\
             -d '{"ha_iso_replay_enabled": false}'

    Skill — set 30s interval::

        curl -X POST http://localhost:8080/api/gw-settings \\
             -H 'Content-Type: application/json' \\
             -d '{"ha_iso_replay_interval_s": 30}'
    """
    try:
        updated = GatewaySettings.instance().apply_from_dict(body)
        return updated
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
