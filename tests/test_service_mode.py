#!/usr/bin/env python3
"""
tests/test_service_mode.py
==========================
Service mode tests for YDNU-02 proxy at four levels:

  1. TestProxyCtrlProtocol    -- raw TCP tests against the ctrl port (:4002)
  2. TestServiceModeRaceFix   -- verify the race-condition fix components
  3. TestProxyControlClient   -- unit tests for ProxyControlClient class
  4. TestDeviceManagerService -- DeviceManager enter/exit_service integration

SERVICE MODE ARCHITECTURE (mini-skill)
======================================
YDNU-02 normally runs in RAW mode: the proxy reads ASCII NMEA frames from
serial and broadcasts them to all TCP clients on :4001.

For service mode the proxy switches to PASSTHROUGH:

  1. Client connects to :4002 (CTRL port)
  2. Client → proxy:  SERVICE_START\\n
  3. Proxy:  service_mode.set()         → serial_reader stops forwarding
             sleep(0.15)                → wait for serial_reader to exit readline()
             reset_input_buffer()       → discard stale NMEA frames accumulated
                                          during the transition window
             READY\\n                   → client can now send device commands
  4. Client → proxy → serial: passthrough (write / read)
  5. Client → proxy:  SERVICE_END\\n
  6. Proxy:  service_mode.clear()       → serial_reader resumes broadcast
             OK\\n

CRITICAL IMPLEMENTATION DETAILS
================================
• serial.Serial(timeout=0.1)  — serial_reader yields quickly (not every 2s)
• conn.settimeout(0.1)        — ctrl handler polls serial → client every 100ms
• sleep(0.15) + reset_input_buffer() — without this, serial_reader finishes
  its current readline() AFTER the proxy sends READY, reads the device reply
  ("YDNU MODE SERVICE" response) before the ctrl client, discards it through
  the NMEA filter, and the ctrl client gets an empty response (race condition)
• Only ONE ctrl session allowed at a time (service_conn_lock + service_conn)
• ProxyControlClient.__init__ captures _PROXY_CTRL_PORT as a DEFAULT ARG at
  class definition time — patching the module variable afterwards has no effect.
  In tests always pass port= explicitly or patch the class itself in dm.
"""
import os
import sys
import socket
import threading
import time
import types
import importlib
import importlib.util
import unittest
from unittest.mock import MagicMock, patch, call

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# STUB OUT SERVER-ONLY DEPENDENCIES
# ---------------------------------------------------------------------------
# device_manager.py imports fastapi, ydnu02, sensors — not installed locally
# (only on gateway.local). Register MagicMock modules in sys.modules BEFORE the
# first import of device_manager. Standard pattern for testing server code
# without installing the full stack.

def _mock_module(name: str, **attrs) -> MagicMock:
    """Create a MagicMock module and register it under sys.modules[name]."""
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

# fastapi — only needed for type hints (WebSocket, WebSocketDisconnect)
if 'fastapi' not in sys.modules:
    fa = _mock_module('fastapi')
    fa.WebSocket = MagicMock
    fa.WebSocketDisconnect = Exception
    _mock_module('fastapi.websockets')
    _mock_module('fastapi.routing')

# ydnu02 — YDNU02Controller and N2KPGNDecoder (real hardware interaction)
if 'ydnu02' not in sys.modules:
    _mock_module('ydnu02',
                 YDNU02Controller=MagicMock,
                 N2KPGNDecoder=MagicMock)

# sensors — sensor drivers (Gobius C, Mopeka)
if 'sensors' not in sys.modules:
    _mock_module('sensors', GobiusCSensor=MagicMock)
    _mock_module('sensors.base_sensor')
    _mock_module('sensors.gobius_sensor')
    _mock_module('sensors.mopeka_sensor')


# ===========================================================================
# HELPERS
# ===========================================================================

def _free_port() -> int:
    """Return a free TCP port on localhost (bind → get → close)."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tcp_connect(port: int, timeout: float = 3.0) -> socket.socket:
    """Open a TCP connection to 127.0.0.1:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(('127.0.0.1', port))
    return s


