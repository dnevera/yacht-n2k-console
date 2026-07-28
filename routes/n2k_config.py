"""
routes/n2k_config.py — Dynamic Device Config REST API

Endpoints for NMEA 2000 dynamic configuration using Group Functions (PGN 126208).
Uses n2k_meta module for frame construction and metadata extraction.
"""
import asyncio
import time
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routes import get_device_mgr

try:
    import n2k_meta
    _HAS_META = True
except ImportError:
    _HAS_META = False

router = APIRouter()


class WriteConfigRequest(BaseModel):
    fields: Dict[str, Any]


@router.get("/n2k/devices")
async def list_devices():
    """List all discovered devices on the bus."""
    dev_mgr = get_device_mgr()
    if not dev_mgr:
        raise HTTPException(503, "Device manager not running")

    with dev_mgr._sensors_lock:
        devices = [dict(dev) for dev in dev_mgr._discovered_bus_devices.values()]

    return {"devices": devices}


@router.get("/n2k/devices/{src}/config/{pgn}")
async def read_device_config(src: int, pgn: int):
    """Read current config values from device via PGN 126208 Read Fields."""
    if not _HAS_META:
        raise HTTPException(501, "n2k_meta module not available")

    dev_mgr = get_device_mgr()
    if not dev_mgr:
        raise HTTPException(503, "Device manager not running")

    # Validate device exists
    with dev_mgr._sensors_lock:
        if src not in dev_mgr._discovered_bus_devices:
            raise HTTPException(404, f"Device SRC {src} not found on bus")

    # Build and send Read Fields Request
    frame = n2k_meta.build_read_fields_frame(target_src=src, target_pgn=pgn)
    dev_mgr.send_raw_command(frame)

    # Subscribe to monitor queue and wait for reply
    q = asyncio.Queue(maxsize=200)
    with dev_mgr._queues_lock:
        dev_mgr._monitor_queues.append(q)

    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                remaining = max(0.1, deadline - time.monotonic())
                frame_data = await asyncio.wait_for(q.get(), timeout=min(remaining, 1.0))

                # Look for PGN 126208 from target device
                if frame_data.get("pgn") == 126208 and frame_data.get("src") == src:
                    raw = frame_data.get("raw", "")
                    decoded = n2k_meta.decode_raw_line(raw)
                    if decoded and decoded.get("id") == "nmeaReadFieldsReplyGroupFunction":
                        return {
                            "status": "ok",
                            "pgn": pgn,
                            "src": src,
                            "fields": decoded.get("fields", {}),
                        }
            except asyncio.TimeoutError:
                continue
    finally:
        with dev_mgr._queues_lock:
            if q in dev_mgr._monitor_queues:
                dev_mgr._monitor_queues.remove(q)

    # Fallback: return last known values from bus traffic
    return {
        "status": "timeout",
        "message": "No Read Fields reply from device (3s timeout). "
                   "Device may not support PGN 126208 Read Fields.",
        "pgn": pgn,
        "src": src,
        "fields": {},
    }


