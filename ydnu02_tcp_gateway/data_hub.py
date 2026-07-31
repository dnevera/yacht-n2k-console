"""Data port hub for YDNU-02 TCP Gateway (:4001).
=================================================

PURPOSE
  Bidirectional fanout hub between the serial stream (/dev/ttyACM0) and all connected
  TCP clients (Home Assistant, Signal K, web console). Handles instant client
  onboarding via active ISO Requests and immediate virtual device identity broadcasts.

ONBOARDING PROTOCOL & DUAL DEVICE GUARANTEE
  1. Pre-registered Devices: `DEFAULT_PHYSICAL_DEVICE` (SA=64, Unique ID=402047) and
     `DEFAULT_VIRTUAL_DEVICE` (SA=200, Unique ID=902047) are pre-registered in `self.device_registry`.
  2. Two-Phase Announcement (CRITICAL):
     When a TCP client (e.g. Home Assistant) connects, the onboarding MUST happen in two phases:

       Phase 1 — Immediately on connect:
         Broadcast PGN 60928 (ISO Address Claim) for ALL devices.
         The nmea2000 decoder on the HA side processes PGN 60928 and populates
         its internal `source_to_iso_name` map: {64: IsoName(402047, ...), 200: IsoName(902047, ...)}.

       Phase 2 — After ANNOUNCE_PRODUCT_INFO_DELAY seconds:
         Broadcast PGN 126996 (Product Information) for ALL devices.
         By this time source_to_iso_name is already built, so the decoder sets
         `message.source_iso_name` correctly for each SA.
         This allows `message.hash` (which uses source_iso_name in primary_key) to be
         UNIQUE per device — no hash collision in Home Assistant device registry.

     WHY THE DELAY IS REQUIRED:
       From nmea2000/decoder.py lines 338-346 (build_network_map=True mode):
         source_iso_name = self.source_to_iso_name.get(source_id, None)
         if source_iso_name is None and self.build_network_map:
             return None  # ← DROPS the PGN 126996 silently!
       Without the delay, PGN 60928 and PGN 126996 arrive simultaneously in the same
       TCP buffer flush, and the decoder processes PGN 126996 before source_to_iso_name
       is populated — causing BOTH devices to get message.source_iso_name=None and
       identical message.hash='818d9516db08fd90ffd1967e3c403bed'. The second device
       ends up with 0 entities in HA ("This device has no entities").

  3. Frame Formatting: All announcement frames are broadcast with `00:00:00.000 R ` prefix.
  4. Physical Bus Request: PGN 59904 (ISO Request) is also sent to the physical serial bus
     to prompt real hardware on the N2K bus to refresh its state.

HA REGISTRY STABILITY & unique_number
  The HA nmea2000 integration keys each device by message.hash, which is an MD5 of
  primary_key. Our fork of nmea2000/message.py uses `source_iso_name.unique_number`
  (21-bit, manufacturer-assigned per NMEA 2000 §3.1.1) in the primary_key:

      primary_key = f"{self.id}_{source_iso_name.unique_number}"

  This is CRITICAL. The alternative — iso_name.name (full 64-bit ISO NAME integer) —
  includes device_instance which changes every time YDNU-02 reinitialises on the bus.
  Using .name would produce a different MD5 on every gateway restart, creating a new
  HA device entry each time and accumulating duplicates in the registry.

  Stable hashes (patch-v2, unique_number-based):
    SA=64  unique_number=402047 → hash=ef195c7c99c762fdfda4e198aae87930
    SA=200 unique_number=902047 → hash=c11f5c824c71fe7e186cba56bf0f8672

  If duplicates appear in HA: run `./deploy.sh --clean-ha` to purge stale entries.
  The patch is applied by scripts/patch_ha_nmea2000_message.py (marker: patch-v2).

SKILLS / DIAGNOSTIC MINI-PROMPTS
================================
  Skill — verify dual device announcements in live stream::

      ssh user@localhost 'nc localhost 4001 | grep -E "19F01440|19F014C8"'

  Skill — trigger manual ISO Request and announcement::

      python3 -c "
      import socket
      s = socket.create_connection(('localhost', 4001))
      s.sendall(b'00:00:00.000 T 18EAFFFE 00 EE 00\\r\\n')
      print('ISO Request sent')
      s.close()
      "

  Skill — watch client onboarding logs::

      ssh user@localhost 'journalctl -u ydnu02-tcp-gateway -n 30 | grep -E "Phase|client|ISO"'
"""

