#!/usr/bin/env python3
"""
NMEA 2000 bidirectional line-buffered TCP proxy for YDNU-02
Serial → TCP: reads complete \r\n lines, serves as \n-terminated stream to all clients.
TCP → Serial: data from HA (ISO requests, commands) forwarded back to the bus.
"""
import socket
import serial
import threading
import sys
import time

SERIAL_PORT = '/dev/ttyACM0'
SERIAL_BAUD = 115200
TCP_HOST = ''
TCP_PORT = 4001

clients: set = set()
clients_lock = threading.Lock()

serial_instance: serial.Serial | None = None
serial_lock = threading.Lock()


def handle_client(conn, addr):
    """Handle one TCP client: register for Serial→TCP broadcast, forward TCP→Serial."""
    print(f"[tcp] client connected: {addr}", flush=True)
    with clients_lock:
        clients.add(conn)
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            # Forward TCP→Serial (HA commands, ISO address claims, etc.)
            with serial_lock:
                if serial_instance and serial_instance.is_open:
                    try:
                        serial_instance.write(data)
                    except serial.SerialException as e:
                        print(f"[serial] write error: {e}", flush=True)
    except OSError:
        pass
    finally:
        with clients_lock:
            clients.discard(conn)
        try:
            conn.close()
        except OSError:
            pass
        print(f"[tcp] client disconnected: {addr}", flush=True)


def serial_reader():
    """Serial→TCP: read lines from YDNU-02, broadcast to all TCP clients."""
    global serial_instance
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=2)
            with serial_lock:
                serial_instance = ser
            print(f"[serial] opened {SERIAL_PORT} @ {SERIAL_BAUD}", flush=True)
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                # Strip \r\n, re-terminate with \n only for clean TCP stream
                line = raw.rstrip(b'\r\n') + b'\n'
                if not line.strip():
                    continue
                dead = set()
                with clients_lock:
                    for conn in list(clients):
                        try:
                            conn.sendall(line)
                        except OSError:
                            dead.add(conn)
                    clients.difference_update(dead)
        except serial.SerialException as e:
            print(f"[serial] error: {e} — retrying in 5s", flush=True)
            with serial_lock:
                serial_instance = None
            time.sleep(5)
        except Exception as e:
            print(f"[serial] unexpected error: {e} — retrying in 5s", flush=True)
            with serial_lock:
                serial_instance = None
            time.sleep(5)


def main():
    # Start serial reader thread
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()

    # TCP server
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((TCP_HOST, TCP_PORT))
    srv.listen(5)
    print(f"[tcp] NMEA proxy listening on :{TCP_PORT}", flush=True)

    while True:
        try:
            conn, addr = srv.accept()
            ct = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            ct.start()
        except KeyboardInterrupt:
            print("Shutting down.", flush=True)
            sys.exit(0)


if __name__ == '__main__':
    main()
