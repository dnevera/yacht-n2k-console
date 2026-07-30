#!/usr/bin/env python3
"""
ydnu02_tcp_gateway.py — NMEA 2000 TCP Gateway for YDNU-02
==========================================================

DEPLOYMENT
  File:    /opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py    (gateway.local.local)
  Service: ydnu02-tcp-gateway.service  (starts BEFORE ydnu02-web.service)
  Deploy:  ./deploy.sh  or  ./deploy.sh user@<gateway-host> --proxy

KEY INVARIANT — only this process ever opens /dev/ttyACM0.
  All other services (ydnu02-web, Home Assistant, Signal K) connect via TCP.
  HA connects to :4001. ydnu02-web connects to :4001 (data) + :4002 (ctrl).

PORTS
  :4001  DATA  — Serial→TCP broadcast of NMEA 2000 ASCII frames. Multiple clients.
  :4002  CTRL  — Exclusive passthrough session for service terminal / firmware flash.

CTRL PROTOCOL (line-oriented UTF-8)
  → SERVICE_START   proxy does: serial.close() → stty hupcl → echo → serial.open()
  ← READY           YDNU-02 is now in service terminal mode
  → <cmd>\r\n       forwarded verbatim to serial
  ← <response>      pushed to client on each 100ms poll
  → SERVICE_END     proxy does: serial.write("MODE RAW\r\n") → reset timeout
  ← OK

CRITICAL DESIGN DECISION — DTR toggle required for service mode entry
  serial.write("YDNU MODE SERVICE") is SILENTLY IGNORED while port is held open.
  YDNU-02 only processes the command when it sees a DTR low→high transition,
  which only happens when the port is CLOSED and then REOPENED (or via OS echo).
  Therefore: _enter_service_mode_on_device() closes the port, uses subprocess echo,
  then reopens. The ctrl client (ProxyControlClient / ydnu02.py) does NOT send
  "YDNU MODE SERVICE" — the gateway handles this entirely internally.
  See also: ydnu02.py::enter_service_mode() — passthrough path reads welcome only.

THREAD MODEL
  serial_reader thread  — owns serial_instance, broadcasts to DATA clients
  ctrl handler thread   — takes over serial_instance during SERVICE_START/END
  serial_reader adopts  — checks serial_instance every 50ms in service_mode loop

FIRMWARE_START vs SERVICE_START
  SERVICE_START: full DTR toggle mode switch (YDNU-02 → service terminal)
  FIRMWARE_START: raw passthrough only, no mode switch (used for firmware flash)

DEVICE FRAME CACHE
  The gateway caches ISO Address Claims (PGN 60928) and Product Information
  (PGN 126996) for every N2K device seen in live traffic.  On each new TCP
  client connect the full cache is replayed so HA immediately builds its network
  map without requiring a manual rescan or device power-cycle.

  The gateway also registers itself as a virtual N2K device (SA=200) using
  synthetic ISO Claim + Product Info frames that are pre-seeded in the cache at
  startup.  This ensures HA always has at least the gateway device visible,
  independent of whether a physical rescan has ever been performed.

GATEWAY VIRTUAL IDENTITY — NMEA 2000 NAME bit layout (64-bit, little-endian):
  [0:20]  Unique Number (21 bits)
  [21:31] Manufacturer Code (11 bits) — 741 = sum(ord(c) for c in "dnevera") % 2048
  [32:35] Device Instance Lower (4 bits)
  [36:39] Device Instance Upper (4 bits)
  [40:46] Device Function  (7 bits)  — 130 = PC Gateway
  [47]    Reserved = 0
  [48:54] Device Class     (7 bits)  — 25  = Internetwork device
  [55:57] System Instance  (3 bits)
  [58:61] Industry Group   (4 bits)  — 4   = Marine Industry
  [62]    Reserved = 0
  [63]    Arbitrary Address Capable = 1
"""
import os
import re
import socket
import struct
import subprocess
import serial
import sys
import threading
import time

# YDNU-02 ASCII NMEA 2000 frame format:
# "HH:MM:SS.mmm R|T XXXXXXXX XX XX ...\n"
# Only lines matching this are broadcast — all init echoes/text are discarded.
_NMEA_LINE_RE = re.compile(
    rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"
)

# ── Configuration (env vars) ──────────────────────────────────────────────────

