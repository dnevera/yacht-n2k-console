"""TCP client wrappers for proxy DATA (:4001) and CTRL (:4002) ports.

These are socket wrappers used by DeviceManager. TCPProxyConnection handles
the broadcast stream (multi-client). ProxyControlClient handles exclusive
serial passthrough for service mode and firmware flashing.
"""

import os
import socket
import time

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

    Architecture & Threading:
        - Designed to be used by a single dedicated worker thread (`_bus_worker`).
        - Uses raw `recv()` with an internal byte buffer to avoid Python's `makefile()`
          socket timeout state corruption bugs.
        - Non-thread-safe on its own; all operations must occur in the owning thread.

    Lifecycle:
        connect() → readline() × N → close()

    readline() behaviour:
        - Returns decoded UTF-8 line on success
        - Returns ""  on socket timeout (bus can be slow — ~1 frame per 2.5s)
        - Raises ConnectionResetError when the proxy closed the connection
          (e.g. proxy restarted — caller must reconnect)

    Skill — Read NMEA broadcast via netcat:
        ```bash
        nc <gateway-host> 4001
        # Output: 16:21:40.123 R 09F11233 11 22 33 44 55 66 77 88
        ```
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

    def readline(self) -> str:
        """
        Read one \\n-terminated NMEA line from the proxy broadcast stream.

        Uses raw socket.recv() instead of makefile().readline() to avoid
        Python's 'cannot read from timed out object' bug.

        Returns:
            Decoded, stripped line string on success.
            Empty string ""  on socket.timeout (normal — bus is slow).

        Raises:
            OSError:              Not connected (call connect() first).
            ConnectionResetError: Proxy closed the connection (proxy restarted).
        """
        if not self._sock:
            raise OSError("Not connected")
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionResetError("Proxy connection closed")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            return line.decode("utf-8", errors="ignore").strip()
        except socket.timeout:
            return ""

    def write(self, data: bytes) -> None:
        """
        Send raw bytes to the proxy's data port.
        The proxy forwards writes to serial (used for ISO Request frames in scan_bus).
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

    Lifecycle:
        enter_service() → passthrough_write/read_for × N → exit_service()
        enter_firmware() → passthrough_write × N → exit_firmware()
    """

    def __init__(self, host: str = _PROXY_HOST, port: int = _PROXY_CTRL_PORT):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._buf = b""     # unified byte buffer for protocol AND passthrough phases

    def _connect(self) -> None:
        """Open TCP connection to control port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((self._host, self._port))
        sock.settimeout(3.0)    # response timeout for control commands
        self._sock = sock
        self._buf = b""     # reset on new connection

    def _recv_line(self, timeout: float = 3.0) -> str:
        """
        Read one \\n-terminated line from self._buf, refilling via recv() as needed.
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
        Raises RuntimeError if proxy does not respond with READY.
        """
        self._connect()
        resp = self._send_cmd("SERVICE_START")
        if "READY" not in resp:
            raise RuntimeError(f"Proxy SERVICE_START failed: {resp}")

    def exit_service(self) -> None:
        """
        Send SERVICE_END and close control connection.
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
        """Send FIRMWARE_END and close."""
        try:
            self._send_cmd("FIRMWARE_END")
        except OSError:
            pass
        self._close()

    # ── Passthrough I/O ───────────────────────────────────────────────────────

    def passthrough_write(self, data: bytes) -> None:
        """Write raw bytes to serial via proxy passthrough."""
        if self._sock:
            try:
                self._sock.sendall(data)
            except OSError as e:
                print(f"[ProxyCtrl] write error: {e}")

    def passthrough_readline(self, timeout: float = 3.0) -> str:
        """Read one \\n-terminated line from serial via proxy passthrough."""
        return self._recv_line(timeout=timeout)

    def passthrough_read_for(self, duration: float) -> str:
        """Read all response lines for `duration` seconds; return joined as string."""
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
