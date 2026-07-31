"""Data port hub for YDNU-02 TCP Gateway (:4001).

Bidirectional fanout hub between serial stream and all connected TCP clients.
Handles device frame caching, client replay, ISO Requests, and client loops.
"""

import time
import socket
import threading
import serial
from typing import Set, Tuple, Optional, Callable
from ydnu02_tcp_gateway.frame_utils import NMEA_LINE_RE, TX_LINE_RE, fmt_frame, get_pgn_sa
from ydnu02_tcp_gateway.device_cache import DeviceFrameCache

_ISO_REQUEST_MIN_INTERVAL = 5.0


class DataHub:
    """Bidirectional N2K frame broadcast hub on port 4001."""

    def __init__(self,
                 device_cache: DeviceFrameCache,
                 get_serial_instance: Callable[[], Optional[serial.Serial]],
                 get_serial_ready: Callable[[], bool],
                 get_service_mode: Callable[[], bool],
                 serial_lock: threading.Lock,
                 get_clients: Optional[Callable[[], Set[socket.socket]]] = None,
                 clients_lock: Optional[threading.Lock] = None):
        self.device_cache = device_cache
        self._get_serial = get_serial_instance
        self._get_serial_ready = get_serial_ready
        self._get_service_mode = get_service_mode
        self._serial_lock = serial_lock

        self._own_clients: Set[socket.socket] = set()
        self._get_clients_fn = get_clients or (lambda: self._own_clients)
        self.clients_lock = clients_lock or threading.Lock()

        self._iso_request_lock = threading.Lock()
        self._iso_request_last_sent: float = 0.0

    @property
    def clients(self) -> Set[socket.socket]:
        return self._get_clients_fn()

    def broadcast(self, line: bytes, exclude: Optional[socket.socket] = None) -> None:
        """Send a line to all DATA clients, updating the device frame cache."""
        self.device_cache.update_from_line(line)

        dead: Set[socket.socket] = set()
        with self.clients_lock:
            for conn in list(self.clients):
                if conn is exclude:
                    continue
                try:
                    conn.sendall(line)
                except OSError:
                    dead.add(conn)
            self.clients.difference_update(dead)

    def send_iso_request(self) -> None:
        """Broadcast ISO Requests (PGN 59904) to serial bus and TCP clients."""
        with self._iso_request_lock:
            now = time.time()
            if now - self._iso_request_last_sent < _ISO_REQUEST_MIN_INTERVAL:
                return
            self._iso_request_last_sent = now

        if not self._get_serial_ready():
            print("[data] ISO Request skipped — YDNU-02 not ready", flush=True)
            return

        with self._serial_lock:
            ser = self._get_serial()

        if ser is None or not getattr(ser, "is_open", False) or getattr(ser, "fd", None) is None or self._get_service_mode():
            return

        frame_claim = b"00:00:00.000 T 18EAFFFE 00 EE 00\r\n"
        frame_prod  = b"00:00:00.000 T 18EAFFFE 14 F0 01\r\n"
        try:
            ser.write(frame_claim)
            ser.write(frame_prod)
            print("[data] ISO Requests (Address Claim + Product Info) TX sent to serial", flush=True)
        except (serial.SerialException, OSError, TypeError, AttributeError) as e:
            print(f"[data] ISO Request TX error: {e}", flush=True)

        tcp_iso_req_claim = fmt_frame('18EAFFFE', b'\x00\xee\x00')
        tcp_iso_req_prod  = fmt_frame('18EAFFFE', b'\x14\xf0\x01')
        self.broadcast(tcp_iso_req_claim)
        self.broadcast(tcp_iso_req_prod)

    def handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """Handle a single DATA port client connection (bidirectional hub)."""
        print(f"[data] client connected: {addr}", flush=True)
        with self.clients_lock:
            self.clients.add(conn)

        self.device_cache.replay(conn)
        self.send_iso_request()

        buf = b''
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1)
                    raw += b'\n'
                    line = raw.rstrip(b'\r\n') + b'\n'

                    if NMEA_LINE_RE.match(line):
                        self.broadcast(line, exclude=conn)

                    elif TX_LINE_RE.match(raw):
                        parts = raw.rstrip(b'\r\n').split(b' ')
                        can_id  = parts[0]
                        data_bz = bytes(int(b, 16) for b in parts[1:] if b)
                        rx_line = fmt_frame(can_id.decode(), data_bz)
                        self.broadcast(rx_line, exclude=conn)

                        try:
                            pgn, _ = get_pgn_sa(can_id)
                            if pgn == 59904 and not self._get_service_mode():
                                with self._serial_lock:
                                    ser = self._get_serial()
                                    if ser and ser.is_open:
                                        ser.write(raw)
                                print(f"[data] ISO Request forwarded to serial", flush=True)
                        except (ValueError, IndexError):
                            pass
        except OSError:
            pass
        finally:
            with self.clients_lock:
                self.clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass
            print(f"[data] client disconnected: {addr}", flush=True)
