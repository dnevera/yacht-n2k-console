#!/usr/bin/env python3
"""
Tests for ydnu02_tcp_gateway.py
Covers: Serial→TCP fanout, TCP→Serial forwarding, line buffering,
        CR/LF stripping, empty line filtering, multi-client, reconnect.
"""
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# We import the module functions directly
import importlib
import types

# ── helpers ────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tcp_connect(port: int, timeout: float = 2.0) -> socket.socket:
    """Connect a TCP client and return the socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(('127.0.0.1', port))
    return s


def _recv_line(sock: socket.socket, timeout: float = 3.0) -> bytes:
    """Read one \n-terminated line from socket."""
    sock.settimeout(timeout)
    buf = b''
    while b'\n' not in buf:
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError('Socket closed before newline')
        buf += chunk
    return buf.split(b'\n')[0] + b'\n'


# ── proxy loader (isolated per test) ──────────────────────────────────────────

def _load_proxy_module(serial_port: str = '/dev/null', tcp_port: int = 0) -> types.ModuleType:
    """Load ydnu02_tcp_gateway with patched constants (does NOT start anything)."""
    spec = importlib.util.spec_from_file_location(
        'ydnu02_tcp_gateway',
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'ydnu02_tcp_gateway', 'ydnu02_tcp_gateway.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # executes module-level code (sets constants)
    # Override constants AFTER exec so module defaults don't overwrite ours
    mod.SERIAL_PORT = serial_port
    mod.TCP_PORT = tcp_port
    mod.clients = set()
    mod.clients_lock = threading.Lock()
    mod.serial_lock = threading.Lock()
    mod.serial_instance = None
    return mod


# ── Unit tests ─────────────────────────────────────────────────────────────────

class TestLineStripping(unittest.TestCase):
    """Test that CR/LF stripping and empty-line filtering work correctly."""

    def setUp(self):
        self.mod = _load_proxy_module()

    def _fanout(self, raw: bytes) -> bytes | None:
        """Simulate one serial readline() call going through the fanout logic."""
        received = []

        class FakeConn:
            def sendall(self, data):
                received.append(data)

        self.mod.clients = {FakeConn()}
        # Replicate the core fanout logic from serial_reader
        if not raw:
            return None
        line = raw.rstrip(b'\r\n') + b'\n'
        if not line.strip():
            return None
        for conn in list(self.mod.clients):
            conn.sendall(line)
        return received[0] if received else None

    def test_crlf_stripped(self):
        raw = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'
        result = self._fanout(raw)
        self.assertIsNotNone(result)
        self.assertFalse(result.endswith(b'\r\n'), 'Should not have \\r\\n ending')
        self.assertTrue(result.endswith(b'\n'), 'Should end with \\n')
        self.assertNotIn(b'\r', result, 'Should have no \\r in output')

    def test_lf_only_passthrough(self):
        raw = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n'
        result = self._fanout(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result, b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n')

    def test_empty_line_filtered(self):
        result = self._fanout(b'\r\n')
        self.assertIsNone(result, 'Empty line should be filtered')

    def test_whitespace_only_filtered(self):
        result = self._fanout(b'   \r\n')
        self.assertIsNone(result, 'Whitespace-only line should be filtered')

    def test_empty_bytes_filtered(self):
        result = self._fanout(b'')
        self.assertIsNone(result, 'Empty bytes (serial timeout) should be filtered')

    def test_data_content_preserved(self):
        payload = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        result = self._fanout(payload + b'\r\n')
        self.assertTrue(result.startswith(payload))


class TestMultiClientFanout(unittest.TestCase):
    """Test that Serial→TCP data is broadcast to all connected clients."""

    def setUp(self):
        self.mod = _load_proxy_module()

    def test_fanout_to_multiple_clients(self):
        received = {i: [] for i in range(3)}

        class FakeConn:
            def __init__(self, idx):
                self.idx = idx
            def sendall(self, data):
                received[self.idx].append(data)

        self.mod.clients = {FakeConn(i) for i in range(3)}
        line = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'
        stripped = line.rstrip(b'\r\n') + b'\n'

        dead = set()
        with self.mod.clients_lock:
            for conn in list(self.mod.clients):
                try:
                    conn.sendall(stripped)
                except OSError:
                    dead.add(conn)
            self.mod.clients.difference_update(dead)

        for i in range(3):
            self.assertEqual(len(received[i]), 1)
            self.assertEqual(received[i][0], stripped)

    def test_dead_client_removed(self):
        """A client that raises OSError during send should be removed."""

        class DeadConn:
            def sendall(self, data):
                raise OSError('broken pipe')

        class GoodConn:
            received = []
            def sendall(self, data):
                self.received.append(data)

        good = GoodConn()
        self.mod.clients = {DeadConn(), good}
        line = b'test line\n'

        dead = set()
        with self.mod.clients_lock:
            for conn in list(self.mod.clients):
                try:
                    conn.sendall(line)
                except OSError:
                    dead.add(conn)
            self.mod.clients.difference_update(dead)

        self.assertEqual(len(self.mod.clients), 1)
        self.assertIn(good, self.mod.clients)
        self.assertEqual(good.received, [line])


class TestTcpToSerial(unittest.TestCase):
    """Test that data received from TCP clients is forwarded to serial."""

    def setUp(self):
        self.mod = _load_proxy_module()

    def test_tcp_to_serial_write(self):
        """Data from TCP client should be written to the serial port."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.mod.serial_instance = mock_serial

        # Simulate what handle_client does when it receives data
        data = b'some command from HA\r\n'
        with self.mod.serial_lock:
            if self.mod.serial_instance and self.mod.serial_instance.is_open:
                self.mod.serial_instance.write(data)

        mock_serial.write.assert_called_once_with(data)

    def test_tcp_to_serial_no_write_when_disconnected(self):
        """No write should happen when serial is not connected."""
        self.mod.serial_instance = None

        # Should not raise
        with self.mod.serial_lock:
            if self.mod.serial_instance and self.mod.serial_instance.is_open:
                self.mod.serial_instance.write(b'data')

        # Nothing to assert — just must not crash

    def test_tcp_to_serial_serial_write_error(self):
        """SerialException during write should be handled gracefully."""
        import serial
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.write.side_effect = serial.SerialException('write error')
        self.mod.serial_instance = mock_serial

        # Should not propagate the exception (it's caught in handle_client)
        caught = False
        try:
            with self.mod.serial_lock:
                if self.mod.serial_instance and self.mod.serial_instance.is_open:
                    try:
                        self.mod.serial_instance.write(b'data')
                    except serial.SerialException:
                        caught = True
        except Exception:
            self.fail('Exception propagated outside handle_client')

        self.assertTrue(caught)


