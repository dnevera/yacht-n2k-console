import os
import glob
import asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from models import ResetHardwareRequest
from routes import get_device_mgr

router = APIRouter()

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@router.post("/backup")
async def api_backup(force: bool = False):
    return await asyncio.to_thread(get_device_mgr().create_backup, force)


@router.get("/backups")
async def api_backups():
    files = sorted(glob.glob(os.path.join(base_dir, "ydnu02_backup_*.json")), reverse=True)
    backups = []
    for f in files:
        stat = os.stat(f)
        backups.append({
            "filename": os.path.basename(f),
            "size": stat.st_size,
            "timestamp": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return {"backups": backups}


@router.get("/backup/download/{filename}")
async def api_backup_download(filename: str):
    filepath = os.path.join(base_dir, filename)
    if not os.path.isfile(filepath) or not filename.startswith("ydnu02_backup_"):
        raise HTTPException(404, "Backup not found")
    return FileResponse(filepath, filename=filename, media_type="application/json")


@router.post("/reset/settings")
async def api_reset_settings():
    return await asyncio.to_thread(get_device_mgr().reset_settings)


@router.post("/reset/filters")
async def api_reset_filters():
    return await asyncio.to_thread(get_device_mgr().reset_filters)


@router.post("/reset/mcu")
async def api_reset_mcu():
    return await asyncio.to_thread(get_device_mgr().reset_mcu)


@router.post("/reset/hardware")
async def api_reset_hardware(req: ResetHardwareRequest):
    if req.confirm != "RESET":
        raise HTTPException(400, "Confirmation required: send {confirm: 'RESET'}")
    return await asyncio.to_thread(get_device_mgr().reset_hardware)
