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

      ssh user@localhost 'netstat -an | grep 4001'

  Skill — inspect live CAN frame stream on port 4001::

      ssh user@localhost 'nc localhost 4001 | head -n 20'

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
from typing import Set, Tuple, Optional, Callable, Dict, Any
from ydnu02_tcp_gateway.device_contract import N2KDeviceRegistry, N2KDeviceInfo
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
        self._get_clients_fn = get_clients if get_clients is not None else (lambda: self._own_clients)
        self.clients_lock = clients_lock if clients_lock is not None else threading.Lock()
        self._iso_request_lock = threading.Lock()
        self._iso_request_last_sent: float = 0.0

        # Unified N2K Device Registry: tracks physical + virtual devices
        self.device_registry = N2KDeviceRegistry()
        # Pre-register virtual TCP Gateway (SA=200) so announce_all_devices() immediately includes it
        self.device_registry.register_device(N2KDeviceInfo(
            sa=200,
            unique_id=902047,
            mfg_code=2047,
            device_class=25,
            device_function=130,
            industry_group=4,
            model_id="YDNU-02 TCP-GW",
            software_version="0.2.0",
            model_serial="SW-GW-00902047",
            model_version="yacht-n2k-console",
        ))

    @property
    def clients(self) -> Set[socket.socket]:
        """Return the active set of connected TCP client sockets."""
        return self._get_clients_fn()

    def broadcast(self, line: bytes, exclude: Optional[socket.socket] = None) -> None:
        """Send an N2K ASCII line to all connected TCP clients.

        Also tracks physical device data from PGN 60928 and PGN 126996 frames
        so _replay_iso_presence() can synthesize HA-compatible device announcements.

        Args:
            line: Raw line bytes ending with newline.
            exclude: Optional client socket to exclude from fanout (e.g. the sender).
        """
        # Track physical device data from passing frames
        self._track_physical_device(line)

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

    def _track_physical_device(self, line: bytes) -> None:
        """Decode PGN 60928 / 126996 from a frame and update N2KDeviceRegistry."""
        self.device_registry.update_from_frame(line)

    def announce_all_devices(self) -> None:
        """Generate and broadcast ISO Claim + Product Info for all registered devices."""
        lines = self.device_registry.generate_all_announcements()
        for line in lines:
            # HA TextNmea2000Gateway expects RX lines starting with timestamp and direction ('00:00:00.000 R ')
            text = line.decode("ascii", errors="ignore").rstrip()
            if not text.startswith("00:00:00.000 R "):
                formatted_line = f"00:00:00.000 R {text}\r\n".encode("ascii")
            else:
                formatted_line = line
            self.broadcast(formatted_line)

    def get_physical_devices(self) -> Dict[int, Dict[str, Any]]:
        """Return a snapshot dictionary of all registered devices for backwards compatibility."""
        devices = self.device_registry.get_all_devices()
        return {
            sa: {
                "src": dev.sa,
                "unique_id": dev.unique_id,
                "mfg_code": dev.mfg_code,
                "device_class": dev.device_class,
                "device_class_int": dev.device_class,
                "device_function": dev.device_function,
                "industry_group": dev.industry_group,
                "model": dev.model_id,
                "firmware": dev.software_version,
                "serial": dev.model_serial,
                "model_version": dev.model_version,
            }
            for sa, dev in devices.items()
        }

    def send_iso_request(self) -> None:
        """Broadcast ISO Requests (PGN 59904) to physical serial bus and TCP clients.

        Requests PGN 60928 (ISO Address Claim) and PGN 126996 (Product Info)
        from all devices on the bus (Destination=255). Rate-limited to 5s.

        Skill — trigger ISO Request from command line::

            ssh user@localhost 'python3 -c "from ydnu02_tcp_gateway.data_hub import DataHub; ..."'
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
        frame_claim = b"18EAFFFE 00 EE 00\r\n"
        frame_prod  = b"18EAFFFE 14 F0 01\r\n"
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

        # Broadcast active announcements for all registered devices
        self.announce_all_devices()

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

            ssh user@localhost 'journalctl -u ydnu02-tcp-gateway -n 30 | grep client'
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
