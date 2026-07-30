import os
import sys
import time
import socket
import asyncio
import threading
import re
import urllib.request
from typing import Optional, Dict, Any, List

from fastapi import WebSocket, WebSocketDisconnect

# Add current directory to path for ydnu02 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ydnu02 import YDNU02Controller, N2KPGNDecoder

from sensors import GobiusCSensor

# ── Proxy connection configuration ────────────────────────────────────────────
# These env vars MUST match what ydnu02_tcp_gateway.py uses (shared config convention).
# Default values work for local deployment (proxy + web on the same host).

_PROXY_HOST      = os.getenv("NMEA_PROXY_HOST", "127.0.0.1")
_PROXY_DATA_PORT = int(os.getenv("NMEA_PROXY_PORT", "4001"))  # read-only NMEA broadcast
_PROXY_CTRL_PORT = int(os.getenv("NMEA_CTRL_PORT",  "4002"))  # exclusive serial passthrough


# ══════════════════════════════════════════════════════════════════════════════
# TCPProxyConnection — data port client (:4001)
# ══════════════════════════════════════════════════════════════════════════════

class TCPProxyConnection:
    """
    Thin TCP wrapper for the proxy's DATA port (:4001).

    The proxy broadcasts NMEA 2000 frames (one per line, \\n-terminated) to ALL
    connected clients simultaneously — ydnu02-web and HA integration read from
    the same stream without interfering with each other.

    This class replaces the old direct serial.Serial access pattern.
    The proxy exclusively owns /dev/ttyACM0; no one else should open it.

    Lifecycle:
        connect() → readline() × N → close()

    readline() behaviour:
        - Returns decoded UTF-8 line on success
        - Returns ""  on socket timeout (bus can be slow — ~1 frame per 2.5s)
        - Raises ConnectionResetError when the proxy closed the connection
          (e.g. proxy restarted — caller must reconnect)
    """

    def __init__(self, host: str = _PROXY_HOST, port: int = _PROXY_DATA_PORT):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._buf  = b""    # internal line buffer — raw recv(), NO makefile (see readline docstring)

    def connect(self) -> None:
        """Open TCP connection; raises ConnectionRefusedError / OSError on failure."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)            # connect timeout
        sock.connect((self._host, self._port))
        sock.settimeout(5.0)            # recv timeout — MUST be > bus frame interval (~2.5s)
        self._sock = sock
        self._buf  = b""                # reset line buffer on new connection
        # NOTE: no makefile() — raw recv() used in readline() to avoid broken-file-after-timeout bug

    def readline(self) -> str:
        """
        Read one \\n-terminated NMEA line from the proxy broadcast stream.

        Uses raw socket.recv() instead of makefile().readline() to avoid
        Python's 'cannot read from timed out object' bug: after socket.timeout
        is raised inside makefile.readline(), the file object enters a broken
        state where ALL subsequent calls raise OSError — not socket.timeout.
        This caused the bus worker to reconnect every ~5s on a quiet N2K bus
        (timeout → makefile broken → OSError on next call → reconnect loop).

        Raw recv() with an internal byte buffer is immune: socket.timeout from
        recv() is caught and returns "" without corrupting any internal state.
        The buffer preserves partial data across calls.

        Returns:
            Decoded, stripped line string on success.
            Empty string ""  on socket.timeout (normal — bus is slow).

        Raises:
            OSError:              Not connected (call connect() first).
            ConnectionResetError: Proxy closed the connection (proxy restarted).
                                  Caller (_bus_worker) will close and reconnect.
        """
        if not self._sock:
            raise OSError("Not connected")
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    # recv() returns b"" on EOF — proxy closed connection.
                    raise ConnectionResetError("Proxy connection closed")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            return line.decode("utf-8", errors="ignore").strip()
        except socket.timeout:
            # Normal — NMEA bus can send only ~0.4 frames/sec on quiet bus.
            # bus_worker inner loop just continues on empty string.
            # Buffer is preserved — partial data is kept for the next call.
            return ""

    def write(self, data: bytes) -> None:
        """
        Send raw bytes to the proxy's data port.
        The proxy forwards writes to serial (used for ISO Request frames in scan_bus).
        Note: For service/firmware commands use ProxyControlClient instead.
        """
        if self._sock:
            try:
                self._sock.sendall(data)
            except OSError as e:
                print(f"[TCPProxy] write error: {e}")

    def close(self) -> None:
        """Close the connection. Safe to call multiple times."""
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._buf  = b""    # discard any buffered partial data

    @property
    def is_connected(self) -> bool:
        """True if socket is open (does NOT detect half-open connections)."""
        return self._sock is not None


# ══════════════════════════════════════════════════════════════════════════════
# ProxyControlClient — control port client (:4002)
# ══════════════════════════════════════════════════════════════════════════════

class ProxyControlClient:
    """
    Client for the proxy's CONTROL port (:4002).

    The control port provides EXCLUSIVE serial passthrough for operations that
    need direct access to the YDNU-02 serial interface:
      - Service mode (YDNU terminal: HELP, FILTER, SET, DIAG, ...)
      - Firmware OTA flash (chunked binary write)
      - OS shell commands (YDNU MODE RAW, YDNU SILENT ON, ...)

    Protocol (line-oriented UTF-8):
        Client → Proxy:  command line (e.g. "SERVICE_START\\n")
        Proxy  → Client: response line (e.g. "READY\\n")
        After READY:     bidirectional raw serial passthrough until *_END command

    Control commands:
        SERVICE_START  / SERVICE_END   — pause broadcast, enter service passthrough
        FIRMWARE_START / FIRMWARE_END  — same, used for firmware flash operations

    While one client holds the control connection, the proxy:
      - Pauses broadcast to all :4001 DATA clients (sets service_mode flag)
      - Routes all serial I/O exclusively to this control client

    IMPORTANT: Only ONE control client at a time. The proxy serialises access
    via a mutex. Concurrent control connections will block until the previous
    one exits.

    Passthrough adapter:
        enter_service() / enter_firmware() opens the connection.
        This object is then attached to YDNU02Controller as _passthrough,
        so all existing service methods (enter_service_mode, _send_terminal_command,
        _read_response) transparently route through TCP instead of direct serial.
        See _service_operation() in DeviceManager for the wiring.

    Lifecycle:
        enter_service() → passthrough_write/read_for × N → exit_service()
        enter_firmware() → passthrough_write × N → exit_firmware()
    """

    def __init__(self, host: str = _PROXY_HOST, port: int = _PROXY_CTRL_PORT):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._buf = b""     # unified byte buffer for protocol AND passthrough phases.
        # NOTE: no makefile() — using raw recv() everywhere eliminates the
        # makefile vs recv() buffer split bug: bytes buffered by makefile after
        # reading "READY\n" (early NMEA frames or welcome text) would be
        # invisible to passthrough_readline() which used socket.recv() directly.
        # With a shared self._buf, bytes left after READY are naturally available
        # for the passthrough phase without any special draining logic.

    def _connect(self) -> None:
        """Open TCP connection to control port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((self._host, self._port))
        sock.settimeout(3.0)    # response timeout for control commands
        self._sock = sock
        self._buf = b""     # reset on new connection
        # NOTE: no makefile() — see __init__ comment

    def _recv_line(self, timeout: float = 3.0) -> str:
        """
        Read one \\n-terminated line from self._buf, refilling via recv() as needed.

        Shared by both phases:
          • Protocol phase: _send_cmd reads "READY\\n" or "OK\\n" responses.
          • Passthrough phase: passthrough_readline reads serial terminal output.

        Buffer-safe: bytes after the extracted line stay in self._buf,
        so the next call finds them immediately without blocking on recv().

        Returns:
            Decoded, stripped line string on success.
            Empty string on socket.timeout (no \\n received within timeout).
            Empty string on EOF (proxy closed connection).
        """
        if not self._sock:
            return ""
        self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return ""   # EOF — proxy closed connection
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            return line.decode("utf-8", errors="ignore").strip()
        except socket.timeout:
            return ""

    def _send_cmd(self, cmd: str) -> str:
        """Send a control command line and return the response line."""
        if not self._sock:
            raise OSError("Not connected to control port")
        self._sock.sendall((cmd + "\n").encode())
        return self._recv_line(timeout=3.0)

    # ── Service mode ──────────────────────────────────────────────────────────

    def enter_service(self) -> None:
        """
        Connect to control port and send SERVICE_START.
        The proxy pauses DATA broadcast and enters serial passthrough mode.
        Raises RuntimeError if proxy does not respond with READY.
        """
        self._connect()
        resp = self._send_cmd("SERVICE_START")
        if "READY" not in resp:
            raise RuntimeError(f"Proxy SERVICE_START failed: {resp}")

    def exit_service(self) -> None:
        """
        Send SERVICE_END (resumes DATA broadcast) and close control connection.
        Safe to call even if connection is already closed (e.g. proxy restarted).
        """
        try:
            self._send_cmd("SERVICE_END")
        except OSError:
            pass
        self._close()

    # ── Firmware mode ─────────────────────────────────────────────────────────

    def enter_firmware(self) -> None:
        """Same as enter_service but uses FIRMWARE_START command."""
        self._connect()
        resp = self._send_cmd("FIRMWARE_START")
        if "READY" not in resp:
            raise RuntimeError(f"Proxy FIRMWARE_START failed: {resp}")

    def exit_firmware(self) -> None:
        """Send FIRMWARE_END and close. Resumes DATA broadcast."""
        try:
            self._send_cmd("FIRMWARE_END")
        except OSError:
            pass
        self._close()

    # ── Passthrough I/O ───────────────────────────────────────────────────────

    def passthrough_write(self, data: bytes) -> None:
        """
        Write raw bytes to serial via proxy passthrough.
        Used by YDNU02Controller._write() when _passthrough is set.
        """
        if self._sock:
            try:
                self._sock.sendall(data)
            except OSError as e:
                print(f"[ProxyCtrl] write error: {e}")

    def passthrough_readline(self, timeout: float = 3.0) -> str:
        """
        Read one \\n-terminated line from serial via proxy passthrough.

        Uses the shared self._buf (same as _send_cmd) to avoid data loss after
        the SERVICE_START/READY handshake: bytes buffered after reading READY
        are immediately available here without blocking on recv().

        Returns "" on timeout (normal — service terminal may be slow to respond).
        """
        return self._recv_line(timeout=timeout)

    def passthrough_read_for(self, duration: float) -> str:
        """
        Read all response lines for `duration` seconds; return joined as string.
        Used by _read_response() in YDNU02Controller passthrough mode to
        collect multi-line responses (HELP output, DIAG output, welcome screen).
        Short per-chunk timeout ensures we keep reading until duration expires.
        """
        chunks = []
        t0 = time.time()
        while time.time() - t0 < duration:
            remaining = duration - (time.time() - t0)
            line = self.passthrough_readline(timeout=min(0.5, remaining))
            if line:
                chunks.append(line)
        return "\n".join(chunks)

    def _close(self) -> None:
        """Close connection. Safe to call multiple times."""
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._buf = b""     # discard any buffered data


