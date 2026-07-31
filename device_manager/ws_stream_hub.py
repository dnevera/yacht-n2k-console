"""WebSocket streaming for live CAN bus monitoring and device scanning.

Monitor: asyncio.Queue per subscriber, filled by BusWorker callbacks.
Scan: independent TCP connection to proxy DATA port, ISO Requests, decoded responses.
"""

import time
import asyncio
import threading
from typing import Dict, Any, List, Optional, Callable
try:
    from fastapi import WebSocket, WebSocketDisconnect
except ImportError:
    WebSocket = Any  # type: ignore
    WebSocketDisconnect = Exception  # type: ignore
from ydnu02 import N2KPGNDecoder
from device_manager.tcp_connection import _PROXY_HOST, _PROXY_DATA_PORT

try:
    from nmea2000 import NMEA2000Decoder
except ImportError:
    NMEA2000Decoder = None


class WSStreamHub:
    """Manages WebSocket frame broadcasting and bus scanning."""

    def __init__(self,
                 queues_lock: threading.Lock,
                 monitor_queues: List[asyncio.Queue],
                 get_discovered_devices: Callable[[], Dict[int, Dict[str, Any]]],
                 get_state: Callable[[], str],
                 proxy_host: str = _PROXY_HOST,
                 proxy_port: int = _PROXY_DATA_PORT):
        self._queues_lock = queues_lock
        self._monitor_queues = monitor_queues
        self._get_discovered_devices = get_discovered_devices
        self._get_state = get_state
        self._proxy_host = proxy_host
        self._proxy_port = proxy_port

    def broadcast_frame(self, parsed: Dict[str, Any], event_loop: Optional[asyncio.AbstractEventLoop]) -> None:
        """Push parsed frame to all active monitor queues (thread-safe)."""
        if not event_loop or not event_loop.is_running():
            return

        payload = {
            "type": "frame",
            "time": parsed.get("time"),
            "pgn": parsed.get("info", {}).get("pgn"),
            "src": parsed.get("info", {}).get("src"),
            "decoded": parsed.get("decoded"),
        }

        with self._queues_lock:
            for q in list(self._monitor_queues):
                try:
                    event_loop.call_soon_threadsafe(q.put_nowait, payload)
                except asyncio.QueueFull:
                    pass    # drop frame for slow client

    async def monitor_raw(self, websocket: WebSocket, duration: float = 300.0) -> None:
        """Stream live NMEA frames to a WebSocket client for duration seconds."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._queues_lock:
            self._monitor_queues.append(q)

        state = self._get_state()
        if state == "STOPPED":
            await websocket.send_json({"type": "status", "message": "I/O is paused — resume from Dashboard to view live data"})
        elif state == "NO_DEVICE":
            await websocket.send_json({"type": "status", "message": "No serial gateway device connected"})
        else:
            await websocket.send_json({"type": "status", "message": "RAW monitoring started"})

        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=1.0)
                    await websocket.send_json(frame)
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            with self._queues_lock:
                if q in self._monitor_queues:
                    self._monitor_queues.remove(q)

    @staticmethod
    def _build_device_msg(dev: Dict[str, Any]) -> Dict[str, Any]:
        """Build clean device summary for scan_bus response."""
        return {
            "src":               dev.get("src", 0),
            "manufacturer":      dev.get("manufacturer", "") or "NMEA 2000 Device",
            "model":             dev.get("model", ""),
            "serial":            dev.get("serial", ""),
            "firmware":          dev.get("firmware", ""),
            "unique_id":         dev.get("unique_id", 0),
            "function_name":     dev.get("function_name", ""),
            "device_class_name": dev.get("device_class_name", ""),
            "mfg_code":          dev.get("mfg_code", 0),
            "product_code":      dev.get("product_code", 0),
        }

    async def scan_bus(self, websocket: WebSocket, duration: float = 10.0) -> None:
        """Scan N2K bus: ISO Requests → stream discovered devices."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._proxy_host, self._proxy_port),
                timeout=5.0
            )
        except Exception as e:
            await websocket.send_json({"type": "error", "message": f"Cannot connect to proxy: {e}"})
            return

        await websocket.send_json({"type": "status", "message": f"Scanning for {duration}s..."})

        try:
            writer.write(b"18EAFFFE 00 EE 00\r\n")
            writer.write(b"18EAFFFE 14 F0 01\r\n")
            await writer.drain()

            decoder = NMEA2000Decoder() if NMEA2000Decoder else None
            devices: Dict[int, Dict[str, Any]] = {}
            frame_count = 0
            t0 = asyncio.get_event_loop().time()

            while asyncio.get_event_loop().time() - t0 < duration:
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

                if not raw:
                    break

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                parsed = N2KPGNDecoder.parse_raw_line(line)
                if not parsed:
                    continue

                info_dict = parsed.get("info", {})
                pgn = info_dict.get("pgn")
                src = info_dict.get("src")
                frame_count += 1

                await websocket.send_json({
                    "type":    "frame",
                    "time":    parsed.get("time"),
                    "pgn":     pgn,
                    "src":     src,
                    "decoded": parsed.get("decoded"),
                })

                if src is None:
                    continue

                if src not in devices:
                    devices[src] = {"src": src}

                if pgn == 60928:
                    dev_info = N2KPGNDecoder.parse_device_info(parsed)
                    if dev_info:
                        devices[src].update(dev_info)
                        await websocket.send_json({
                            "type": "device",
                            **self._build_device_msg(devices[src]),
                        })

                if decoder and line:
                    lib_msg = decoder.decode(line)
                    if lib_msg is not None and lib_msg.PGN == 126996:
                        fields = {f.id: f for f in lib_msg.fields}
                        dev = devices.setdefault(lib_msg.source, {"src": lib_msg.source})
                        for field_id, attr in (
                            ("modelId",            "model"),
                            ("softwareVersionCode", "firmware"),
                            ("modelSerialCode",     "serial"),
                            ("modelVersion",        "model_version"),
                        ):
                            fld = fields.get(field_id)
                            if fld and fld.value:
                                dev[attr] = str(fld.value).strip()
                        await websocket.send_json({
                            "type": "device",
                            **self._build_device_msg(dev),
                        })

            discovered = self._get_discovered_devices()
            for src in list(devices):
                known = discovered.get(src, {})
                for k in ("manufacturer", "model", "serial", "firmware",
                          "function_name", "device_class_name",
                          "unique_id", "mfg_code", "product_code", "model_version"):
                    if known.get(k) and not devices[src].get(k):
                        devices[src][k] = known[k]
            for src, known in discovered.items():
                if src not in devices:
                    devices[src] = dict(known)

            for src, info in sorted(devices.items()):
                await websocket.send_json({
                    "type": "device",
                    **self._build_device_msg(info),
                })

            await websocket.send_json({
                "type":         "done",
                "device_count": len(devices),
                "frame_count":  frame_count,
            })

        except WebSocketDisconnect:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
