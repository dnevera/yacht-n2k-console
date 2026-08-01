"""WebSocket stream hub — monitor_raw and scan_bus handlers."""

import asyncio
import threading
from typing import Callable, Dict, Any, List, Optional

try:
    from fastapi import WebSocket, WebSocketDisconnect
except ImportError:
    WebSocket = Any          # type: ignore
    WebSocketDisconnect = Exception  # type: ignore

from ydnu02 import N2KPGNDecoder

try:
    from ydnu02 import NMEA2000Decoder
except ImportError:
    NMEA2000Decoder = None   # type: ignore


class WSStreamHub:
    """Handles WebSocket endpoints: raw frame monitor and CAN bus scanner."""

    def __init__(self,
                 queues_lock: threading.Lock,
                 monitor_queues: List[asyncio.Queue],
                 get_discovered_devices: Callable[[], Dict[int, Dict[str, Any]]],
                 get_state: Callable[[], str],
                 proxy_host: str = "127.0.0.1",
                 proxy_port: int = 4001):
        self._queues_lock        = queues_lock
        self._monitor_queues     = monitor_queues
        self._get_discovered_devices = get_discovered_devices
        self._get_state          = get_state
        self._proxy_host         = proxy_host
        self._proxy_port         = proxy_port

    def broadcast_frame(self,
                        parsed: Dict[str, Any],
                        event_loop: Optional[asyncio.AbstractEventLoop]) -> None:
        """Push a parsed frame to all subscribed monitor queues (thread-safe)."""
        if not event_loop:
            return
        with self._queues_lock:
            queues = list(self._monitor_queues)
        for q in queues:
            try:
                event_loop.call_soon_threadsafe(q.put_nowait, parsed)
            except asyncio.QueueFull:
                pass

    async def monitor_raw(self, websocket: WebSocket, duration: float = 300.0) -> None:
        """Stream raw NMEA frames to WebSocket client for `duration` seconds."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._queues_lock:
            self._monitor_queues.append(q)
        try:
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - t0 < duration:
                try:
                    parsed = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                info    = parsed.get("info", {})
                await websocket.send_json({
                    "type":    "frame",
                    "time":    parsed.get("time"),
                    "pgn":     info.get("pgn"),
                    "src":     info.get("src"),
                    "decoded": parsed.get("decoded"),
                    "raw":     parsed.get("raw"),
                })
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
            "claimed":           dev.get("claimed", False),
            "manufacturer":      dev.get("manufacturer", ""),
            "model":             dev.get("model", ""),
            "model_version":     dev.get("model_version", ""),
            "serial":            dev.get("serial", ""),
            "firmware":          dev.get("firmware", ""),
            "unique_id":         dev.get("unique_id", 0),
            "function_name":     dev.get("function_name", ""),
            "device_class_name": dev.get("device_class_name", ""),
            "mfg_code":          dev.get("mfg_code", 0),
            "product_code":      dev.get("product_code", 0),
            "active_pgns":       dev.get("active_pgns", []),
        }

    async def scan_bus(self, websocket: WebSocket, duration: float = 10.0) -> None:
        """Scan N2K bus: ISO Requests → stream discovered devices.

        SensorRegistry (via BusWorker) is the single source of truth for device state.
        This method handles: ISO request write, frame streaming to WS, and device push.
        No local device dict — all state comes from _get_discovered_devices().
        """
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
            # Send ISO Address Claim + Product Info requests to trigger device announcements
            writer.write(b"18EAFFFE 00 EE 00\r\n")
            writer.write(b"18EAFFFE 14 F0 01\r\n")
            await writer.drain()

            frame_count = 0
            # fingerprint per src: detected change → push device update to WS
            seen: Dict[int, str] = {}
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

                if src is None or src >= 254:
                    continue

                # PGN 60928 = ISO Address Claim. BusWorker (thread) processes the same
                # broadcast frame and sets claimed=True in SensorRegistry, but it runs
                # in a separate thread and may lag behind this asyncio coroutine.
                # Yield briefly so the thread has time to process before we read state.
                if pgn == 60928:
                    await asyncio.sleep(0.05)

                # SensorRegistry (BusWorker) already processed this frame — no duplicate parsing.
                # Check if device state changed and push update if needed.
                current = self._get_discovered_devices()
                dev = current.get(src)
                if dev is None:
                    continue

                fingerprint = f"{dev.get('claimed')}:{dev.get('model')}:{dev.get('manufacturer')}"
                if seen.get(src) != fingerprint:
                    seen[src] = fingerprint
                    await websocket.send_json({
                        "type": "device",
                        **self._build_device_msg(dev),
                    })

            # Final snapshot — push complete authoritative state from SensorRegistry
            final = self._get_discovered_devices()
            for src, dev in sorted(final.items()):
                await websocket.send_json({
                    "type": "device",
                    **self._build_device_msg(dev),
                })

            await websocket.send_json({
                "type":         "done",
                "device_count": len(final),
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
