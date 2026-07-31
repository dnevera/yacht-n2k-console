"""Control port handler for YDNU-02 TCP Gateway (:4002).

Exclusive control session for service terminal operations and OTA firmware flashing.
DTR toggle mode switching and serial passthrough.
"""

import os
import sys
import time
import socket
import subprocess
import threading
import serial
from typing import Optional, Tuple, Callable


def ctrl_send(conn: socket.socket, msg: str) -> None:
    """Send a line-terminated UTF-8 message to the CTRL client."""
    try:
        conn.sendall(f"{msg}\r\n".encode("utf-8"))
    except OSError:
        pass


class CtrlHandler:
    """Exclusive control port handler (:4002)."""

    def __init__(self,
                 service_mode: threading.Event,
                 get_serial_instance: Callable[[], Optional[serial.Serial]],
                 set_serial_instance: Callable[[Optional[serial.Serial]], None],
                 serial_lock: threading.Lock,
                 serial_port: str,
                 serial_baud: int):
        self.service_mode = service_mode
        self._get_serial = get_serial_instance
        self._set_serial = set_serial_instance
        self._serial_lock = serial_lock
        self.serial_port = serial_port
        self.serial_baud = serial_baud

        self.service_conn: Optional[socket.socket] = None
        self.service_conn_lock = threading.Lock()

    def enter_service_mode_on_device(self) -> None:
        """DTR toggle sequence to switch YDNU-02 from RAW to SERVICE terminal mode."""
        print("[ctrl] DTR toggle sequence starting for service mode...", flush=True)
        with self._serial_lock:
            ser = self._get_serial()
            is_mock = hasattr(ser, "reset_input_buffer") or hasattr(ser, "_mock_name")
            if ser and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass
            self._set_serial(None)

        if os.path.exists(self.serial_port) and not self.serial_port.startswith("/dev/null"):
            try:
                stty_flag = "-f" if sys.platform == "darwin" else "-F"
                cmd = (f"stty {stty_flag} {self.serial_port} {self.serial_baud} raw -echo hupcl; "
                       f"echo 'YDNU MODE SERVICE' > {self.serial_port}")
                subprocess.run(cmd, shell=True, check=True, timeout=5)
                print("[ctrl] stty + echo YDNU MODE SERVICE succeeded", flush=True)
            except Exception as e:
                print(f"[ctrl] stty/echo command failed: {e}", flush=True)

        time.sleep(0.15)
        if is_mock:
            with self._serial_lock:
                if ser:
                    ser.is_open = True
                    if hasattr(ser, "reset_input_buffer"):
                        try:
                            ser.reset_input_buffer()
                        except Exception:
                            pass
                self._set_serial(ser)
            print("[ctrl] Serial restored (mock mode)", flush=True)
            return

        try:
            new_ser = serial.Serial(
                self.serial_port,
                self.serial_baud,
                timeout=0.1,
                dsrdtr=True,
                rtscts=False
            )
            new_ser.dtr = True
            with self._serial_lock:
                self._set_serial(new_ser)
            print(f"[ctrl] Serial reopened in service mode: {self.serial_port}", flush=True)
        except serial.SerialException as e:
            print(f"[ctrl] Failed to reopen serial port: {e}", flush=True)

    def exit_service_mode_on_device(self) -> None:
        """Send MODE RAW command to switch YDNU-02 back to normal N2K frame mode."""
        with self._serial_lock:
            ser = self._get_serial()

        if ser and ser.is_open:
            try:
                ser.write(b"MODE RAW\r\n")
                time.sleep(0.5)
                ser.timeout = 0.1
            except serial.SerialException as e:
                print(f"[ctrl] error during service exit: {e}", flush=True)

        print("[ctrl] YDNU-02 switched back to RAW mode", flush=True)

    def handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """Handle a single CTRL port client connection."""
        print(f"[ctrl] client connected: {addr}", flush=True)

        with self.service_conn_lock:
            if self.service_mode.is_set():
                ctrl_send(conn, "ERROR: another control session is active")
                conn.close()
                return
            self.service_conn = conn

        ctrl_mode: Optional[str] = None

        try:
            conn.settimeout(0.1)
            buf = b""

            while True:
                try:
                    chunk = conn.recv(256)
                except socket.timeout:
                    if self.service_mode.is_set():
                        with self._serial_lock:
                            ser = self._get_serial()
                        if ser and getattr(ser, "is_open", False):
                            try:
                                in_w = ser.in_waiting
                                if in_w:
                                    data = ser.read(in_w)
                                    conn.sendall(data)
                            except (serial.SerialException, OSError, TypeError, AttributeError):
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
                        self.service_mode.set()
                        print(f"[ctrl] {cmd} — broadcast paused", flush=True)

                        time.sleep(0.15)

                        if cmd == "SERVICE_START":
                            self.enter_service_mode_on_device()
                        else:
                            with self._serial_lock:
                                ser = self._get_serial()
                            if ser and ser.is_open:
                                ser.reset_input_buffer()

                        ctrl_send(conn, "READY")

                    elif cmd in ("SERVICE_END", "FIRMWARE_END"):
                        if ctrl_mode == "SERVICE":
                            self.exit_service_mode_on_device()

                        self.service_mode.clear()
                        ctrl_mode = None
                        print(f"[ctrl] {cmd} — broadcast resumed", flush=True)
                        ctrl_send(conn, "OK")

                    elif self.service_mode.is_set():
                        raw = cmd_bytes + b"\n"
                        with self._serial_lock:
                            ser = self._get_serial()
                            if ser and getattr(ser, "is_open", False) and getattr(ser, "fd", None) is not None:
                                try:
                                    ser.write(raw)
                                except (serial.SerialException, OSError, TypeError, AttributeError) as e:
                                    ctrl_send(conn, f"ERROR: serial write: {e}")

                    else:
                        ctrl_send(conn, "ERROR: not in service mode")

        except OSError:
            pass
        finally:
            with self.service_conn_lock:
                if self.service_conn is conn:
                    if self.service_mode.is_set():
                        if ctrl_mode == "SERVICE":
                            try:
                                self.exit_service_mode_on_device()
                            except Exception:
                                pass
                        self.service_mode.clear()
                        print("[ctrl] session ended — broadcast resumed", flush=True)
                    self.service_conn = None
            try:
                conn.close()
            except OSError:
                pass
            print(f"[ctrl] client disconnected: {addr}", flush=True)
