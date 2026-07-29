#!/usr/bin/env python3
"""
Tests for service mode — proxy control protocol, race-condition fix,
ProxyControlClient, and DeviceManager Enter/Exit.

Test levels:
  1. TestProxyCtrlProtocol  — raw TCP tests against the ctrl port handler
  2. TestServiceModeRaceFix — verify serial_reader is flushed before READY
  3. TestProxyControlClient — unit tests for ProxyControlClient class
  4. TestDeviceManagerService — DeviceManager enter/exit_service behaviour
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
# Stub out server-only dependencies so device_manager can be imported locally
# without fastapi / ydnu02 / sensors being installed.
# ---------------------------------------------------------------------------

def _mock_module(name: str, **attrs) -> MagicMock:
    """Create a MagicMock module and register it under sys.modules[name]."""
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

if 'fastapi' not in sys.modules:
    fa = _mock_module('fastapi')
    fa.WebSocket = MagicMock
    fa.WebSocketDisconnect = Exception
    _mock_module('fastapi.websockets')
    _mock_module('fastapi.routing')

if 'ydnu02' not in sys.modules:
    _mock_module('ydnu02',
                 YDNU02Controller=MagicMock,
                 N2KPGNDecoder=MagicMock)

if 'sensors' not in sys.modules:
    _mock_module('sensors', GobiusCSensor=MagicMock)
    _mock_module('sensors.base_sensor')
    _mock_module('sensors.gobius_sensor')
    _mock_module('sensors.mopeka_sensor')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tcp_connect(port: int, timeout: float = 3.0) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(('127.0.0.1', port))
    return s


def _recv_line(sock: socket.socket, timeout: float = 3.0) -> str:
    """Read one \\n-terminated line from socket, return decoded string."""
    sock.settimeout(timeout)
    buf = b''
    while b'\n' not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError('Socket closed before newline')
        buf += chunk
    return buf.split(b'\n')[0].decode('utf-8', errors='replace').strip()


def _load_proxy_module(ctrl_port: int = 0) -> types.ModuleType:
    """Load a fresh isolated copy of nmea_tcp_proxy (no side-effects)."""
    spec = importlib.util.spec_from_file_location(
        f'nmea_tcp_proxy_{ctrl_port}',
        os.path.join(ROOT, 'nmea_tcp_proxy.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SERIAL_PORT  = '/dev/null'
    mod.TCP_PORT     = 0
    mod.CTRL_PORT    = ctrl_port
    mod.clients      = set()
    mod.clients_lock = threading.Lock()
    mod.serial_lock  = threading.Lock()
    mod.serial_instance = None
    return mod


def _start_ctrl_server(mod: types.ModuleType, ctrl_port: int,
                       fake_serial) -> threading.Event:
    """
    Bind a TCP server on ctrl_port and dispatch each connection to
    mod.handle_ctrl_client in a daemon thread.
    Returns a stop_event; set it to stop the server.
    """
    mod.serial_instance = fake_serial
    ready = threading.Event()
    stop  = threading.Event()

    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', ctrl_port))
        srv.listen(5)
        srv.settimeout(0.1)
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
    """Mock serial.Serial with in_waiting=0 and reset_input_buffer tracked."""
    fake = MagicMock()
    fake.is_open       = True
    fake.in_waiting    = 0
    fake.readline.return_value = b''
    fake.read.return_value     = read_data
    return fake


# ===========================================================================
# 1. Raw TCP tests against handle_ctrl_client
# ===========================================================================

class TestProxyCtrlProtocol(unittest.TestCase):
    """Test the ctrl port line protocol in isolation."""

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

    # --- Happy path ---------------------------------------------------------

    def test_service_start_returns_ready(self):
        """SERVICE_START must be answered with READY."""
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            resp = _recv_line(sock)
        self.assertEqual(resp, 'READY')

    def test_service_end_returns_ok(self):
        """SERVICE_END (after SERVICE_START) must be answered with OK."""
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)          # READY
            sock.sendall(b'SERVICE_END\n')
            resp = _recv_line(sock)
        self.assertEqual(resp, 'OK')

    def test_firmware_start_end(self):
        """FIRMWARE_START/END are aliases for SERVICE_START/END."""
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'FIRMWARE_START\n')
            self.assertEqual(_recv_line(sock), 'READY')
            sock.sendall(b'FIRMWARE_END\n')
            self.assertEqual(_recv_line(sock), 'OK')

    def test_passthrough_writes_to_serial(self):
        """Commands sent after READY must be forwarded to serial."""
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            sock.sendall(b'YDNU MODE SERVICE\r\n')
            time.sleep(0.15)

        self.fake_ser.write.assert_called()
        written = b''.join(c.args[0] for c in self.fake_ser.write.call_args_list)
        self.assertIn(b'YDNU MODE SERVICE', written)

    def test_command_before_start_returns_error(self):
        """Passthrough commands without SERVICE_START must be rejected."""
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SOME COMMAND\n')
            resp = _recv_line(sock)
        self.assertIn('ERROR', resp)

    # --- Concurrency --------------------------------------------------------

    def test_second_session_rejected(self):
        """While a service session is active a second ctrl connection
        must receive an ERROR line immediately."""
        with _tcp_connect(self.ctrl_port) as first:
            first.sendall(b'SERVICE_START\n')
            _recv_line(first)  # READY — first session is active

            with _tcp_connect(self.ctrl_port) as second:
                resp = _recv_line(second, timeout=3.0)
            self.assertIn('ERROR', resp)

    def test_session_freed_after_disconnect(self):
        """After the first client disconnects, a new one can enter service."""
        with _tcp_connect(self.ctrl_port) as first:
            first.sendall(b'SERVICE_START\n')
            _recv_line(first)  # READY

        time.sleep(0.3)  # wait for handler to clean up

        with _tcp_connect(self.ctrl_port) as second:
            second.sendall(b'SERVICE_START\n')
            resp = _recv_line(second)
        self.assertEqual(resp, 'READY')


# ===========================================================================
# 2. Race-condition fix: serial buffer flushed before READY
# ===========================================================================

class TestServiceModeRaceFix(unittest.TestCase):
    """
    Verify the race-condition fix:
      1. ctrl handler conn.settimeout(0.1) — not 2.0s
      2. SERVICE_START: sleep(0.15) + reset_input_buffer() before READY
      3. serial.Serial(timeout=0.1) — fast readline() yield
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
        The proxy must flush the serial input buffer (reset_input_buffer)
        between service_mode.set() and sending READY, so stale NMEA frames
        accumulated during the transition window are discarded.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # wait for READY

        self.fake_ser.reset_input_buffer.assert_called_once()

    def test_service_start_minimum_delay(self):
        """
        SERVICE_START must NOT send READY instantly — it sleeps ≥ 0.1s
        to let serial_reader exit its readline() cycle.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            t0 = time.monotonic()
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            elapsed = time.monotonic() - t0

        # We sleep 0.15s in the handler before READY
        self.assertGreaterEqual(elapsed, 0.10,
            f'READY came too fast ({elapsed:.3f}s) — serial_reader flush sleep missing')

    def test_serial_data_forwarded_within_200ms(self):
        """
        With ctrl timeout=0.1s, serial data must be forwarded to the
        ctrl client within ~200ms. Previously this took up to 2 seconds.
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
                self.fail('No serial data forwarded within 1s — ctrl timeout too long')

        self.assertLess(elapsed, 0.5,
            f'Data forwarded after {elapsed:.3f}s — expected <0.5s with 0.1s ctrl timeout')
        self.assertIn(b'YDNU-02', data)

    def test_service_mode_flag_set_during_session(self):
        """service_mode Event must be set between START and END."""
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            self.assertTrue(self.mod.service_mode.is_set(),
                'service_mode not set after SERVICE_START')
            sock.sendall(b'SERVICE_END\n')
            _recv_line(sock)  # OK
            time.sleep(0.05)
            self.assertFalse(self.mod.service_mode.is_set(),
                'service_mode still set after SERVICE_END')