# ══════════════════════════════════════════════════════════════════════════════
# DeviceManager
# ══════════════════════════════════════════════════════════════════════════════

class DeviceManager:
    """
    Central manager for NMEA 2000 data and YDNU-02 device operations.

    Architecture overview:
    ┌─────────────────────────────────────────────────────────────┐
    │                      DeviceManager                          │
    │                                                             │
    │  _bus_worker (thread)                                       │
    │    └─ TCPProxyConnection → :4001 (DATA)                    │
    │         reads NMEA frames → _update_sensor_state           │
    │                           → _broadcast_frame (WS monitor)  │
    │                                                             │
    │  _service_operation / _locked_operation / _raw_locked_op   │
    │    └─ ProxyControlClient → :4002 (CTRL)                    │
    │         pauses bus worker via _pause_event                 │
    │         wires YDNU02Controller._passthrough = pcc          │
    │         ← existing service methods work transparently      │
    └─────────────────────────────────────────────────────────────┘

    Stop/Resume mechanism (_pause_event):
        _pause_event is a threading.Event that acts as a "bus suspended" flag.
        When SET:
          - _bus_worker pauses reading (inner while loop exits, outer loop sleeps)
          - All three operation patterns set it before entering the proxy control port
        When CLEARED:
          - _bus_worker resumes and reconnects to :4001
          - All operation patterns clear it in their `finally` block (guaranteed)

        This ensures the control port has exclusive serial access while service/
        firmware operations run, and the bus worker does not interfere.

    Three operation patterns:
        1. _service_operation(func)   — full service mode: enter → func → exit service
        2. _locked_operation(func)    — service mode but exits to RAW after (OS commands)
        3. _raw_locked_operation(func)— service mode, no auto-exit (MCU reset, firmware)
    """

    def __init__(self, port: Optional[str] = None, debug: bool = False):
        # `port` is kept for backward compatibility — YDNU02Controller still needs it
        # for legacy direct-serial paths (service_backup reads binary data via serial).
        # In normal operation the bus worker uses TCP, not this port.
        self.port = port
        self.debug = debug

        # Mutex for YDNU02Controller access (service/firmware operations are blocking)
        self._lock = threading.Lock()

        # Serializes ALL service/firmware operations (enter_service through exit_service).
        # Prevents concurrent API calls from racing into pcc.enter_service() at the same time,
        # which would cause proxy to refuse with "ERROR: another control session is active".
        # Acquired BEFORE pcc.enter_service() so the proxy sees only one client at a time.
        self._service_lock = threading.Lock()

        # YDNU02Controller is created lazily (only when service/firmware op runs).
        # In passthrough mode it routes I/O through ProxyControlClient, not serial.
        self._ctrl: Optional[YDNU02Controller] = None

        # Human-readable state: "IDLE" | "LISTENING" | "SERVICE" | "NO_DEVICE"
        self._state = "IDLE"

        # Cached result of get_info() (TTL: 60s). Invalidated on firmware flash.
        self._info_cache: Optional[Dict[str, Any]] = None
        self._info_cache_time: float = 0
        self._cache_ttl: float = 60.0

        # Sensor registry: N2K source address → GobiusCSensor
        # Populated by _update_sensor_state() as PGN 127505 frames arrive.
        self.sensors: Dict[int, GobiusCSensor] = {}
        self._sensors_lock = threading.Lock()

        # All N2K devices seen on bus (populated from PGN 60928 / 126996)
        self._discovered_bus_devices: Dict[int, Dict[str, Any]] = {}

        # Bus worker thread state
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running = False

        # ── Stop/Resume flag ──────────────────────────────────────────────────
        # SET   → bus worker pauses (service/firmware op owns the serial)
        # CLEAR → bus worker runs  (normal NMEA reading)
        # Always cleared in `finally` of all operation patterns.
        self._pause_event = threading.Event()

        # WebSocket monitor subscribers (async queues, one per connected client)
        self._ws_clients: List[WebSocket] = []
        self._monitor_queues: List[asyncio.Queue] = []
        self._queues_lock = threading.Lock()

        # Reference to the running asyncio event loop.
        # Required for thread-safe queue.put_nowait() from _bus_worker thread.
        # Set by app.py via set_event_loop() during lifespan startup.
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Active TCP data connection (held by _bus_worker)
        self._tcp: Optional[TCPProxyConnection] = None

    def _get_ctrl(self) -> YDNU02Controller:
        """
        Lazy-init YDNU02Controller (serial controller for service/firmware ops).
        Created once and reused. In passthrough mode, _passthrough is set on it
        so it routes I/O through ProxyControlClient instead of opening serial directly.
        """
        if self._ctrl is None:
            self._ctrl = YDNU02Controller(port=self.port, debug=self.debug)
        return self._ctrl

    def get_port(self) -> str:
        """Returns human-readable connection string for status/info endpoints."""
        return self.port or _PROXY_HOST + ":" + str(_PROXY_DATA_PORT)

    # ══════════════════════════════════════════════════════════════════════════
    # Bus Worker — continuous NMEA reader thread
    # ══════════════════════════════════════════════════════════════════════════

    def start_bus_worker(self):
        """
        Start the bus worker thread (called once from app.py lifespan on startup).
        The worker connects to the proxy DATA port and continuously reads NMEA frames.
        """
        if self._worker_running:
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._bus_worker, daemon=True)
        self._worker_thread.start()
        print("[Gateway] Bus Worker started (TCP proxy mode)")

    def stop_bus_worker(self):
        """
        Stop the bus worker thread (called from app.py lifespan on shutdown).
        Closes the TCP connection to unblock any pending readline().
        """
        self._worker_running = False
        if self._tcp:
            self._tcp.close()   # unblocks readline() in the worker thread
        if self._worker_thread:
            self._worker_thread.join(timeout=3)
            self._worker_thread = None
        print("[Gateway] Bus Worker stopped")

    def _bus_worker(self):
        """
        Worker thread: connects to proxy DATA port, reads NMEA frames in a loop.

        Control flow:
        ┌─ outer loop (runs while _worker_running) ──────────────────────────┐
        │                                                                     │
        │  1. If _pause_event is SET:                                         │
        │       sleep 0.1s and continue  ← service/firmware op owns serial   │
        │                                                                     │
        │  2. If not connected:                                               │
        │       TCPProxyConnection.connect() with exponential backoff        │
        │       (1s → 2s → 4s → … → 30s max)                                │
        │                                                                     │
        │  3. Inner read loop:                                                │
        │     ┌─ while running and not paused ────────────────────────────┐  │
        │     │  readline() → ""       → continue (bus timeout, normal)   │  │
        │     │  readline() → "data"   → parse → update sensors + WS     │  │
        │     │  readline() raises     → break (proxy disconnected)       │  │
        │     └────────────────────────────────────────────────────────────┘  │
        │       on ConnectionResetError / OSError: close tcp, sleep 1s       │
        │                                                                     │
        │  4. If _pause_event SET after inner loop exits:                     │
        │       wait until cleared → go to step 1 (reconnect)               │
        └─────────────────────────────────────────────────────────────────────┘

        NOTE: The inner loop exits when _pause_event is set mid-read (e.g.
        service op started while we were waiting for readline). The outer loop
        then immediately re-checks the pause flag and sleeps until cleared.
        """
        _retry_delay = 1.0
        while self._worker_running:

            # ── Step 1: check Stop/Resume flag ────────────────────────────────
            if self._pause_event.is_set():
                time.sleep(0.1)
                continue

            # ── Step 2: (re)connect to proxy DATA port ────────────────────────
            if not (self._tcp and self._tcp.is_connected):
                self._state = "NO_DEVICE"
                tcp = TCPProxyConnection()
                try:
                    tcp.connect()
                    self._tcp = tcp
                    _retry_delay = 1.0          # reset backoff on successful connect
                    self._state = "LISTENING"
                    print(f"[Gateway] Connected to proxy :{_PROXY_DATA_PORT}")
                except (ConnectionRefusedError, OSError) as e:
                    print(f"[Gateway] Proxy not available ({e}) — retrying in {_retry_delay:.0f}s")
                    time.sleep(min(_retry_delay, 30.0))
                    _retry_delay = min(_retry_delay * 2, 30.0)  # exponential backoff, cap at 30s
                    continue

            # ── Step 3: inner read loop ───────────────────────────────────────
            try:
                while self._worker_running and not self._pause_event.is_set():
                    line = self._tcp.readline()
                    if not line:
                        # Socket timeout — bus is quiet (normal for slow N2K bus).
                        # Also gives us a chance to re-check _pause_event each 5s.
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(line)
                    if parsed:
                        self._update_sensor_state(parsed)   # update sensor registry
                        self._broadcast_frame(parsed)       # push to WS monitor queues
            except (ConnectionResetError, OSError) as e:
                # Proxy was restarted or connection dropped — reconnect next iteration
                print(f"[Gateway] Proxy connection lost: {e} — reconnecting")
                self._tcp.close()
                self._tcp = None
                self._state = "IDLE"
                time.sleep(1.0)

            # ── Step 4: wait for unpause if service op is still running ───────
            # (inner loop exited because _pause_event was set mid-loop)
            while self._worker_running and self._pause_event.is_set():
                time.sleep(0.1)
            # After unpause: outer loop iterates → reconnect to proxy data port

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """
        Store reference to the running asyncio event loop.
        Required for thread-safe queue operations from _bus_worker thread.
        Called by app.py during lifespan startup before start_bus_worker().
        """
        self._event_loop = loop

    # ══════════════════════════════════════════════════════════════════════════
    # Frame broadcast (bus worker → WebSocket monitor subscribers)
    # ══════════════════════════════════════════════════════════════════════════

    def _broadcast_frame(self, parsed: Dict[str, Any]):
        """
        Push a parsed NMEA frame to all active WebSocket monitor queues.

        Called from _bus_worker thread → uses call_soon_threadsafe() to safely
        schedule queue.put_nowait() on the asyncio event loop.

        Queues that have been garbage-collected or disconnected are removed.
        Queues are filled by monitor_raw() and scan_bus() subscribers.
        """
        with self._queues_lock:
            if not self._monitor_queues or not self._event_loop:
                return
            frame = {
                "type": "frame",
                "time": parsed["time"],
                "dir": parsed["dir"],
                "pgn": parsed["info"]["pgn"],
                "pgn_name": N2KPGNDecoder.pgn_name(parsed["info"]["pgn"]),
                "src": parsed["info"]["src"],
                "dst": parsed["info"]["dst"],
                "decoded": parsed["decoded"],
                "raw": parsed["raw"],
            }
            dead = []
            for q in self._monitor_queues:
                try:
                    self._event_loop.call_soon_threadsafe(q.put_nowait, frame)
                except Exception:
                    dead.append(q)  # queue is full or subscriber gone
            for q in dead:
                self._monitor_queues.remove(q)

    # ══════════════════════════════════════════════════════════════════════════
    # Sensor state update (PGN 127505 Fluid Level + device info)
    # ══════════════════════════════════════════════════════════════════════════

    def _update_sensor_state(self, parsed: Dict[str, Any]):
        """
        Process a decoded NMEA frame and update internal state:
          - Track all N2K source addresses seen on bus (_discovered_bus_devices)
          - Parse PGN 60928/126996 (Device Info) → update device metadata
          - Parse PGN 127505 (Fluid Level) → update GobiusCSensor in self.sensors

        Called from _bus_worker inner loop on every valid frame.
        Protected by _sensors_lock (also accessed by REST API handlers).
        """
        info = parsed.get("info", {})
        pgn = info.get("pgn")
        src = info.get("src")
        data = parsed.get("data", b"")

        with self._sensors_lock:
            # ── Track all CAN-bus devices by source address ───────────────────
            if src is not None:
                if src not in self._discovered_bus_devices:
                    self._discovered_bus_devices[src] = {
                        "src": src,
                        "manufacturer": "NMEA 2000 Device",
                        "model": f"Device (SRC {src})",
                        "serial": "--",
                        "firmware": "--",
                        "device_class": "--",
                        "function_name": "--",
                        "device_class_name": "--",
                        "active_pgns": [],
                    }
                if pgn and pgn not in self._discovered_bus_devices[src]["active_pgns"]:
                    self._discovered_bus_devices[src]["active_pgns"].append(pgn)

                # ── PGN 60928 (Address Claim) / 126996 (Product Info) ─────────
                # These PGNs carry manufacturer/model/serial/firmware info.
                # We use the library decoder for these — richer field mapping.
                if pgn in (60928, 126996):
                    dev_info = N2KPGNDecoder.parse_device_info(parsed)
                    if dev_info:
                        dev = self._discovered_bus_devices[src]
                        for key in ("manufacturer", "model", "serial", "firmware",
                                    "function_name", "device_class_name", "model_version",
                                    "unique_id"):
                            if key in dev_info:
                                dev[key] = dev_info[key]
                        if "device_class" in dev_info:
                            # Prefer human-readable name over numeric code
                            dev["device_class"] = dev_info.get("device_class_name",
                                                               str(dev_info["device_class"]))

            # ── PGN 127505: Fluid Level ───────────────────────────────────────
            # Gobius C sends this every ~2.5s with fill level, capacity, fluid type.
            # NOTE: Gobius C firmware bug — fluid_type byte is always 0x00 (Fuel)
            #       regardless of BLE configuration. Use registry override instead.
            if pgn == 127505 and len(data) >= 5:
                # Byte layout (per NMEA 2000 PGN 127505 spec):
                #   bits 0-3: fluid instance (tank ID, 0-15)
                #   bits 4-7: fluid type (0=Fuel, 1=FreshWater, 2=Waste, ...)
                #   bytes 1-2: level ‱ (0-25000, ×0.004 → percent)
                #   bytes 3-6: capacity in 0.1L units (0xFFFFFFFF = unavailable)
                instance  = data[0] & 0x0F
                type_code = (data[0] >> 4) & 0x0F
                raw_level = data[1] | (data[2] << 8)
                level_pct = round(raw_level * 0.004, 1) if raw_level <= 25000 else None

                capacity_l = None
                if len(data) >= 7:
                    raw_cap = int.from_bytes(data[3:7], 'little')
                    if raw_cap != 0xFFFFFFFF:   # 0xFFFFFFFF = field not available
                        capacity_l = round(raw_cap * 0.1, 1)

                # Auto-create sensor on first seen instance
                if instance not in self.sensors:
                    self.sensors[instance] = GobiusCSensor(instance=instance,
                                                           name=f"Tank {instance}")

                self.sensors[instance].update_from_nmea127505({
                    "instance":   instance,
                    "type_code":  type_code,
                    "level_pct":  level_pct,
                    "capacity_l": capacity_l,
                    "src":        src,
                })

    # ══════════════════════════════════════════════════════════════════════════
    # REST API helpers
    # ══════════════════════════════════════════════════════════════════════════

    def send_raw_command(self, cmd_str: str):
        """
        Send a raw NMEA command string to YDNU-02 via the proxy DATA connection.
        Used for ISO Request frames (e.g. scan_bus sends Address Claim requests).
        The proxy forwards DATA port writes directly to serial.
        Returns True if sent, False if not connected.
        """
        if self._tcp and self._tcp.is_connected:
            self._tcp.write(cmd_str.encode("utf-8") + b"\r\n")
            return True
        return False

    def get_sensors_state(self) -> Dict[str, Any]:
        """
        Non-blocking snapshot of all known sensors (thread-safe).
        Called by REST endpoint GET /api/sensors.
        """
        with self._sensors_lock:
            fluid_levels = [sensor.to_dict() for sensor in self.sensors.values()]
        return {
            "status": "ok",
            "fluid_levels": fluid_levels,
            "count": len(fluid_levels)
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Three operation patterns
    # ══════════════════════════════════════════════════════════════════════════
    #
    # All three patterns follow the same Stop/Resume sequence:
    #
    #   1. _pause_event.SET()           ← bus worker sees it, exits inner loop
    #   2. sleep(0.2)                   ← give worker time to finish current readline()
    #   3. ProxyControlClient.enter_*() ← proxy pauses broadcast, opens passthrough
    #   4. _lock.acquire()              ← exclusive access to YDNU02Controller
    #   5. ctrl._passthrough = pcc      ← wire passthrough adapter
    #   6. func(ctrl)                   ← service / OS / firmware operation
    #   7. ctrl._passthrough = None     ← unwire passthrough
    #   8. pcc.exit_*()                 ← proxy resumes broadcast (in finally)
    #   9. _pause_event.CLEAR()         ← bus worker resumes + reconnects (in finally)
    #
    # Pattern differences:
    #   _service_operation   → func uses service terminal (HELP, FILTER, etc.)
    #   _locked_operation    → func sends OS shell commands (MODE, SILENT)
    #   _raw_locked_operation → no auto-exit from service mode (MCU reset, firmware)
    # ══════════════════════════════════════════════════════════════════════════

    def _service_operation(self, func, exit_mode: str = "RAW"):
        """
        Full service mode operation pattern.

        Stops bus worker → enters service via control port → runs func(ctrl)
        with YDNU02Controller._passthrough wired to ProxyControlClient →
        exits service → resumes bus worker.

        The proxy enters passthrough mode: all serial I/O goes to this
        control client. YDNU02Controller methods (enter_service_mode,
        _send_terminal_command, _read_response) use _passthrough transparently.

        Used by: get_info, get_filters, get_settings, get_diag, send_service_cmd,
                 create_backup, reset_settings, reset_filters.

        Thread-safety: _service_lock ensures only ONE service operation runs at a
        time. Concurrent API calls (e.g. user spamming HELP) will queue up and
        execute sequentially — no racing into pcc.enter_service().
        """
        with self._service_lock:    # Serialize: only ONE service op at a time
            self._pause_event.set()     # Stop: signal bus worker to pause
            time.sleep(0.2)             # Wait: give worker time to exit readline()
            pcc = ProxyControlClient()
            try:
                pcc.enter_service()     # Proxy: pause broadcast, open passthrough
                with self._lock:
                    ctrl = self._get_ctrl()
                    try:
                        ctrl._passthrough = pcc     # Wire: ctrl uses TCP passthrough

                        # Enter YDNU-02 service terminal through passthrough.
                        # This sends "YDNU MODE SERVICE\r\n" to serial and reads welcome.
                        # Without this, _send_terminal_command writes to RAW-mode device
                        # which doesn't understand HELP/FILTER/etc commands.
                        ctrl.enter_service_mode()

                        result = func(ctrl)

                        # Exit service terminal, return device to RAW mode
                        ctrl.exit_service_mode(exit_mode)

                        ctrl._passthrough = None    # Unwire before returning
                        self._state = "IDLE"
                        return result
                    except Exception:
                        ctrl._passthrough = None
                        ctrl._close_terminal()      # Ensure terminal is cleanly closed
                        self._state = "IDLE"
                        raise
            finally:
                pcc.exit_service()          # Resume: proxy restores broadcast
                self._pause_event.clear()   # Resume: bus worker reconnects and reads

    def _locked_operation(self, func):
        """
        OS shell command pattern (no service terminal needed).

        Same Stop/Resume sequence as _service_operation but func sends
        OS-level YDNU commands (e.g. 'YDNU MODE RAW\\r\\n') rather than
        entering the interactive service terminal.

        The _passthrough adapter is still wired so YDNU02Controller.set_mode()
        and set_silent() route their serial writes through the proxy.

        Used by: set_mode, set_silent.
        """
        with self._service_lock:
            self._pause_event.set()
            time.sleep(0.2)
            pcc = ProxyControlClient()
            try:
                pcc.enter_service()
                with self._lock:
                    ctrl = self._get_ctrl()
                    ctrl._passthrough = pcc
                    try:
                        result = func(ctrl)
                        self._state = "IDLE"
                        return result
                    finally:
                        ctrl._passthrough = None    # Always unwire, even on exception
            finally:
                pcc.exit_service()
                self._pause_event.clear()

    def _raw_locked_operation(self, func):
        """
        Raw operation pattern — func manages its own exit from service mode.

        Used when the operation itself determines how (or whether) to exit
        service mode:
          - MCU reset: device reboots, no exit needed
          - Hardware reset: device reboots, no exit needed
          - Firmware flash: chunked write, then device reboots
          - Manual enter_service / exit_service: user controls the state

        The caller (func) is responsible for calling ctrl._close_terminal() if
        it opened the service terminal, since the device may reboot mid-operation.

        Used by: reset_mcu, reset_hardware, flash_firmware, enter_service,
                 exit_service (manual control endpoints).
        """
        with self._service_lock:
            self._pause_event.set()
            time.sleep(0.2)
            pcc = ProxyControlClient()
            try:
                pcc.enter_service()
                with self._lock:
                    ctrl = self._get_ctrl()
                    ctrl._passthrough = pcc
                    try:
                        return func(ctrl)
                    except Exception:
                        ctrl._passthrough = None
                        ctrl._close_terminal()
                        self._state = "IDLE"
                        raise
            finally:
                pcc.exit_service()          # Always resumes broadcast even after reboot
                self._pause_event.clear()   # Always resumes bus worker

    # ══════════════════════════════════════════════════════════════════════════
    # Service mode operations (use _service_operation pattern)
    # ══════════════════════════════════════════════════════════════════════════

    def get_info(self, force: bool = False) -> Dict[str, Any]:
        """
        Read device info from YDNU-02 service terminal (HELP command).
        Results are cached for 60s (cache_ttl). Pass force=True to bypass cache.
        """
        if not force and self._info_cache and (time.time() - self._info_cache_time) < self._cache_ttl:
            return self._info_cache
        def _do(ctrl):
            welcome = ctrl._send_terminal_command("HELP", wait=2.0)
            info = ctrl._parse_welcome_screen(welcome)
            info["port"] = ctrl.port
            info["state"] = "online"
            return info
        result = self._service_operation(_do)
        if result and result.get("firmware_version"):
            self._info_cache = result
            self._info_cache_time = time.time()
        return result

    def get_filters(self) -> Dict[str, Any]:
        """
        Read all 8 YDNU-02 filter tables via service terminal (PRINT <NAME> commands).
        Returns records count and filter type (BLACK/WHITE) for each table.
        """
        FILTER_NAMES = ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                        "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]
        def _do(ctrl):
            filters = {}
            for name in FILTER_NAMES:
                raw = ctrl._send_terminal_command(f"PRINT {name}", wait=1.5)
                records, ftype = 0, "BLACK"
                if "contains" in raw:
                    try: records = int(raw.split("contains")[1].strip().split()[0])
                    except (ValueError, IndexError): pass
                if "type is" in raw:
                    try: ftype = raw.split("type is")[1].strip().split()[0].upper()
                    except IndexError: pass
                filters[name] = {"records": records, "type": ftype, "raw": raw}
                time.sleep(0.15)    # small delay between PRINT commands
            return {"filters": filters}
        return self._service_operation(_do)

    def get_settings(self) -> Dict[str, str]:
        """Read current YDNU-02 settings via service terminal (HELP SET)."""
        return self._service_operation(
            lambda c: {"settings_raw": c._send_terminal_command("HELP SET", wait=2.0)})

    def get_diag(self, scope: str) -> Dict[str, str]:
        """Run DIAG command in service terminal. scope: ALL/USB_RX/USB_TX/N2K_RX/N2K_TX."""
        return self._service_operation(
            lambda c: {"data": c._send_terminal_command(f"DIAG {scope.upper()}", wait=10.0)})

    def send_service_cmd(self, cmd: str) -> Dict[str, str]:
        """Send an arbitrary service terminal command and return the response."""
        return self._service_operation(
            lambda c: {"response": c._send_terminal_command(cmd, wait=3.0)})

    # ── Backup helpers ────────────────────────────────────────────────────────

    def _find_existing_backup(self, fw_version: str) -> str | None:
        """
        Check if a backup for this firmware version already exists.
        Backup filename pattern: ydnu02_backup_{serial}_fw{version}_{date}_{time}.json
        Returns the path of the most recent matching backup, or None.
        """
        import glob
        backup_dir = os.path.dirname(os.path.abspath(__file__))
        fw_norm = fw_version.replace(' ', '_').replace('/', '-')
        pattern = os.path.join(backup_dir, f"ydnu02_backup_*_fw{fw_norm}_*.json")
        existing = sorted(glob.glob(pattern), reverse=True)
        return existing[0] if existing else None

    def create_backup(self, force: bool = False) -> Dict[str, str]:
        """
        Create a settings backup via service terminal.
        Smart: skips if a backup for the current firmware version already exists
        (unless force=True). Avoids redundant backups on repeated calls.
        """
        backup_dir = os.path.dirname(os.path.abspath(__file__))

        # Quick pre-check using cached info (avoids entering service mode unnecessarily)
        if not force and self._info_cache and self._info_cache.get("firmware_version"):
            existing = self._find_existing_backup(self._info_cache["firmware_version"])
            if existing:
                return {"status": "skipped", "filepath": existing,
                        "filename": os.path.basename(existing),
                        "message": "Backup already exists for this firmware version"}

        def _do(ctrl):
            # Re-verify inside service mode (cache may be stale)
            if not force:
                welcome = ctrl._send_terminal_command("HELP", wait=2.0)
                info = ctrl._parse_welcome_screen(welcome)
                fw = info.get("firmware_version", "")
                if fw:
                    existing = self._find_existing_backup(fw)
                    if existing:
                        return {"status": "skipped", "filepath": existing,
                                "filename": os.path.basename(existing),
                                "message": "Backup already exists for this firmware version"}
            filepath = ctrl.service_backup(backup_dir)
            return {"status": "ok", "filepath": filepath, "filename": os.path.basename(filepath)}
        return self._service_operation(_do)

    def reset_settings(self) -> Dict[str, str]:
        """Reset all YDNU-02 settings to factory defaults via service terminal."""
        return self._service_operation(
            lambda c: {"status": "ok", "response": c.service_reset_settings()})

    def reset_filters(self) -> Dict[str, str]:
        """Reset all YDNU-02 filter tables via service terminal."""
        return self._service_operation(
            lambda c: {"status": "ok", "response": c.service_reset_filters()})

    # ── Reset operations (use _raw_locked_operation — device reboots after) ──

    def reset_mcu(self) -> Dict[str, str]:
        """
        Soft MCU reset via service terminal (RESET MCU command).
        Device reboots — no service mode exit is possible/needed.
        Uses _raw_locked_operation because the device disappears after reset.
        """
        def _do(ctrl):
            ctrl.enter_service_mode()
            resp = ctrl.service_reset_mcu()
            ctrl._close_terminal()      # terminal is gone after reboot
            self._state = "IDLE"
            return {"status": "ok", "response": resp}
        return self._raw_locked_operation(_do)

    def reset_hardware(self) -> Dict[str, str]:
        """
        Full hardware reset via service terminal (RESET HARDWARE).
        Auto-creates backup first (skips if backup for current fw already exists).
        Device reboots after reset — uses _raw_locked_operation.
        """
        backup_dir = os.path.dirname(os.path.abspath(__file__))
        def _do(ctrl):
            ctrl.enter_service_mode()
            # Read info for smart backup check
            welcome = ctrl._send_terminal_command("HELP", wait=2.0)
            info = ctrl._parse_welcome_screen(welcome)
            fw = info.get("firmware_version", "")
            existing = self._find_existing_backup(fw) if fw else None
            if existing:
                filepath = existing     # reuse existing backup for this fw version
            else:
                filepath = ctrl.service_backup(backup_dir)  # create new backup
            resp = ctrl.service_reset_hardware()
            ctrl._close_terminal()
            self._state = "IDLE"
            return {"status": "ok", "response": resp, "backup": filepath}
        return self._raw_locked_operation(_do)

    # ══════════════════════════════════════════════════════════════════════════
    # OS shell operations (use _locked_operation — no service terminal needed)
    # ══════════════════════════════════════════════════════════════════════════

    def set_mode(self, mode: str) -> Dict[str, str]:
        """
        Switch YDNU-02 operating mode (AUTO/RAW/N2K/0183).
        Sends 'YDNU MODE <mode>\\r\\n' via proxy passthrough.
        Mode is persisted to EEPROM by the device.
        """
        return self._locked_operation(
            lambda c: (c.set_mode(mode), {"status": "ok", "message": f"Mode set to {mode.upper()}"})[1])

    def set_silent(self, state: str) -> Dict[str, str]:
        """
        Enable/disable YDNU-02 silent mode (suppresses TX on bus).
        Sends 'YDNU SILENT ON/OFF\\r\\n' via proxy passthrough.
        Persisted to EEPROM.
        """
        return self._locked_operation(
            lambda c: (c.set_silent(state.lower() == "on"),
                       {"status": "ok", "message": f"Silent mode {state.upper()}"})[1])

    # ══════════════════════════════════════════════════════════════════════════
    # Manual service mode control (for UI service tab)
    # ══════════════════════════════════════════════════════════════════════════

    def enter_service(self) -> Dict[str, str]:
        """
        Manually enter service mode (for interactive service terminal in UI).
        Uses _raw_locked_operation — stays paused until exit_service() is called.
        While in SERVICE state, bus worker is paused and no NMEA data is read.
        """
        def _do(ctrl):
            welcome = ctrl.enter_service_mode()
            self._state = "SERVICE"
            return {"status": "ok", "state": "SERVICE", "welcome": welcome}
        return self._raw_locked_operation(_do)

    def exit_service(self, target_mode: str = "AUTO") -> Dict[str, str]:
        """
        Manually exit service mode and return device to target_mode (default: AUTO).
        Resumes bus worker via _pause_event.clear() in _raw_locked_operation finally.
        """
        def _do(ctrl):
            resp = ctrl.exit_service_mode(target_mode)
            self._state = "IDLE"
            return {"status": "ok", "state": "IDLE", "response": resp}
        return self._raw_locked_operation(_do)

    def get_state(self) -> str:
        """Return current device state string: IDLE / LISTENING / SERVICE / NO_DEVICE."""
        return self._state

    # ══════════════════════════════════════════════════════════════════════════
    # Firmware OTA (uses _raw_locked_operation — device reboots after flash)
    # ══════════════════════════════════════════════════════════════════════════

    # Progress is polled by UI via GET /api/firmware/progress
    _fw_progress: Dict[str, Any] = {"stage": "idle", "percent": 0}

    def flash_firmware(self, bin_path: str) -> Dict[str, str]:
        """
        Flash firmware via proxy passthrough (chunked binary write to serial).
        progress_cb updates _fw_progress which is polled by the UI.
        Invalidates info cache after flash (firmware version has changed).
        Device reboots after flash — uses _raw_locked_operation.
        """
        def _progress(stage, pct):
            self._fw_progress = {"stage": stage, "percent": pct}

        def _do(ctrl):
            self._fw_progress = {"stage": "starting", "percent": 0}
            result = ctrl.update_firmware(bin_path, skip_backup=False, progress_cb=_progress)
            self._info_cache = None     # invalidate: firmware version changed
            self._fw_progress = {"stage": "done", "percent": 100}
            written = result.get("written", 0) if result else 0
            return {"status": "ok", "message": f"Firmware uploaded ({written} bytes). Device rebooting."}
        return self._raw_locked_operation(_do)

    @staticmethod
    def check_latest_firmware() -> Dict[str, Any]:
        """
        Fetch and parse yachtd.com/downloads/ to find the latest YDNU-02 firmware.
        Returns: latest_version, release_date (DD/MM/YYYY), download_url, changelog.
        No serial access needed — pure HTTP request.
        """
        url = "https://www.yachtd.com/downloads/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "YDNU02-Console/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

            # Find the firmware block: <a name="ydnufw" href="UUPDATE.zip" ...>
            fw_match = re.search(
                r'name="ydnufw"\s+href="([^"]+)".*?'        # download URL
                r'Firmware\s+Update.*?'                       # label
                r'text-dark-400">\s*([\w\s,]+?\d{4})\s*<.*?' # date (e.g. "August 7, 2025")
                r'text-dark-400[^>]*>\s*([\d.]+)\s*<',        # version (e.g. "1.75")
                html, re.DOTALL | re.IGNORECASE
            )
            if not fw_match:
                return {"status": "ok", "latest_version": None,
                        "message": "Could not parse firmware block", "url": url}

            download_file = fw_match.group(1).strip()
            date_raw      = fw_match.group(2).strip()
            version       = fw_match.group(3).strip()
            download_url  = f"https://www.yachtd.com/downloads/{download_file}"

            # Normalize date: "August 7, 2025" → "07/08/2025" (DD/MM/YYYY, like firmware)
            from datetime import datetime as _dt
            try:
                dt   = _dt.strptime(date_raw, "%B %d, %Y")
                date = dt.strftime("%d/%m/%Y")
            except ValueError:
                date = date_raw

            # Try to extract changelog text
            changelog = ""
            cl_match = re.search(
                r'name="ydnufw".*?border-t\s+border-slate-200">\s*(.*?)\s*</div>',
                html, re.DOTALL | re.IGNORECASE
            )
            if cl_match:
                changelog = cl_match.group(1).strip()

            return {
                "status": "ok",
                "latest_version": version,
                "release_date": date,
                "download_url": download_url,
                "changelog": changelog,
                "url": url,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "url": url}

    # ══════════════════════════════════════════════════════════════════════════
    # WebSocket: CAN bus monitor (live frame stream)
    # ══════════════════════════════════════════════════════════════════════════

    async def monitor_raw(self, websocket: WebSocket, duration: float = 300.0):
        """
        Stream live NMEA frames to a WebSocket client for `duration` seconds.

        Subscribes to bus worker's broadcast queue — no direct port access.
        Each frame arrives via call_soon_threadsafe() from _bus_worker thread.
        Frames are buffered in asyncio.Queue(maxsize=500) to handle bursts.

        The bus worker does NOT pause during monitoring — both NMEA reading and
        WebSocket streaming run concurrently.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._queues_lock:
            self._monitor_queues.append(q)

        if self._state != "LISTENING":
            await websocket.send_json({"type": "error", "message": "Bus worker not active — no NMEA data"})

        await websocket.send_json({"type": "status", "message": "RAW monitoring started"})

        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=1.0)
                    await websocket.send_json(frame)
                except asyncio.TimeoutError:
                    continue    # no frames in 1s — loop again (bus may be quiet)

        except WebSocketDisconnect:
            pass
        finally:
            with self._queues_lock:
                if q in self._monitor_queues:
                    self._monitor_queues.remove(q)

    # ══════════════════════════════════════════════════════════════════════════
    # WebSocket: bus device scanner
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _build_device_msg(dev: Dict[str, Any]) -> Dict[str, Any]:
        """Build a clean device summary dict for WebSocket scan_bus response."""
        return {
            "src":               dev.get("src", 0),
            "manufacturer":      dev.get("manufacturer", "Unknown"),
            "model":             dev.get("model", ""),
            "serial":            dev.get("serial", ""),
            "firmware":          dev.get("firmware", ""),
            "unique_id":         dev.get("unique_id", 0),
            "function_name":     dev.get("function_name", ""),
            "device_class_name": dev.get("device_class_name", ""),
            "mfg_code":          dev.get("mfg_code", 0),
            "product_code":      dev.get("product_code", 0),
        }

    async def scan_bus(self, websocket: WebSocket, duration: float = 10.0):
        """
        Scan the N2K CAN bus for devices and stream results to WebSocket.

        Sequence:
          1. Pause bus worker (_pause_event.SET) so it doesn't compete for frames
          2. Send ISO Request frames via proxy DATA connection (Address Claim + Product Info)
          3. Subscribe to broadcast queue, then RESUME bus worker (_pause_event.CLEAR)
             → worker reconnects and starts feeding frames into the queue
          4. Read frames for `duration` seconds; identify devices from PGN 60928/126996
          5. Send final device summary; unsubscribe from queue

        NOTE: scan_bus temporarily pauses then resumes the bus worker so that
        ISO Request responses are not consumed before the queue is ready.
        """
        # Step 1: Pause worker briefly while we set up ISO requests
        self._pause_event.set()
        await asyncio.sleep(0.3)    # give worker time to finish current readline()

        # Step 2: Send ISO Request frames (proxy forwards writes to serial)
        if self._tcp and self._tcp.is_connected:
            self._tcp.write(b"18EAFF10 00 EE 00\r\n")  # Address Claim request (PGN 60928)
            self._tcp.write(b"18EAFF10 14 F0 01\r\n")  # Product Info request  (PGN 126996)
        else:
            await websocket.send_json({"type": "error", "message": "Proxy not connected"})
            self._pause_event.clear()
            return

        await websocket.send_json({"type": "status", "message": f"Scanning for {duration}s..."})

        # Step 3: Subscribe to queue, then resume worker to feed it
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._queues_lock:
            self._monitor_queues.append(q)
        self._pause_event.clear()   # Resume: worker reconnects and starts broadcasting

        devices: Dict[int, Dict[str, Any]] = {}
        frame_count = 0

        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=1.0)
                    pgn = frame.get("pgn")
                    src = frame.get("src")
                    frame_count += 1

                    await websocket.send_json({
                        "type":    "frame",
                        "time":    frame.get("time"),
                        "pgn":     pgn,
                        "src":     src,
                        "decoded": frame.get("decoded"),
                    })

                    if src is not None:
                        if src not in devices:
                            devices[src] = {"src": src}
                            # Pre-seed from known device info (from previous bus traffic or replayed cache)
                            with self._sensors_lock:
                                known = self._discovered_bus_devices.get(src, {})
                                for k in ("manufacturer", "model", "serial", "firmware",
                                          "function_name", "device_class_name", "unique_id", "mfg_code"):
                                    if k in known:
                                        devices[src][k] = known[k]
                        if pgn in (60928, 126996):
                            # Re-parse from raw for full device info fields
                            raw_line = frame.get("raw", "")
                            if raw_line:
                                parsed = N2KPGNDecoder.parse_raw_line(raw_line)
                                if parsed:
                                    devices[src].update(N2KPGNDecoder.parse_device_info(parsed))
                            await websocket.send_json({
                                "type": "device",
                                **self._build_device_msg(devices[src]),
                            })

                except asyncio.TimeoutError:
                    continue    # no frames — bus is quiet, keep waiting

            # Final summary: re-send all discovered devices after scan window
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
            with self._queues_lock:
                if q in self._monitor_queues:
                    self._monitor_queues.remove(q)
            # Note: _pause_event was already cleared in Step 3.
            # No need to clear again here.
