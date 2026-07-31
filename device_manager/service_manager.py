"""YDNU-02 service terminal and settings operations.

Wraps YDNU02Controller service commands with OperationRunner lifecycle patterns.
"""

import os
import glob
import time
from typing import Dict, Any, Optional, Callable
from device_manager.operation_runner import OperationRunner


class ServiceManager:
    """High-level service terminal operations for REST API.

    Every method returns a JSON-serializable dict.
    All operations are serialized via OperationRunner.
    """

    def __init__(self,
                 ops: OperationRunner,
                 get_ctrl: Callable[[], Any],
                 set_state: Optional[Callable[[str], None]] = None,
                 cache_ttl: float = 60.0,
                 backup_dir: Optional[str] = None):
        self._ops = ops
        self._get_ctrl = get_ctrl
        self._set_state = set_state or (lambda s: None)
        self._cache_ttl = cache_ttl
        self._info_cache: Optional[Dict[str, Any]] = None
        self._info_cache_time: float = 0.0
        # Single source of truth for backup directory.
        # Default is the project root (one level above device_manager/).
        self._backup_dir: str = backup_dir or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )

    def invalidate_info_cache(self) -> None:
        """Invalidate cached get_info() result (e.g. after firmware flash)."""
        self._info_cache = None
        self._info_cache_time = 0.0

    def get_info(self, force: bool = False) -> Dict[str, Any]:
        """Read device info from YDNU-02 service terminal (HELP command)."""
        if not force and self._info_cache and (time.time() - self._info_cache_time) < self._cache_ttl:
            return self._info_cache

        def _do(ctrl):
            welcome = getattr(ctrl, "_welcome_text", "")
            if not welcome or "Firmware version" not in welcome:
                welcome = ctrl._send_terminal_command("HELP", wait=2.0)
            info = ctrl._parse_welcome_screen(welcome)
            info["port"] = ctrl.port
            info["state"] = "online"
            return info

        result = self._ops.service_operation(_do)
        if result and result.get("firmware_version"):
            self._info_cache = result
            self._info_cache_time = time.time()
        return result

    def get_filters(self) -> Dict[str, Any]:
        """Read all 8 YDNU-02 filter tables via service terminal (PRINT commands)."""
        FILTER_NAMES = ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                        "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]

        def _do(ctrl):
            filters = {}
            for name in FILTER_NAMES:
                raw = ctrl._send_terminal_command(f"PRINT {name}", wait=1.5)
                records, ftype = 0, "BLACK"
                if "contains" in raw:
                    try:
                        records = int(raw.split("contains")[1].strip().split()[0])
                    except (ValueError, IndexError):
                        pass
                if "type is" in raw:
                    try:
                        ftype = raw.split("type is")[1].strip().split()[0].upper()
                    except IndexError:
                        pass
                filters[name] = {"records": records, "type": ftype, "raw": raw}
                time.sleep(0.15)
            return {"filters": filters}

        return self._ops.service_operation(_do)

    def get_settings(self) -> Dict[str, str]:
        """Read current YDNU-02 settings via service terminal (HELP SET)."""
        return self._ops.service_operation(
            lambda c: {"settings_raw": c._send_terminal_command("HELP SET", wait=2.0)})

    def get_diag(self, scope: str) -> Dict[str, str]:
        """Run DIAG command in service terminal."""
        return self._ops.service_operation(
            lambda c: {"data": c._send_terminal_command(f"DIAG {scope.upper()}", wait=10.0)})

    def send_service_cmd(self, cmd: str) -> Dict[str, str]:
        """Send an arbitrary service terminal command and return the response."""
        return self._ops.service_operation(
            lambda c: {"response": c._send_terminal_command(cmd, wait=3.0)})

    # ── Backup helpers ────────────────────────────────────────────────────────

    def _find_existing_backup(self, fw_version: str) -> Optional[str]:
        """Check if a backup for this firmware version already exists in backup_dir."""
        fw_norm = fw_version.replace(' ', '_').replace('/', '-')
        pattern = os.path.join(self._backup_dir, f"ydnu02_backup_*_fw{fw_norm}_*.json")
        existing = sorted(glob.glob(pattern), reverse=True)
        return existing[0] if existing else None

    def create_backup(self, force: bool = False) -> Dict[str, str]:
        """Create a settings backup via service terminal."""
        if not force and self._info_cache and self._info_cache.get("firmware_version"):
            existing = self._find_existing_backup(self._info_cache["firmware_version"])
            if existing:
                return {"status": "skipped", "filepath": existing,
                        "filename": os.path.basename(existing),
                        "message": "Backup already exists for this firmware version"}

        def _do(ctrl):
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
            filepath = ctrl.service_backup(self._backup_dir)
            return {"status": "ok", "filepath": filepath, "filename": os.path.basename(filepath)}

        return self._ops.service_operation(_do)

    def reset_settings(self) -> Dict[str, str]:
        """Reset all YDNU-02 settings to factory defaults via service terminal."""
        return self._ops.service_operation(
            lambda c: {"status": "ok", "response": c.service_reset_settings()})

    def reset_filters(self) -> Dict[str, str]:
        """Reset all YDNU-02 filter tables via service terminal."""
        return self._ops.service_operation(
            lambda c: {"status": "ok", "response": c.service_reset_filters()})

    # ── Reset operations ──────────────────────────────────────────────────────

    def reset_mcu(self) -> Dict[str, str]:
        """Soft MCU reset via service terminal (RESET MCU command)."""
        def _do(ctrl):
            ctrl.enter_service_mode()
            resp = ctrl.service_reset_mcu()
            ctrl._close_terminal()
            self._set_state("IDLE")
            return {"status": "ok", "response": resp}

        return self._ops.raw_locked_operation(_do)

    def reset_hardware(self) -> Dict[str, str]:
        """Full hardware reset via service terminal (RESET HARDWARE)."""
        def _do(ctrl):
            ctrl.enter_service_mode()
            welcome = ctrl._send_terminal_command("HELP", wait=2.0)
            info = ctrl._parse_welcome_screen(welcome)
            fw = info.get("firmware_version", "")
            existing = self._find_existing_backup(fw) if fw else None
            filepath = existing or ctrl.service_backup(self._backup_dir)
            resp = ctrl.service_reset_hardware()
            ctrl._close_terminal()
            self._set_state("IDLE")
            return {"status": "ok", "response": resp, "backup": filepath}

        return self._ops.raw_locked_operation(_do)

    # ── OS shell operations ───────────────────────────────────────────────────

    def set_mode(self, mode: str) -> Dict[str, str]:
        """Switch YDNU-02 operating mode (AUTO/RAW/N2K/0183)."""
        return self._ops.locked_operation(
            lambda c: (c.set_mode(mode), {"status": "ok", "message": f"Mode set to {mode.upper()}"})[1])

    def set_silent(self, state: str) -> Dict[str, str]:
        """Enable/disable YDNU-02 silent mode (suppresses TX on bus)."""
        return self._ops.locked_operation(
            lambda c: (c.set_silent(state.lower() == "on"),
                       {"status": "ok", "message": f"Silent mode {state.upper()}"})[1])

    # ── Manual service mode control ───────────────────────────────────────────

    def enter_service(self) -> Dict[str, str]:
        """Manually enter service mode for interactive service terminal in UI."""
        def _do(ctrl):
            welcome = ctrl.enter_service_mode()
            self._set_state("SERVICE")
            return {"status": "ok", "state": "SERVICE", "welcome": welcome}

        return self._ops.raw_locked_operation(_do)

    def exit_service(self, target_mode: str = "AUTO") -> Dict[str, str]:
        """Manually exit service mode and return device to target_mode."""
        def _do(ctrl):
            resp = ctrl.exit_service_mode(target_mode)
            self._set_state("IDLE")
            return {"status": "ok", "state": "IDLE", "response": resp}

        return self._ops.raw_locked_operation(_do)
