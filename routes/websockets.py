import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from routes import get_device_mgr

router = APIRouter()


@router.websocket("/ws/monitor")
async def ws_monitor(websocket: WebSocket):
    await websocket.accept()
    try:
        try:
            config = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
            duration = config.get("duration", 300)
        except (asyncio.TimeoutError, Exception):
            duration = 300

        await get_device_mgr().monitor_raw(websocket, duration=duration)
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/scan")
async def ws_scan(websocket: WebSocket):
    await websocket.accept()
    try:
        try:
            config = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
            duration = config.get("duration", 10)
        except (asyncio.TimeoutError, Exception):
            duration = 10

        await get_device_mgr().scan_bus(websocket, duration=duration)
    except WebSocketDisconnect:
        pass
