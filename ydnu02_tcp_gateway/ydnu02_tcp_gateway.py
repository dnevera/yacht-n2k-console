#!/usr/bin/env python3
"""
NMEA 2000 bidirectional TCP proxy for YDNU-02
==============================================
Data port  (NMEA_PROXY_PORT, default 4001):
    Serial → TCP: broadcasts \\n-terminated NMEA lines to all clients.
    TCP → Serial: forwards commands from clients to the bus.

Control port (NMEA_CTRL_PORT, default 4002):
    Single-client control API for service / firmware mode.
    Protocol:
        → SERVICE_START   pause broadcast; proxy switches YDNU-02 to service terminal
        ← READY
        ← <serial lines>  (passthrough: service terminal output from YDNU-02)
        → <commands>      (passthrough: service terminal commands forwarded to serial)
        → SERVICE_END     proxy switches YDNU-02 back to RAW; broadcast resumes
        ← OK

        → FIRMWARE_START  same as SERVICE_START but WITHOUT mode switch (raw passthrough)
        → FIRMWARE_END    same as SERVICE_END but WITHOUT mode switch

    Design — proxy as gateway:
        The YDNU-02 requires an OS-level DTR transition to enter service terminal mode.
        serial.write("YDNU MODE SERVICE") does NOT work while the port is held open.
        The proxy handles the full mode switch internally (close → stty hupcl →
        echo > port → reopen) so the ctrl client never needs to know about it.
        The ctrl client (ProxyControlClient) only sends/receives terminal commands.
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

# ── Data port helpers ─────────────────────────────────────────────────────────

def _broadcast(line: bytes) -> None:
    """Send a line to all data clients, removing dead ones."""
    dead: set = set()
    with clients_lock:
        for conn in list(clients):
            try:
                conn.sendall(line)
            except OSError:
                dead.add(conn)
        clients.difference_update(dead)


def handle_data_client(conn: socket.socket, addr) -> None:
    """Data port client: register for Serial→TCP broadcast, forward TCP→Serial."""
    print(f"[data] client connected: {addr}", flush=True)
    with clients_lock:
        clients.add(conn)
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

            # Initialize YDNU-02 into RAW mode.
            # serial.write() works here because we just opened a fresh connection
            # (DTR transitioned low→high). The device processes YDNU MODE RAW on startup.
            # "0\n" sets the filter to show-all (equivalent to PRINT GLOBAL_RX 0).
            ser.write(b"YDNU MODE RAW\r\n")
            time.sleep(2.0)
            if ser.in_waiting:
                ser.read(ser.in_waiting)   # flush mode-switch echo
            ser.write(b"0\n")
            time.sleep(0.5)
            if ser.in_waiting:
                ser.read(ser.in_waiting)   # flush filter echo
            print("[serial] YDNU-02 initialized in RAW mode", flush=True)

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
            with serial_lock:
                serial_instance = None
            service_mode.clear()   # safety: exit service mode on serial error
            time.sleep(5)
        except Exception as e:
            print(f"[serial] unexpected error: {e} — retrying in 5s", flush=True)
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
