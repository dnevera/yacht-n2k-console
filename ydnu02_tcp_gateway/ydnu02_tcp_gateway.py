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
"""
import os
import re
import socket
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

# ISO Address Claim (PGN 60928) cache: keyed by source-address bytes (last 2 hex
# chars of the CAN ID field). Populated from startup serial data and live traffic.
# Replayed to every new TCP client so HA's nmea2000 decoder can build its network
# map immediately without needing to transmit an ISO Request on the N2K bus.
#
# CAN ID pattern for ISO Address Claim (PGN 60928):
#   Prio=6, R=0, DP=0, PF=0xEE, PS=0xFF (bcast), SA=varies
#   => 18EEFFxx in YD RAW format (first 6 chars of 8-char CAN ID = '18EEFF')
_iso_claim_cache: dict[bytes, bytes] = {}   # SA-hex-bytes → last YD_RAW line
_iso_claim_lock  = threading.Lock()
_ISO_CLAIM_RE    = re.compile(rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] 18[Ee]{2}[0-9A-Fa-f]{4}")

# ── Data port helpers ─────────────────────────────────────────────────────────

def _broadcast(line: bytes) -> None:
    """Send a line to all data clients, removing dead ones.
    
    Also caches ISO Address Claim lines (PGN 60928) for replay to new clients.
    """
    # Cache ISO Address Claims so new clients can receive them on connect
    if _ISO_CLAIM_RE.match(line):
        sa_key = line[21:23]   # last 2 hex chars of 8-char CAN ID = source address
        with _iso_claim_lock:
            _iso_claim_cache[sa_key] = line

    dead: set = set()
    with clients_lock:
        for conn in list(clients):
            try:
                conn.sendall(line)
            except OSError:
                dead.add(conn)
        clients.difference_update(dead)


def _replay_iso_claims(conn: socket.socket) -> None:
    """Send all cached ISO Address Claim lines to a newly connected client.

    Called immediately after a new data client registers, before normal data
    starts flowing. This primes HA's network map so that subsequent PGN decodes
    return non-None and sensor states update.

    ISO Address Claims are cached from:
      1. YDNU-02 startup data (captured during serial init)
      2. Live N2K traffic (devices send Claims on power-on or address conflict)
    """
    with _iso_claim_lock:
        claims = list(_iso_claim_cache.values())
    if not claims:
        print("[data] no cached ISO Claims to replay", flush=True)
        return
    sent = 0
    for claim in claims:
        try:
            conn.sendall(claim)
            sent += 1
        except OSError:
            break
    print(f"[data] replayed {sent}/{len(claims)} cached ISO Claim(s)", flush=True)


_iso_request_lock = threading.Lock()
_iso_request_last_sent: float = 0.0
_ISO_REQUEST_MIN_INTERVAL = 5.0  # seconds: don't flood bus with requests


def _send_iso_request() -> None:
    """Best-effort: transmit ISO Request (PGN 59904) via YDNU-02 RAW TX.

    Asks all N2K devices to broadcast their ISO Address Claim (PGN 60928).
    Effective only if YDNU-02 firmware supports TX in RAW mode.
    Primary mechanism is _replay_iso_claims() which works unconditionally.

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

    # 1. Replay cached ISO Address Claims so HA can build its network map immediately.
    #    Claims are cached from YDNU-02 startup data and live N2K traffic.
    #    Without this, HA's decoder (build_network_map=True) returns None for all
    #    messages and sensors stay Unavailable indefinitely.
    _replay_iso_claims(conn)

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
            if _ser.in_waiting:
                _ser.read(_ser.in_waiting)   # flush mode-switch response
            # Reset timeout from 2.0s (service terminal) to 0.1s (fast NMEA polling)
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
            # any ISO Address Claims the YDNU-02 or other devices sent at startup.
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

            # Parse startup data for ISO Address Claims and cache them.
            # YDNU-02 sends its own Claim (and may relay others) right after init.
            # These are the only Claims we'll see without an explicit ISO Request.
            for raw_line in init_data.split(b"\n"):
                if not raw_line:
                    continue
                line = raw_line.rstrip(b"\r") + b"\n"
                if _NMEA_LINE_RE.match(line) and _ISO_CLAIM_RE.match(line):
                    sa_key = line[21:23]
                    with _iso_claim_lock:
                        _iso_claim_cache[sa_key] = line
                    print(f"[serial] cached startup ISO Claim: "
                          f"{line.decode(errors='ignore').strip()}", flush=True)

            claimed = len(_iso_claim_cache)
            print(f"[serial] {claimed} ISO Claim(s) cached from startup data", flush=True)

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


def main() -> None:
    # Start serial reader thread
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()

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
