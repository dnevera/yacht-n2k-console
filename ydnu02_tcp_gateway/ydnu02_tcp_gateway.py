#!/usr/bin/env python3
"""
ydnu02_tcp_gateway.py — NMEA 2000 TCP Gateway for YDNU-02
==========================================================

OVERVIEW
  This is the central multiplexer between the YDNU-02 USB-to-N2K adapter
  (/dev/ttyACM0) and all software consumers on the Raspberry Pi 5. It is
  the ONLY process that opens the physical serial port. All other services
  communicate via TCP.

DEPLOYMENT
  File:    /opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py    (<gateway-host>)
  Service: ydnu02-tcp-gateway.service  (starts BEFORE ydnu02-web.service)
  Deploy:  ./deploy.sh  or  ./deploy.sh user@gateway-host --proxy

KEY INVARIANT — only this process ever opens /dev/ttyACM0.
  All other services (ydnu02-web, Home Assistant, Signal K) connect via TCP.
  HA connects to :4001. ydnu02-web connects to :4001 (data) + :4002 (ctrl).

ARCHITECTURE
  ::

      /dev/ttyACM0  (YDNU-02, 115200 baud, CAN_FRAME_ASCII)
            │
      serial_reader thread  (owns serial port)
            │
            ▼
      ┌─ port :4001 ─ DATA ─ Bidirectional TCP Hub ─┐
      │                                              │
      │  Serial→TCP:  broadcast to all TCP clients   │
      │  TCP→TCP:     forward to all OTHER clients   │
      │  TCP→Serial:  ISO Requests ONLY (PGN 59904)  │
      │                                              │
      ├── Home Assistant  (nmea2000 IOClient)         │
      ├── Signal K        (optional)                  │
      ├── ydnu02-web      (monitor tab)               │
      └── N2KDevice SA=200 (virtual gateway device)   │
                                                      │
      ┌─ port :4002 ─ CTRL ─ Exclusive Passthrough ──┐
      │                                              │
      │  Single client at a time (ydnu02-web admin)  │
      │  SERVICE_START → DTR toggle → service mode   │
      │  FIRMWARE_START → raw passthrough (no switch) │
      └──────────────────────────────────────────────┘

PORTS
  :4001  DATA  — Serial→TCP broadcast of NMEA 2000 ASCII frames. Multiple clients.
  :4002  CTRL  — Exclusive passthrough session for service terminal / firmware flash.

FRAME FORMATS
  RX format (from YDNU-02):   "HH:MM:SS.mmm R XXXXXXXX XX XX ...\\n"
  TX format (to YDNU-02):     "HH:MM:SS.mmm T XXXXXXXX XX XX ...\\r\\n"
  Hub format (virtual N2K):   "XXXXXXXX XX XX ...\\r\\n"  (no timestamp)

  The gateway normalizes all frames to RX format before broadcasting.
  TX-format frames from virtual N2KDevice clients are converted to RX format
  via _fmt_frame() before entering the cache and broadcast pipeline.

CTRL PROTOCOL (line-oriented UTF-8)
  → SERVICE_START   proxy does: serial.close() → stty hupcl → echo → serial.open()
  ← READY           YDNU-02 is now in service terminal mode
  → <cmd>\\r\\n       forwarded verbatim to serial
  ← <response>      pushed to client on each 100ms poll
  → SERVICE_END     proxy does: serial.write("MODE RAW\\r\\n") → reset timeout
  ← OK

CRITICAL DESIGN DECISION — DTR toggle required for service mode entry
  serial.write("YDNU MODE SERVICE") is SILENTLY IGNORED while port is held open.
  YDNU-02 only processes the command when it sees a DTR low→high transition,
  which only happens when the port is CLOSED and then REOPENED (or via OS echo).
  Therefore: _enter_service_mode_on_device() closes the port, uses subprocess echo,
  then reopens. The ctrl client (ProxyControlClient / ydnu02.py) does NOT send
  "YDNU MODE SERVICE" — the gateway handles this entirely internally.
  See also: ydnu02.py::enter_service_mode() — passthrough path reads welcome only.

FIRMWARE_START vs SERVICE_START
  SERVICE_START: full DTR toggle mode switch (YDNU-02 → service terminal)
  FIRMWARE_START: raw passthrough only, no mode switch (used for firmware flash)

THREAD MODEL
  ::

      Main thread        → _accept_loop(data_srv)  — accepts DATA clients
      serial_reader      → owns serial port, broadcasts to DATA clients
      ctrl_accept        → _accept_loop(ctrl_srv)  — accepts CTRL clients
      handle_data_client → one per DATA client (bidirectional hub)
      handle_ctrl_client → one CTRL client at a time (service/firmware mode)
      gateway-n2k-device → N2KDevice async loop (ydnu02_gateway_device.py)

  Lock hierarchy (always acquire in this order to prevent deadlocks):
    1. serial_lock
    2. clients_lock
    3. _device_frame_lock
    4. _fp_lock
    5. _iso_request_lock
    6. service_conn_lock

DEVICE FRAME CACHE
  The gateway caches ISO Address Claims (PGN 60928) and Product Information
  (PGN 126996) for every N2K device seen in live traffic.  On each new TCP
  client connect the full cache is replayed so HA immediately builds its network
  map without requiring a manual rescan or device power-cycle.

  Structure: {sa_int: {'iso_claim': bytes, 'product_info': [bytes, ...]}}

  Pre-seeded: The gateway virtual device (SA=200) frames are injected into the
  cache when N2KDevice connects to port 4001 and broadcasts its claims.

  Fast-packet reassembly: PGN 126996 uses ISO 11783-3 fast-packet transport
  (multiple CAN frames for a single PGN). Frames are reassembled in _fp_buf
  before being stored in _device_frame_cache.

GATEWAY VIRTUAL IDENTITY
  The gateway registers itself as a virtual N2K device (SA=200) so Home Assistant
  and Signal K can track its liveness.  All identity and telemetry is handled by
  ydnu02_gateway_device.py which uses the nmea2000 Python library's N2KDevice,
  connecting back to our own port 4001 (CAN_FRAME_ASCII, bidirectional hub).

  Port 4001 is a bidirectional N2K bus hub:
    serial reader → broadcast to all TCP clients
    TCP client frame → broadcast to all OTHER TCP clients (not to serial)
  This lets the N2KDevice's frames (ISO Claim, Product Info, HB, Temp) reach HA.

ISO REQUEST MECHANISM
  On each new TCP client connect, the gateway sends ISO Requests (PGN 59904)
  to the physical N2K bus via serial, requesting:
    - PGN 60928  (ISO Address Claim) from all devices (destination 0xFE = global)
    - PGN 126996 (Product Information) from all devices
  This triggers physical devices to re-announce, populating the cache for
  future client connections. Rate-limited to 1 request per 5 seconds.

  ISO Requests are also broadcast to TCP clients so virtual devices (SA=200)
  receive them and respond via the bidirectional hub.

TODO:
  - TODO(ipv6): TCP servers bind to "" (all interfaces). Consider IPv6 support
    or restricting to localhost for security on untrusted networks.
  - TODO(max-clients): No limit on simultaneous DATA clients. A flood of
    connections could exhaust file descriptors. Add a configurable max.
  - TODO(health-endpoint): Add a simple HTTP health check endpoint or a
    TCP "PING" command on CTRL port for monitoring integration.
  - TODO(metrics): Track total frames processed, cache hits/misses,
    client connect/disconnect counts, serial reconnections.
  - TODO(cache-expiry): Device frame cache entries never expire. If a device
    is permanently removed from the bus, its stale cache entry persists.
    Consider TTL-based expiration (e.g. no heartbeat for 60s → evict).

ISSUES:
  - ISSUE(serial-contention): During service mode, serial_reader busy-waits
    with time.sleep(0.05) polling. This is 20 wakeups/sec doing no useful work.
    Consider using service_mode.wait() with a timeout instead.
  - ISSUE(broadcast-blocking): _broadcast() iterates all clients with sendall()
    under clients_lock. A slow/stalled client blocks broadcast to all others.
    Consider per-client send queues with async drain.
  - ISSUE(no-client-auth): Any TCP client can connect to :4001 or :4002 with
    no authentication. On a boat network this is usually acceptable, but
    :4002 (CTRL) allows firmware flashing — consider IP-based ACL.
  - ISSUE(init-race): serial_reader sends "YDNU MODE RAW" + "0\\n" but doesn't
    verify the device responded correctly. If the YDNU-02 is in an unexpected
    state, the initialization may silently fail.
  - ISSUE(frame-ordering): Frames from serial and virtual devices arrive on
    different threads. No global ordering guarantee exists, though N2K does
    not require strict ordering for most PGNs.
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

# ── Frame format regexes ──────────────────────────────────────────────────────
#
# YDNU-02 outputs N2K frames in ASCII CAN_FRAME format. Two patterns exist:

# RX format: full timestamp + direction + CAN ID + data bytes.
# Example: "00:12:34.567 R 19FD08C8 5F 00 02 5D 7F FF FF 00\n"
# Only lines matching this regex are broadcast to TCP clients.
# Non-matching lines (init echoes, "MODE RAW", text responses) are silently discarded.
_NMEA_LINE_RE = re.compile(
    rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"
)

# TX format: CAN ID + data bytes, no timestamp, no direction marker.
# Example: "19FD08C8 5F 00 02 5D 7F FF FF 00\r\n"
# Used by nmea2000 library N2KDevice when sending frames to port :4001.
# The gateway converts TX→RX format via _fmt_frame() before broadcasting.
_TX_LINE_RE = re.compile(
    rb"^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r?\n$"
)

# ── Configuration (env vars) ──────────────────────────────────────────────────
#
# All settings are configurable via environment variables with sensible defaults.
# Override in ydnu02-tcp-gateway.service [Service] Environment= directives.

SERIAL_PORT = os.getenv("NMEA_SERIAL_PORT", "/dev/ttyACM0")
"""Path to the YDNU-02 serial device. Default: /dev/ttyACM0 (USB CDC ACM)."""

SERIAL_BAUD = int(os.getenv("NMEA_SERIAL_BAUD", "115200"))
"""Serial baud rate. YDNU-02 operates at 115200 baud in both RAW and service modes."""

TCP_HOST    = os.getenv("NMEA_PROXY_HOST", "")
"""TCP bind address. Empty string = bind to all interfaces (0.0.0.0).
TODO(security): Consider defaulting to '127.0.0.1' for localhost-only access."""

DATA_PORT   = int(os.getenv("NMEA_PROXY_PORT", "4001"))
"""TCP port for the DATA hub (bidirectional N2K frame bus)."""

CTRL_PORT   = int(os.getenv("NMEA_CTRL_PORT",  "4002"))
"""TCP port for the CTRL channel (exclusive service/firmware mode)."""

# ── Shared state ──────────────────────────────────────────────────────────────
#
# Module-level mutable state shared between threads.
# All access MUST be guarded by the corresponding lock.
#
# ISSUE(module-globals): Using module-level globals makes unit testing difficult
# (tests must patch module attributes). Consider refactoring into a Gateway class
# in the future.

# Data clients (port DATA_PORT)
clients: set = set()
"""Set of connected DATA client sockets. Protected by clients_lock."""
clients_lock = threading.Lock()
"""Guards the clients set. Acquired during broadcast and client connect/disconnect."""

# Serial instance — owned by serial_reader, temporarily replaced by ctrl handler
# during service mode (close for mode switch → reopen in service terminal mode).
# Always access under serial_lock. serial_reader adopts changes via its sleep loop.
serial_instance: serial.Serial | None = None
"""The active pyserial Serial object. None when disconnected or during mode switch.
INVARIANT: Only serial_reader creates new Serial instances (except during
_enter_service_mode_on_device which reopens after DTR toggle)."""
serial_lock = threading.Lock()
"""Guards serial_instance reads and writes. Must be held for any serial access."""

# Service/firmware mode flag — set while control client holds exclusive serial
service_mode = threading.Event()
"""Set when a CTRL client has exclusive serial access (SERVICE_START or FIRMWARE_START).
While set, serial_reader enters a sleep loop and does not read from serial.
DATA clients still receive frames from virtual N2K devices (TCP→TCP path)."""
service_conn: socket.socket | None = None
"""Active CTRL client socket. None when no control session is active."""
service_conn_lock = threading.Lock()
"""Guards service_conn assignment and service_mode transitions."""

# Signals that YDNU-02 has finished its init sequence and is ready for N2K frames.
# ISO Requests must NOT be sent before this is set — YDNU-02 will ignore them.
_serial_ready = threading.Event()
"""Set after serial_reader successfully initializes the YDNU-02 in RAW mode.
Cleared on serial disconnect/error. Used by _send_iso_request() to gate
ISO Request transmission until the device is ready."""


def _fmt_frame(can_id_hex: str, data: bytes) -> bytes:
    """Format raw CAN data as a YDNU-02 ASCII RX-format text line.

    Converts a CAN ID and raw data bytes into the standard YDNU-02 ASCII format
    that matches _NMEA_LINE_RE and can be broadcast to all TCP clients.

    Output format: ``00:00:00.000 R XXXXXXXX XX XX XX ...\\n``

    The timestamp is always ``00:00:00.000`` because the gateway does not track
    real CAN timestamps for synthetic frames. The direction is always ``R``
    (received) because from the perspective of TCP clients, all frames are
    "received" from the bus.

    Args:
        can_id_hex: 8-character hex CAN ID (e.g. "19FD08C8").
        data: Raw CAN data bytes (1-8 bytes for single frame, variable for FP).

    Returns:
        ASCII-encoded line matching _NMEA_LINE_RE, terminated with ``\\n``.

    Skill — build a synthetic ISO Address Claim frame::

        >>> _fmt_frame('18EEFF5C', b'\x01\x02\x03\x04\x05\x06\x07\x08')
        b'00:00:00.000 R 18EEFF5C 01 02 03 04 05 06 07 08\n'

    Skill — inject a test frame into the TCP hub via netcat::

        # Simulate a heartbeat frame from SA=92 (Gobius C):
        echo -ne '00:00:00.000 R 19F11160 5F 00 02 5D 7F FF FF 00\n' | nc <gateway-host> 4001

    TODO(timestamp): Consider using monotonic clock or NTP-synced timestamp
      for synthetic frames so consumers can correlate with real traffic.
    """
    return f'00:00:00.000 R {can_id_hex} {" ".join(f"{b:02X}" for b in data)}\n'.encode()



# ── Device frame cache ────────────────────────────────────────────────────────
#
# Per-SA storage of device identification frames for replay to new TCP clients.
# Pre-seeded with gateway synthetic frames; continuously updated from live traffic.
#
# Structure: {sa_int: {'iso_claim': bytes, 'product_info': [bytes, ...]}}
#
# Why cache? Home Assistant builds its N2K device list from ISO Address Claims and
# Product Information frames. These are only broadcast once (on device power-up or
# ISO Request). Without caching, HA would need to wait for a manual rescan or device
# restart to discover devices. The cache provides instant device discovery.
#
# ISSUE(stale-entries): Cache entries are never evicted. If a device is removed from
# the bus, its entry persists indefinitely. HA shows it as "offline" via heartbeat
# timeout, but the cache still replays its claim to new clients.

_device_frame_cache: dict[int, dict] = {}
"""Maps N2K Source Address (int) to cached identification frames.
Each entry has optional 'iso_claim' (bytes) and 'product_info' ([bytes])."""
_device_frame_lock  = threading.Lock()
"""Guards all reads and writes to _device_frame_cache."""

# Fast-packet reassembly buffer (PGN 126996 only).
# Holds in-progress multi-frame messages until all frames arrive.
#
# Fast-packet protocol (ISO 11783-3):
#   Frame 0: [counter_byte] [total_payload_len] [6 payload bytes]
#   Frame N: [counter_byte] [7 payload bytes]
#   counter_byte: bits 7-5 = sequence ID (wraps 0-7), bits 4-0 = frame number (0-31)
#
# ISSUE(fp-timeout): Partially received fast-packets are never cleaned up.
# If frame 0 arrives but subsequent frames are lost, _fp_buf grows unbounded.
# Consider adding a TTL or periodic cleanup for stale entries.
_fp_buf:  dict[int, dict] = {}   # {sa: {'seq': int, 'total': int, 'lines': [bytes]}}
"""In-progress fast-packet reassembly state per Source Address."""
_fp_lock  = threading.Lock()
"""Guards _fp_buf reads and writes."""


def _get_pgn_sa(can_id: bytes | str) -> tuple[int, int]:
    """Decode (PGN, SourceAddress) from 8-char hex CAN ID in YDNU-02 RAW format.

    CAN ID layout (29-bit extended, zero-padded to 32-bit hex):
    ::

        Bits 31-29: unused (always 0 in 29-bit CAN)
        Bits 28-26: Priority (0-7, lower = higher priority)
        Bit  25:    Reserved (always 0 for N2K)
        Bit  24:    DP (Data Page, extends PGN space)
        Bits 23-16: PF (PDU Format — determines PDU1 vs PDU2)
        Bits 15-8:  PS (PDU Specific)
        Bits  7-0:  SA (Source Address)

    PDU routing:
      - PF >= 240 (PDU2, broadcast): PS = Group Extension → part of PGN
      - PF <  240 (PDU1, addressed): PS = Destination Address → NOT part of PGN

    Examples:
      - ``19FD08C8`` → PGN=127752 (0x1FD08), SA=200 (0xC8)
      - ``18EAFFFE`` → PGN=59904  (0xEA00+0xFF), SA=254

    Args:
        can_id: 8-character hex CAN ID as bytes or str (e.g. "19FD08C8").

    Returns:
        Tuple of (pgn: int, source_address: int).

    Raises:
        ValueError: If can_id is not a valid hex string.
        IndexError: If can_id is too short.

    Skill — quick decode from live traffic::

        >>> _get_pgn_sa('09FD0260')     # Gobius fluid level, SA=96
        (64770, 96)
        >>> _get_pgn_sa('19F11160')     # Heartbeat PGN 126993, SA=96
        (126993, 96)
        >>> _get_pgn_sa('18EEFF5C')     # ISO Address Claim, SA=92 (Gobius C)
        (60928, 92)

    Skill — decode CAN IDs from raw YDNU-02 log via bash::

        # Extract all unique PGN numbers from a live stream:
        nc <gateway-host> 4001 | awk '{print $3}' | sort -u | while read cid; do
          python3 -c "from ydnu02_tcp_gateway import _get_pgn_sa; print(f'{cid} → PGN={_get_pgn_sa(\"'$cid'\")[0]}')"
        done
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

    Product Information (PGN 126996) uses fast-packet transport because the
    payload (134 bytes) exceeds the single CAN frame limit of 8 bytes.

    Fast-packet reassembly:
      - Frame 0: counter byte (seq=3bits, frame_num=5bits) + total payload
        length byte + 6 payload bytes.
      - Frames 1-N: counter byte + 7 payload bytes each.
      - Complete when received_bytes >= total_payload_length.

    On successful reassembly, all frames are stored in _device_frame_cache
    as a list of raw line bytes (to be replayed verbatim to new clients).

    Incomplete packets (missing frames, sequence mismatch) are silently discarded.
    A new frame 0 with a different sequence ID replaces any in-progress reassembly.

    Args:
        sa: Source Address of the transmitting device (0-253).
        line: Raw ASCII line bytes matching _NMEA_LINE_RE.

    ISSUE(interleaving): If two devices with the same SA transmit simultaneously
      (shouldn't happen per N2K spec, but possible with misconfigured devices),
      fast-packet reassembly will produce corrupt data.

    TODO(multi-pgn): Only PGN 126996 is reassembled. Other fast-packet PGNs
      (e.g. 128275 Distance Log) are not cached. Extend if needed.
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

def _broadcast(line: bytes, exclude: socket.socket | None = None) -> None:
    """Send a line to all DATA clients, updating the device frame cache.

    This is the core fanout function. Every N2K frame that should be visible
    to TCP consumers passes through here.

    Cache update logic:
      - PGN 60928 (ISO Address Claim): single CAN frame, overwrites per SA.
        This is the primary device identification frame in NMEA 2000.
      - PGN 126996 (Product Info): fast-packet, delegated to
        _cache_product_info_frame() for multi-frame reassembly.

    Broadcast semantics:
      - ``exclude=None``: send to ALL connected clients (serial_reader path).
      - ``exclude=conn``: send to all clients EXCEPT conn (hub self-echo prevention).

    Dead client cleanup:
      If sendall() raises OSError (broken pipe, connection reset), the client
      is immediately removed from the clients set within the same lock scope.

    Args:
        line: Raw ASCII line bytes to broadcast (must match _NMEA_LINE_RE).
        exclude: Optional socket to skip (the sender in hub mode).

    Skill — monitor live broadcast traffic from the outside::

        # Connect as a DATA client and watch all frames in real time:
        nc <gateway-host> 4001

        # Same with socat (auto-reconnect on disconnect):
        socat - TCP:<gateway-host>:4001

        # Count frames per second:
        nc <gateway-host> 4001 | pv -l > /dev/null

    Skill — verify cache is being updated (check device count)::

        # After connecting, cached frames are replayed immediately.
        # Count unique SAs in the first 2 seconds of output:
        timeout 2 nc <gateway-host> 4001 | awk '{print $3}' | \
          python3 -c "import sys; from ydnu02_tcp_gateway import _get_pgn_sa; \
          sas=set(); [sas.add(_get_pgn_sa(l.strip())[1]) for l in sys.stdin]; \
          print(f'Devices: {len(sas)}, SAs: {sorted(sas)}')"

    ISSUE(blocking-sendall): sendall() can block if a client's TCP buffer is full.
      This holds clients_lock and blocks broadcast to ALL other clients.
      Mitigation: clients should drain their buffers fast (HA does). But a
      pathological client (e.g. frozen Signal K) could stall the entire pipeline.

    TODO(send-queue): Consider per-client asyncio queues with backpressure and
      configurable drop policy (e.g. drop oldest frame if queue > 1000).
    """
    # Update device frame cache from live N2K traffic (serial OR virtual device)
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
            if conn is exclude:
                continue
            try:
                conn.sendall(line)
            except OSError:
                dead.add(conn)
        clients.difference_update(dead)


def _replay_device_frames(conn: socket.socket) -> None:
    """Replay cached device identification frames to a newly connected client.

    Sends ISO Address Claims and Product Info for all known N2K devices,
    ordered by Source Address. This allows HA to immediately populate its
    device list without waiting for the next periodic announcement.

    Snapshot isolation: Takes a copy of the cache under lock, then sends
    without holding the lock. This prevents blocking the serial_reader
    during potentially slow TCP sends.

    If the client disconnects during replay (OSError), sending stops early
    and the client will be cleaned up by the next _broadcast() call.

    Args:
        conn: The newly connected TCP client socket.

    TODO(selective-replay): Could add a "last seen SA" filter so reconnecting
      clients don't receive frames they already have.
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
"""Guards _iso_request_last_sent timestamp for rate limiting."""

_iso_request_last_sent: float = 0.0
"""Monotonic timestamp of last ISO Request transmission."""

_ISO_REQUEST_MIN_INTERVAL = 5.0
"""Minimum seconds between ISO Request transmissions.
Prevents bus flooding when multiple clients connect simultaneously."""


def _send_iso_request() -> None:
    """Best-effort: transmit ISO Request (PGN 59904) via YDNU-02 RAW TX.

    Sends two ISO Requests to the N2K bus via serial:
      1. PGN 60928 request (ISO Address Claim) — ``00 EE 00``
      2. PGN 126996 request (Product Info) — ``14 F0 01``

    Both requests are addressed to destination 0xFF (global) from SA 0xFE
    (cannot claim), requesting all devices on the bus to respond.

    CAN ID breakdown for ISO Request:
      ``18EAFFFE`` = Priority 6 | PF=0xEA (PGN 59904) | PS=0xFF (global) | SA=0xFE

    Rate limiting: Only one request set per ``_ISO_REQUEST_MIN_INTERVAL`` seconds.
    Subsequent calls within the interval are silently dropped.

    Serial readiness: Waits up to 10s for ``_serial_ready`` event before sending.
    If YDNU-02 hasn't completed initialization, the request is skipped.

    Service mode: Skipped entirely if a CTRL client has exclusive serial access.

    The requests are also broadcast to TCP clients so virtual N2K devices
    (like our own SA=200 gateway device) receive them and respond with their
    Address Claim and Product Information frames via the bidirectional hub.

    Skill — manually trigger ISO Request rescan from a remote machine::

        # Simply open a new TCP connection — the gateway auto-sends ISO Requests:
        nc <gateway-host> 4001
        # Within ~1s you'll see ISO Address Claims (PGN 60928) from all devices.

    Skill — send a raw ISO Request for a specific PGN via TCP::

        # Request PGN 127505 (Fluid Level) from all devices:
        # PGN 127505 = 0x01F211 → little-endian 3 bytes: 11 F2 01
        echo -ne '18EAFFFE 11 F2 01\r\n' | nc <gateway-host> 4001

    Skill — decode the ISO Request payload::

        # ISO Request carries requested PGN as 3 LE bytes:
        # 00 EE 00 → PGN 0x00EE00 = 60928 (ISO Address Claim)
        # 14 F0 01 → PGN 0x01F014 = 126996 (Product Information)
        python3 -c "print(int.from_bytes(bytes.fromhex('00EE00'), 'little'))"  # → 60928
        python3 -c "print(int.from_bytes(bytes.fromhex('14F001'), 'little'))"  # → 126996

    ISSUE(two-writes): The two serial.write() calls are not atomic. If the serial
      port errors between them, only the first request is sent. Consider combining
      into a single write with both frames.

    TODO(configurable-interval): _ISO_REQUEST_MIN_INTERVAL is hardcoded. Consider
      making it configurable via env var for networks with many/few devices.
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
    frame_claim = b"00:00:00.000 T 18EAFFFE 00 EE 00\r\n"
    frame_prod  = b"00:00:00.000 T 18EAFFFE 14 F0 01\r\n"
    try:
        ser.write(frame_claim)
        ser.write(frame_prod)
        print("[data] ISO Requests (Address Claim + Product Info) TX sent to serial", flush=True)
    except serial.SerialException as e:
        print(f"[data] ISO Request TX error: {e}", flush=True)

    # Broadcast ISO Requests to TCP data clients so virtual devices (SA=200)
    # receive them and respond with their Address Claim (60928) & Product Info (126996).
    tcp_iso_req_claim = _fmt_frame('18EAFFFE', b'\x00\xee\x00')
    tcp_iso_req_prod  = _fmt_frame('18EAFFFE', b'\x14\xf0\x01')
    _broadcast(tcp_iso_req_claim)
    _broadcast(tcp_iso_req_prod)


def handle_data_client(conn: socket.socket, addr) -> None:
    """Handle a single DATA port client connection (bidirectional N2K bus hub).

    Lifecycle:
      1. Register in clients set → starts receiving broadcast frames.
      2. Replay cached device frames (ISO Claims + Product Info).
      3. Send ISO Requests to trigger device re-announcements.
      4. Enter receive loop: read frames from client, forward to other clients.

    Bidirectional hub rules:
      - Client-sent frames in RX format (_NMEA_LINE_RE): forward to all OTHER clients.
      - Client-sent frames in TX format (_TX_LINE_RE): convert to RX, forward to others.
      - ISO Requests (PGN 59904) from TCP: also forward to serial (physical bus).
      - All other TX frames: TCP-only (not forwarded to serial).
      - Frames from serial: handled by serial_reader → _broadcast(line) → all clients.

    Self-echo prevention: ``_broadcast(line, exclude=conn)`` ensures the sender
    does not receive its own frame back.

    ISO Request forwarding rationale:
      When the N2KDevice library handles an ISO Request response, it sends
      PGN 59904 to port 4001. We must forward this to serial so physical
      devices on the CAN bus also receive the request and respond.
      Other TX frames (ISO Claims from SA=200, etc.) are virtual-only.

    Args:
        conn: Connected TCP client socket.
        addr: Client address tuple (ip, port).

    Skill — connect as a DATA client and read N2K frames (Python)::

        import socket
        s = socket.create_connection(('<gateway-host>', 4001))
        while True:
            line = b''
            while not line.endswith(b'\n'):
                line += s.recv(1)
            print(line.decode().strip())

    Skill — send a virtual ISO Address Claim from an external script::

        import socket
        s = socket.create_connection(('<gateway-host>', 4001))
        # ISO Address Claim for SA=99 (PGN 60928, CAN ID 18EEFF63):
        s.sendall(b'18EEFF63 01 02 03 04 05 06 07 08\r\n')
        # The hub converts TX→RX format and broadcasts to all other clients.

    Skill — test bidirectional hub with two terminals::

        # Terminal 1 (listener): nc <gateway-host> 4001
        # Terminal 2 (sender):   echo -ne '18EAFFFE 00 EE 00\r\n' | nc <gateway-host> 4001
        # Terminal 1 will see the frame; Terminal 2 will not (self-echo prevention).

    ISSUE(buffer-accumulation): The buf variable accumulates data until a \\n is
      found. If a client sends a large amount of data without newlines (e.g.
      binary data), buf grows unbounded. Consider adding a max buffer size.

    TODO(client-identification): Log the client purpose (HA, Signal K, ydnu02-web)
      based on connection pattern or initial handshake for better diagnostics.
    """
    print(f"[data] client connected: {addr}", flush=True)
    with clients_lock:
        clients.add(conn)

    _replay_device_frames(conn)
    _send_iso_request()

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

                if _NMEA_LINE_RE.match(line):
                    # Already full RX format (HH:MM:SS.mmm R CANID ...)
                    _broadcast(line, exclude=conn)   # cache + forward to others

                elif _TX_LINE_RE.match(raw):
                    # YDNU-02 TX format from virtual N2KDevice or bus monitor (no timestamp).
                    # Convert to RX format so it can be cached and forwarded to TCP clients.
                    parts = raw.rstrip(b'\r\n').split(b' ')
                    can_id  = parts[0]
                    data_bz = bytes(int(b, 16) for b in parts[1:] if b)
                    rx_line = _fmt_frame(can_id.decode(), data_bz)
                    _broadcast(rx_line, exclude=conn)

                    # ISO Request (PGN 59904): forward to serial so physical devices respond.
                    # Other TX frames (ISO Claims, Product Info) are virtual-only — stay in hub.
                    try:
                        pgn, _ = _get_pgn_sa(can_id)
                        if pgn == 59904 and not service_mode.is_set():
                            with serial_lock:
                                if serial_instance and serial_instance.is_open:
                                    serial_instance.write(raw)
                            print(f"[data] ISO Request forwarded to serial", flush=True)
                    except (ValueError, IndexError):
                        pass

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
    """Send a line-terminated UTF-8 message to the CTRL client.

    Fire-and-forget: if the socket is broken, the error is silently ignored.
    The caller (handle_ctrl_client) will detect the disconnect on the next recv().

    Args:
        conn: CTRL client socket.
        msg: Message string (without trailing newline — added automatically).
    """
    try:
        conn.sendall((msg + "\n").encode())
    except OSError:
        pass


def _enter_service_mode_on_device() -> None:
    """Switch YDNU-02 from RAW mode to interactive service terminal mode.

    This is the most delicate operation in the gateway. The YDNU-02 requires
    a DTR low→high transition to accept the "YDNU MODE SERVICE" command.
    Simply writing the command to an already-open serial port is SILENTLY IGNORED.

    Sequence:
      1. Close serial port (DTR drops to low).
      2. ``stty -F /dev/ttyACM0 hupcl`` — ensures DTR follows port state.
      3. ``echo "YDNU MODE SERVICE" > /dev/ttyACM0`` — OS opens port (DTR rises),
         writes command, closes port. The open triggers the DTR transition.
      4. Sleep 1.5s — YDNU-02 needs time to process the mode switch.
      5. Reopen serial port with dsrdtr=True, dtr=True.
      6. Flush any pending input (welcome banner, echoed command).
      7. Store new Serial instance for ctrl handler to use.

    The ctrl client (ydnu02.py ProxyControlClient) does NOT send "YDNU MODE SERVICE".
    It only reads the welcome banner and then sends interactive commands.

    ISSUE(platform): ``stty -F`` is Linux-specific. On macOS, use ``stty -f``.
      This module only runs on Raspberry Pi 5, so this is acceptable.

    ISSUE(race): Between serial.close() and the OS echo, another process could
      open the serial port and intercept the DTR transition. Mitigated by the
      KEY INVARIANT (only this process opens /dev/ttyACM0).

    ISSUE(timeout): No timeout on the subprocess.run() calls (only capture_output
      timeout=5). If the serial port is stuck, the gateway thread blocks.

    TODO(verification): After reopening, could verify that the YDNU-02 is actually
      in service mode by reading and checking the welcome banner.
    """
    global serial_instance

    with serial_lock:
        _ser = serial_instance
        serial_instance = None

    if _ser and _ser.is_open:
        try:
            _ser.close()
        except serial.SerialException:
            pass
    print("[ctrl] serial closed for service mode entry", flush=True)

    subprocess.run(
        ["stty", "-F", SERIAL_PORT, "hupcl"],
        capture_output=True, timeout=5
    )
    subprocess.run(
        f'echo "YDNU MODE SERVICE" > {SERIAL_PORT}',
        shell=True, capture_output=True, timeout=5
    )
    time.sleep(1.5)
    print("[ctrl] YDNU MODE SERVICE sent via OS echo", flush=True)

    new_ser = serial.Serial(
        SERIAL_PORT, SERIAL_BAUD, timeout=2.0, dsrdtr=True, rtscts=False
    )
    new_ser.dtr = True
    time.sleep(0.2)
    if new_ser.in_waiting:
        new_ser.read(new_ser.in_waiting)

    with serial_lock:
        serial_instance = new_ser

    print("[ctrl] YDNU-02 in service terminal mode — serial reopened", flush=True)


def _exit_service_mode_on_device() -> None:
    """Switch YDNU-02 from service terminal back to RAW mode.

    Sends ``MODE RAW\\r\\n`` to the service terminal, which is the command
    to exit service mode and resume N2K frame output.

    Unlike entering service mode, exiting does NOT require a DTR toggle.
    The command is accepted over the already-open serial port.

    After sending the command:
      - 0.5s sleep for the YDNU-02 to process.
      - Timeout reduced to 0.1s for immediate serial_reader resumption.

    The serial_reader thread will detect that service_mode is cleared and
    resume its normal read loop on the same serial_instance.

    ISSUE(no-ack): No verification that MODE RAW was accepted. If the YDNU-02
      doesn't switch back, the serial_reader will read service terminal responses
      as CAN frames (they won't match _NMEA_LINE_RE, so they'll be silently
      discarded — but no N2K data will flow).

    TODO(force-restart): If MODE RAW fails, consider a full serial port close/reopen
      cycle (same as _enter_service_mode_on_device) to force a mode reset.
    """
    with serial_lock:
        _ser = serial_instance

    if _ser and _ser.is_open:
        try:
            _ser.write(b"MODE RAW\r\n")
            time.sleep(0.5)
            _ser.timeout = 0.1
        except serial.SerialException as e:
            print(f"[ctrl] error during service exit: {e}", flush=True)

    print("[ctrl] YDNU-02 switched back to RAW mode", flush=True)


def handle_ctrl_client(conn: socket.socket, addr) -> None:
    """Handle a single CTRL port client connection (exclusive service/firmware mode).

    Only ONE control session is allowed at a time. If a second client connects
    while a session is active, it receives "ERROR: another control session is active"
    and is immediately disconnected.

    Protocol state machine:
    ::

        IDLE ──SERVICE_START──► SERVICE (serial_reader paused, DTR toggled)
          │                        │
          │                        ├── <cmd>\\n → serial.write(cmd) → serial.read() → client
          │                        │
          │                        └── SERVICE_END → MODE RAW → IDLE
          │
          └──FIRMWARE_START──► FIRMWARE (serial_reader paused, raw passthrough)
                                   │
                                   ├── <data> → serial.write(data) → serial.read() → client
                                   │
                                   └── FIRMWARE_END → IDLE

    Cleanup on disconnect:
      If the client disconnects without sending SERVICE_END/FIRMWARE_END, the
      handler automatically calls _exit_service_mode_on_device() (if in SERVICE
      mode) and clears service_mode. This prevents the serial port from being
      stuck in service terminal mode indefinitely.

    Serial polling:
      During active service mode, the handler polls serial.in_waiting every 100ms
      (via conn.settimeout(0.1) recv timeout). This provides near-realtime
      response forwarding without busy-waiting.

    Args:
        conn: Connected CTRL client socket.
        addr: Client address tuple (ip, port).

    Skill — enter YDNU-02 service mode via netcat (interactive)::

        # Open a raw TCP session to the CTRL port:
        nc <gateway-host> 4002
        # Type:  SERVICE_START  (press Enter)
        # Wait:  gateway responds with "READY"
        # Type:  SHOW ALL       (any YDNU-02 service command)
        # Read:  device response lines
        # Type:  SERVICE_END    (press Enter)
        # Wait:  gateway responds with "OK"

    Skill — enter service mode programmatically (Python)::

        import socket, time
        s = socket.create_connection(('<gateway-host>', 4002))
        s.sendall(b'SERVICE_START\n')
        time.sleep(2)  # wait for DTR toggle + READY
        resp = s.recv(1024)  # b'READY\n'
        s.sendall(b'SHOW ALL\n')
        time.sleep(0.5)
        print(s.recv(4096).decode())  # device info
        s.sendall(b'SERVICE_END\n')
        time.sleep(0.5)
        print(s.recv(1024).decode())  # b'OK\n'
        s.close()

    Skill — check if service mode is currently active::

        # Try connecting — if another session is active you get:
        echo '' | nc <gateway-host> 4002
        # Output: "ERROR: another control session is active"

    Skill — emergency recovery if service mode is stuck::

        # If the ctrl client crashed without sending SERVICE_END,
        # connect and immediately disconnect — the cleanup handler fires:
        nc -w1 <gateway-host> 4002 < /dev/null
        # Or restart the gateway service:
        ssh user@gateway-host sudo systemctl restart ydnu02-tcp-gateway

    ISSUE(single-threaded-polling): Serial read and client recv share the same
      thread via timeout-based multiplexing. A burst of serial output could be
      delayed by up to 100ms if recv() is waiting. Consider using select() or
      separate threads.

    TODO(session-timeout): No timeout for inactive sessions. A client that sends
      SERVICE_START but never sends commands or SERVICE_END blocks the CTRL port
      forever. Consider adding a configurable idle timeout.
    """
    global service_conn
    print(f"[ctrl] client connected: {addr}", flush=True)

    with service_conn_lock:
        if service_mode.is_set():
            _ctrl_send(conn, "ERROR: another control session is active")
            conn.close()
            return
        service_conn = conn

    ctrl_mode: str | None = None   # "SERVICE" | "FIRMWARE"

    try:
        conn.settimeout(0.1)
        buf = b""

        while True:
            try:
                chunk = conn.recv(256)
            except socket.timeout:
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

                    time.sleep(0.15)

                    if cmd == "SERVICE_START":
                        _enter_service_mode_on_device()
                    else:
                        with serial_lock:
                            _ser = serial_instance
                        if _ser and _ser.is_open:
                            _ser.reset_input_buffer()

                    _ctrl_send(conn, "READY")

                elif cmd in ("SERVICE_END", "FIRMWARE_END"):
                    if ctrl_mode == "SERVICE":
                        _exit_service_mode_on_device()

                    service_mode.clear()
                    ctrl_mode = None
                    print(f"[ctrl] {cmd} — broadcast resumed", flush=True)
                    _ctrl_send(conn, "OK")

                elif service_mode.is_set():
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
        with service_conn_lock:
            if service_conn is conn:
                if service_mode.is_set():
                    if ctrl_mode == "SERVICE":
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
    """Serial→TCP: own the serial port, read lines, broadcast to data clients.

    This is the main I/O loop of the gateway. It exclusively owns the serial
    port (/dev/ttyACM0) and reads CAN frames continuously.

    Initialization sequence on each serial connect:
      1. Open serial port at SERIAL_BAUD (115200), dsrdtr=True, dtr=True.
      2. Send ``YDNU MODE RAW\\r\\n`` to ensure YDNU-02 is in N2K frame mode.
      3. Wait 2.0s for the YDNU-02 to process and start outputting frames.
      4. Read any init data and cache device frames found in it.
      5. Send ``0\\n`` (empty raw frame) to reset any pending YDNU-02 state.
      6. Wait 0.5s, read and cache any additional init frames.
      7. Set ``_serial_ready`` and send initial ISO Requests.

    Main loop:
      - If service_mode is set: yield to CTRL handler, poll serial_instance
        every 50ms for ownership changes (CTRL handler may replace it).
      - Otherwise: readline() with 100ms timeout, filter with _NMEA_LINE_RE,
        broadcast matching lines via _broadcast().

    Serial adoption pattern:
      The CTRL handler may close and reopen the serial port during service mode
      (_enter_service_mode_on_device). When service_mode clears, serial_reader
      detects that serial_instance has changed and adopts the new Serial object.

    Error recovery:
      On SerialException (USB disconnect, permission error), the serial port
      is closed, _serial_ready is cleared, and the loop retries after 5s.
      This handles hot-plug of the YDNU-02 USB device.

    Skill — check if serial_reader is running and YDNU-02 is ready::

        # Check service status:
        ssh user@gateway-host systemctl status ydnu02-tcp-gateway

        # Grep gateway logs for serial init:
        ssh user@gateway-host journalctl -u ydnu02-tcp-gateway -n 20 --no-pager
        # Look for: "[serial] opened /dev/ttyACM0 @ 115200"
        #           "[serial] YDNU-02 initialized in RAW mode"

    Skill — verify frames are flowing from serial → TCP::

        # Connect and count frames for 5 seconds:
        timeout 5 nc <gateway-host> 4001 | wc -l
        # Expected: 50-200 lines (depends on N2K bus activity).
        # 0 lines = serial not reading or YDNU-02 not in RAW mode.

    Skill — manually initialize YDNU-02 via direct serial (debugging only!)::

        # DANGER: only use if gateway service is stopped!
        ssh user@gateway-host
        sudo systemctl stop ydnu02-tcp-gateway
        # Open minicom or screen:
        screen /dev/ttyACM0 115200
        # Type: YDNU MODE RAW  (Enter)
        # You should see CAN frames scrolling.
        # Ctrl+A, K to exit screen.
        sudo systemctl start ydnu02-tcp-gateway

    ISSUE(init-unchecked): The initialization sends MODE RAW and "0" but doesn't
      verify the YDNU-02 responded correctly. If the device is in firmware update
      mode or a corrupted state, init may silently fail.

    ISSUE(busy-wait-service): During service mode, time.sleep(0.05) runs 20×/sec.
      This is wasted CPU. Use service_mode.wait() with a timeout instead:
      ``service_mode.wait(timeout=0.05)`` returns immediately if the event clears.

    ISSUE(readline-blocking): serial.readline() with timeout=0.1 means each
      empty read cycle takes up to 100ms. This is the effective maximum latency
      for processing a frame after it arrives on the serial port. For most N2K
      applications this is acceptable (frames arrive at CAN bus rate, not serial).

    TODO(watchdog): Add a watchdog timer that detects if no frames have been
      received for >30s (possible YDNU-02 hang or USB disconnect without error).
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
                    elif pgn == 126996:
                        _cache_product_info_frame(sa, line)
                except (ValueError, IndexError):
                    pass

            _serial_ready.set()
            _send_iso_request()

            while True:
                if service_mode.is_set():
                    time.sleep(0.05)
                    with serial_lock:
                        current = serial_instance
                    if current is not None and current is not ser:
                        ser = current
                    continue

                raw = ser.readline()
                if not raw:
                    continue

                line = raw.rstrip(b"\r\n") + b"\n"
                if not _NMEA_LINE_RE.match(line):
                    continue

                _broadcast(line)

        except serial.SerialException as e:
            print(f"[serial] error: {e} — retrying in 5s", flush=True)
            _serial_ready.clear()
            with serial_lock:
                serial_instance = None
            service_mode.clear()
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
    """Create and bind a TCP server socket with SO_REUSEADDR.

    SO_REUSEADDR allows immediate rebind after gateway restart (avoids
    "Address already in use" during the TIME_WAIT period).

    Backlog of 5 connections (default for listen()). This is sufficient for
    the expected client count (HA + Signal K + ydnu02-web + N2KDevice = 4).

    Args:
        port: TCP port number to bind to.

    Returns:
        Bound and listening socket.socket.

    ISSUE(no-timeout): accept() on the returned socket blocks indefinitely.
      This is fine for the main thread (_accept_loop), but makes graceful
      shutdown impossible without closing the socket from another thread.

    TODO(dual-stack): Consider adding IPv6 support with socket.AF_INET6
      and IPV6_V6ONLY=False for dual-stack operation.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((TCP_HOST, port))
    srv.listen(5)
    return srv


def _accept_loop(srv: socket.socket, handler, label: str) -> None:
    """Accept TCP connections in a loop, spawning a handler thread per client.

    Each accepted connection gets its own daemon thread running the provided
    handler function. This is the standard thread-per-client model.

    Exit conditions:
      - KeyboardInterrupt: clean shutdown on Ctrl+C (main thread only).
      - OSError: socket closed by another thread (graceful shutdown).

    Args:
        srv: Listening server socket from _make_server().
        handler: Callable(conn, addr) → None for each client.
        label: Human-readable label for log messages (unused currently).

    ISSUE(thread-leak): No maximum thread count. Each client spawns a new thread.
      A DoS attack with rapid connect/disconnect could exhaust system resources.
      Mitigated in practice by the small number of expected clients on a boat LAN.

    TODO(thread-pool): Consider using concurrent.futures.ThreadPoolExecutor with
      a bounded pool size for better resource control.

    TODO(label-unused): The label parameter is accepted but never used. Either
      use it in log messages or remove it.
    """
    while True:
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=handler, args=(conn, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except OSError:
            break



def main() -> None:
    """Gateway entry point: start all subsystems and enter the main accept loop.

    Startup order (critical for dependencies):
      1. serial_reader thread — opens /dev/ttyACM0, begins reading N2K frames.
      2. Gateway N2K device thread — waits 5s, then connects to port 4001.
         Depends on: DATA server must be accepting connections.
      3. DATA server (port 4001) — accepts HA, Signal K, ydnu02-web, N2KDevice.
      4. CTRL server (port 4002) — accepts ydnu02-web admin interface.
      5. Main thread enters DATA accept loop (blocks here until shutdown).

    Shutdown:
      KeyboardInterrupt (Ctrl+C) in the main accept loop triggers sys.exit(0).
      All daemon threads are terminated by the Python interpreter on exit.

    Skill — start the gateway manually for development::

        # On the Raspberry Pi (with YDNU-02 connected):
        cd /opt/nmea2000/ydnu02-web
        python3 -m ydnu02_tcp_gateway.ydnu02_tcp_gateway

        # With custom ports (e.g. to avoid conflict with production):
        NMEA_PROXY_PORT=4011 NMEA_CTRL_PORT=4012 python3 -m ydnu02_tcp_gateway.ydnu02_tcp_gateway

    Skill — deploy and restart via deploy.sh::

        # From the dev machine (macOS):
        ./deploy.sh user@gateway-host           # full deploy (proxy + web + HA patches)
        ./deploy.sh user@gateway-host --proxy    # proxy only (gateway + device)
        ./deploy.sh user@gateway-host --web      # web only (UI + backend)

    Skill — check all gateway components are healthy::

        ssh user@gateway-host
        systemctl status ydnu02-tcp-gateway   # gateway process
        systemctl status ydnu02-web           # web UI process
        ss -tlnp | grep -E '400[12]'          # ports 4001/4002 listening
        nc -z localhost 4001 && echo 'DATA OK' || echo 'DATA FAIL'
        nc -z localhost 4002 && echo 'CTRL OK' || echo 'CTRL FAIL'
        curl -s http://localhost:8080/api/devices | python3 -m json.tool | head

    Skill — view live gateway logs::

        ssh user@gateway-host journalctl -u ydnu02-tcp-gateway -f --no-pager
        # Key log patterns:
        #   [serial] opened /dev/ttyACM0     → serial port connected
        #   [cache] ISO Claim cached SA=X    → device discovered
        #   [data] client connected          → HA/Signal K/web connected
        #   [ctrl] SERVICE_START             → service mode entered
        #   [gwdev] Address claimed: SA=200  → virtual device online

    ISSUE(unclean-shutdown): No graceful shutdown sequence. Serial port is not
      explicitly closed, pending TCP sends are not drained, and the YDNU-02
      may be left in whatever mode it was in. On systemd restart, the next
      serial_reader will reinitialize the YDNU-02 in RAW mode.

    TODO(signal-handling): Register SIGTERM handler for systemd stop integration.
      Close serial port, drain clients, send "Cannot Claim" from N2KDevice.
    """
    # Import gateway device module (uses nmea2000 library's N2KDevice).
    # Deferred import so gateway can start without the library on non-target platforms.
    from ydnu02_gateway_device import start_in_thread as start_gw_device

    # Start serial reader thread
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()

    # Start virtual N2K device (SA=200: ISO Claim, Product Info, Heartbeat, CPU Temp).
    # Connects back to our own port 4001 after a 5s startup delay.
    # The library handles all frame encoding correctly via N2KDevice.for_text_gateway().
    start_gw_device()

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