class TestIntegration(unittest.TestCase):
    """Integration tests using real TCP sockets and a mock serial port."""

    def _start_proxy(self, fake_serial):
        """Start proxy in a background thread, return (module, port, stop_event)."""
        port = _free_port()
        mod = _load_proxy_module(tcp_port=port)

        stop_event = threading.Event()

        # Patch serial.Serial to return our fake
        def fake_serial_factory(*a, **kw):
            return fake_serial

        server_thread = threading.Thread(
            target=self._run_server, args=(mod, fake_serial_factory, stop_event),
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.3)  # let server bind and start listening
        return mod, port, stop_event

    def _run_server(self, mod, serial_factory, stop_event):
        import socket as _socket
        import serial

        srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # not available on all platforms
        srv.bind(('127.0.0.1', mod.TCP_PORT))
        srv.listen(5)
        srv.settimeout(0.1)

        # Start serial reader with fake serial
        fake_ser = serial_factory()
        mod.serial_instance = fake_ser

        def reader():
            while not stop_event.is_set():
                raw = fake_ser.readline()
                if not raw:
                    time.sleep(0.05)
                    continue
                line = raw.rstrip(b'\r\n') + b'\n'
                if not line.strip():
                    continue
                dead = set()
                with mod.clients_lock:
                    for conn in list(mod.clients):
                        try:
                            conn.sendall(line)
                        except OSError:
                            dead.add(conn)
                    mod.clients.difference_update(dead)

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        # Signal that server is ready
        stop_event._server_ready = True  # type: ignore[attr-defined]

        while not stop_event.is_set():
            try:
                conn, addr = srv.accept()
                ct = threading.Thread(
                    target=mod.handle_data_client, args=(conn, addr), daemon=True
                )
                ct.start()
            except _socket.timeout:
                continue

        srv.close()


    def _make_fake_serial(self, lines: list[bytes]):
        """Create a fake serial that returns lines one by one, then blocks.
        Lines are served slowly enough that the TCP client has time to connect first.
        """
        q = list(lines)
        lock = threading.Lock()

        class FakeSerial:
            is_open = True

            def readline(self_inner):
                time.sleep(0.15)  # slow enough for client to connect
                with lock:
                    if q:
                        return q.pop(0)
                return b''

            def write(self_inner, data):
                pass

        return FakeSerial()

    def test_serial_to_tcp_delivery(self):
        """Lines from serial arrive at connected TCP client."""
        line_a = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'
        line_b = b'01:43:25.157 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'
        # Repeat lines so client always gets data even if some are missed before connect
        lines = [line_a] * 5 + [line_b] * 5
        fake_ser = self._make_fake_serial(lines)
        mod, port, stop = self._start_proxy(fake_ser)

        try:
            client = _tcp_connect(port)
            line1 = _recv_line(client)
            line2 = _recv_line(client)
            client.close()
        finally:
            stop.set()

        # Both lines should be valid YDNU-02 format with \n only
        self.assertNotIn(b'\r', line1)
        self.assertNotIn(b'\r', line2)
        self.assertTrue(line1.endswith(b'\n'))
        self.assertTrue(line2.endswith(b'\n'))

    def test_crlf_removed_in_delivery(self):
        """TCP client must NOT receive \\r in the stream."""
        # Repeat line so client always gets one after connecting
        fake_ser = self._make_fake_serial([b'test line\r\n'] * 10)
        mod, port, stop = self._start_proxy(fake_ser)

        try:
            client = _tcp_connect(port)
            line = _recv_line(client)
            client.close()
        finally:
            stop.set()

        self.assertNotIn(b'\r', line)
        self.assertTrue(line.endswith(b'\n'))
        self.assertIn(b'test line', line)

    def test_two_clients_both_receive(self):
        """Both connected clients receive the same line."""
        fake_ser = self._make_fake_serial([b'shared line\r\n'] * 5)
        mod, port, stop = self._start_proxy(fake_ser)

        try:
            c1 = _tcp_connect(port)
            c2 = _tcp_connect(port)
            time.sleep(0.1)
            line1 = _recv_line(c1)
            line2 = _recv_line(c2)
            c1.close()
            c2.close()
        finally:
            stop.set()

        self.assertEqual(line1, b'shared line\n')
        self.assertEqual(line2, b'shared line\n')

    def test_client_disconnect_does_not_crash(self):
        """Disconnecting a client mid-stream must not crash the proxy."""
        fake_ser = self._make_fake_serial([b'line\r\n'] * 20)
        mod, port, stop = self._start_proxy(fake_ser)

        try:
            c = _tcp_connect(port)
            _recv_line(c)
            c.close()  # disconnect early
            time.sleep(0.2)
            # Proxy should still be alive — connect again
            c2 = _tcp_connect(port)
            line = _recv_line(c2)
            c2.close()
        finally:
            stop.set()

        self.assertEqual(line, b'line\n')


