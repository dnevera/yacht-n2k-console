#!/usr/bin/env python3
"""
NMEA 2000 bidirectional TCP proxy for YDNU-02
==============================================
Data port  (NMEA_PROXY_PORT, default 4001):
    Serial → TCP: broadcasts \\n-terminated NMEA lines to all clients.
    TCP → Serial: forwards commands from clients to the bus.

Control port (NMEA_CTRL_PORT, default 4002):
    Single-client control API for service / firmware mode.
    Commands: SERVICE_START | SERVICE_END | FIRMWARE_START | FIRMWARE_END
    In service/firmware mode the data broadcast is paused and the control
    client gets exclusive serial passthrough.
"""
import os
import socket
import serial
import threading
import sys
import time
import re

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

# Serial instance — owned by serial_reader thread
serial_instance: serial.Serial | None = None
serial_lock = threading.Lock()

# Service/firmware mode flag — set while control client holds exclusive serial
service_mode = threading.Event()          # set  → serial in exclusive mode
service_conn: socket.socket | None = None # control client socket
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


def handle_ctrl_client(conn: socket.socket, addr) -> None:
    """
    Control port client: single session for service/firmware mode.
    Protocol (line-oriented, UTF-8):
        → SERVICE_START      pause broadcast, enter passthrough
        ← READY
        ← <serial lines...>  (passthrough from serial)
        → <commands...>      (forwarded to serial)
        → SERVICE_END
        ← OK

        → FIRMWARE_START     same as SERVICE_START (alias)
        → FIRMWARE_END       same as SERVICE_END
    """
    global service_conn
    print(f"[ctrl] client connected: {addr}", flush=True)

    with service_conn_lock:
        if service_mode.is_set():
            _ctrl_send(conn, "ERROR: another control session is active")
            conn.close()
            return
        service_conn = conn

    try:
        conn.settimeout(2.0)
        buf = b""

        while True:
            # Read one command line
            try:
                chunk = conn.recv(256)
            except socket.timeout:
                # In passthrough: push any available serial data to client
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
                    service_mode.set()
                    print(f"[ctrl] {cmd} — broadcast paused", flush=True)
                    _ctrl_send(conn, "READY")

                elif cmd in ("SERVICE_END", "FIRMWARE_END"):
                    service_mode.clear()
                    print(f"[ctrl] {cmd} — broadcast resumed", flush=True)
                    _ctrl_send(conn, "OK")

                elif service_mode.is_set():
                    # Passthrough: forward raw command bytes to serial
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
        # Always exit service mode when control client disconnects
        if service_mode.is_set():
            service_mode.clear()
            print("[ctrl] session ended — broadcast resumed", flush=True)
        with service_conn_lock:
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
    In service_mode: stop broadcast, let control client use the serial directly.
    """
    global serial_instance
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=2,
                                dsrdtr=True, rtscts=False)
            ser.dtr = True
            with serial_lock:
                serial_instance = ser
            print(f"[serial] opened {SERIAL_PORT} @ {SERIAL_BAUD}", flush=True)

            # Init YDNU-02 into RAW mode so device_manager doesn't need to
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
                    # Exclusive mode: serial is controlled by ctrl client
                    time.sleep(0.05)
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
