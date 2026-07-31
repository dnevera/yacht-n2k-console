"""Background NMEA reader worker thread.

Connects to proxy DATA port (:4001) and continuously reads NMEA frames,
dispatching parsed packets to SensorRegistry and WebSocketStreamHub callbacks.
"""

import time
import threading
from typing import Dict, Any, Optional, Callable
from ydnu02 import N2KPGNDecoder
from device_manager.tcp_connection import TCPProxyConnection, _PROXY_HOST, _PROXY_DATA_PORT


class BusWorker:
    """Continuous NMEA reader thread for TCP proxy data stream."""

    def __init__(self,
                 pause_event: threading.Event,
                 on_frame: Callable[[Dict[str, Any]], None],
                 set_state: Callable[[str], None],
                 proxy_host: str = _PROXY_HOST,
                 proxy_port: int = _PROXY_DATA_PORT):
        self._pause_event = pause_event
        self._on_frame = on_frame
        self._set_state = set_state
        self._proxy_host = proxy_host
        self._proxy_port = proxy_port

        self._tcp: Optional[TCPProxyConnection] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._worker_running

    def start(self) -> None:
        """Start the bus worker thread."""
        if self._worker_running:
            return
        self._worker_running = True

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        print("[Gateway] Bus Worker started (TCP proxy mode)")

    def stop(self) -> None:
        """Stop the bus worker thread."""
        self._worker_running = False
        if self._tcp:
            self._tcp.close()
        if self._worker_thread:
            self._worker_thread.join(timeout=3)
            self._worker_thread = None
        print("[Gateway] Bus Worker stopped")

    def _worker_loop(self) -> None:
        """Outer loop: reconnect and inner read loop."""
        retry_delay = 1.0
        while self._worker_running:
            if self._pause_event.is_set():
                time.sleep(0.1)
                continue

            if not (self._tcp and self._tcp.is_connected):
                self._set_state("NO_DEVICE")
                tcp = TCPProxyConnection(host=self._proxy_host, port=self._proxy_port)
                try:
                    tcp.connect()
                    self._tcp = tcp
                    retry_delay = 1.0
                    self._set_state("LISTENING")
                    print(f"[Gateway] Connected to proxy :{self._proxy_port}")
                except (ConnectionRefusedError, OSError) as e:
                    print(f"[Gateway] Proxy not available ({e}) — retrying in {retry_delay:.0f}s")
                    time.sleep(min(retry_delay, 30.0))
                    retry_delay = min(retry_delay * 2, 30.0)
                    continue

            try:
                while self._worker_running and not self._pause_event.is_set():
                    line = self._tcp.readline()
                    if not line:
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(line)
                    if parsed:
                        self._on_frame(parsed)
            except (ConnectionResetError, OSError) as e:
                print(f"[Gateway] Proxy connection lost: {e} — reconnecting")
                if self._tcp:
                    self._tcp.close()
                self._tcp = None
                self._set_state("IDLE")
                time.sleep(1.0)

            while self._worker_running and self._pause_event.is_set():
                time.sleep(0.1)
