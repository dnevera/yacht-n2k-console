"""YDNU-02 serial port controller for diagnostics, mode switching, and monitoring.

Implements a two-level command architecture based on the YDNU-02 specification:
Level 1: OS Shell (echo directly to the closed port) for mode switching.
Level 2: Service Menu (interactive serial session) for diagnostics and configuration.
"""

import os
import time
import subprocess
import json
import glob
import re
from datetime import datetime
import serial
from typing import Optional, List, Dict, Any

from ydnu02.pgn_decoder import N2KPGNDecoder


class YDNU02Controller:
    """YDNU-02 serial port controller for diagnostics, mode switching, and monitoring."""

    def __init__(self, port: Optional[str] = None, debug: bool = False):
        self.port = port or self._find_port()
        self.debug = debug
        self.ser: Optional[serial.Serial] = None
        self.mode: Optional[str] = None
        # Injected by OperationRunner before each proxied service operation;
        # cleared on completion. When set, all serial I/O is routed through
        # the ProxyControlClient instead of the direct serial port.
        self._passthrough: Optional[Any] = None

    @staticmethod
    def _find_port() -> str:
        """Auto-detect YDNU-02 USB serial port across Linux and macOS."""
        for pattern in ["/dev/ttyACM*", "/dev/cu.usbmodem*", "/dev/tty.usbmodem*"]:
            found = sorted(glob.glob(pattern))
            if found:
                return found[0]
        return "/dev/ttyACM0"

    def _log(self, direction: str, data):
        """Emit a debug-level TX/RX trace line when debug=True."""
        if self.debug:
            print(f"  [{direction}] {data}")

    # --- Level 1: OS Shell ---

    def _send_shell_command(self, cmd: str):
        """Send a Level-1 OS Shell command via echo > port. Port must be CLOSED first."""
        self._close_terminal()

        subprocess.run(["stty", "-F", self.port, "hupcl"], capture_output=True, timeout=5)
        self._log("SHELL", f'stty -F {self.port} hupcl')

        full_cmd = f'echo "{cmd}" > {self.port}'
        subprocess.run(full_cmd, shell=True, capture_output=True, timeout=5)
        self._log("SHELL", full_cmd)

        time.sleep(1.5)

    # --- Level 2: Terminal Session ---

    def _open_terminal(self) -> bool:
        """Open a direct serial session with DTR=True for Level-2 service terminal."""
        if self._passthrough:
            return True
        if self.ser and self.ser.is_open:
            return True
        try:
            self.ser = serial.Serial(self.port, baudrate=115200, timeout=1.5, dsrdtr=True, rtscts=False)
            self.ser.dtr = True
            self.ser.rts = True
            time.sleep(0.3)
            self._log("TERM", f"Opened {self.port}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to open port {self.port}: {e}")
            return False

    def _close_terminal(self):
        """Close the serial session. Safe to call multiple times."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            self._log("TERM", f"Closed {self.port}")
        self.ser = None

    def _write(self, data: bytes):
        """Write raw bytes via ProxyControlClient passthrough or direct serial."""
        pcc = self._passthrough
        if pcc:
            pcc.passthrough_write(data)
            self._log("TX[proxy]", data)
            return
        if self.ser and self.ser.is_open:
            self.ser.write(data)
            self.ser.flush()
            self._log("TX", data)

    def _read_response(self, duration: float = 2.0) -> str:
        """Read serial response for `duration` seconds via passthrough or direct serial."""
        pcc = self._passthrough
        if pcc:
            return pcc.passthrough_read_for(duration)
        if not self.ser:
            return ""
        chunks = []
        t0 = time.time()
        while time.time() - t0 < duration:
            if self.ser.in_waiting:
                chunk = self.ser.read(self.ser.in_waiting)
                self._log("RX", chunk)
                chunks.append(chunk.decode('utf-8', errors='ignore'))
            time.sleep(0.05)
        return "".join(chunks)

    def _send_terminal_command(self, cmd: str, wait: float = 2.0) -> str:
        """Send a Service Menu text command and return the response."""
        pcc = self._passthrough
        if not pcc and (not self.ser or not self.ser.is_open):
            raise RuntimeError("Service terminal not open. Call enter_service_mode() first.")
        if not pcc:
            if self.ser.in_waiting:
                self.ser.read(self.ser.in_waiting)
            self.ser.reset_input_buffer()
            time.sleep(0.1)
        self._write(f"{cmd}\r\n".encode('ascii'))
        time.sleep(0.3)
        return self._read_response(duration=wait)

    # --- Service Mode lifecycle ---

    def enter_service_mode(self) -> str:
        """Enter YDNU-02 service terminal mode."""
        pcc = self._passthrough
        if pcc:
            print("[YDNU02] Service mode via proxy (device already in service terminal)")
            welcome = pcc.passthrough_read_for(0.5)
            pcc.passthrough_write(b"HELP\r\n")
            welcome += pcc.passthrough_read_for(2.0)
            self.mode = "SERVICE"
            return welcome

        print(f"[YDNU02] Entering service mode directly ({self.port})...")
        self._send_shell_command("YDNU MODE SERVICE")

        if not self._open_terminal():
            return "[ERROR] Failed to open port after YDNU MODE SERVICE"

        self.mode = "SERVICE"
        welcome = self._read_response(duration=1.0)
        self._write(b"HELP\r\n")
        welcome += self._read_response(duration=2.0)
        return welcome

    def exit_service_mode(self, target_mode: str = "AUTO") -> str:
        """Exit YDNU-02 service terminal mode and return to operational state."""
        pcc = self._passthrough
        if pcc:
            self._close_terminal()
            self.mode = target_mode.upper()
            print(f"[YDNU02] Exit service mode (proxy will send MODE RAW on SERVICE_END)")
            return "OK"

        result = ""
        if self.ser and self.ser.is_open:
            result = self._send_terminal_command(f"MODE {target_mode.upper()}", wait=1.5)
        self._close_terminal()
        self.mode = target_mode.upper()
        print(f"[YDNU02] Mode set to: {self.mode}")
        return result

    # --- Service Menu commands (Level 2) ---

    def service_help(self, cmd: Optional[str] = None) -> str:
        """Send HELP or HELP <cmd> to the service terminal."""
        if cmd:
            return self._send_terminal_command(f"HELP {cmd.upper()}")
        return self._send_terminal_command("HELP")

    def service_diag(self, scope: str = "ALL") -> str:
        """DIAG ALL|SETTINGS|USB_RX|USB_TX|N2K_RX|N2K_TX."""
        return self._send_terminal_command(f"DIAG {scope.upper()}", wait=10.0)

    def service_reset_settings(self) -> str:
        """RESET SETTINGS — factory-reset all settings (firmware is NOT affected)."""
        return self._send_terminal_command("RESET SETTINGS", wait=3.0)

    def service_reset_filters(self) -> str:
        """RESET FILTERS — clear all PGN filter tables."""
        return self._send_terminal_command("RESET FILTERS", wait=2.0)

    def service_reset_mcu(self) -> str:
        """RESET MCU — soft reboot device (settings and firmware are NOT affected)."""
        return self._send_terminal_command("RESET MCU", wait=3.0)

    def service_reset_hardware(self) -> str:
        """RESET HARDWARE — roll back to factory firmware stored in EEPROM."""
        return self._send_terminal_command("RESET HARDWARE", wait=5.0)

    def service_print_filter(self, name: Optional[str] = None) -> str:
        """PRINT [filter_name] — dump filter table entries."""
        if name:
            return self._send_terminal_command(f"PRINT {name.upper()}", wait=2.0)
        return self._send_terminal_command("PRINT", wait=2.0)

    def service_set(self, key: Optional[str] = None, val: Optional[str] = None) -> str:
        """SET [key [value]] — read or write a device setting."""
        parts = ["SET"]
        if key:
            parts.append(key.upper())
        if val:
            parts.append(val)
        return self._send_terminal_command(" ".join(parts), wait=2.0)

    def _parse_welcome_screen(self, text: str) -> Dict[str, str]:
        """Parse YDNU-02 Welcome Screen output into a structured dict."""
        info = {}
        for line in text.split('\n'):
            line = line.strip().strip('*').strip()
            m = re.search(r'Firmware version\s*:\s*(.+?)(?:\s{2,}|$)', line)
            if m:
                info['firmware_version'] = m.group(1).strip()
            m = re.search(r'Serial number\s*:\s*(\S+)', line)
            if m:
                info['serial_number'] = m.group(1)
            m = re.search(r'NMEA 2000 silent\s*:\s*(\S+)', line)
            if m:
                info['silent_mode'] = m.group(1)
            m = re.search(r'Previous mode\s*:\s*(\S+)', line)
            if m:
                info['previous_mode'] = m.group(1)
        return info

    def service_backup(self, backup_dir: Optional[str] = None) -> str:
        """Collect full device state (settings, filters, diag) and save as JSON."""
        backup_dir = backup_dir or os.path.dirname(os.path.abspath(__file__))

        print("[BACKUP] Collecting device state...")

        welcome = self._send_terminal_command("HELP", wait=2.0)
        device_info = self._parse_welcome_screen(welcome)
        device_info['welcome_screen_raw'] = welcome

        print("[BACKUP] Reading settings (HELP SET)...")
        settings_raw = self._send_terminal_command("HELP SET", wait=2.0)
        device_info['settings_raw'] = settings_raw

        filters = {}
        filter_names = ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                        "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]
        for fname in filter_names:
            print(f"[BACKUP] Filter {fname}...")
            filters[fname] = self._send_terminal_command(f"PRINT {fname}", wait=1.5)
            time.sleep(0.2)
        device_info['filters'] = filters

        print("[BACKUP] DIAG SETTINGS...")
        diag_settings = self._send_terminal_command("DIAG SETTINGS", wait=3.0)
        device_info['diag_settings_raw'] = diag_settings

        device_info['backup_timestamp'] = datetime.now().isoformat()
        device_info['port'] = self.port

        serial_num = device_info.get('serial_number', 'unknown')
        fw_ver = device_info.get('firmware_version', 'unknown').replace(' ', '_').replace('/', '-')
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"ydnu02_backup_{serial_num}_fw{fw_ver}_{ts}.json"
        filepath = os.path.join(backup_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(device_info, f, indent=2, ensure_ascii=False)

        print(f"[BACKUP] Saved: {filepath}")
        return filepath

    def service_interactive(self):
        """Interactive REPL for the YDNU-02 Service Menu."""
        print("\n=== YDNU-02 Service Menu (type 'exit' to quit) ===")
        print("Commands: HELP, MODE, FILTER, PRINT, TYPE, ADD, REMOVE, SET, RESET, DIAG")
        print("exit/quit = close terminal.\n")

        while True:
            try:
                cmd = input("YDNU> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ('exit', 'quit'):
                    break
                resp = self._send_terminal_command(cmd, wait=2.0)
                if resp:
                    print(resp)
                else:
                    print("  [no response]")
            except (KeyboardInterrupt, EOFError):
                print("\n[Done]")
                break

    # --- OS Shell quick commands (Level 1) ---

    def set_mode(self, mode: str):
        """Switch operating mode via OS Shell command: YDNU MODE <mode>."""
        mode_upper = mode.upper()
        print(f"[YDNU02] Setting mode: {mode_upper}...")
        if self.mode == "SERVICE" and self.ser and self.ser.is_open:
            self.exit_service_mode(target_mode=mode_upper)
        else:
            self._send_shell_command(f"YDNU MODE {mode_upper}")
            self.mode = mode_upper
        print(f"[YDNU02] Mode {mode_upper} set (LED should blink green).")

    def set_silent(self, on: bool):
        """Enable or disable silent mode (suppresses TX on bus) via OS Shell."""
        state = "ON" if on else "OFF"
        self._send_shell_command(f"YDNU SILENT {state}")
        print(f"[YDNU02] Silent mode: {state}")

    def start_diag_record(self):
        """Start EEPROM diagnostic recording via OS Shell (YDNU DIAG command)."""
        self._send_shell_command("YDNU DIAG")
        print("[YDNU02] Diagnostic recording started (LED = 3s green).")

    def update_firmware(self, bin_path: str, skip_backup: bool = False,
                        progress_cb=None):
        """Flash new firmware (.BIN) to the YDNU-02 device."""
        if not os.path.isfile(bin_path):
            raise FileNotFoundError(f"Firmware file not found: {bin_path}")

        file_size = os.path.getsize(bin_path)

        if file_size < 1024:
            raise ValueError(f"Firmware file too small ({file_size} bytes), likely corrupt")
        if file_size > 512 * 1024:
            raise ValueError(f"Firmware file too large ({file_size} bytes), max 512KB expected")

        with open(bin_path, 'rb') as f:
            magic = f.read(2)
        if magic == b'PK':
            raise ValueError("File is a ZIP archive, not a BIN. Extract .BIN from ZIP first")

        if not os.path.exists(self.port):
            raise RuntimeError(f"Device port {self.port} not found. Is YDNU-02 connected?")

        def _progress(stage, pct):
            print(f"[FIRMWARE] {stage}: {pct}%")
            if progress_cb:
                progress_cb(stage, pct)

        if not skip_backup:
            _progress("backup", 0)
            print("[FIRMWARE] Auto-backup before flashing...")
            self.enter_service_mode()
            backup_path = self.service_backup()
            self.exit_service_mode("AUTO")
            print(f"[FIRMWARE] Backup saved: {backup_path}")
            _progress("backup", 100)
            time.sleep(1.0)

        self._close_terminal()

        _progress("flashing", 0)
        print(f"[FIRMWARE] Uploading {bin_path} ({file_size} bytes)...")

        CHUNK_SIZE = 4096
        written = 0
        try:
            with open(bin_path, 'rb') as src, open(self.port, 'wb') as dst:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    dst.write(chunk)
                    dst.flush()
                    written += len(chunk)
                    pct = min(100, int(written * 100 / file_size))
                    _progress("flashing", pct)
        except PermissionError:
            raise RuntimeError(f"Permission denied writing to {self.port}. Check user is in 'dialout' group")
        except OSError as e:
            raise RuntimeError(f"Failed to write firmware to {self.port}: {e}")

        _progress("done", 100)
        print(f"[FIRMWARE] Upload complete ({written} bytes). LED indicators:")
        print("  GREEN x3  = success")
        print("  RED RED GREEN = firmware version already installed")
        print("  RED GREEN RED = file corrupt")
        print("  No signal    = file not recognised")
        return {"written": written, "total": file_size}

    # --- NMEA Monitoring ---

    def monitor_raw(self, duration: float = 10.0, log_file: Optional[str] = None) -> List[Dict[str, Any]]:
        """Monitor CAN bus traffic in RAW mode and decode PGNs."""
        self.set_mode("RAW")
        if not self._open_terminal():
            return []

        self._write(b"0\n")
        time.sleep(0.5)

        results = []
        fh = open(log_file, 'w') if log_file else None

        print(f"[YDNU02] RAW monitoring ({duration}s)... Ctrl+C to stop.")
        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(line)
                    if parsed:
                        results.append(parsed)
                        print(f"  {parsed['decoded']}")
                        if fh:
                            fh.write(f"{line}\n")
                    else:
                        print(f"  [RAW] {line}")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n[Stopped]")
        finally:
            if fh:
                fh.close()
            self._close_terminal()

        return results

    def monitor_0183(self, duration: float = 10.0, log_file: Optional[str] = None):
        """Monitor NMEA 0183 sentences from the bus."""
        self.set_mode("0183")
        if not self._open_terminal():
            return

        self._write(b"$\n")
        time.sleep(0.5)

        fh = open(log_file, 'w') if log_file else None
        print(f"[YDNU02] NMEA 0183 monitoring ({duration}s)... Ctrl+C to stop.")
        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"  {line}")
                        if fh:
                            fh.write(f"{line}\n")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n[Stopped]")
        finally:
            if fh:
                fh.close()
            self._close_terminal()

    def scan_bus(self, duration: float = 5.0) -> List[Dict[str, Any]]:
        """Actively scan the CAN bus by requesting ISO Address Claims and Product Info."""
        self.set_mode("RAW")
        if not self._open_terminal():
            return []

        self._write(b"0\n")
        time.sleep(0.5)

        self._write(b"18EAFF10 00 EE 00\r\n")
        time.sleep(0.2)

        self._write(b"18EAFF10 14 F0 01\r\n")
        time.sleep(0.2)

        devices: Dict[int, Dict[str, Any]] = {}
        results = []

        print(f"[YDNU02] Scanning CAN bus ({duration}s)...")
        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(line)
                    if parsed:
                        results.append(parsed)
                        src = parsed['info']['src']
                        pgn = parsed['info']['pgn']
                        if src not in devices:
                            devices[src] = {"src": src}
                        if pgn == 60928:
                            devices[src]["address_claim"] = parsed['decoded']
                        elif pgn == 126996:
                            devices[src]["product_info"] = parsed['decoded']
                        print(f"  {parsed['decoded']}")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n[Stopped]")
        finally:
            self._close_terminal()

        if devices:
            print(f"\n=== Found {len(devices)} device(s) ===")
            for src, info in sorted(devices.items()):
                print(f"  Src {src:3d}: {info.get('address_claim', '')}  {info.get('product_info', '')}")
        else:
            print("\n  CAN bus silent: no devices found.")

        return results
