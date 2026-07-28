import os
import asyncio
import zipfile
import io
import urllib.request
from fastapi import APIRouter, HTTPException, UploadFile, File
from routes import get_device_mgr

router = APIRouter()

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@router.get("/firmware/latest")
async def api_firmware_latest():
    return await asyncio.to_thread(get_device_mgr().check_latest_firmware)


@router.get("/firmware/progress")
async def api_firmware_progress():
    return get_device_mgr()._fw_progress


@router.post("/firmware/download")
async def api_firmware_download():
    """Download firmware ZIP from yachtd.com, extract .BIN, save to firmware/ dir."""
    def _do():
        info = get_device_mgr().check_latest_firmware()
        if info.get("status") != "ok" or not info.get("download_url"):
            raise HTTPException(400, "Cannot determine download URL")

        url = info["download_url"]
        version = info.get("latest_version", "unknown")

        # Download ZIP
        req = urllib.request.Request(url, headers={"User-Agent": "YDNU02-Console/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            zip_data = resp.read()

        # Extract .BIN from ZIP
        fw_dir = os.path.join(base_dir, "firmware")
        os.makedirs(fw_dir, exist_ok=True)

        bin_files = []
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if name.upper().endswith(".BIN"):
                    content = zf.read(name)
                    # Rename to include version: UUPDATE_1.75.BIN
                    dest_name = f"{os.path.splitext(name)[0]}_{version}.BIN"
                    dest = os.path.join(fw_dir, dest_name)
                    with open(dest, "wb") as f:
                        f.write(content)
                    bin_files.append({"filename": dest_name, "size": len(content)})

        if not bin_files:
            raise HTTPException(400, "No .BIN files found in ZIP")

        return {
            "status": "ok",
            "version": version,
            "files": bin_files,
            "message": f"Downloaded {version}, ready to flash",
        }

    return await asyncio.to_thread(_do)


@router.post("/firmware/upload")
async def api_firmware_upload(file: UploadFile = File(...)):
    if not file.filename.upper().endswith(".BIN"):
        raise HTTPException(400, "Only .BIN files accepted")
    fw_dir = os.path.join(base_dir, "firmware")
    os.makedirs(fw_dir, exist_ok=True)
    dest = os.path.join(fw_dir, file.filename)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)
    return {"status": "ok", "filename": file.filename, "path": dest, "size": len(content)}


@router.post("/firmware/flash/{filename}")
async def api_firmware_flash(filename: str):
    fw_dir = os.path.join(base_dir, "firmware")
    bin_path = os.path.join(fw_dir, filename)
    if not os.path.isfile(bin_path) or not filename.upper().endswith(".BIN"):
        raise HTTPException(404, f"Firmware file not found: {filename}")
    return await asyncio.to_thread(get_device_mgr().flash_firmware, bin_path)


@router.get("/firmware/files")
async def api_firmware_files():
    fw_dir = os.path.join(base_dir, "firmware")
    if not os.path.isdir(fw_dir):
        return {"files": []}
    files = [f for f in os.listdir(fw_dir) if f.upper().endswith(".BIN")]
    return {"files": sorted(files, reverse=True)}
