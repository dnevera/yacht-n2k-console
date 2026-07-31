"""Serial port reader thread for YDNU-02 TCP Gateway.

Owns the serial connection (/dev/ttyACM0), performs RAW mode initialization sequence,
reads CAN frames continuously, and feeds the DataHub broadcast pipeline.
"""

import time
import threading
import serial
from typing import Optional, Callable
from ydnu02_tcp_gateway.frame_utils import NMEA_LINE_RE, get_pgn_sa
from ydnu02_tcp_gateway.device_cache import DeviceFrameCache


class SerialReader:
    """Serial port reader daemon thread."""

    def __init__(self,
                 serial_port: str,
                 serial_baud: int,
                 get_serial_instance: Callable[[], Optional[serial.Serial]],
                 set_serial_instance: Callable[[Optional[serial.Serial]], None],
                 serial_lock: threading.Lock,
                 serial_ready: threading.Event,
                 service_mode: threading.Event,
                 broadcast: Callable[[bytes], None],
                 send_iso_request: Callable[[], None],
                 device_cache: DeviceFrameCache):
        self.serial_port = serial_port
        self.serial_baud = serial_baud
        self._get_serial = get_serial_instance
        self._set_serial = set_serial_instance
        self._serial_lock = serial_lock
        self.serial_ready = serial_ready
        self.service_mode = service_mode
        self.broadcast = broadcast
        self.send_iso_request = send_iso_request
        self.device_cache = device_cache

    def run(self) -> None:
        """Main serial loop (runs forever)."""
        while True:
            try:
                ser = serial.Serial(self.serial_port, self.serial_baud, timeout=0.1,
                                    dsrdtr=True, rtscts=False)
                ser.dtr = True
                with self._serial_lock:
                    self._set_serial(ser)
                print(f"[serial] opened {self.serial_port} @ {self.serial_baud}", flush=True)

                init_data = b""

                try:
                    ser.write(b"YDNU MODE RAW\r\n")
                    time.sleep(2.0)
                    if getattr(ser, "in_waiting", 0):
                        init_data += ser.read(ser.in_waiting)
                    ser.write(b"0\n")
                    time.sleep(0.5)
                    if getattr(ser, "in_waiting", 0):
                        init_data += ser.read(ser.in_waiting)
                    print("[serial] YDNU-02 initialized in RAW mode", flush=True)
                except (serial.SerialException, OSError, TypeError, AttributeError) as e:
                    print(f"[serial] init sequence error: {e}", flush=True)

                for raw_line in init_data.split(b"\n"):
                    if not raw_line:
                        continue
                    line = raw_line.rstrip(b"\r") + b"\n"
                    if not NMEA_LINE_RE.match(line):
                        continue
                    self.device_cache.update_from_line(line)

                self.serial_ready.set()
                self.send_iso_request()

                while True:
                    if self.service_mode.is_set():
                        time.sleep(0.05)
                        with self._serial_lock:
                            current = self._get_serial()
                        if current is not None and current is not ser:
                            ser = current
                        continue

                    raw = ser.readline()
                    if not raw:
                        continue

                    line = raw.rstrip(b"\r\n") + b"\n"
                    if not NMEA_LINE_RE.match(line):
                        continue

                    self.broadcast(line)

            except serial.SerialException as e:
                print(f"[serial] error: {e} — retrying in 5s", flush=True)
                self.serial_ready.clear()
                with self._serial_lock:
                    self._set_serial(None)
                self.service_mode.clear()
                time.sleep(5)
            except Exception as e:
                print(f"[serial] unexpected error: {e} — retrying in 5s", flush=True)
                time.sleep(5)