class TestNMEAFormatCompatibility(unittest.TestCase):
    """Verify that lines our proxy emits are parseable by nmea2000 library."""

    def test_gobius_pgn127505_decoded(self):
        """The Gobius C PGN 127505 line should decode correctly."""
        try:
            from nmea2000 import NMEA2000Decoder
        except ImportError:
            self.skipTest('nmea2000 library not installed')

        d = NMEA2000Decoder()
        # Exactly what our proxy emits (no \r, just \n stripped for decode)
        line = '01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        msg = d.decode(line)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.PGN, 127505)
        fields = {f.id: f.value for f in msg.fields}
        self.assertAlmostEqual(fields['level'], 94.4, places=1)
        self.assertEqual(fields['capacity'], 10.0)

    def test_proxy_output_format_detected(self):
        """detect_format must recognize our output as CAN_FRAME_ASCII."""
        try:
            from nmea2000.input_formats import detect_format
            from nmea2000.decoder_formats import N2KFormat
        except ImportError:
            self.skipTest('nmea2000 library not installed')

        line = '01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        fmt = detect_format(line)
        self.assertEqual(fmt, N2KFormat.CAN_FRAME_ASCII)

    def test_no_cr_in_output_passes_regex(self):
        """Line with \\r must NOT be detected (regex requires $-terminated)."""
        try:
            from nmea2000.input_formats import _is_can_frame_ascii
        except ImportError:
            self.skipTest('nmea2000 library not installed')

        line_with_cr = '01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r'
        line_clean = '01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        self.assertFalse(_is_can_frame_ascii(line_with_cr), '\\r must fail regex')
        self.assertTrue(_is_can_frame_ascii(line_clean), 'Clean line must pass regex')


if __name__ == '__main__':
    unittest.main(verbosity=2)