@router.post("/n2k/devices/{src}/config/{pgn}")
async def write_device_config(src: int, pgn: int, request: WriteConfigRequest):
    """Write config values to device via PGN 126208 Command/Write Fields.

    1. Read current values (pre-write snapshot)
    2. Send Command Group Function with new values
    3. Wait for Acknowledge
    4. Re-read to verify
    5. Return diff
    """
    if not _HAS_META:
        raise HTTPException(501, "n2k_meta module not available")

    dev_mgr = get_device_mgr()
    if not dev_mgr:
        raise HTTPException(503, "Device manager not running")

    with dev_mgr._sensors_lock:
        if src not in dev_mgr._discovered_bus_devices:
            raise HTTPException(404, f"Device SRC {src} not found on bus")

    # 1. Read old values
    old_result = await read_device_config(src, pgn)
    old_fields = old_result.get("fields", {})

    # 2. Build field_pairs from request
    # Get metadata to know field indices
    metadata = n2k_meta.get_pgn_field_metadata(pgn)
    field_index_map = {}
    for i, meta in enumerate(metadata, start=1):
        field_index_map[meta['id']] = i

    field_pairs = []
    for field_id, value in request.fields.items():
        idx = field_index_map.get(field_id)
        if idx is None:
            raise HTTPException(400, f"Unknown field '{field_id}' for PGN {pgn}")
        # Convert value to bytes based on field type
        meta_entry = next((m for m in metadata if m['id'] == field_id), None)
        if meta_entry and not meta_entry.get('configurable', True):
            raise HTTPException(400, f"Field '{field_id}' is read-only")

        raw_val = int(value)
        # Determine byte length from value range
        if raw_val <= 0xFF:
            val_bytes = bytes([raw_val & 0xFF])
        elif raw_val <= 0xFFFF:
            val_bytes = (raw_val & 0xFFFF).to_bytes(2, 'little')
        else:
            val_bytes = (raw_val & 0xFFFFFFFF).to_bytes(4, 'little')

        field_pairs.append((idx, val_bytes))

    # Send Command Group Function (more universally supported than Write Fields)
    frame = n2k_meta.build_command_frame(
        target_src=src,
        target_pgn=pgn,
        field_pairs=field_pairs,
    )
    dev_mgr.send_raw_command(frame)

    # 3. Wait for Acknowledge
    q = asyncio.Queue(maxsize=200)
    with dev_mgr._queues_lock:
        dev_mgr._monitor_queues.append(q)

    ack_received = False
    ack_error = None
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                remaining = max(0.1, deadline - time.monotonic())
                frame_data = await asyncio.wait_for(q.get(), timeout=min(remaining, 1.0))

                if frame_data.get("pgn") == 126208 and frame_data.get("src") == src:
                    raw = frame_data.get("raw", "")
                    decoded = n2k_meta.decode_raw_line(raw)
                    if decoded:
                        msg_id = decoded.get("id", "")
                        if "acknowledge" in msg_id.lower() or "reply" in msg_id.lower():
                            ack_received = True
                            # Check for error codes in acknowledge
                            fields = decoded.get("fields", {})
                            if "errorCode" in fields:
                                err = fields["errorCode"]
                                if err.get("value") not in (None, 0, "Accept"):
                                    ack_error = str(err.get("value", "Unknown error"))
                            break
            except asyncio.TimeoutError:
                continue
    finally:
        with dev_mgr._queues_lock:
            if q in dev_mgr._monitor_queues:
                dev_mgr._monitor_queues.remove(q)

    if ack_error:
        return {
            "status": "error",
            "message": f"Device rejected the command: {ack_error}",
            "errors": [ack_error],
        }

    # 4. Re-read to verify (even if no ACK — command might still have worked)
    await asyncio.sleep(0.5)  # Give device time to apply
    new_result = await read_device_config(src, pgn)
    new_fields = new_result.get("fields", {})

    # 5. Generate diff
    diff = {}
    for field_id in request.fields.keys():
        old_val = old_fields.get(field_id, {}).get("value") if isinstance(old_fields.get(field_id), dict) else old_fields.get(field_id)
        new_val = new_fields.get(field_id, {}).get("value") if isinstance(new_fields.get(field_id), dict) else new_fields.get(field_id)
        diff[field_id] = {"old": old_val, "new": new_val}

    return {
        "status": "ok" if ack_received else "sent_no_ack",
        "message": "Command sent" + (" and acknowledged" if ack_received else " (no ACK received, verify diff)"),
        "diff": diff,
        "errors": [],
    }


@router.get("/n2k/pgn/{pgn}/metadata")
async def get_pgn_metadata(pgn: int):
    """Get field metadata for a PGN (types, options, units, configurable)."""
    if not _HAS_META:
        raise HTTPException(501, "n2k_meta module not available")

    metadata = n2k_meta.get_pgn_field_metadata(pgn)
    pgn_name = n2k_meta.get_pgn_name(pgn)

    return {
        "pgn": pgn,
        "name": pgn_name,
        "fields": metadata,
    }
