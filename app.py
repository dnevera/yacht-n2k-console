#!/usr/bin/env python3
"""
YDNU-02 Web Console — FastAPI backend.

Provides REST API and WebSocket endpoints for the YDNU-02 NMEA 2000 USB Gateway.
Uses YDNU02Controller from ydnu02.py as the device interface.
"""

import os
import asyncio
import argparse
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from device_manager import DeviceManager
from mopeka_scanner import MopekaScanner
from ble_registry import BLERegistry
from gobius_ble_poller import GobiusBLEPoller

# --- Parse args early so device_mgr is ready before routes import ---

def _parse_args():
    parser = argparse.ArgumentParser(description="YDNU-02 Web Console")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--device", type=str, default=None, help="Serial port (default: auto-detect)")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    return parser.parse_args()

_args = _parse_args()
device_mgr = DeviceManager(port=_args.device, debug=_args.debug)
ble_registry = BLERegistry()
mopeka_scanner = MopekaScanner(registry=ble_registry)
gobius_poller = GobiusBLEPoller(device_manager=device_mgr, registry=ble_registry, mopeka_scanner=mopeka_scanner)

# --- App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    from routes.service import is_io_paused
    print(f"[YDNU02 Web Console] Device port: {device_mgr.get_port()}")
    print(f"[YDNU02 Web Console] Static dir: {os.path.join(os.path.dirname(__file__), 'static')}")
    device_mgr.set_event_loop(asyncio.get_event_loop())
    if is_io_paused():
        print("[YDNU02 Web Console] I/O was STOPPED — skipping bus/BLE start")
        device_mgr._state = "STOPPED"
    else:
        device_mgr.start_bus_worker()
        await mopeka_scanner.start()
        await gobius_poller.start()
    yield
    await gobius_poller.stop()
    await mopeka_scanner.stop()
    device_mgr.stop_bus_worker()
    print("[YDNU02 Web Console] Shutting down...")

app = FastAPI(title="YDNU-02 Web Console", lifespan=lifespan)
app.state.mopeka_scanner = mopeka_scanner
app.state.ble_registry = ble_registry
app.state.gobius_poller = gobius_poller

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes (imported after device_mgr is created) ---

from routes import device, service, maintenance, firmware, websockets, gobius, mopeka, ble, n2k, n2k_config

app.include_router(device.router, prefix="/api")
app.include_router(service.router, prefix="/api")
app.include_router(maintenance.router, prefix="/api")
app.include_router(firmware.router, prefix="/api")
app.include_router(gobius.router, prefix="/api")
app.include_router(mopeka.router, prefix="/api")
app.include_router(ble.router, prefix="/api")
app.include_router(n2k.router, prefix="/api")
app.include_router(n2k_config.router, prefix="/api")
app.include_router(websockets.router)

# --- Static files + index.html ---

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

@app.get("/")
async def index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "index.html not found", "path": index_path}, status_code=404)

if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# --- Entry point ---

if __name__ == "__main__":
    print(f"[YDNU02 Web Console] Starting on http://{_args.host}:{_args.port}")
    print(f"[YDNU02 Web Console] Device: {device_mgr.get_port()}")
    uvicorn.run(app, host=_args.host, port=_args.port, log_level="info")
