"""Data port hub for YDNU-02 TCP Gateway (:4001).
=================================================

PURPOSE
  Bidirectional fanout hub between the serial stream (/dev/ttyACM0) and all connected
  TCP clients (Home Assistant, Signal K, web console). Handles instant client
  onboarding via active ISO Requests and immediate virtual device identity broadcasts.

ARCHITECTURE DECISION (NO CACHING)
  We do NOT use a passive frame cache for device identification.
  Instead, we rely on canonical NMEA 2000 protocols:
    1. Virtual Gateway Identity: On every new TCP client connection, we immediately
       transmit PGN 60928 (ISO Address Claim) and PGN 126996 (Product Information)
       for our virtual gateway (SA=200).
    2. Physical Bus Discovery: Simultaneously, we broadcast PGN 59904 (ISO Request)
       to the physical bus (Destination=255). Physical devices (such as the physical YDNU-02)
       reply with their own authentic PGN 60928 and PGN 126996 frames.

  This guarantees zero stale data, zero frame mutation, and zero entity confusion in Home Assistant.

SKILLS / DIAGNOSTIC MINI-PROMPTS
================================
  Skill — verify TCP clients connected to data hub::

      ssh user@gateway.local 'netstat -an | grep 4001'

  Skill — inspect live CAN frame stream on port 4001::

      ssh user@gateway.local 'nc localhost 4001 | head -n 20'

  Skill — trigger manual ISO Request to physical bus::

      python3 -c "
      import socket
      s = socket.create_connection(('localhost', 4001))
      s.sendall(b'00:00:00.000 T 18EAFFFE 00 EE 00\\r\\n')
      print('ISO Request PGN 60928 sent')
      s.close()
      "
"""

import time
import socket
import threading
import serial
from typing import Set, Tuple, Optional, Callable
from ydnu02_tcp_gateway.frame_utils import NMEA_LINE_RE, TX_LINE_RE, fmt_frame, get_pgn_sa

_ISO_REQUEST_MIN_INTERVAL = 5.0


class DataHub:
    """Bidirectional N2K frame broadcast hub on port 4001.

    Manages TCP clients, serial forwarding, and active ISO Requests.

    Skill — test broadcasting synthetic frame to all connected clients::

        from ydnu02_tcp_gateway.data_hub import DataHub
        # hub instance forwards frame to all clients
    """

    def __init__(self,
                 get_serial_instance: Callable[[], Optional[serial.Serial]] = None,
                 get_serial_ready: Callable[[], bool] = None,
                 get_service_mode: Callable[[], bool] = None,
                 serial_lock: threading.Lock = None,
                 get_clients: Optional[Callable[[], Set[socket.socket]]] = None,
                 clients_lock: Optional[threading.Lock] = None):
        """Initialize DataHub.

        Args:
            get_serial_instance: Callable returning active serial.Serial or None.
            get_serial_ready: Callable returning True if serial port is ready in RAW mode.
            get_service_mode: Callable returning True if service mode is active.
            serial_lock: Thread lock guarding serial port access.
            get_clients: Optional callable returning active socket set.
            clients_lock: Optional thread lock guarding client set operations.
        """
        self._get_serial = get_serial_instance
        self._get_serial_ready = get_serial_ready or (lambda: True)
        self._get_service_mode = get_service_mode or (lambda: False)
        self._serial_lock = serial_lock or threading.Lock()

        self._own_clients: Set[socket.socket] = set()
        self._get_clients_fn = get_clients or (lambda: self._own_clients)
        self.clients_lock = clients_lock or threading.Lock()

        self._iso_request_lock = threading.Lock()
        self._iso_request_last_sent: float = 0.0

    @property
    def clients(self) -> Set[socket.socket]:
        """Return the active set of connected TCP client sockets."""
        return self._get_clients_fn()

    def broadcast(self, line: bytes, exclude: Optional[socket.socket] = None) -> None:
        """Send an N2K ASCII line to all connected TCP clients.

        Args:
            line: Raw line bytes ending with newline.
            exclude: Optional client socket to exclude from fanout (e.g. the sender).

        Skill — inspect frame broadcast in logs::

            ssh user@gateway.local 'journalctl -u ydnu02-tcp-gateway -f | grep data'
        """
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
        """Broadcast ISO Requests (PGN 59904) to physical serial bus and TCP clients.

        Requests PGN 60928 (ISO Address Claim) and PGN 126996 (Product Info)
        from all devices on the bus (Destination=255). Rate-limited to 5s.

        Skill — trigger ISO Request from command line::

            ssh user@gateway.local 'python3 -c "from ydnu02_tcp_gateway.data_hub import DataHub; ..."'
        """
        with self._iso_request_lock:
            now = time.time()
            if now - self._iso_request_last_sent < _ISO_REQUEST_MIN_INTERVAL:
                return
            self._iso_request_last_sent = now

        if not self._get_serial_ready():
            print("[data] ISO Request skipped — YDNU-02 serial not ready", flush=True)
            return

        with self._serial_lock:
            ser = self._get_serial()

        if ser is None or not getattr(ser, "is_open", False) or getattr(ser, "fd", None) is None or self._get_service_mode():
            return

        # Canonical N2K ISO Requests (PGN 59904) for PGN 60928 (Address Claim) and 126996 (Product Info)
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
        """Handle lifecycle and bidirectional frame loop for a single connected TCP client.

        Onboard Sequence:
          1. Add client socket to active set.
          2. Trigger ISO Request to prompt physical devices to identify themselves.
          3. Read incoming lines from client socket, fan out to other clients or serial.

        Args:
            conn: Connected client socket.
            addr: (host, port) client address tuple.

        Skill — watch client onboarding logs::

            ssh user@gateway.local 'journalctl -u ydnu02-tcp-gateway -n 30 | grep client'
        """
        print(f"[data] client connected: {addr}", flush=True)
        with self.clients_lock:
            self.clients.add(conn)

        # Active onboarding: prompt physical devices via ISO Request
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