SERIAL_PORT = os.getenv("NMEA_SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = int(os.getenv("NMEA_SERIAL_BAUD", "115200"))
TCP_HOST    = os.getenv("NMEA_PROXY_HOST", "")
DATA_PORT   = int(os.getenv("NMEA_PROXY_PORT", "4001"))
CTRL_PORT   = int(os.getenv("NMEA_CTRL_PORT",  "4002"))

# ── Shared state ──────────────────────────────────────────────────────────────

# Data clients (port DATA_PORT)
clients: set = set()
clients_lock = threading.Lock()

# Serial instance — owned by serial_reader, temporarily replaced by ctrl handler
# during service mode (close for mode switch → reopen in service terminal mode).
# Always access under serial_lock. serial_reader adopts changes via its sleep loop.
serial_instance: serial.Serial | None = None
serial_lock = threading.Lock()

# Service/firmware mode flag — set while control client holds exclusive serial
service_mode = threading.Event()          # set  → serial in exclusive passthrough mode
service_conn: socket.socket | None = None # active control client socket
service_conn_lock = threading.Lock()

# Signals that YDNU-02 has finished its init sequence and is ready for N2K frames.
# ISO Requests must NOT be sent before this is set — YDNU-02 will ignore them.
_serial_ready = threading.Event()

# ── Gateway virtual identity constants ────────────────────────────────────────

_GW_SA                = 200   # virtual source address (0xC8) for this process
_GW_UNIQUE_NUMBER     = 12345 # 21-bit (arbitrary, non-colliding with real devices)
_GW_MANUFACTURER_CODE = 741   # 11-bit: sum(ord(c) for c in "dnevera") % 2048
_GW_DEVICE_FUNCTION   = 130   # PC Gateway
_GW_DEVICE_CLASS      = 25    # Internetwork device
_GW_INDUSTRY_GROUP    = 4     # Marine Industry
_GW_AAC               = 1     # Arbitrary Address Capable


def _read_version() -> str:
    """Read software version from VERSION file in project root."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, '..', 'VERSION'),
                 os.path.join(here, 'VERSION')):
        try:
            with open(path) as fh:
                return fh.read().strip()
        except OSError:
            pass
    return '0.0.0'


def _fmt_frame(can_id_hex: str, data: bytes) -> bytes:
    """Format raw CAN data as a YDNU-02 ASCII text line."""
    return f'00:00:00.000 R {can_id_hex} {" ".join(f"{b:02X}" for b in data)}\n'.encode()


def _make_gw_iso_claim() -> bytes:
    """Construct synthetic ISO Address Claim line (PGN 60928) for the gateway."""
    name_int = (
        (_GW_UNIQUE_NUMBER     & 0x1FFFFF)       |
        ((_GW_MANUFACTURER_CODE & 0x7FF)  << 21) |
        ((_GW_DEVICE_FUNCTION   & 0x7F)   << 40) |
        ((_GW_DEVICE_CLASS      & 0x7F)   << 48) |
        ((_GW_INDUSTRY_GROUP    & 0x0F)   << 58) |
        ((_GW_AAC & 0x01) << 63)
    )
    name_bytes = name_int.to_bytes(8, 'little')
    # CAN ID: priority=6, DP=0, PF=0xEE, PS=0xFF (broadcast), SA=_GW_SA
    can_id = (6 << 26) | (0xEE << 16) | (0xFF << 8) | _GW_SA
    return _fmt_frame(f'{can_id:08X}', name_bytes)


def _make_gw_product_info(version: str, serial_code: str) -> list[bytes]:
    """Construct synthetic Product Information fast-packet lines (PGN 126996).

    PGN 126996 payload (134 bytes):
      [0:2]    N2K Version (uint16, units 0.001 → 1301 = 1.301)
      [2:4]    Product Code (uint16)
      [4:36]   Model ID (strfix32)
      [36:68]  Software Version Code (strfix32)
      [68:100] Model Version (strfix32)
      [100:132] Model Serial Code (strfix32)
      [132]    Certification Level (uint8, 2=Level B)
      [133]    Load Equivalency (uint8)
    """
    def pad(s: str, n: int = 32) -> bytes:
        b = s.encode('ascii', errors='replace')[:n]
        return b + b'\xff' * (n - len(b))

    payload = bytearray()
    payload += struct.pack('<H', 1301)               # N2K Version (1.301)
    payload += struct.pack('<H', 1)                  # Product Code
    payload += pad('YDNU-02 TCP-GW')                 # Model ID
    payload += pad(version)                           # Software Version Code
    payload += pad('yacht-n2k-console')               # Model Version
    payload += pad(serial_code[:32])                  # Model Serial Code
    payload += bytes([2, 1])                          # Cert Level B=2, Load Equiv=1

    # CAN ID: priority=6, DP=1, PF=0xF0, PS=0x14, SA=_GW_SA
    can_id = (6 << 26) | (1 << 24) | (0xF0 << 16) | (0x14 << 8) | _GW_SA
    cid_hex = f'{can_id:08X}'

    total = len(payload)    # 134
    seq   = 0
    frames: list[bytes] = []

    # Frame 0: [seq<<5|0] [total_bytes] [first 6 payload bytes]
    frames.append(_fmt_frame(cid_hex,
                             bytes([(seq << 5) | 0, total]) + bytes(payload[:6])))

    # Frames 1..N: [seq<<5|fn] [7 payload bytes, 0xFF-padded on last frame]
    offset, fn = 6, 1
    while offset < total:
        chunk = bytes(payload[offset:offset + 7]).ljust(7, b'\xff')
        frames.append(_fmt_frame(cid_hex, bytes([(seq << 5) | fn]) + chunk))
        offset += 7
        fn += 1

    return frames


# ── Device frame cache ────────────────────────────────────────────────────────
#
# Per-SA storage of device identification frames for replay to new TCP clients.
# Pre-seeded with gateway synthetic frames; continuously updated from live traffic.
#
# Structure: {sa_int: {'iso_claim': bytes, 'product_info': [bytes, ...]}}

_device_frame_cache: dict[int, dict] = {}
_device_frame_lock  = threading.Lock()

# Fast-packet reassembly buffer (PGN 126996 only).
# Holds in-progress multi-frame messages until all frames arrive.
_fp_buf:  dict[int, dict] = {}   # {sa: {'seq': int, 'total': int, 'lines': [bytes]}}
_fp_lock  = threading.Lock()


def _get_pgn_sa(can_id: bytes | str) -> tuple[int, int]:
    """Decode (PGN, SourceAddress) from 8-char hex CAN ID in YDNU-02 RAW format.

    CAN ID layout (29-bit, zero-extended to 32-bit):
      bits 28-26 : priority
      bit  25    : reserved
      bit  24    : DP (Data Page)
      bits 23-16 : PF (PDU Format)
      bits 15-8  : PS (PDU Specific — destination if PF<240, group ext if PF>=240)
      bits  7-0  : SA (Source Address)
    """
    cid = int(can_id, 16)
    sa  = cid & 0xFF
    pf  = (cid >> 16) & 0xFF
    ps  = (cid >> 8)  & 0xFF
    dp  = (cid >> 24) & 0x01
    # PDU2 (PF >= 240): PS is group extension → part of PGN
    # PDU1 (PF < 240):  PS is destination address → NOT part of PGN
    pgn = (dp << 16) | (pf << 8) | (ps if pf >= 240 else 0)
    return pgn, sa


def _cache_product_info_frame(sa: int, line: bytes) -> None:
    """Buffer a PGN 126996 (Product Information) fast-packet frame.

    Complete packets (all frames received) are stored in _device_frame_cache.
    Incomplete or out-of-order sequences are silently discarded.
    """
    # Data bytes follow "HH:MM:SS.mmm R XXXXXXXX " — 24 ASCII chars
    data_parts = line[24:].decode('ascii', errors='ignore').split()
    if not data_parts:
        return
    try:
        fb = int(data_parts[0], 16)
    except ValueError:
        return

    frame_num = fb & 0x1F
    seq_num   = (fb >> 5) & 0x07

    with _fp_lock:
        if frame_num == 0:
            # First frame: data_parts[1] carries total payload byte count
            total = int(data_parts[1], 16) if len(data_parts) > 1 else 0
            _fp_buf[sa] = {'seq': seq_num, 'total': total, 'lines': [line]}
        else:
            buf = _fp_buf.get(sa)
            if buf is None or buf['seq'] != seq_num:
                return   # missed frame 0 or sequence mismatch
            if len(buf['lines']) != frame_num:
                return   # missed intermediate frame; discard this packet
            buf['lines'].append(line)
            # Frame 0 carries 6 payload bytes; frames 1..N carry 7 each
            received = 6 + (len(buf['lines']) - 1) * 7
            if received >= buf['total']:
                complete = list(buf['lines'])
                with _device_frame_lock:
                    _device_frame_cache.setdefault(sa, {})['product_info'] = complete
                del _fp_buf[sa]
                print(f"[cache] Product Info cached SA={sa} "
                      f"({len(complete)} frames)", flush=True)


# ── Data port helpers ─────────────────────────────────────────────────────────

def _broadcast(line: bytes) -> None:
    """Send a line to all data clients, removing dead ones.

    Also updates the device frame cache for:
      PGN 60928  — ISO Address Claim (single frame, keyed by SA)
      PGN 126996 — Product Information (fast-packet, reassembled per SA)
    Cached frames are replayed to every new client on connect so HA can build
    its N2K network map without requiring a rescan.
    """
    # Update device frame cache from live N2K traffic
    if len(line) >= 24:
        try:
            pgn, sa = _get_pgn_sa(line[15:23])
            if pgn == 60928:      # ISO Address Claim — single frame, overwrite per SA
                with _device_frame_lock:
                    _device_frame_cache.setdefault(sa, {})['iso_claim'] = line
                print(f"[cache] ISO Claim cached SA={sa}", flush=True)
            elif pgn == 126996:   # Product Information — fast-packet reassembly
                _cache_product_info_frame(sa, line)
        except (ValueError, IndexError):
            pass

    dead: set = set()
    with clients_lock:
        for conn in list(clients):
            try:
                conn.sendall(line)
            except OSError:
                dead.add(conn)
        clients.difference_update(dead)


def _replay_device_frames(conn: socket.socket) -> None:
    """Replay cached device identification frames to a newly connected client.

    Sends ISO Address Claims and Product Info for all known N2K devices,
    including the gateway's own synthetic virtual identity (SA=_GW_SA).
    HA receives these and immediately builds its network map so sensor states
    become available without waiting for a manual rescan or device power-cycle.
    """
    with _device_frame_lock:
        snapshot = {sa: dict(e) for sa, e in _device_frame_cache.items()}

    if not snapshot:
        print("[data] no cached device frames to replay", flush=True)
        return

    sent = 0
    for sa, entry in sorted(snapshot.items()):
        try:
            if 'iso_claim' in entry:
                conn.sendall(entry['iso_claim'])
                sent += 1
            for frame in entry.get('product_info', []):
                conn.sendall(frame)
                sent += 1
        except OSError:
            break

    print(f"[data] replayed {sent} frame(s) for {len(snapshot)} device(s)", flush=True)


_iso_request_lock      = threading.Lock()
_iso_request_last_sent: float = 0.0
_ISO_REQUEST_MIN_INTERVAL = 5.0  # seconds: minimum interval between ISO Requests


def _send_iso_request() -> None:
    """Best-effort: transmit ISO Request (PGN 59904) via YDNU-02 RAW TX.

    Asks all N2K devices to broadcast their ISO Address Claim (PGN 60928).
    Effective only if YDNU-02 firmware supports TX in RAW mode.
    Primary mechanism is _replay_device_frames() which works unconditionally.

    YDNU-02 RAW TX format: "HH:MM:SS.mmm T CANID DD DD DD\r\n"
    CAN ID 18EAFFFE: prio=6, PF=0xEA (ISO Request), PS=0xFF, SA=0xFE (null)
    Data: 00 EE 00  (PGN 60928 in little-endian, 3 bytes)
    """
    global _iso_request_last_sent
    with _iso_request_lock:
        now = time.time()
        if now - _iso_request_last_sent < _ISO_REQUEST_MIN_INTERVAL:
            return
        _iso_request_last_sent = now

    if not _serial_ready.wait(timeout=10.0):
        print("[data] ISO Request skipped — YDNU-02 not ready after 10s", flush=True)
        return

    with serial_lock:
        ser = serial_instance
    if ser is None or not ser.is_open or service_mode.is_set():
        return
    frame = b"00:00:00.000 T 18EAFFFE 00 EE 00\r\n"
    try:
        ser.write(frame)
        print("[data] ISO Request TX sent (best-effort)", flush=True)
    except serial.SerialException as e:
        print(f"[data] ISO Request TX error: {e}", flush=True)


def handle_data_client(conn: socket.socket, addr) -> None:
    """Data port client: register for Serial→TCP broadcast, forward TCP→Serial."""
    print(f"[data] client connected: {addr}", flush=True)
    with clients_lock:
        clients.add(conn)

    # 1. Replay all cached device identification frames (ISO Claims + Product Info
    #    for every known N2K device, including gateway's own virtual identity).
    #    This primes HA's network map immediately so sensors become available
    #    without a manual rescan or device power-cycle.
    _replay_device_frames(conn)

    # 2. Best-effort ISO Request TX (works only if YDNU-02 supports RAW TX mode).
    #    Triggers fresh Claims from devices that came online after our startup.
    _send_iso_request()

    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            # Forward TCP→Serial only when NOT in exclusive mode
            if not service_mode.is_set():
                with serial_lock:
                    if serial_instance and serial_instance.is_open:
                        try:
                            serial_instance.write(data)
                        except serial.SerialException as e:
                            print(f"[serial] write error from data client: {e}", flush=True)
    except OSError:
        pass
    finally:
        with clients_lock:
            clients.discard(conn)
        try:
            conn.close()
        except OSError:
            pass
        print(f"[data] client disconnected: {addr}", flush=True)


# ── Control port helpers ──────────────────────────────────────────────────────

def _ctrl_send(conn: socket.socket, msg: str) -> None:
    try:
        conn.sendall((msg + "\n").encode())
    except OSError:
        pass


def _enter_service_mode_on_device() -> None:
    """
    Switch YDNU-02 from RAW mode to interactive service terminal mode.

    The YDNU-02 ONLY processes OS-level mode commands (YDNU MODE SERVICE) when
    the serial port is NOT held open in terminal mode. The mechanism requires a
    DTR transition: the port must be CLOSED first, then the command is written
    via echo (which opens, writes, and closes — toggling DTR), then the port is
    REOPENED for the service terminal session.

    serial.write("YDNU MODE SERVICE\\r\\n") does NOT work while port is open
    because the DTR line stays high and YDNU-02 never sees the transition.

    This function is called by the ctrl handler after service_mode.set() and
    after serial_reader has exited its readline() cycle. It replaces serial_instance
    with a new Serial object configured for service terminal use (timeout=2.0s).
    serial_reader will adopt the new instance via its sleep-loop adoption check.

    Must be called with service_mode.is_set() == True.
    """
    global serial_instance

    # Step 1: close the proxy's serial connection to release the DTR line
    with serial_lock:
        _ser = serial_instance
        serial_instance = None

    if _ser and _ser.is_open:
        try:
            _ser.close()
        except serial.SerialException:
            pass
    print("[ctrl] serial closed for service mode entry", flush=True)

    # Step 2: OS-level mode switch.
    # stty hupcl: make the port lower DTR on close (ensures clean DTR toggle).
    # echo: opens the port (DTR high), writes the command, closes (DTR low).
    # YDNU-02 sees DTR transition → high → low and processes the command.
    subprocess.run(
        ["stty", "-F", SERIAL_PORT, "hupcl"],
        capture_output=True, timeout=5
    )
    subprocess.run(
        f'echo "YDNU MODE SERVICE" > {SERIAL_PORT}',
        shell=True, capture_output=True, timeout=5
    )
    # Wait for YDNU-02 to switch modes and output its welcome screen
    time.sleep(1.5)
    print("[ctrl] YDNU MODE SERVICE sent via OS echo", flush=True)

    # Step 3: reopen the serial port for service terminal I/O.
    # timeout=2.0: service terminal responses can be slow (DIAG, PRINT filters).
    new_ser = serial.Serial(
        SERIAL_PORT, SERIAL_BAUD, timeout=2.0, dsrdtr=True, rtscts=False
    )
    new_ser.dtr = True
    time.sleep(0.2)
    # Flush the welcome screen printed by YDNU-02 after mode switch.
    # The ctrl client reads terminal output via passthrough; the welcome is not needed.
    if new_ser.in_waiting:
        new_ser.read(new_ser.in_waiting)

    with serial_lock:
        serial_instance = new_ser

    print("[ctrl] YDNU-02 in service terminal mode — serial reopened", flush=True)


def _exit_service_mode_on_device() -> None:
    """
    Switch YDNU-02 from service terminal back to RAW mode.

    Unlike entry (which requires OS-level echo), EXIT works via a normal serial
    write because YDNU-02 is actively listening on the serial line in service
    terminal mode. Sending 'MODE RAW\\r\\n' causes it to switch back immediately.

    After the mode switch, serial.timeout is reset to 0.1s for fast NMEA polling.
    No close/reopen needed: serial_reader resumes readline() on the same fd,
    which is now in RAW mode and will start receiving N2K frames again.

    NOTE: we do NOT flush the serial input buffer after MODE RAW.
    YDNU-02 sends ISO Address Claims shortly after re-entering RAW mode (N2K
    re-enumeration).  Flushing would discard them before serial_reader can cache
    them.  The mode-switch text response is discarded by _NMEA_LINE_RE in the
    serial_reader loop, so it does not cause any harm.

    Must be called with service_mode.is_set() == True (before clear).
    """
    with serial_lock:
        _ser = serial_instance

    if _ser and _ser.is_open:
        try:
            # Exit YDNU-02 service terminal: MODE RAW works here because the device
            # is in service terminal mode and is reading serial input normally.
            _ser.write(b"MODE RAW\r\n")
            time.sleep(0.5)
            # Reset timeout from 2.0s (service terminal) to 0.1s (fast NMEA polling).
            # Do NOT flush _ser.in_waiting here — ISO Claims from N2K re-enumeration
            # arrive immediately after MODE RAW and must reach serial_reader for caching.
            _ser.timeout = 0.1
        except serial.SerialException as e:
            print(f"[ctrl] error during service exit: {e}", flush=True)

    print("[ctrl] YDNU-02 switched back to RAW mode", flush=True)


def handle_ctrl_client(conn: socket.socket, addr) -> None:
    """
    Control port client: single session for service/firmware mode.

    SERVICE flow (proxy as gateway):
      1. SERVICE_START  → proxy: service_mode.set(), serial close, OS mode switch,
                          serial reopen → YDNU-02 is in service terminal → READY
      2. passthrough    → client sends terminal commands; proxy reads serial responses
      3. SERVICE_END    → proxy: MODE RAW to device, reset timeout → OK, service_mode.clear()

    FIRMWARE flow (raw passthrough, no mode switch):
      1. FIRMWARE_START → service_mode.set(), flush buffer → READY
      2. passthrough    → raw binary data forwarded to/from serial
      3. FIRMWARE_END   → service_mode.clear() → OK

    The ctrl_mode variable tracks which flow is active for the finally-block cleanup.
    """
    global service_conn
    print(f"[ctrl] client connected: {addr}", flush=True)

    with service_conn_lock:
        if service_mode.is_set():
            _ctrl_send(conn, "ERROR: another control session is active")
            conn.close()
            return
        service_conn = conn

    # Track session type so the finally block knows whether to do a device mode exit
    ctrl_mode: str | None = None   # "SERVICE" | "FIRMWARE"

    try:
        conn.settimeout(0.1)
        buf = b""

        while True:
            # Poll loop: read ctrl commands, push serial data to client
            try:
                chunk = conn.recv(256)
            except socket.timeout:
                # In passthrough: push any available serial data to client.
                # This is the main data path for reading service terminal responses.
                if service_mode.is_set():
                    with serial_lock:
                        ser = serial_instance
                    if ser and ser.is_open and ser.in_waiting:
                        try:
                            data = ser.read(ser.in_waiting)
                            conn.sendall(data)
                        except (serial.SerialException, OSError):
                            pass
                continue

            if not chunk:
                break

            buf += chunk
            while b"\n" in buf:
                cmd_bytes, buf = buf.split(b"\n", 1)
                cmd = cmd_bytes.decode("utf-8", errors="ignore").strip()

                if cmd in ("SERVICE_START", "FIRMWARE_START"):
                    ctrl_mode = "SERVICE" if cmd == "SERVICE_START" else "FIRMWARE"
                    service_mode.set()
                    print(f"[ctrl] {cmd} — broadcast paused", flush=True)

                    # Wait: serial_reader exits its current readline() in ≤100ms
                    # (serial timeout=0.1s), then enters the service_mode sleep loop.
                    time.sleep(0.15)

                    if cmd == "SERVICE_START":
                        # Full gateway mode switch: close → echo → reopen.
                        # After this call, YDNU-02 is in service terminal mode and
                        # serial_instance points to the reopened service terminal fd.
                        _enter_service_mode_on_device()
                    else:
                        # FIRMWARE_START: no mode switch, just flush stale data.
                        with serial_lock:
                            _ser = serial_instance
                        if _ser and _ser.is_open:
                            _ser.reset_input_buffer()

                    _ctrl_send(conn, "READY")

                elif cmd in ("SERVICE_END", "FIRMWARE_END"):
                    if ctrl_mode == "SERVICE":
                        # Send MODE RAW to device and reset serial timeout.
                        # This must happen BEFORE service_mode.clear() so that
                        # serial_reader does not race with the mode-switch write.
                        _exit_service_mode_on_device()

                    service_mode.clear()
                    ctrl_mode = None
                    print(f"[ctrl] {cmd} — broadcast resumed", flush=True)
                    _ctrl_send(conn, "OK")

                elif service_mode.is_set():
                    # Passthrough: forward raw terminal command bytes to serial.
                    # cmd_bytes still has \r if client sent \r\n, so raw = cmd_bytes + \n
                    # reconstructs the original \r\n terminator for the device.
                    raw = cmd_bytes + b"\n"
                    with serial_lock:
                        if serial_instance and serial_instance.is_open:
                            try:
                                serial_instance.write(raw)
                            except serial.SerialException as e:
                                _ctrl_send(conn, f"ERROR: serial write: {e}")

                else:
                    _ctrl_send(conn, "ERROR: not in service mode")

    except OSError:
        pass
    finally:
        # Safety cleanup: if session ended without SERVICE_END (client crash, timeout),
        # ensure YDNU-02 is returned to RAW mode and broadcast is resumed.
        # Race-safe: only the session that registered as service_conn cleans up.
        with service_conn_lock:
            if service_conn is conn:
                if service_mode.is_set():
                    if ctrl_mode == "SERVICE":
                        # Best-effort: device may already have timed out of service mode
                        try:
                            _exit_service_mode_on_device()
                        except Exception:
                            pass
                    service_mode.clear()
                    print("[ctrl] session ended — broadcast resumed", flush=True)
                service_conn = None
        try:
            conn.close()
        except OSError:
            pass
        print(f"[ctrl] client disconnected: {addr}", flush=True)


# ── Serial reader ─────────────────────────────────────────────────────────────

def serial_reader() -> None:
    """
    Serial→TCP: own the serial port, read lines, broadcast to data clients.

    In service_mode: stop reading, enter a 50ms sleep loop. The ctrl handler
    may replace serial_instance (close for mode switch → reopen in service
    terminal mode). The sleep loop adopts the new serial_instance so that when
    service_mode clears, readline() uses the correct (already-reopen) fd.

    Adoption logic:
        while service_mode.is_set():
            sleep(0.05)
            if serial_instance changed → adopt it
            continue
        ser.readline()   ← uses adopted ser (service terminal or new RAW fd)
    """
    global serial_instance
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1,
                                dsrdtr=True, rtscts=False)
            ser.dtr = True
            with serial_lock:
                serial_instance = ser
            print(f"[serial] opened {SERIAL_PORT} @ {SERIAL_BAUD}", flush=True)

            # Capture ALL bytes during init (don't discard) so we can extract
            # any device identification frames the YDNU-02 sent at startup.
            init_data = b""

            ser.write(b"YDNU MODE RAW\r\n")
            time.sleep(2.0)
            if ser.in_waiting:
                init_data += ser.read(ser.in_waiting)
            ser.write(b"0\n")
            time.sleep(0.5)
            if ser.in_waiting:
                init_data += ser.read(ser.in_waiting)
            print("[serial] YDNU-02 initialized in RAW mode", flush=True)

            # Cache any ISO Claims (PGN 60928) or Product Info (PGN 126996) that
            # arrived during init.  YDNU-02 sends its own Claim right after init;
            # other devices may do the same if they were already on the bus.
            for raw_line in init_data.split(b"\n"):
                if not raw_line:
                    continue
                line = raw_line.rstrip(b"\r") + b"\n"
                if not _NMEA_LINE_RE.match(line):
                    continue
                try:
                    pgn, sa = _get_pgn_sa(line[15:23])
                    if pgn == 60928:
                        with _device_frame_lock:
                            _device_frame_cache.setdefault(sa, {})['iso_claim'] = line
                        print(f"[serial] startup ISO Claim cached SA={sa}: "
                              f"{line.decode(errors='ignore').strip()}", flush=True)
                    elif pgn == 126996:
                        _cache_product_info_frame(sa, line)
                except (ValueError, IndexError):
                    pass

            non_gw = sum(1 for sa in _device_frame_cache if sa != _GW_SA)
            print(f"[serial] {non_gw} real device(s) cached from startup data", flush=True)

            # Signal readiness and send initial ISO Request so all N2K devices
            # announce themselves. This primes HA's network map even if HA connected
            # before init completed (the _serial_ready.wait() in _send_iso_request
            # ensures the on-connect call also runs after this point).
            _serial_ready.set()
            _send_iso_request()

            while True:
                if service_mode.is_set():
                    # Exclusive mode: ctrl handler owns the serial.
                    # The ctrl handler may replace serial_instance (close → reopen)
                    # during the SERVICE_START mode switch. Adopt the new fd so that
                    # when service_mode clears, readline() uses the correct object.
                    time.sleep(0.05)
                    with serial_lock:
                        current = serial_instance
                    if current is not None and current is not ser:
                        ser = current   # adopt: ctrl handler replaced serial_instance
                    continue

                raw = ser.readline()
                if not raw:
                    continue

                # Strip \r\n, re-terminate with \n only for clean TCP stream
                line = raw.rstrip(b"\r\n") + b"\n"

                # Discard non-NMEA lines (init echoes, mode-switch text, etc.)
                if not _NMEA_LINE_RE.match(line):
                    continue

                _broadcast(line)

        except serial.SerialException as e:
            print(f"[serial] error: {e} — retrying in 5s", flush=True)
            _serial_ready.clear()  # not ready until re-initialized
            with serial_lock:
                serial_instance = None
            service_mode.clear()   # safety: exit service mode on serial error
            time.sleep(5)
        except Exception as e:
            print(f"[serial] unexpected error: {e} — retrying in 5s", flush=True)
            _serial_ready.clear()
            with serial_lock:
                serial_instance = None
            service_mode.clear()
            time.sleep(5)


# ── TCP servers ───────────────────────────────────────────────────────────────

def _make_server(port: int) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((TCP_HOST, port))
    srv.listen(5)
    return srv


def _accept_loop(srv: socket.socket, handler, label: str) -> None:
    while True:
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=handler, args=(conn, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except OSError:
            break


# ── Gateway telemetry ─────────────────────────────────────────────────────────

def _read_cpu_temp() -> float | None:
    """Read Raspberry Pi CPU temperature in Celsius from sysfs thermal zone."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as fh:
            return int(fh.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def _make_heartbeat(seq: int, interval_ms: int = 10000) -> bytes:
    """Construct a Heartbeat frame (PGN 126993) for the gateway virtual device.

    PGN 126993 — single CAN frame, 8 payload bytes:
      [0:2] Data transmit interval (uint16 LE, ms)
      [2]   Sequence counter (uint8, bits[7:2] = counter, bits[1:0] = comm state=0)
      [3:8] Reserved (0xFF)

    CAN ID: priority=7, DP=1, PF=0xF0, PS=0x11, SA=_GW_SA
    """
    can_id = (7 << 26) | (1 << 24) | (0xF0 << 16) | (0x11 << 8) | _GW_SA
    # seq counter: bits[7:2] increment each heartbeat, bits[1:0] = comm state (0=OK)
    seq_byte = ((seq & 0x3F) << 2) & 0xFC
    data = struct.pack('<H', interval_ms) + bytes([seq_byte]) + b'\xff' * 5
    return _fmt_frame(f'{can_id:08X}', data)


def _make_cpu_temp_frames(temp_c: float, seq: int) -> list[bytes]:
    """Construct Temperature Extended Range fast-packet (PGN 130316).

    PGN 130316 — fast-packet, 9 payload bytes → 2 CAN frames:
      [0]   SID (uint8, rolling sequence)
      [1]   Temperature Instance (uint8, 0 = first sensor on this device)
      [2]   Temperature Source  (uint8, 3 = Engine Room — closest to CPU/board temp)
      [3:6] Actual Temperature  (uint24 LE, units 0.001 K, e.g. 318150 = 318.15 K = 45 °C)
      [6:9] Set Temperature     (uint24 LE, 0xFFFFFF = N/A)

    CAN ID: priority=6, DP=1, PF=0xFD, PS=0x0C, SA=_GW_SA
    """
    can_id  = (6 << 26) | (1 << 24) | (0xFD << 16) | (0x0C << 8) | _GW_SA
    cid_hex = f'{can_id:08X}'

    temp_raw   = int((temp_c + 273.15) * 1000)    # Celsius → 0.001 K units
    temp_bytes = temp_raw.to_bytes(3, 'little')

    # payload: SID=seq, instance=0, source=3, actual_temp, set_temp=N/A
    payload = bytes([seq & 0xFF, 0, 3]) + temp_bytes + b'\xff\xff\xff'  # 9 bytes

    fp_seq  = 0
    # Frame 0: [fp_seq<<5|0] [total=9] [payload[0:6]]
    frame0  = bytes([(fp_seq << 5) | 0, len(payload)]) + payload[:6]
    # Frame 1: [fp_seq<<5|1] [payload[6:9]] [0xFF × 4 padding]
    frame1  = bytes([(fp_seq << 5) | 1]) + payload[6:] + b'\xff' * 4

    return [_fmt_frame(cid_hex, frame0), _fmt_frame(cid_hex, frame1)]


def _telemetry_sender() -> None:
    """Periodic telemetry broadcaster for the gateway's virtual N2K identity (SA=_GW_SA).

    CPU Temperature (PGN 130316): every 3 seconds — keeps device alive, real Pi metric
    Heartbeat       (PGN 126993): every 10 seconds — explicit N2K liveness signal

    Frames are sent via _broadcast() to all connected DATA clients (HA, Signal K, etc.).
    They are NOT cached in _device_frame_cache — telemetry is always live data.
    The ISO Claim + Product Info for SA=_GW_SA remain cached for new-client replay.
    """
    hb_seq   = 0
    temp_seq = 0
    last_hb  = 0.0

    while True:
        time.sleep(3.0)
        now = time.time()

        # CPU temperature — every 3 seconds
        temp = _read_cpu_temp()
        if temp is not None:
            for frame in _make_cpu_temp_frames(temp, temp_seq):
                _broadcast(frame)
            temp_seq = (temp_seq + 1) & 0xFF
        else:
            print("[gw] CPU temp unavailable (non-Pi platform?)", flush=True)

        # Heartbeat — every 10 seconds (roughly every 3rd temp cycle)
        if now - last_hb >= 10.0:
            _broadcast(_make_heartbeat(hb_seq, interval_ms=10000))
            hb_seq  = (hb_seq + 1) % 64
            last_hb = now


def main() -> None:
    # Seed device frame cache with synthetic gateway identity frames.
    # Gateway presents itself as a virtual N2K device (SA=_GW_SA) so HA
    # always has at least one device on connect, independent of whether a
    # physical network scan has been performed.
    _gw_version      = _read_version()
    _gw_serial       = socket.gethostname()
    _gw_iso_claim    = _make_gw_iso_claim()
    _gw_product_info = _make_gw_product_info(_gw_version, _gw_serial)
    with _device_frame_lock:
        _device_frame_cache[_GW_SA] = {
            'iso_claim':    _gw_iso_claim,
            'product_info': _gw_product_info,
        }
    print(f"[gw] SA={_GW_SA}  version={_gw_version}  serial={_gw_serial}", flush=True)

    # Start serial reader thread
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()

    # Start gateway telemetry sender (CPU temp every 3s, Heartbeat every 10s)
    tt = threading.Thread(target=_telemetry_sender, daemon=True)
    tt.start()

    # Data server
    data_srv = _make_server(DATA_PORT)
    print(f"[proxy] NMEA data  listening on :{DATA_PORT}", flush=True)

    # Control server (runs in its own thread)
    ctrl_srv = _make_server(CTRL_PORT)
    print(f"[proxy] NMEA ctrl  listening on :{CTRL_PORT}", flush=True)
    ct = threading.Thread(
        target=_accept_loop,
        args=(ctrl_srv, handle_ctrl_client, "ctrl"),
        daemon=True,
    )
    ct.start()

    # Data accept loop (main thread)
    try:
        _accept_loop(data_srv, handle_data_client, "data")
    except KeyboardInterrupt:
        print("Shutting down.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