# ===========================================================================
# 3. ProxyControlClient unit tests
# ===========================================================================

class TestProxyControlClient(unittest.TestCase):
    """Unit tests for ProxyControlClient — protocol layer only (no real proxy)."""

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
        Create a ProxyControlClient pointing at our test ctrl server.

        NOTE: ProxyControlClient.__init__ has `port=_PROXY_CTRL_PORT` as a
        DEFAULT ARGUMENT — evaluated once at class-definition time to the
        module constant (4002).  Patching `dm._PROXY_CTRL_PORT` after import
        does NOT affect already-captured default args.  We always pass port
        explicitly to bypass this.
        """
        from device_manager import ProxyControlClient
        return ProxyControlClient(port=self.ctrl_port)

    def test_enter_service_sends_start_and_gets_ready(self):
        """enter_service() must send SERVICE_START and not raise on READY."""
        pcc = self._pcc()
        pcc.enter_service()   # must not raise
        pcc.exit_service()

    def test_exit_service_sends_end_and_gets_ok(self):
        """exit_service() must send SERVICE_END and not raise on OK."""
        pcc = self._pcc()
        pcc.enter_service()
        pcc.exit_service()    # must not raise

    def test_passthrough_write_delivers_to_serial(self):
        """passthrough_write() must reach the mock serial via the proxy."""
        pcc = self._pcc()
        pcc.enter_service()
        pcc.passthrough_write(b'YDNU MODE SERVICE\r\n')
        time.sleep(0.2)
        pcc.exit_service()

        written = b''.join(c.args[0] for c in self.fake_ser.write.call_args_list)
        self.assertIn(b'YDNU MODE SERVICE', written)

    def test_passthrough_read_for_collects_serial_lines(self):
        """passthrough_read_for() must collect data pushed by the proxy."""
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
# 4. DeviceManager enter_service / exit_service
# ===========================================================================

class TestDeviceManagerService(unittest.TestCase):
    """
    Test DeviceManager.enter_service() and exit_service() state machine.

    We patch dm.ProxyControlClient at the class level (not _PROXY_CTRL_PORT)
    so that `pcc = ProxyControlClient()` inside _raw_locked_operation gets
    a subclass whose __init__ passes our test ctrl_port explicitly.
    YDNU02Controller is a MagicMock stub — its methods are set via the
    mock returned by _make_manager_with_mock_ctrl().
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
            """Subclass that always connects to the test ctrl server."""
            def __init__(self_):          # noqa: N805
                super().__init__(port=_port)

        self._orig_pcc_cls = _orig_cls
        dm.ProxyControlClient = _TestPCC

    def tearDown(self):
        import device_manager as dm
        dm.ProxyControlClient = self._orig_pcc_cls
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def _make_manager_with_mock_ctrl(self,
                                     enter_ret: str = 'ok',
                                     exit_ret:  str = 'RAW mode.\r\n'):
        """
        Return a DeviceManager with _ctrl pre-populated with a MagicMock
        so enter_service_mode / exit_service_mode don't touch real serial.
        """
        import device_manager as dm
        mgr = dm.DeviceManager(port='127.0.0.1:4001')
        mock_ctrl = MagicMock()
        mock_ctrl.enter_service_mode.return_value = enter_ret
        mock_ctrl.exit_service_mode.return_value  = exit_ret
        mgr._ctrl = mock_ctrl
        return mgr

    # --- State machine tests ------------------------------------------------

    def test_enter_service_returns_service_state(self):
        """enter_service() must return {status:'ok', state:'SERVICE', welcome:...}."""
        mgr = self._make_manager_with_mock_ctrl(enter_ret='YDNU-02 Service Terminal\n')
        result = mgr.enter_service()

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['state'],  'SERVICE')
        self.assertIn('welcome', result)

    def test_exit_service_returns_idle_state(self):
        """exit_service() must return {status:'ok', state:'IDLE', response:...}."""
        mgr = self._make_manager_with_mock_ctrl()
        mgr.enter_service()
        result = mgr.exit_service()

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['state'],  'IDLE')

    def test_get_state_reflects_enter_exit(self):
        """get_state() must track SERVICE→IDLE transitions."""
        mgr = self._make_manager_with_mock_ctrl()

        self.assertEqual(mgr.get_state(), 'IDLE')
        mgr.enter_service()
        self.assertEqual(mgr.get_state(), 'SERVICE')
        mgr.exit_service()
        self.assertEqual(mgr.get_state(), 'IDLE')

    def test_concurrent_enter_serialized_by_lock(self):
        """
        Two concurrent enter→exit cycles on the SAME DeviceManager must be
        SERIALIZED by _service_lock — the second call waits until the first
        cycle completes before entering.  Neither should see 'another control
        session is active' because the lock prevents concurrent proxy access.
        """
        mgr     = self._make_manager_with_mock_ctrl()
        results = []
        errors  = []
        lock    = threading.Lock()
        # barrier ensures both threads are running before either acquires the lock
        barrier = threading.Barrier(2)

        def one_cycle():
            barrier.wait()
            try:
                r = mgr.enter_service()
                time.sleep(0.05)  # hold service briefly
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
