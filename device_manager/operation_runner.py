"""Three operation patterns for YDNU-02 interactions.

All patterns handle bus worker pause/resume, service lock serialization,
and proxy control client passthrough setup.
"""

import time
import threading
from typing import Callable, Any, Optional
from device_manager.tcp_connection import ProxyControlClient


class OperationRunner:
    """Executes YDNU-02 operations with proper bus worker lifecycle.

    Three patterns (least → most complex):
      1. service_operation    — full interactive service terminal session
      2. locked_operation     — OS shell command (MODE RAW, SILENT)
      3. raw_locked_operation — raw passthrough (reboots, firmware flash)

    All patterns:
      - Acquire service_lock (serialize concurrent API operations)
      - Pause bus_worker via pause_event (stop reading the TCP data stream)
      - Open ProxyControlClient for serial access via the CTRL port
      - Execute user function with ctrl._passthrough wired to the PCC
      - Resume bus_worker on exit (even on exception)

    Lock architecture:
      service_lock    — outer coarse lock; one service operation at a time.
                        Prevents two REST endpoints from racing (e.g. /backup
                        and /reset called simultaneously by the UI).
      controller_lock — inner fine lock shared with bus_worker's on_frame
                        callback. Prevents the frame parser from reading a
                        partially-initialised YDNU02Controller while
                        _passthrough is being set/cleared.
    """

    def __init__(self,
                 pause_event: threading.Event,
                 service_lock: threading.Lock,
                 controller_lock: threading.Lock,
                 get_ctrl: Callable[[], Any],
                 set_state: Optional[Callable[[str], None]] = None):
        self._pause_event = pause_event
        self._service_lock = service_lock
        self._controller_lock = controller_lock
        self._get_ctrl = get_ctrl
        self._set_state = set_state or (lambda state: None)

    def service_operation(self, func: Callable, exit_mode: str = "RAW") -> Any:
        """Full service mode operation pattern.

        Stops bus worker → enters service via control port → runs func(ctrl)
        with YDNU02Controller._passthrough wired to ProxyControlClient →
        exits service → resumes bus worker.

        Args:
            exit_mode: Target mode after service session (default "RAW").
                       "RAW" is used here rather than "AUTO" because the
                       proxy CTRL handler already sends MODE RAW on
                       SERVICE_END. Sending it twice is harmless; asking
                       for "AUTO" would add a round-trip without benefit.
        """
        with self._service_lock:
            self._pause_event.set()
            # 200 ms guard: give the bus_worker's current readline() call
            # time to return before we take the CTRL port. Without this
            # pause the worker can race with ProxyControlClient.enter_service()
            # and issue a second SERVICE_START before the first READY arrives.
            time.sleep(0.2)
            pcc = ProxyControlClient()
            try:
                pcc.enter_service()
                with self._controller_lock:
                    ctrl = self._get_ctrl()
                    try:
                        welcome = ctrl.enter_service_mode()
                        ctrl._welcome_text = welcome
                        result = func(ctrl)
                        ctrl.exit_service_mode(exit_mode)
                        ctrl._passthrough = None
                        self._set_state("IDLE")
                        return result
                    except Exception:
                        ctrl._passthrough = None
                        ctrl._close_terminal()
                        self._set_state("IDLE")
                        raise
            finally:
                pcc.exit_service()
                self._pause_event.clear()

    def locked_operation(self, func: Callable) -> Any:
        """OS shell command pattern (no service terminal needed).

        Used for Level-1 commands (MODE, SILENT) that write directly to the
        closed serial port via echo. Still needs the CTRL port paused so the
        gateway does not interfere with the echo write.
        """
        with self._service_lock:
            self._pause_event.set()
            # Same 200 ms guard as service_operation — see rationale there.
            time.sleep(0.2)
            pcc = ProxyControlClient()
            try:
                pcc.enter_service()
                with self._controller_lock:
                    ctrl = self._get_ctrl()
                    ctrl._passthrough = pcc
                    try:
                        result = func(ctrl)
                        self._set_state("IDLE")
                        return result
                    finally:
                        ctrl._passthrough = None
            finally:
                pcc.exit_service()
                self._pause_event.clear()

    def raw_locked_operation(self, func: Callable) -> Any:
        """Raw operation pattern — func manages its own exit from service mode.

        Used when the operation causes the device to reboot or disconnect
        (MCU reset, hardware reset, firmware flash). The caller is responsible
        for calling ctrl._close_terminal() before the device reboots so the
        serial port is released cleanly; if the device disconnects mid-flight
        the except block below handles cleanup.
        """
        with self._service_lock:
            self._pause_event.set()
            # Same 200 ms guard as service_operation — see rationale there.
            time.sleep(0.2)
            pcc = ProxyControlClient()
            try:
                pcc.enter_service()
                with self._controller_lock:
                    ctrl = self._get_ctrl()
                    ctrl._passthrough = pcc
                    try:
                        return func(ctrl)
                    except Exception:
                        ctrl._passthrough = None
                        ctrl._close_terminal()
                        self._set_state("IDLE")
                        raise
            finally:
                pcc.exit_service()
                self._pause_event.clear()