def _recv_line(sock: socket.socket, timeout: float = 3.0) -> str:
    """
    Read one \\n-terminated line from a socket.
    Returns the decoded, stripped string.

    WHY not makefile().readline():
    After socket.timeout, Python's makefile enters a broken state with error
    "cannot read from timed out object". We read via raw recv() with manual
    buffering to avoid this Python bug.
    """
    sock.settimeout(timeout)
    buf = b''
    while b'\n' not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError('Socket closed before newline')
        buf += chunk
    return buf.split(b'\n')[0].decode('utf-8', errors='replace').strip()


def _load_proxy_module(ctrl_port: int = 0) -> types.ModuleType:
    """
    Load an isolated copy of nmea_tcp_proxy.py.

    Each call returns a NEW module with a unique name — this is essential
    for test isolation: every test gets its own service_mode Event, clients
    set, service_conn, etc. Without this, all tests would share one global
    service_mode flag and interfere with each other.
    """
    spec = importlib.util.spec_from_file_location(
        f'nmea_tcp_proxy_{ctrl_port}',
        os.path.join(ROOT, 'nmea_tcp_proxy.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Override constants AFTER exec_module (exec sets the defaults from env)
    mod.SERIAL_PORT  = '/dev/null'
    mod.TCP_PORT     = 0           # DATA port not needed in ctrl-only tests
    mod.CTRL_PORT    = ctrl_port
    mod.clients      = set()
    mod.clients_lock = threading.Lock()
    mod.serial_lock  = threading.Lock()
    mod.serial_instance = None
    return mod


def _start_ctrl_server(mod: types.ModuleType, ctrl_port: int,
                       fake_serial) -> threading.Event:
    """
    Start a TCP server on ctrl_port in a background thread.
    Each incoming connection is dispatched to mod.handle_ctrl_client().

    Returns stop_event — set it in tearDown() to shut the server down.

    WHY not start the full proxy:
    We only need the ctrl handler in isolation. The full proxy also starts
    serial_reader and a DATA server — unnecessary dependencies for unit tests.
    """
    mod.serial_instance = fake_serial
    ready = threading.Event()
    stop  = threading.Event()

    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', ctrl_port))
        srv.listen(5)
        srv.settimeout(0.1)   # check stop_event every 100ms
        ready.set()
        while not stop.is_set():
            try:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=mod.handle_ctrl_client,
                    args=(conn, addr),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    ready.wait(timeout=3.0)
    return stop


def _make_fake_serial(read_data: bytes = b'') -> MagicMock:
    """
    Create a MagicMock simulating serial.Serial.

    Key attributes:
    - is_open = True        : proxy checks this before write
    - in_waiting = 0        : no data in buffer (default — quiet bus)
    - readline() → b''      : empty response (quiet bus)
    - read() → read_data    : simulates a device response payload
    - reset_input_buffer    : tracked call (race-condition test)
    """
    fake = MagicMock()
    fake.is_open       = True
    fake.in_waiting    = 0
    fake.readline.return_value = b''
    fake.read.return_value     = read_data
    return fake


# ===========================================================================
# 1. RAW TCP CTRL PROTOCOL TESTS
# ===========================================================================

class TestProxyCtrlProtocol(unittest.TestCase):
    """
    Tests for the raw TCP line protocol on the ctrl port (:4002).

    Verifies that handle_ctrl_client correctly:
    - replies READY to SERVICE_START
    - replies OK to SERVICE_END
    - forwards passthrough commands to serial
    - rejects commands sent without SERVICE_START
    - rejects a second simultaneous ctrl client
    - releases the session after client disconnect
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

    def tearDown(self):
        self.stop.set()
        # Reset global proxy state between tests
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_service_start_returns_ready(self):
        """
        SERVICE_START → READY.

        WHY READY instead of OK:
        The proxy must complete its init sequence (sleep + reset_input_buffer)
        BEFORE allowing passthrough. READY signals: "initialisation done,
        you may now send device commands".
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            resp = _recv_line(sock)
        self.assertEqual(resp, 'READY')

    def test_service_end_returns_ok(self):
        """
        SERVICE_END (after SERVICE_START) → OK.

        OK means: proxy has resumed NMEA broadcast.
        Client should close the connection immediately after receiving OK.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)          # wait for READY
            sock.sendall(b'SERVICE_END\n')
            resp = _recv_line(sock)
        self.assertEqual(resp, 'OK')

    def test_firmware_start_end(self):
        """
        FIRMWARE_START/END are aliases for SERVICE_START/END.

        Used during OTA firmware updates. Same pause/resume mechanism,
        same passthrough. Different names for clarity in logs.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'FIRMWARE_START\n')
            self.assertEqual(_recv_line(sock), 'READY')
            sock.sendall(b'FIRMWARE_END\n')
            self.assertEqual(_recv_line(sock), 'OK')

    def test_passthrough_writes_to_serial(self):
        """
        Commands sent after READY are forwarded to serial.write().

        Data path: pcc.socket → proxy recv → serial_instance.write()
        The reverse direction (serial → ctrl socket) is tested in
        test_serial_data_forwarded_within_200ms.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # wait for READY
            sock.sendall(b'YDNU MODE SERVICE\r\n')
            time.sleep(0.15)  # give the handler time to write to serial

        self.fake_ser.write.assert_called()
        written = b''.join(c.args[0] for c in self.fake_ser.write.call_args_list)
        self.assertIn(b'YDNU MODE SERVICE', written)

    def test_command_before_start_returns_error(self):
        """
        Passthrough commands without prior SERVICE_START → ERROR.

        Protects against accidentally writing to serial without holding an
        exclusive session. Without SERVICE_START the proxy is still
        broadcasting NMEA frames — passthrough would conflict.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SOME COMMAND\n')
            resp = _recv_line(sock)
        self.assertIn('ERROR', resp)

    # -----------------------------------------------------------------------
    # Concurrency
    # -----------------------------------------------------------------------

    def test_second_session_rejected(self):
        """
        While a service session is active a second ctrl client gets ERROR.

        Guarantees that at any point in time only ONE client controls serial.
        service_conn_lock + service_conn = None/Socket implements this mutex
        at the proxy level.
        """
        with _tcp_connect(self.ctrl_port) as first:
            first.sendall(b'SERVICE_START\n')
            _recv_line(first)  # READY — first session is now active

            with _tcp_connect(self.ctrl_port) as second:
                resp = _recv_line(second, timeout=3.0)
            self.assertIn('ERROR', resp)

    def test_session_freed_after_disconnect(self):
        """
        After the first client disconnects a new client can enter service.

        handle_ctrl_client clears service_conn = None in a finally block
        on any exit path (normal, exception, client disconnect).
        This test verifies the cleanup actually happens.
        """
        with _tcp_connect(self.ctrl_port) as first:
            first.sendall(b'SERVICE_START\n')
            _recv_line(first)  # READY

        time.sleep(0.3)  # wait for handle_ctrl_client thread to finish cleanup

        with _tcp_connect(self.ctrl_port) as second:
            second.sendall(b'SERVICE_START\n')
            resp = _recv_line(second)
        self.assertEqual(resp, 'READY')


# ===========================================================================
# 2. RACE-CONDITION FIX TESTS
# ===========================================================================

class TestServiceModeRaceFix(unittest.TestCase):
    """
    Verifies the three components of the race-condition fix.

    THE PROBLEM (before the fix):
    serial_reader runs in a loop: readline() → broadcast. When DeviceManager
    calls enter_service(), the proxy sets service_mode.set(). But serial_reader
    might be mid-readline() with timeout=2.0s — it keeps reading for up to 2s.
    During this window YDNU-02 replies to "YDNU MODE SERVICE\\r\\n" — but
    serial_reader consumes the reply, tries to broadcast it (NMEA filter
    discards it), and the ctrl client gets an empty response.

    THE FIX (three parts):
    1. serial.Serial(timeout=0.1)       → serial_reader exits readline() in ≤100ms
    2. sleep(0.15)                      → wait for serial_reader to finish its
                                          current readline() before sending READY
    3. reset_input_buffer()             → discard anything serial_reader buffered
                                          during the transition window
    4. conn.settimeout(0.1) in handler  → fast serial→client polling (not 2s waits)
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

    def tearDown(self):
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def test_reset_input_buffer_called_on_service_start(self):
        """
        reset_input_buffer() must be called exactly ONCE on SERVICE_START.

        Called AFTER sleep(0.15) and BEFORE sending READY — at this point
        serial_reader has already exited readline(), and the buffer contains
        only stale NMEA data that must be discarded.

        Without this call: the ctrl client may receive stale NMEA frames
        as the "reply" to its first passthrough command.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # wait for READY (sent AFTER reset)

        self.fake_ser.reset_input_buffer.assert_called_once()

    def test_service_start_minimum_delay(self):
        """
        READY must not arrive instantly — the proxy sleeps ≥100ms between
        SERVICE_START and READY (actual sleep is 150ms).

        If READY arrives in <100ms the sleep() was removed or shortened,
        re-opening the race window for serial_reader.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            t0 = time.monotonic()
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            elapsed = time.monotonic() - t0

        self.assertGreaterEqual(elapsed, 0.10,
            f'READY arrived too fast ({elapsed:.3f}s) — sleep() removed?')

    def test_serial_data_forwarded_within_200ms(self):
        """
        With conn.settimeout(0.1), serial data must reach the ctrl client
        within ~200ms. Before the fix this took up to 2 seconds.

        Simulates: device replies → in_waiting > 0 → read() → proxy
        forwards to ctrl socket within the next poll cycle (≤100ms).
        """
        response_payload = b'YDNU-02 Service Terminal\n'
        self.fake_ser.in_waiting = len(response_payload)
        self.fake_ser.read.return_value = response_payload

        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            sock.sendall(b'YDNU MODE SERVICE\r\n')

            sock.settimeout(1.0)
            t0 = time.monotonic()
            try:
                data = sock.recv(1024)
                elapsed = time.monotonic() - t0
            except socket.timeout:
                self.fail('No serial data within 1s — conn.settimeout() too large')

        self.assertLess(elapsed, 0.5,
            f'Data forwarded after {elapsed:.3f}s — expected <0.5s with settimeout(0.1)')
        self.assertIn(b'YDNU-02', data)

    def test_service_mode_flag_set_during_session(self):
        """
        service_mode Event must be set between START and END, cleared after.

        serial_reader checks this flag on every iteration:
          if service_mode.is_set(): time.sleep(0.05); continue  # yield
        Without this flag serial_reader would keep reading and forwarding
        NMEA frames while the ctrl handler is talking to the device.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            self.assertTrue(self.mod.service_mode.is_set(),
                'service_mode not set after SERVICE_START')
            sock.sendall(b'SERVICE_END\n')
            _recv_line(sock)  # OK
            time.sleep(0.05)  # handler clears the flag after sending OK
            self.assertFalse(self.mod.service_mode.is_set(),
                'service_mode still set after SERVICE_END')


# ===========================================================================
# 3. ProxyControlClient UNIT TESTS
# ===========================================================================

class TestProxyControlClient(unittest.TestCase):
    """
    Unit tests for ProxyControlClient — Python client class in device_manager.py.

    ProxyControlClient is a thin client for the ctrl port that:
    - Opens a TCP connection to the proxy :4002
    - Sends SERVICE_START / SERVICE_END
    - Forwards passthrough_write() data into the socket
    - Reads replies via passthrough_read_for(duration)

    KEY TRAP — default arg capture:
    class ProxyControlClient:
        def __init__(self, host=_PROXY_HOST, port=_PROXY_CTRL_PORT):
            ...
    Python evaluates default args ONCE at class definition time (module load).
    Even if we later change dm._PROXY_CTRL_PORT, __init__ already captured
    the old value (4002). In tests ALWAYS pass port=self.ctrl_port explicitly
    rather than patching the module variable.
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

    def tearDown(self):
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def _pcc(self):
        """
        Create a ProxyControlClient connected to our test ctrl server.
        port= is passed explicitly to bypass the default-arg capture issue.
        """
        from device_manager import ProxyControlClient
        return ProxyControlClient(port=self.ctrl_port)

    def test_enter_service_sends_start_and_gets_ready(self):
        """
        enter_service() sends SERVICE_START and must not raise on READY.
        If the proxy replies ERROR (not READY) it raises RuntimeError.
        """
        pcc = self._pcc()
        pcc.enter_service()   # must not raise
        pcc.exit_service()

    def test_exit_service_sends_end_and_gets_ok(self):
        """
        exit_service() sends SERVICE_END and must not raise on OK.
        Always call exit_service() in a finally block — otherwise the proxy
        stays in service mode and the next call gets ERROR.
        """
        pcc = self._pcc()
        pcc.enter_service()
        pcc.exit_service()  # must not raise

    def test_passthrough_write_delivers_to_serial(self):
        """
        passthrough_write() reaches serial.write() through the proxy.

        Data path: pcc.socket → proxy recv → serial_instance.write()
        YDNU02Controller uses this method for all commands in service mode:
          ctrl._passthrough = pcc
          ctrl.enter_service_mode()  # internally: self._write("YDNU MODE SERVICE\\r\\n")
        """
        pcc = self._pcc()
        pcc.enter_service()
        pcc.passthrough_write(b'YDNU MODE SERVICE\r\n')
        time.sleep(0.2)  # wait for the proxy to process and write to serial
        pcc.exit_service()

        written = b''.join(c.args[0] for c in self.fake_ser.write.call_args_list)
        self.assertIn(b'YDNU MODE SERVICE', written)

    def test_passthrough_read_for_collects_serial_lines(self):
        """
        passthrough_read_for(duration) collects serial data for duration seconds.

        Used in _read_response() to collect multi-line replies from YDNU-02
        (e.g. HELP outputs 10+ lines over ~300ms).
        Simulated here: serial.in_waiting > 0 → proxy reads → forwards.
        """
        response_payload = b'Welcome to YDNU-02 Service Terminal\n'
        self.fake_ser.in_waiting = len(response_payload)
        self.fake_ser.read.return_value = response_payload

        pcc = self._pcc()
        pcc.enter_service()
        pcc.passthrough_write(b'YDNU MODE SERVICE\r\n')
        text = pcc.passthrough_read_for(1.0)
        pcc.exit_service()

        self.assertIn('Welcome', text, f'Got: {text!r}')


# ===========================================================================
# 4. DeviceManager INTEGRATION TESTS
# ===========================================================================

class TestDeviceManagerService(unittest.TestCase):
    """
    Integration tests for DeviceManager.enter_service() / exit_service().

    DeviceManager wraps ProxyControlClient in a higher-level pattern:
      1. _pause_event.set()          → bus worker stops reading :4001
      2. sleep(0.2)                  → wait for current readline() to finish
      3. pcc = ProxyControlClient()  ← intercepted by our _TestPCC subclass
      4. pcc.enter_service()         → SERVICE_START → READY
      5. ctrl._passthrough = pcc     → YDNU02Controller writes through pcc
      6. ctrl.enter_service_mode()   → "YDNU MODE SERVICE\\r\\n" → reply
      7. self._state = "SERVICE"
      ...later on exit_service():
      8. ctrl.exit_service_mode()    → "MODE RAW\\r\\n"
      9. pcc.exit_service()          → SERVICE_END → OK
      10. _pause_event.clear()       → bus worker reconnects to :4001

    PATCHING ProxyControlClient (not _PROXY_CTRL_PORT):
    Default arg is captured at class definition — patching the variable after
    import has no effect. We patch the class itself in dm:
      dm.ProxyControlClient = _TestPCC  (our subclass with port=ctrl_port)
    This works because inside _raw_locked_operation:
      pcc = ProxyControlClient()   ← name lookup in dm.__dict__ at CALL time
    The name is resolved when the function is CALLED, not when it was defined.

    YDNU02Controller is a MagicMock (from the ydnu02 module stub).
    We inject return values via the mock installed in mgr._ctrl.
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

        import device_manager as dm
        _port = self.ctrl_port
        _orig_cls = dm.ProxyControlClient

        class _TestPCC(_orig_cls):
            """
            ProxyControlClient subclass that always connects to the test ctrl server.
            Replaces dm.ProxyControlClient for the duration of the test so that
            _raw_locked_operation's `pcc = ProxyControlClient()` uses our port.
            """
            def __init__(self_):          # noqa: N805
                super().__init__(port=_port)

        self._orig_pcc_cls = _orig_cls
        dm.ProxyControlClient = _TestPCC  # patch the class in the module

    def tearDown(self):
        import device_manager as dm
        dm.ProxyControlClient = self._orig_pcc_cls  # restore original class
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def _make_manager_with_mock_ctrl(self,
                                     enter_ret: str = 'ok',
                                     exit_ret:  str = 'RAW mode.\r\n'):
        """
        Return a DeviceManager with a pre-installed mock YDNU02Controller.

        Without the mock, enter_service_mode() would try to talk to real
        hardware through passthrough (which doesn't exist in unit tests).
        We inject a ready-made ctrl object directly into mgr._ctrl, bypassing
        the real init sequence.
        """
        import device_manager as dm
        mgr = dm.DeviceManager(port='127.0.0.1:4001')
        mock_ctrl = MagicMock()
        mock_ctrl.enter_service_mode.return_value = enter_ret
        mock_ctrl.exit_service_mode.return_value  = exit_ret
        mgr._ctrl = mock_ctrl
        return mgr

    # -----------------------------------------------------------------------
    # State machine
    # -----------------------------------------------------------------------

    def test_enter_service_returns_service_state(self):
        """
        enter_service() → {status:'ok', state:'SERVICE', welcome:'...'}

        'welcome' is the YDNU-02 reply to YDNU MODE SERVICE (multi-line
        HELP text). The frontend displays it in the terminal panel.
        """
        mgr = self._make_manager_with_mock_ctrl(enter_ret='YDNU-02 Service Terminal\n')
        result = mgr.enter_service()

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['state'],  'SERVICE')
        self.assertIn('welcome', result)

    def test_exit_service_returns_idle_state(self):
        """
        exit_service() → {status:'ok', state:'IDLE', response:'RAW mode.\\r\\n'}

        'RAW mode.' is YDNU-02 confirming it returned to normal operation.
        After this the NMEA broadcast resumes automatically.
        """
        mgr = self._make_manager_with_mock_ctrl()
        mgr.enter_service()
        result = mgr.exit_service()

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['state'],  'IDLE')

    def test_get_state_reflects_enter_exit(self):
        """
        get_state() correctly tracks SERVICE → IDLE transitions.

        This value is used by:
        - GET /api/service/state → {state: 'SERVICE'|'IDLE'}
        - Frontend #svc-state badge (green when SERVICE, grey when IDLE)
        """
        mgr = self._make_manager_with_mock_ctrl()

        self.assertEqual(mgr.get_state(), 'IDLE')    # initial state
        mgr.enter_service()
        self.assertEqual(mgr.get_state(), 'SERVICE')
        mgr.exit_service()
        self.assertEqual(mgr.get_state(), 'IDLE')    # back to normal

    def test_concurrent_enter_serialized_by_lock(self):
        """
        Two concurrent enter_service() → exit_service() cycles on the SAME
        DeviceManager must be SERIALIZED by _service_lock. Neither should
        receive 'another control session is active'.

        HOW IT WORKS:
        DeviceManager._service_lock = threading.Lock()
        enter_service() goes through _raw_locked_operation() which does:
          with self._service_lock: ...  ← second caller waits here

        The proxy-level guard (service_conn_lock) is a SECOND layer, triggered
        only when two DIFFERENT managers try concurrently. On a single manager
        _service_lock prevents concurrent proxy access first.

        WHY a Barrier:
        Without it the first thread might complete before the second starts,
        turning a concurrency test into a sequential one.
        """
        mgr     = self._make_manager_with_mock_ctrl()
        results = []
        errors  = []
        lock    = threading.Lock()
        barrier = threading.Barrier(2)

        def one_cycle():
            barrier.wait()  # both threads start simultaneously
            try:
                r = mgr.enter_service()
                time.sleep(0.05)  # hold the session to create real contention
                mgr.exit_service()
                with lock:
                    results.append(r)
            except Exception as e:
                with lock:
                    errors.append(e)

        t1 = threading.Thread(target=one_cycle, daemon=True)
        t2 = threading.Thread(target=one_cycle, daemon=True)
        t1.start(); t2.start()
        t1.join(timeout=15); t2.join(timeout=15)

        self.assertEqual(errors, [],
            f'Concurrent enter_service raised: {errors}')
        self.assertEqual(len(results), 2,
            f'Expected 2 results, got {len(results)}: {results}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