import time
import socket
import threading
import serial
from typing import Set, Tuple, Optional, Callable, Dict, Any
from ydnu02_tcp_gateway.device_contract import (
    N2KDeviceRegistry,
    N2KDeviceInfo,
    DEFAULT_PHYSICAL_DEVICE,
    DEFAULT_VIRTUAL_DEVICE,
    N2KDeviceEncoder,
)
from ydnu02_tcp_gateway.frame_utils import NMEA_LINE_RE, TX_LINE_RE, fmt_frame, get_pgn_sa

_ISO_REQUEST_MIN_INTERVAL = 5.0

# Delay between Phase 1 (PGN 60928) and Phase 2 (PGN 126996) announcements.
# Required so the HA nmea2000 decoder builds source_to_iso_name before receiving Product Info.
# See docstring above for full explanation.
ANNOUNCE_PRODUCT_INFO_DELAY = 0.6  # seconds


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
        self.device_registry.register_device(DEFAULT_PHYSICAL_DEVICE)
        self.device_registry.register_device(DEFAULT_VIRTUAL_DEVICE)

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

    def _broadcast_lines(self, lines) -> None:
        """Helper: broadcast a list of raw encoder byte lines with correct R-frame prefix."""
        for line in lines:
            text = line.decode("ascii", errors="ignore").rstrip()
            if not text.startswith("00:00:00.000 R "):
                formatted_line = f"00:00:00.000 R {text}\n".encode("ascii")
            else:
                formatted_line = f"{text}\n".encode("ascii")
            self.broadcast(formatted_line)

    def announce_all_devices(self, product_info_delay: float = 0.0) -> None:
        """Two-phase broadcast: PGN 60928 immediately, PGN 126996 after a delay.

        Phase 1 (immediate): broadcast PGN 60928 (ISO Address Claim) for all devices.
          → HA decoder builds source_to_iso_name: {64: IsoName(402047), 200: IsoName(902047)}

        Phase 2 (after delay): broadcast PGN 126996 (Product Info).
          → source_to_iso_name is now populated, so message.source_iso_name is unique per SA.
          → message.hash becomes unique per device → no HA device registry collision.

        Args:
            product_info_delay: Seconds to wait before Phase 2 (Product Info broadcast).
                Default 0 = synchronous (both phases sent immediately, used by unit tests
                and any direct call that needs all frames available at once).
                Production code (send_iso_request) passes ANNOUNCE_PRODUCT_INFO_DELAY (0.6s)
                to give the HA nmea2000 decoder time to register source_to_iso_name from
                Phase 1 before Product Info arrives.
        """
        delay = product_info_delay
        snapshot = self.device_registry.get_all_devices()

        # Phase 1: broadcast only Address Claims (PGN 60928) immediately
        claim_lines = []
        prod_lines = []
        for dev in snapshot.values():
            all_lines = N2KDeviceEncoder.encode_announcement(dev)
            for line in all_lines:
                text = line.decode("ascii", errors="ignore")
                # Detect PGN 60928 (ISO Claim): CAN ID 18EEFF<SA_hex>
                # Detect PGN 126996 (Product Info): CAN ID 19F014<SA> fastpacket frames
                if "EEFF" in text.upper() or "18EE" in text.upper():
                    claim_lines.append(line)
                else:
                    prod_lines.append(line)

        self._broadcast_lines(claim_lines)
        print(f"[data] Phase 1: broadcast {len(claim_lines)} Address Claim frames", flush=True)

        # Phase 2: broadcast Product Info — synchronously if delay==0, else via Timer
        def _send_product_info():
            self._broadcast_lines(prod_lines)
            print(f"[data] Phase 2: broadcast {len(prod_lines)} Product Info frames", flush=True)

        if delay:
            threading.Timer(delay, _send_product_info).start()
        else:
            _send_product_info()


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

        # Two-phase announcement: PGN 60928 now, PGN 126996 after ANNOUNCE_PRODUCT_INFO_DELAY
        self.announce_all_devices(product_info_delay=ANNOUNCE_PRODUCT_INFO_DELAY)

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
