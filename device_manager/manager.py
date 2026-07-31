"""DeviceManager Facade module.

Orchestrates specialized sub-managers for connection, sensor tracking,
error logging, operation lifecycles, service terminal commands,
OTA firmware updates, WebSocket streaming, and bus worker loop.
"""

import time
import asyncio
import threading
import re
from typing import Optional, Dict, Any, List
try:
    from fastapi import WebSocket
except ImportError:
    WebSocket = Any  # type: ignore

# ydnu02 and sensors packages are resolved via PYTHONPATH (set in the
# systemd/launchd unit file) or by running from the project root.
# Do NOT use sys.path.insert() here — modifying sys.path in library
# code breaks test isolation and masks missing-package errors.
import os
from ydnu02 import YDNU02Controller


def get_app_version() -> str:
    """Read software version from VERSION file in the project root."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, '..', 'VERSION'),
                 os.path.join(here, 'VERSION')):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                ver = f.read().strip()
                if ver:
                    return ver
        except OSError:
            pass
    return '0.0.0'

from device_manager.error_logger import ErrorLogger
from device_manager.sensor_registry import SensorRegistry
from device_manager.operation_runner import OperationRunner
from device_manager.service_manager import ServiceManager
from device_manager.firmware_manager import FirmwareManager
from device_manager.ws_stream_hub import WSStreamHub
from device_manager.bus_worker import BusWorker
from device_manager.tcp_connection import (
    TCPProxyConnection,
    ProxyControlClient,
    _PROXY_HOST,
    _PROXY_DATA_PORT,
    _PROXY_CTRL_PORT,
)


class DeviceManager:
    """Central facade for NMEA 2000 data and YDNU-02 operations."""

    def __init__(self, port: Optional[str] = None, debug: bool = False):
        self.port = port
        self.debug = debug

        self._lock = threading.Lock()
        self._service_lock = threading.Lock()
        self._ctrl: Optional[YDNU02Controller] = None
        self._state = "IDLE"

        self._cache_ttl: float = 60.0

        self._sensors_lock = threading.Lock()
        self._sensor_registry = SensorRegistry(lock=self._sensors_lock)
        self.sensors = self._sensor_registry.sensors
        self._discovered_bus_devices = self._sensor_registry.discovered_bus_devices

        self._pause_event = threading.Event()

        self._ws_clients: List[WebSocket] = []
        self._monitor_queues: List[asyncio.Queue] = []
        self._queues_lock = threading.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        self._tcp: Optional[TCPProxyConnection] = None

        self._error_log_lock = threading.Lock()
        self._error_logger = ErrorLogger(lock=self._error_log_lock, max_size=500)

        self._op_runner = OperationRunner(
            pause_event=self._pause_event,
            service_lock=self._service_lock,
            controller_lock=self._lock,
            get_ctrl=self._get_ctrl,
            set_state=self._set_state,
        )

        self._service_mgr = ServiceManager(
            ops=self._op_runner,
            get_ctrl=self._get_ctrl,
            set_state=self._set_state,
            cache_ttl=self._cache_ttl,
        )

        self._fw_mgr = FirmwareManager(
            ops=self._op_runner,
            invalidate_cache=self._service_mgr.invalidate_info_cache,
        )

        self._ws_hub = WSStreamHub(
            queues_lock=self._queues_lock,
            monitor_queues=self._monitor_queues,
            get_discovered_devices=self._sensor_registry.get_bus_devices,
            get_state=self.get_state,
        )

        def _on_frame(parsed: Dict[str, Any]):
            self._update_sensor_state(parsed)
            self._broadcast_frame(parsed)

        self._bus_worker_mgr = BusWorker(
            pause_event=self._pause_event,
            on_frame=_on_frame,
            set_state=self._set_state,
        )

    def _get_ctrl(self) -> YDNU02Controller:
        """Lazy-init YDNU02Controller."""
        if self._ctrl is None:
            self._ctrl = YDNU02Controller(port=self.port, debug=self.debug)
        return self._ctrl

    def get_port(self) -> str:
        """Returns connection string."""
        return self.port or _PROXY_HOST + ":" + str(_PROXY_DATA_PORT)

    def _set_state(self, state: str) -> None:
        self._state = state

    def start_bus_worker(self) -> None:
        """Start the bus worker thread."""
        self._bus_worker_mgr.start()

    def stop_bus_worker(self) -> None:
        """Stop the bus worker thread."""
        self._bus_worker_mgr.stop()

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store asyncio event loop reference."""
        self._event_loop = loop

    def _broadcast_frame(self, parsed: Dict[str, Any]) -> None:
        """Broadcast frame to WS queues."""
        self._ws_hub.broadcast_frame(parsed, self._event_loop)

    def get_sensors_state(self) -> Dict[str, Any]:
        """Return live sensor state dictionary from SensorRegistry."""
        return self._sensor_registry.get_sensors_state()

    def get_bus_devices(self) -> Dict[int, Dict[str, Any]]:
        """Return discovered bus devices dictionary from SensorRegistry."""
        return self._sensor_registry.get_bus_devices()

    def _update_sensor_state(self, parsed: Dict[str, Any]) -> None:
        """Update sensor registry state from NMEA frame."""
        self._sensor_registry.update(parsed)
        decoded_str = parsed.get("decoded", "") or ""
        if decoded_str and not re.search(r"error active", decoded_str, re.IGNORECASE):
            if re.search(r"error|fault|fail|bus off", decoded_str, re.IGNORECASE):
                self._record_error_event(parsed)

    def _get_dev_name(self, src: int) -> str:
        dev = self._discovered_bus_devices.get(src, {})
        if dev:
            mfr = dev.get("manufacturer", "")
            mdl = dev.get("model", "")
            return f"{mfr} {mdl}".strip() or f"Device SA:{src}"
        return f"Device SA:{src}"

    def _record_error_event(self, parsed: Dict[str, Any]) -> None:
        self._error_logger.record(parsed, get_dev_name=self._get_dev_name)

    def get_error_log(self, limit: int = 100, src: Optional[int] = None) -> Dict[str, Any]:
        return self._error_logger.get_log(limit=limit, src=src)

    def clear_error_log(self) -> Dict[str, Any]:
        return self._error_logger.clear()

    def get_info(self, force: bool = False) -> Dict[str, Any]:
        return self._service_mgr.get_info(force=force)

    def get_filters(self) -> Dict[str, Any]:
        return self._service_mgr.get_filters()

    def get_settings(self) -> Dict[str, str]:
        return self._service_mgr.get_settings()

    def get_diag(self, scope: str) -> Dict[str, str]:
        return self._service_mgr.get_diag(scope=scope)

    def send_service_cmd(self, cmd: str) -> Dict[str, str]:
        return self._service_mgr.send_service_cmd(cmd=cmd)

    def create_backup(self, force: bool = False) -> Dict[str, str]:
        return self._service_mgr.create_backup(force=force)

    def reset_settings(self) -> Dict[str, str]:
        return self._service_mgr.reset_settings()

    def reset_filters(self) -> Dict[str, str]:
        return self._service_mgr.reset_filters()

    def reset_mcu(self) -> Dict[str, str]:
        return self._service_mgr.reset_mcu()

    def reset_hardware(self) -> Dict[str, str]:
        return self._service_mgr.reset_hardware()

    def set_mode(self, mode: str) -> Dict[str, str]:
        return self._service_mgr.set_mode(mode=mode)

    def set_silent(self, state: str) -> Dict[str, str]:
        return self._service_mgr.set_silent(state=state)

    def enter_service(self) -> Dict[str, str]:
        return self._service_mgr.enter_service()

    def exit_service(self, target_mode: str = "AUTO") -> Dict[str, str]:
        return self._service_mgr.exit_service(target_mode=target_mode)

    def get_state(self) -> str:
        return self._state

    def get_app_version(self) -> str:
        return get_app_version()

    @property
    def _fw_progress(self) -> Dict[str, Any]:
        return self._fw_mgr.fw_progress

    def flash_firmware(self, bin_path: str) -> Dict[str, str]:
        return self._fw_mgr.flash_firmware(bin_path)

    @staticmethod
    def check_latest_firmware() -> Dict[str, Any]:
        return FirmwareManager.check_latest_firmware()

    async def monitor_raw(self, websocket: WebSocket, duration: float = 300.0) -> None:
        await self._ws_hub.monitor_raw(websocket, duration=duration)

    async def scan_bus(self, websocket: WebSocket, duration: float = 10.0) -> None:
        await self._ws_hub.scan_bus(websocket, duration=duration)
