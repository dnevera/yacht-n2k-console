import os
import sys
import time
import asyncio
import threading
import re
import urllib.request
from typing import Optional, Dict, Any, List

from fastapi import WebSocket, WebSocketDisconnect

# Add current directory to path for ydnu02 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ydnu02 import YDNU02Controller, N2KPGNDecoder

from sensors import GobiusCSensor


class DeviceManager:
    """Thread-safe wrapper around YDNU02Controller with mutex."""

    def __init__(self, port: Optional[str] = None, debug: bool = False):
        self.port = port
        self.debug = debug
        self._lock = threading.Lock()
        self._ctrl: Optional[YDNU02Controller] = None
        self._state = "IDLE"
        self._info_cache: Optional[Dict[str, Any]] = None
        self._info_cache_time: float = 0
        self._cache_ttl: float = 60.0

        self.sensors: Dict[int, GobiusCSensor] = {}
        self._sensors_lock = threading.Lock()  # protects sensors + _discovered_bus_devices
        self._discovered_bus_devices: Dict[int, Dict[str, Any]] = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running = False
        self._pause_event = threading.Event()
        self._ws_clients: List[WebSocket] = []
        self._monitor_queues: List[asyncio.Queue] = []  # subscriber queues for monitor
        self._queues_lock = threading.Lock()  # protects _monitor_queues
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_ctrl(self) -> YDNU02Controller:
        if self._ctrl is None:
            self._ctrl = YDNU02Controller(port=self.port, debug=self.debug)
        return self._ctrl

    def get_port(self) -> str:
        return self._get_ctrl().port

    # === Single USB Worker Thread ===

    def start_bus_worker(self):
        if self._worker_running:
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._bus_worker, daemon=True)
        self._worker_thread.start()
        print("[Gateway] Single USB Bus Worker started")

    def stop_bus_worker(self):
        self._worker_running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=3)
            self._worker_thread = None
        print("[Gateway] Bus Worker stopped")

    def _bus_worker(self):
        """Single USB worker thread. Owns /dev/ttyACM0, continuously parses NMEA 2000 frames."""
        ctrl = self._get_ctrl()
        _port_missing_logged = False
        _retry_delay = 1.0
        while self._worker_running:
            if self._pause_event.is_set():
                time.sleep(0.1)
                continue

            # Check if device exists before trying to open
            import os
            if not os.path.exists(ctrl.port):
                if not _port_missing_logged:
                    print(f"[Gateway] Device {ctrl.port} not found — waiting for USB connection...")
                    self._state = "NO_DEVICE"
                    _port_missing_logged = True
                time.sleep(min(_retry_delay, 30.0))
                _retry_delay = min(_retry_delay * 2, 30.0)
                continue

            with self._lock:
                opened = ctrl._open_terminal()
                if opened:
                    _port_missing_logged = False
                    _retry_delay = 1.0
                    # Set RAW mode via serial (NOT shell) to avoid DTR reset
                    ctrl._write(b"YDNU MODE RAW\r\n")
                    time.sleep(2)
                    # Clear mode-switch echo from buffer
                    if ctrl.ser and ctrl.ser.in_waiting:
                        ctrl.ser.read(ctrl.ser.in_waiting)
                    ctrl._write(b"0\n")
                    time.sleep(0.5)
                    # Clear filter echo
                    if ctrl.ser and ctrl.ser.in_waiting:
                        ctrl.ser.read(ctrl.ser.in_waiting)
                    ctrl.mode = "RAW"
                    self._state = "LISTENING"
                    print(f"[Gateway] Bus Worker reading on {ctrl.port}")

            if not opened:
                time.sleep(min(_retry_delay, 30.0))
                _retry_delay = min(_retry_delay * 2, 30.0)
                continue

            try:
                line_count = 0
                while self._worker_running and not self._pause_event.is_set():
                    if ctrl.ser and ctrl.ser.is_open and ctrl.ser.in_waiting:
                        line = ctrl.ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            line_count += 1
                            parsed = N2KPGNDecoder.parse_raw_line(line)
                            if parsed:
                                self._update_sensor_state(parsed)
                                # Broadcast to monitor subscribers
                                self._broadcast_frame(parsed)
                    else:
                        time.sleep(0.01)
            except Exception as e:
                print(f"[Gateway] Read error: {e}")
            finally:
                with self._lock:
                    ctrl._close_terminal()
                    self._state = "IDLE"

            # If worker was paused by a service operation, wait for unpause then loop back to re-enable RAW
            while self._worker_running and self._pause_event.is_set():
                time.sleep(0.1)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Store reference to async event loop for thread-safe queue operations."""
        self._event_loop = loop

    def _broadcast_frame(self, parsed: Dict[str, Any]):
        """Broadcast parsed frame to all monitor subscribers (thread-safe)."""
        with self._queues_lock:
            if not self._monitor_queues or not self._event_loop:
                return
            frame = {
                "type": "frame",
                "time": parsed["time"],
                "dir": parsed["dir"],
                "pgn": parsed["info"]["pgn"],
                "pgn_name": N2KPGNDecoder.pgn_name(parsed["info"]["pgn"]),
                "src": parsed["info"]["src"],
                "dst": parsed["info"]["dst"],
                "decoded": parsed["decoded"],
                "raw": parsed["raw"],
            }
            dead = []
            for q in self._monitor_queues:
                try:
                    self._event_loop.call_soon_threadsafe(q.put_nowait, frame)
                except Exception:
                    dead.append(q)
            for q in dead:
                self._monitor_queues.remove(q)

    def _update_sensor_state(self, parsed: Dict[str, Any]):
        info = parsed.get("info", {})
        pgn = info.get("pgn")
        src = info.get("src")
        data = parsed.get("data", b"")

        with self._sensors_lock:
            # Track discovered CAN-bus devices
            if src is not None:
                if src not in self._discovered_bus_devices:
                    self._discovered_bus_devices[src] = {
                        "src": src,
                        "manufacturer": "NMEA 2000 Device",
                        "model": f"Device (SRC {src})",
                        "serial": "--",
                        "firmware": "--",
                        "device_class": "--",
                        "function_name": "--",
                        "device_class_name": "--",
                        "active_pgns": [],
                    }
                if pgn and pgn not in self._discovered_bus_devices[src]["active_pgns"]:
                    self._discovered_bus_devices[src]["active_pgns"].append(pgn)

                # PGN 60928 / 126996: Use library decoder for device info
                if pgn in (60928, 126996):
                    dev_info = N2KPGNDecoder.parse_device_info(parsed)
                    if dev_info:
                        dev = self._discovered_bus_devices[src]
                        if "manufacturer" in dev_info:
                            dev["manufacturer"] = dev_info["manufacturer"]
                        if "model" in dev_info:
                            dev["model"] = dev_info["model"]
                        if "serial" in dev_info:
                            dev["serial"] = dev_info["serial"]
                        if "firmware" in dev_info:
                            dev["firmware"] = dev_info["firmware"]
                        if "function_name" in dev_info:
                            dev["function_name"] = dev_info["function_name"]
                        if "device_class_name" in dev_info:
                            dev["device_class_name"] = dev_info["device_class_name"]
                        if "device_class" in dev_info:
                            dev["device_class"] = dev_info.get("device_class_name", str(dev_info["device_class"]))
                        if "model_version" in dev_info:
                            dev["model_version"] = dev_info["model_version"]
                        if "unique_id" in dev_info:
                            dev["unique_id"] = dev_info["unique_id"]

            if pgn == 127505 and len(data) >= 5:  # Fluid Level PGN
                instance = data[0] & 0x0F
                type_code = (data[0] >> 4) & 0x0F
                raw_level = data[1] | (data[2] << 8)
                level_pct = round(raw_level * 0.004, 1) if raw_level <= 25000 else None

                capacity_l = None
                if len(data) >= 7:
                    raw_cap = int.from_bytes(data[3:7], 'little')
                    if raw_cap != 0xFFFFFFFF:
                        capacity_l = round(raw_cap * 0.1, 1)

                if instance not in self.sensors:
                    self.sensors[instance] = GobiusCSensor(instance=instance, name=f"Tank {instance}")

                self.sensors[instance].update_from_nmea127505({
                    "instance": instance,
                    "type_code": type_code,
                    "level_pct": level_pct,
                    "capacity_l": capacity_l,
                    "src": src,
                })


    def send_raw_command(self, cmd_str: str):
        """Send raw command string to YDNU-02 serial port."""
        def _send(ctrl):
            if ctrl.ser and ctrl.ser.is_open:
                ctrl._write(cmd_str.encode("utf-8") + b"\r\n")
                return True
            return False
        return self._locked_operation(_send)

    def get_sensors_state(self) -> Dict[str, Any]:
        """Instant non-blocking REST API output for all sensors."""
        with self._sensors_lock:
            fluid_levels = [sensor.to_dict() for sensor in self.sensors.values()]
        return {
            "status": "ok",
            "fluid_levels": fluid_levels,
            "count": len(fluid_levels)
        }

    # === Base patterns (pauses bus worker briefly for command execution) ===

    def _service_operation(self, func, exit_mode: str = "RAW"):
        """Pause worker → enter service → func(ctrl) → exit service → resume worker."""
        self._pause_event.set()
        time.sleep(0.2)
        try:
            with self._lock:
                ctrl = self._get_ctrl()
                try:
                    ctrl.enter_service_mode()
                    result = func(ctrl)
                    ctrl.exit_service_mode(exit_mode)
                    self._state = "IDLE"
                    return result
                except Exception:
                    ctrl._close_terminal()
                    self._state = "IDLE"
                    raise
        finally:
            self._pause_event.clear()

    def _locked_operation(self, func):
        """Pause worker → func(ctrl) → resume worker."""
        self._pause_event.set()
        time.sleep(0.2)
        try:
            with self._lock:
                ctrl = self._get_ctrl()
                result = func(ctrl)
                self._state = "IDLE"
                return result
        finally:
            self._pause_event.clear()

    def _raw_locked_operation(self, func):
        """Pause worker → lock → func(ctrl) with manual lifecycle. Caller closes terminal."""
        self._pause_event.set()
        time.sleep(0.2)
        try:
            with self._lock:
                ctrl = self._get_ctrl()
                try:
                    return func(ctrl)
                except Exception:
                    ctrl._close_terminal()
                    self._state = "IDLE"
                    raise
        finally:
            self._pause_event.clear()

    # === Service mode operations ===

    def get_info(self, force: bool = False) -> Dict[str, Any]:
        if not force and self._info_cache and (time.time() - self._info_cache_time) < self._cache_ttl:
            return self._info_cache
        def _do(ctrl):
            welcome = ctrl._send_terminal_command("HELP", wait=2.0)
            info = ctrl._parse_welcome_screen(welcome)
            info["port"] = ctrl.port
            info["state"] = "online"
            return info
        result = self._service_operation(_do)
        if result and result.get("firmware_version"):
            self._info_cache = result
            self._info_cache_time = time.time()
        return result

    def get_filters(self) -> Dict[str, Any]:
        FILTER_NAMES = ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                        "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]
        def _do(ctrl):
            filters = {}
            for name in FILTER_NAMES:
                raw = ctrl._send_terminal_command(f"PRINT {name}", wait=1.5)
                records, ftype = 0, "BLACK"
                if "contains" in raw:
                    try: records = int(raw.split("contains")[1].strip().split()[0])
                    except (ValueError, IndexError): pass
                if "type is" in raw:
                    try: ftype = raw.split("type is")[1].strip().split()[0].upper()
                    except IndexError: pass
                filters[name] = {"records": records, "type": ftype, "raw": raw}
                time.sleep(0.15)
            return {"filters": filters}
        return self._service_operation(_do)

    def get_settings(self) -> Dict[str, str]:
        return self._service_operation(
            lambda c: {"settings_raw": c._send_terminal_command("HELP SET", wait=2.0)})

    def get_diag(self, scope: str) -> Dict[str, str]:
        return self._service_operation(
            lambda c: {"data": c._send_terminal_command(f"DIAG {scope.upper()}", wait=10.0)})

    def send_service_cmd(self, cmd: str) -> Dict[str, str]:
        return self._service_operation(
            lambda c: {"response": c._send_terminal_command(cmd, wait=3.0)})

    def _find_existing_backup(self, fw_version: str) -> str | None:
        """Check if a backup for this fw version already exists. Return path or None."""
        import glob
        backup_dir = os.path.dirname(os.path.abspath(__file__))
        # Filename pattern: ydnu02_backup_{serial}_fw{version}_{date}_{time}.json
        # Normalize version for filename matching (spaces/slashes → underscores/dashes)
        fw_norm = fw_version.replace(' ', '_').replace('/', '-')
        pattern = os.path.join(backup_dir, f"ydnu02_backup_*_fw{fw_norm}_*.json")
        existing = sorted(glob.glob(pattern), reverse=True)
        return existing[0] if existing else None

    def create_backup(self, force: bool = False) -> Dict[str, str]:
        """Create backup only if none exists for current fw version (unless force=True)."""
        backup_dir = os.path.dirname(os.path.abspath(__file__))

        # Check existing backups by fw version (need info first)
        if not force and self._info_cache and self._info_cache.get("firmware_version"):
            existing = self._find_existing_backup(self._info_cache["firmware_version"])
            if existing:
                return {"status": "skipped", "filepath": existing,
                        "filename": os.path.basename(existing),
                        "message": "Backup already exists for this firmware version"}

        def _do(ctrl):
            # Get fw version inside service mode to check
            if not force:
                welcome = ctrl._send_terminal_command("HELP", wait=2.0)
                info = ctrl._parse_welcome_screen(welcome)
                fw = info.get("firmware_version", "")
                if fw:
                    existing = self._find_existing_backup(fw)
                    if existing:
                        return {"status": "skipped", "filepath": existing,
                                "filename": os.path.basename(existing),
                                "message": "Backup already exists for this firmware version"}
            filepath = ctrl.service_backup(backup_dir)
            return {"status": "ok", "filepath": filepath, "filename": os.path.basename(filepath)}
        return self._service_operation(_do)

    def reset_settings(self) -> Dict[str, str]:
        return self._service_operation(
            lambda c: {"status": "ok", "response": c.service_reset_settings()})

    def reset_filters(self) -> Dict[str, str]:
        return self._service_operation(
            lambda c: {"status": "ok", "response": c.service_reset_filters()})

    def reset_mcu(self) -> Dict[str, str]:
        """MCU resets the device — no exit_service_mode possible."""
        def _do(ctrl):
            ctrl.enter_service_mode()
            resp = ctrl.service_reset_mcu()
            ctrl._close_terminal()
            self._state = "IDLE"
            return {"status": "ok", "response": resp}
        return self._raw_locked_operation(_do)

    def reset_hardware(self) -> Dict[str, str]:
        """Full hardware reset with auto-backup (only if needed). Device reboots after."""
        backup_dir = os.path.dirname(os.path.abspath(__file__))
        def _do(ctrl):
            ctrl.enter_service_mode()
            # Smart backup — only if no backup for this fw version exists
            welcome = ctrl._send_terminal_command("HELP", wait=2.0)
            info = ctrl._parse_welcome_screen(welcome)
            fw = info.get("firmware_version", "")
            existing = self._find_existing_backup(fw) if fw else None
            if existing:
                filepath = existing
            else:
                filepath = ctrl.service_backup(backup_dir)
            resp = ctrl.service_reset_hardware()
            ctrl._close_terminal()
            self._state = "IDLE"
            return {"status": "ok", "response": resp, "backup": filepath}
        return self._raw_locked_operation(_do)

    # === OS shell operations (no service mode) ===

    def set_mode(self, mode: str) -> Dict[str, str]:
        return self._locked_operation(
            lambda c: (c.set_mode(mode), {"status": "ok", "message": f"Mode set to {mode.upper()}"})[1])

    def set_silent(self, state: str) -> Dict[str, str]:
        return self._locked_operation(
            lambda c: (c.set_silent(state.lower() == "on"), {"status": "ok", "message": f"Silent mode {state.upper()}"})[1])

    # === Service mode manual control ===

    def enter_service(self) -> Dict[str, str]:
        def _do(ctrl):
            welcome = ctrl.enter_service_mode()
            self._state = "SERVICE"
            return {"status": "ok", "state": "SERVICE", "welcome": welcome}
        return self._raw_locked_operation(_do)

    def exit_service(self, target_mode: str = "AUTO") -> Dict[str, str]:
        def _do(ctrl):
            resp = ctrl.exit_service_mode(target_mode)
            self._state = "IDLE"
            return {"status": "ok", "state": "IDLE", "response": resp}
        return self._raw_locked_operation(_do)

    def get_state(self) -> str:
        return self._state

    # === Firmware ===
    # Firmware update progress (polled by UI)
    _fw_progress: Dict[str, Any] = {"stage": "idle", "percent": 0}

    def flash_firmware(self, bin_path: str) -> Dict[str, str]:
        def _progress(stage, pct):
            self._fw_progress = {"stage": stage, "percent": pct}

        def _do(ctrl):
            self._fw_progress = {"stage": "starting", "percent": 0}
            result = ctrl.update_firmware(bin_path, skip_backup=False, progress_cb=_progress)
            self._info_cache = None
            self._fw_progress = {"stage": "done", "percent": 100}
            written = result.get("written", 0) if result else 0
            return {"status": "ok", "message": f"Firmware uploaded ({written} bytes). Device rebooting."}
        return self._raw_locked_operation(_do)

    @staticmethod
    def check_latest_firmware() -> Dict[str, Any]:
        """Parse yachtd.com/downloads/ for YDNU-02 firmware info."""
        url = "https://www.yachtd.com/downloads/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "YDNU02-Console/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

            # Find the firmware block: <a name="ydnufw" href="UUPDATE.zip" ...>
            fw_match = re.search(
                r'name="ydnufw"\s+href="([^"]+)".*?'        # download URL
                r'Firmware\s+Update.*?'                       # label
                r'text-dark-400">\s*([\w\s,]+?\d{4})\s*<.*?' # date (e.g. "August 7, 2025")
                r'text-dark-400[^>]*>\s*([\d.]+)\s*<',        # version (e.g. "1.75")
                html, re.DOTALL | re.IGNORECASE
            )
            if not fw_match:
                return {"status": "ok", "latest_version": None,
                        "message": "Could not parse firmware block", "url": url}

            download_file = fw_match.group(1).strip()
            date_raw = fw_match.group(2).strip()
            version = fw_match.group(3).strip()
            download_url = f"https://www.yachtd.com/downloads/{download_file}"

            # Normalize date: "August 7, 2025" → "07/08/2025" (DD/MM/YYYY like firmware)
            from datetime import datetime as _dt
            try:
                dt = _dt.strptime(date_raw, "%B %d, %Y")
                date = dt.strftime("%d/%m/%Y")
            except ValueError:
                date = date_raw

            # Try to get changelog
            changelog = ""
            cl_match = re.search(
                r'name="ydnufw".*?border-t\s+border-slate-200">\s*(.*?)\s*</div>',
                html, re.DOTALL | re.IGNORECASE
            )
            if cl_match:
                changelog = cl_match.group(1).strip()

            return {
                "status": "ok",
                "latest_version": version,
                "release_date": date,
                "download_url": download_url,
                "changelog": changelog,
                "url": url,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "url": url}

    # --- Monitoring (long-running, uses lock while active) ---

    async def monitor_raw(self, websocket: WebSocket, duration: float = 300.0):
        """Subscribe to bus worker's frame stream via async queue. No direct port access."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._queues_lock:
            self._monitor_queues.append(q)

        if self._state != "LISTENING":
            await websocket.send_json({"type": "error", "message": "Bus worker not active — no NMEA data"})

        await websocket.send_json({"type": "status", "message": "RAW monitoring started"})

        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=1.0)
                    await websocket.send_json(frame)
                except asyncio.TimeoutError:
                    continue  # no frames yet, keep waiting

        except WebSocketDisconnect:
            pass
        finally:
            with self._queues_lock:
                if q in self._monitor_queues:
                    self._monitor_queues.remove(q)

    # ─── N2K device info — delegates to N2KPGNDecoder (ydnu02.py) ───

    @staticmethod
    def _build_device_msg(dev: Dict[str, Any]) -> Dict[str, Any]:
        """Build a clean device message for WebSocket client."""
        return {
            "src": dev.get("src", 0),
            "manufacturer": dev.get("manufacturer", "Unknown"),
            "model": dev.get("model", ""),
            "serial": dev.get("serial", ""),
            "firmware": dev.get("firmware", ""),
            "unique_id": dev.get("unique_id", 0),
            "function_name": dev.get("function_name", ""),
            "device_class_name": dev.get("device_class_name", ""),
            "mfg_code": dev.get("mfg_code", 0),
            "product_code": dev.get("product_code", 0),
        }

    async def scan_bus(self, websocket: WebSocket, duration: float = 10.0):
        """Scan CAN bus with ISO Requests and stream results."""
        # Pause bus worker to avoid port contention
        self._pause_event.set()
        await asyncio.sleep(0.3)

        ctrl = self._get_ctrl()
        opened = False
        with self._lock:
            ctrl._close_terminal()  # ensure worker's FD is closed
            self._state = "MONITORING"
            ctrl.set_mode("RAW")
            opened = ctrl._open_terminal()
            if opened:
                ctrl._write(b"0\n")
                ctrl._write(b"18EAFF10 00 EE 00\r\n")  # Address Claim
                ctrl._write(b"18EAFF10 14 F0 01\r\n")  # Product Info

        if not opened:
            await websocket.send_json({"type": "error", "message": "Cannot open port"})
            return

        await websocket.send_json({"type": "status", "message": f"Scanning for {duration}s..."})

        devices: Dict[int, Dict[str, Any]] = {}
        frame_count = 0

        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                try:
                    if ctrl.ser and ctrl.ser.is_open and ctrl.ser.in_waiting:
                        line = ctrl.ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            parsed = N2KPGNDecoder.parse_raw_line(line)
                            if parsed:
                                frame_count += 1
                                src = parsed["info"]["src"]
                                pgn = parsed["info"]["pgn"]

                                await websocket.send_json({
                                    "type": "frame",
                                    "time": parsed["time"],
                                    "pgn": pgn,
                                    "src": src,
                                    "decoded": parsed["decoded"],
                                })

                                if src not in devices:
                                    devices[src] = {"src": src}
                                if pgn in (60928, 126996):
                                    devices[src].update(N2KPGNDecoder.parse_device_info(parsed))

                                # Send device update on both PGNs
                                if pgn in (60928, 126996):
                                    await websocket.send_json({
                                        "type": "device",
                                        **self._build_device_msg(devices[src]),
                                    })
                except OSError as e:
                    print(f"[Gateway] Scan read error: {e}")
                    await websocket.send_json({"type": "error", "message": f"Serial port error: {e}"})
                    ctrl._close_terminal()
                    await asyncio.sleep(1.0)
                    if not ctrl._open_terminal():
                        await websocket.send_json({"type": "error", "message": "Port recovery failed"})
                        break
                    ctrl._write(b"0\n")
                    await asyncio.sleep(0.5)
                    continue

                await asyncio.sleep(0.01)

            # Send final summary
            for src, info in sorted(devices.items()):
                await websocket.send_json({
                    "type": "device",
                    **self._build_device_msg(info),
                })

            await websocket.send_json({
                "type": "done",
                "device_count": len(devices),
                "frame_count": frame_count,
            })

        except WebSocketDisconnect:
            pass
        finally:
            with self._lock:
                ctrl._close_terminal()
                self._state = "IDLE"
            self._pause_event.clear()  # resume bus worker
