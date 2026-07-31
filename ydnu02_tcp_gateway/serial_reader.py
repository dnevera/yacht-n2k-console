"""Serial port reader thread for YDNU-02 TCP Gateway.
===================================================

PURPOSE & HARDWARE PROTOCOL SPECIFICATION:
------------------------------------------
1. HARDWARE CONNECTION:
   - Owns the physical USB serial connection (/dev/ttyACM0 or /dev/ttyUSB0).
   - Baud rate: 115200 8N1, DTR=True.

2. RAW MODE INITIALIZATION SEQUENCE:
   - To force YDNU-02 into ASCII raw mode:
     a. Writes `YDNU MODE RAW\\r\\n` to serial port.
     b. Waits 2.0s for device handshake response.
     c. Writes `0\\n` to clear any pending prompt.
   - Upon successful initialization, marks `serial_ready` event and triggers
     `send_iso_request()` to query physical bus identity (PGN 60928 + 126996).

3. CONTINUOUS FRAME STREAM & NORMALIZATION:
   - Reads ASCII line bytes continuously from serial.
   - Delegates framing cleanup and T-to-R flag repacking to `normalize_frame()`.
   - Validates format against `NMEA_LINE_RE` regex before forwarding to `DataHub.broadcast()`.
   - Pauses reading when `service_mode` event is set to allow exclusive CTRL terminal operations.

DIAGNOSTIC SKILL / MINI-PROMPTS:
================================
  Skill — check serial port status on host::

      ssh user@localhost 'ls -l /dev/ttyACM0 /dev/ttyUSB0 2>/dev/null'

  Skill — inspect serial reader logs in systemd service::

      ssh user@localhost 'journalctl -u ydnu02-tcp-gateway -n 50 | grep serial'
"""

import time
import threading
import serial
from typing import Optional, Callable
from ydnu02_tcp_gateway.frame_utils import NMEA_LINE_RE, normalize_frame


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
                 send_iso_request: Callable[[], None]):
        self.serial_port = serial_port
        self.serial_baud = serial_baud
        self._get_serial = get_serial_instance
        self._set_serial = set_serial_instance
        self._serial_lock = serial_lock
        self.serial_ready = serial_ready
        self.service_mode = service_mode
        self.broadcast = broadcast
        self.send_iso_request = send_iso_request

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

                    line = normalize_frame(raw)
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
