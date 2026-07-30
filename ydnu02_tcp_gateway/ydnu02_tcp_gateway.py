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

GATEWAY VIRTUAL IDENTITY
  The gateway registers itself as a virtual N2K device (SA=200) so Home Assistant
  and Signal K can track its liveness.  All identity and telemetry is handled by
  ydnu02_gateway_device.py which uses the nmea2000 Python library's N2KDevice,
  connecting back to our own port 4001 (CAN_FRAME_ASCII, bidirectional hub).

  Port 4001 is a bidirectional N2K bus hub:
    serial reader → broadcast to all TCP clients
    TCP client frame → broadcast to all OTHER TCP clients (not to serial)
  This lets the N2KDevice's frames (ISO Claim, Product Info, HB, Temp) reach HA.
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

# YDNU-02 TX (outgoing) format: "XXXXXXXX XX XX ...\r\n" (no timestamp, no direction).
# Used by nmea2000 lib N2KDevice when sending frames to port :4001.
_TX_LINE_RE = re.compile(
    rb"^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r?\n$"
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


def _fmt_frame(can_id_hex: str, data: bytes) -> bytes:
    """Format raw CAN data as a YDNU-02 ASCII text line."""
    return f'00:00:00.000 R {can_id_hex} {" ".join(f"{b:02X}" for b in data)}\n'.encode()



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

def _broadcast(line: bytes, exclude: socket.socket | None = None) -> None:
    """Send a line to all data clients (optionally excluding one sender).

    Also updates the device frame cache for:
      PGN 60928  — ISO Address Claim (single frame, keyed by SA)
      PGN 126996 — Product Information (fast-packet, reassembled per SA)
    Cached frames are replayed to every new client on connect so HA can build
    its N2K network map without requiring a rescan.

    exclude: when set (e.g. the client that sent this frame), skip sending to it.
    Serial reader calls _broadcast(line) with exclude=None (send to all clients).
    handle_data_client calls _broadcast(line, exclude=conn) to avoid self-echo.
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

    Sends ISO Address Claims and Product Info for all known N2K devices.
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
    """Best-effort: transmit ISO Request (PGN 59904) via YDNU-02 RAW TX."""
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
        print("[data] ISO Request TX sent to serial (best-effort)", flush=True)
    except serial.SerialException as e:
        print(f"[data] ISO Request TX error: {e}", flush=True)

    # Also broadcast ISO Request to TCP data clients so virtual devices
    # (e.g. N2KDevice at SA=200) receive it and respond with their ISO Claim.
    # Format: R-type frame (incoming), SA=0xFE (null address, from gateway itself)
    tcp_iso_req = _fmt_frame('18EAFFFE', b'\x00\xee\x00')
    _broadcast(tcp_iso_req)


def handle_data_client(conn: socket.socket, addr) -> None:
    """Data port client: N2K bus hub (bidirectional TCP only, no serial forwarding)."""
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
    try:
        conn.sendall((msg + "\n").encode())
    except OSError:
        pass


def _enter_service_mode_on_device() -> None:
    """Switch YDNU-02 from RAW mode to interactive service terminal mode."""
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
    """Switch YDNU-02 from service terminal back to RAW mode."""
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
    """Control port client: single session for service/firmware mode."""
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
    """Serial→TCP: own the serial port, read lines, broadcast to data clients."""
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



def main() -> None:
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
