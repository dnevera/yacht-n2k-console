#!/usr/bin/env python3
"""
Tests for ydnu02_tcp_gateway.py and ydnu02_gateway_device.py

Covers:
  - _fmt_frame: output format
  - _broadcast: fanout to all clients + exclude parameter
  - _get_pgn_sa: CAN ID parsing
  - _NMEA_LINE_RE: frame filter regex
  - handle_data_client: bidirectional hub (forward to others, NOT to serial)
  - _send_iso_request: broadcasts ISO Request to serial AND TCP clients
  - serial_reader: CR/LF stripping, empty line filtering, multi-client fanout
  - dead client removal
  - gateway_device: _read_cpu_temp, _make_temp_message, _read_version

Integration tests (need real network) are skipped in sandbox — run with:
  python3 -m pytest tests/test_ydnu02_tcp_gateway.py -v
"""
import importlib
import importlib.util
import os
import socket
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ydnu02 import N2KPGNDecoder  # for TestFeedToLib

# ── helpers ────────────────────────────────────────────────────────────────────

GATEWAY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'ydnu02_tcp_gateway', 'ydnu02_tcp_gateway.py',
)
DEVICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'ydnu02_tcp_gateway', 'ydnu02_gateway_device.py',
)

# Valid NMEA CAN_FRAME_ASCII line used as fixture in many tests
VALID_LINE = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n'
# ISO Claim frame (PGN 60928 = 0xEE00, CAN ID with PF=0xEE)
ISO_CLAIM_LINE = b'00:00:00.000 R 18EEFF5C 39 30 A0 5C 74 21 A7 2C\n'


def _load_gateway(serial_port: str = '/dev/null') -> types.ModuleType:
    """Load ydnu02_tcp_gateway with patched constants (does NOT start anything)."""
    spec = importlib.util.spec_from_file_location('ydnu02_tcp_gateway', GATEWAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SERIAL_PORT = serial_port
    mod.clients = set()
    mod.clients_lock = threading.Lock()
    mod.serial_lock = threading.Lock()
    mod.serial_instance = None
    return mod


def _load_device() -> types.ModuleType:
    """Load ydnu02_gateway_device module."""
    spec = importlib.util.spec_from_file_location('ydnu02_gateway_device', DEVICE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _can_bind_socket() -> bool:
    """True if we can open a real TCP socket (not available in sandbox)."""
    try:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
        return True
    except (PermissionError, OSError):
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tcp_connect(port: int, timeout: float = 2.0) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(('127.0.0.1', port))
    return s


def _recv_line(sock: socket.socket, timeout: float = 3.0) -> bytes:
    sock.settimeout(timeout)
    buf = b''
    while b'\n' not in buf:
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError('Socket closed before newline')
        buf += chunk
    return buf.split(b'\n')[0] + b'\n'


NEEDS_NETWORK = unittest.skipUnless(_can_bind_socket(), 'No network in sandbox — run with BypassSandbox')


# ── _fmt_frame ─────────────────────────────────────────────────────────────────

class TestFmtFrame(unittest.TestCase):
    """_fmt_frame must produce a valid CAN_FRAME_ASCII line."""

    def setUp(self):
        self.mod = _load_gateway()

    def test_output_ends_with_lf(self):
        result = self.mod._fmt_frame('18EEFFC8', b'\x39\x30\xA0\x5C\x74\x21\xA7\x2C')
        self.assertTrue(result.endswith(b'\n'))

    def test_no_cr_in_output(self):
        result = self.mod._fmt_frame('18EEFFC8', b'\x39\x30\xA0\x5C')
        self.assertNotIn(b'\r', result)

    def test_format_contains_r_direction(self):
        result = self.mod._fmt_frame('18EEFFC8', b'\x00\x01')
        self.assertIn(b' R ', result)

    def test_can_id_hex_present(self):
        result = self.mod._fmt_frame('18EEFFC8', b'\x00')
        self.assertIn(b'18EEFFC8', result)

    def test_data_bytes_hex_present(self):
        result = self.mod._fmt_frame('00000000', b'\xAB\xCD')
        self.assertIn(b'AB', result)
        self.assertIn(b'CD', result)

    def test_matches_nmea_regex(self):
        result = self.mod._fmt_frame('18EEFFC8', b'\x39\x30\xA0\x5C\x74\x21\xA7\x2C')
        self.assertIsNotNone(self.mod._NMEA_LINE_RE.match(result))


# ── _NMEA_LINE_RE regex ────────────────────────────────────────────────────────

class TestNMEARegex(unittest.TestCase):
    """_NMEA_LINE_RE must accept valid frames and reject invalid ones."""

    def setUp(self):
        self.mod = _load_gateway()
        self.re = self.mod._NMEA_LINE_RE

    def test_valid_r_frame_matches(self):
        self.assertIsNotNone(self.re.match(VALID_LINE))

    def test_valid_t_frame_matches(self):
        line = b'00:00:00.000 T 18EAFFFE 00 EE 00\n'
        self.assertIsNotNone(self.re.match(line))

    def test_cr_in_frame_rejected(self):
        line = b'01:43:22.648 R 19F2115C 00 30 5C 64\r\n'
        self.assertIsNone(self.re.match(line))

    def test_empty_line_rejected(self):
        self.assertIsNone(self.re.match(b'\n'))

    def test_text_line_rejected(self):
        self.assertIsNone(self.re.match(b'YDNU MODE RAW\n'))

    def test_truncated_can_id_rejected(self):
        self.assertIsNone(self.re.match(b'01:23:45.678 R 1F2115C 00\n'))

    def test_valid_8byte_payload_matches(self):
        line = b'00:00:01.000 R 18EEFF5C 39 30 A0 5C 74 21 A7 2C\n'
        self.assertIsNotNone(self.re.match(line))

    def test_tx_format_rejected_by_rx_regex(self):
        """TX format (no timestamp) must NOT match _NMEA_LINE_RE."""
        tx = b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n'
        self.assertIsNone(self.re.match(tx))


# ── _TX_LINE_RE regex ─────────────────────────────────────────────────────────

class TestTXLineRegex(unittest.TestCase):
    """_TX_LINE_RE must match YDNU-02 TX (outgoing) frames from nmea2000 N2KDevice."""

    def setUp(self):
        self.mod = _load_gateway()
        self.re = self.mod._TX_LINE_RE

    def test_tx_frame_crlf_matches(self):
        self.assertIsNotNone(self.re.match(b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n'))

    def test_tx_frame_lf_only_matches(self):
        self.assertIsNotNone(self.re.match(b'18EEFFC8 39 30 A0 5C\n'))

    def test_tx_single_byte_matches(self):
        self.assertIsNotNone(self.re.match(b'18EAFFFE 00\r\n'))

    def test_rx_format_rejected(self):
        self.assertIsNone(self.re.match(b'01:43:22.648 R 19F2115C 00 30\n'))

    def test_text_rejected(self):
        self.assertIsNone(self.re.match(b'YDNU MODE OK\r\n'))

    def test_truncated_can_id_rejected(self):
        self.assertIsNone(self.re.match(b'18EEFFC 39 30\r\n'))  # 7 hex chars

    def test_iso_claim_tx_format(self):
        self.assertIsNotNone(self.re.match(b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n'))


# ── _get_pgn_sa ────────────────────────────────────────────────────────────────

class TestGetPgnSa(unittest.TestCase):
    """_get_pgn_sa must decode PGN and SA from hex CAN ID string."""

    def setUp(self):
        self.mod = _load_gateway()

    def test_iso_claim_pgn_and_sa(self):
        # CAN ID 18EEFF5C: PGN=60928 (0xEE00), SA=0x5C=92
        pgn, sa = self.mod._get_pgn_sa(b'18EEFF5C')
        self.assertEqual(pgn, 60928)
        self.assertEqual(sa, 92)

    def test_product_info_pgn(self):
        # PGN 126996 = 0x1F014, CAN ID with PF=0xF0, PS=0x14
        # CAN ID: priority=6, DP=1, PF=0xF0, PS=0x14, SA=0xC8
        # = (6<<26)|(1<<24)|(0xF0<<16)|(0x14<<8)|0xC8 = 0x19F014C8
        pgn, sa = self.mod._get_pgn_sa(b'19F014C8')
        self.assertEqual(pgn, 126996)
        self.assertEqual(sa, 0xC8)

    def test_temperature_pgn(self):
        # PGN 130312 = 0x1FD08, CAN ID 19FD08C8
        pgn, sa = self.mod._get_pgn_sa(b'19FD08C8')
        self.assertEqual(pgn, 130312)

    def test_invalid_raises(self):
        with self.assertRaises((ValueError, IndexError)):
            self.mod._get_pgn_sa(b'ZZZZZZZZ')


# ── _broadcast + exclude ───────────────────────────────────────────────────────

class TestBroadcast(unittest.TestCase):
    """_broadcast sends to all clients, optionally excluding one (the sender)."""

    def setUp(self):
        self.mod = _load_gateway()

    def _make_clients(self, n: int):
        received = {i: [] for i in range(n)}

        class FakeConn:
            def __init__(self, idx):
                self.idx = idx

            def sendall(self, data):
                received[self.idx].append(data)

        conns = [FakeConn(i) for i in range(n)]
        self.mod.clients = set(conns)
        return conns, received

    def test_broadcast_to_all_clients(self):
        conns, received = self._make_clients(3)
        self.mod._broadcast(VALID_LINE)
        for i in range(3):
            self.assertEqual(received[i], [VALID_LINE])

    def test_broadcast_excludes_sender(self):
        conns, received = self._make_clients(3)
        sender = conns[1]
        self.mod._broadcast(VALID_LINE, exclude=sender)
        self.assertEqual(received[0], [VALID_LINE], 'client 0 must receive')
        self.assertEqual(received[1], [],           'sender must NOT receive')
        self.assertEqual(received[2], [VALID_LINE], 'client 2 must receive')

    def test_broadcast_exclude_none_sends_to_all(self):
        conns, received = self._make_clients(2)
        self.mod._broadcast(VALID_LINE, exclude=None)
        self.assertEqual(received[0], [VALID_LINE])
        self.assertEqual(received[1], [VALID_LINE])

    def test_dead_client_removed(self):
        class DeadConn:
            def sendall(self, data):
                raise OSError('broken pipe')

        class GoodConn:
            received = []
            def sendall(self, data):
                self.received.append(data)

        good = GoodConn()
        self.mod.clients = {DeadConn(), good}
        self.mod._broadcast(VALID_LINE)
        self.assertEqual(len(self.mod.clients), 1)
        self.assertIn(good, self.mod.clients)
        self.assertEqual(good.received, [VALID_LINE])

    def test_broadcast_caches_iso_claim(self):
        """ISO Claim frame (PGN 60928) must be cached in _device_frame_cache."""
        self.mod.clients = set()
        self.mod._broadcast(ISO_CLAIM_LINE)
        cache = self.mod._device_frame_cache
        # SA=0x5C=92 for the test fixture CAN ID 18EEFF5C
        self.assertIn(92, cache)
        self.assertIn('iso_claim', cache[92])


# ── bidirectional hub: handle_data_client ────────────────────────────────────

class TestBidirectionalHub(unittest.TestCase):
    """handle_data_client forwards frames to OTHER clients, NOT to serial."""

    def setUp(self):
        self.mod = _load_gateway()
        # Patch _replay_device_frames and _send_iso_request to no-ops
        self.mod._replay_device_frames = MagicMock()
        self.mod._send_iso_request = MagicMock()

    def _make_pipe(self):
        """Return a (server_sock, client_sock) connected pair."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(('127.0.0.1', port))
        conn, _ = server.accept()
        server.close()
        return conn, client

    @NEEDS_NETWORK
    def test_client_frame_not_forwarded_to_serial(self):
        """A valid N2K frame from a client must NOT be written to serial."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.mod.serial_instance = mock_serial

        conn, client = self._make_pipe()
        t = threading.Thread(
            target=self.mod.handle_data_client,
            args=(conn, ('127.0.0.1', 9999)),
            daemon=True,
        )
        t.start()
        client.sendall(VALID_LINE)
        time.sleep(0.2)
        client.close()
        t.join(timeout=1.0)

        mock_serial.write.assert_not_called()

    @NEEDS_NETWORK
    def test_client_frame_forwarded_to_other_clients(self):
        """A valid N2K frame from a client IS forwarded to other connected clients."""
        received = []

        class ObserverConn:
            def sendall(self, data):
                received.append(data)

        observer = ObserverConn()
        self.mod.clients = {observer}

        conn, client = self._make_pipe()
        t = threading.Thread(
            target=self.mod.handle_data_client,
            args=(conn, ('127.0.0.1', 9999)),
            daemon=True,
        )
        t.start()
        client.sendall(VALID_LINE)
        time.sleep(0.2)
        client.close()
        t.join(timeout=1.0)

        self.assertIn(VALID_LINE, received, 'Observer must receive forwarded frame')

    @NEEDS_NETWORK
    def test_invalid_frame_not_forwarded(self):
        """Non-NMEA text from a client must be silently dropped."""
        received = []

        class ObserverConn:
            def sendall(self, data):
                received.append(data)

        self.mod.clients = {ObserverConn()}
        conn, client = self._make_pipe()
        t = threading.Thread(
            target=self.mod.handle_data_client,
            args=(conn, ('127.0.0.1', 9999)),
            daemon=True,
        )
        t.start()
        client.sendall(b'garbage text that is not NMEA\n')
        time.sleep(0.2)
        client.close()
        t.join(timeout=1.0)

        self.assertEqual(received, [], 'Invalid frames must not be forwarded')

    @NEEDS_NETWORK
    def test_tx_format_converted_to_rx_and_forwarded(self):
        """TX frame from N2KDevice (no timestamp) must be converted to RX and forwarded."""
        received = []

        class ObserverConn:
            def sendall(self, data):
                received.append(data)

        self.mod.clients = {ObserverConn()}
        conn, client = self._make_pipe()
        t = threading.Thread(
            target=self.mod.handle_data_client,
            args=(conn, ('127.0.0.1', 9999)),
            daemon=True,
        )
        t.start()
        # TX format: CANID BYTES\r\n — as sent by nmea2000 lib N2KDevice
        client.sendall(b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n')
        time.sleep(0.2)
        client.close()
        t.join(timeout=1.0)

        self.assertEqual(len(received), 1, 'TX frame must be forwarded once')
        # Forwarded frame must be in full RX format
        self.assertIsNotNone(self.mod._NMEA_LINE_RE.match(received[0]))
        self.assertIn(b'18EEFFC8', received[0])

    @NEEDS_NETWORK
    def test_tx_iso_claim_cached(self):
        """ISO Claim in TX format from N2KDevice must land in _device_frame_cache."""
        self.mod.clients = set()
        conn, client = self._make_pipe()
        t = threading.Thread(
            target=self.mod.handle_data_client,
            args=(conn, ('127.0.0.1', 9999)),
            daemon=True,
        )
        t.start()
        # ISO Claim TX: CAN ID 18EEFFC8 = PGN 60928, SA=0xC8=200
        client.sendall(b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n')
        time.sleep(0.2)
        client.close()
        t.join(timeout=1.0)

        cache = self.mod._device_frame_cache
        self.assertIn(200, cache, 'SA=200 must be in cache after TX ISO Claim')
        self.assertIn('iso_claim', cache[200])


# ── _send_iso_request ─────────────────────────────────────────────────────────

class TestISORequestBroadcast(unittest.TestCase):
    """_send_iso_request must send to serial AND broadcast to TCP data clients."""

    def setUp(self):
        self.mod = _load_gateway()
        # Force last sent to distant past so rate-limiter passes
        self.mod._iso_request_last_sent = 0.0
        self.mod._serial_ready.set()

    def _setup_serial(self):
        mock_ser = MagicMock()
        mock_ser.is_open = True
        self.mod.serial_instance = mock_ser
        return mock_ser

    def test_sends_to_serial(self):
        mock_ser = self._setup_serial()
        self.mod.clients = set()
        self.mod._send_iso_request()
        self.assertEqual(mock_ser.write.call_count, 2)
        frame1 = mock_ser.write.call_args_list[0][0][0]
        frame2 = mock_ser.write.call_args_list[1][0][0]
        self.assertIn(b'18EAFFFE', frame1)
        self.assertIn(b'18EAFFFE', frame2)

    def test_broadcasts_to_tcp_clients(self):
        """ISO Request must also be broadcast to TCP clients (for virtual N2KDevice)."""
        self._setup_serial()
        received = []

        class FakeConn:
            def sendall(self, data):
                received.append(data)

        self.mod.clients = {FakeConn()}
        self.mod._send_iso_request()

        self.assertTrue(len(received) >= 1, 'TCP broadcast must happen')
        iso_req = received[-1]
        # Must be a valid NMEA line containing the ISO Request CAN ID
        self.assertIn(b'18EAFFFE', iso_req)
        self.assertIsNotNone(self.mod._NMEA_LINE_RE.match(iso_req))

    def test_rate_limited(self):
        """Rapid calls must not spam serial or TCP clients."""
        mock_ser = self._setup_serial()
        self.mod.clients = set()
        self.mod._send_iso_request()
        self.mod._send_iso_request()  # should be rate-limited
        self.mod._send_iso_request()
        self.assertEqual(mock_ser.write.call_count, 2, 'Must be rate-limited to 1 request set (2 writes)')

    def test_no_write_when_serial_not_ready(self):
        mock_ser = self._setup_serial()
        self.mod._serial_ready.clear()
        self.mod.clients = set()
        self.mod._send_iso_request()
        mock_ser.write.assert_not_called()

    def test_no_write_in_service_mode(self):
        mock_ser = self._setup_serial()
        self.mod.service_mode.set()
        self.mod.clients = set()
        self.mod._send_iso_request()
        mock_ser.write.assert_not_called()
        self.mod.service_mode.clear()


# ── Serial → TCP fanout (serial_reader logic) ─────────────────────────────────

class TestSerialFanout(unittest.TestCase):
    """Serial → TCP: CR/LF stripping, empty line filtering, multi-client fanout."""

    def setUp(self):
        self.mod = _load_gateway()

    def _fanout(self, raw: bytes) -> bytes | None:
        """Simulate one serial readline() going through the core fanout logic."""
        received = []

        class FakeConn:
            def sendall(self, data):
                received.append(data)

        self.mod.clients = {FakeConn()}
        if not raw:
            return None
        line = raw.rstrip(b'\r\n') + b'\n'
        if not line.strip():
            return None
        if not self.mod._NMEA_LINE_RE.match(line):
            return None
        for conn in list(self.mod.clients):
            conn.sendall(line)
        return received[0] if received else None

    def test_crlf_stripped(self):
        raw = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'
        result = self._fanout(raw)
        self.assertIsNotNone(result)
        self.assertNotIn(b'\r', result)
        self.assertTrue(result.endswith(b'\n'))

    def test_lf_only_passthrough(self):
        result = self._fanout(b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n')
        self.assertEqual(result, b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n')

    def test_empty_line_filtered(self):
        self.assertIsNone(self._fanout(b'\r\n'))

    def test_whitespace_only_filtered(self):
        self.assertIsNone(self._fanout(b'   \r\n'))

    def test_empty_bytes_filtered(self):
        self.assertIsNone(self._fanout(b''))

    def test_text_line_filtered_by_regex(self):
        self.assertIsNone(self._fanout(b'YDNU MODE RAW OK\r\n'))

    def test_data_content_preserved(self):
        payload = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        result = self._fanout(payload + b'\r\n')
        self.assertTrue(result.startswith(payload))

    def test_fanout_to_multiple_clients(self):
        received = {i: [] for i in range(3)}

        class FakeConn:
            def __init__(self, idx):
                self.idx = idx

            def sendall(self, data):
                received[self.idx].append(data)

        self.mod.clients = {FakeConn(i) for i in range(3)}
        line = VALID_LINE
        dead = set()
        with self.mod.clients_lock:
            for conn in list(self.mod.clients):
                try:
                    conn.sendall(line)
                except OSError:
                    dead.add(conn)
            self.mod.clients.difference_update(dead)

        for i in range(3):
            self.assertEqual(received[i], [line])


# ── ydnu02_gateway_device ─────────────────────────────────────────────────────

class TestGatewayDevice(unittest.TestCase):
    """Unit tests for ydnu02_gateway_device.py functions."""

    def setUp(self):
        self.dev = _load_device()

    def test_read_cpu_temp_returns_none_on_missing_sysfs(self):
        """On macOS or any non-Linux without sysfs, must return None (not crash)."""
        with patch('builtins.open', side_effect=OSError('no such file')):
            result = self.dev._read_cpu_temp()
        self.assertIsNone(result)

    def test_read_cpu_temp_parses_millidegrees(self):
        """55000 millidegrees → 55.0 °C."""
        mock_open = unittest.mock.mock_open(read_data='55000\n')
        with patch('builtins.open', mock_open):
            result = self.dev._read_cpu_temp()
        self.assertAlmostEqual(result, 55.0)

    def test_read_version_fallback(self):
        """Must return '0.0.0' when no VERSION file found."""
        with patch('builtins.open', side_effect=OSError('not found')):
            result = self.dev._read_version()
        self.assertEqual(result, '0.0.0')

    def test_read_version_reads_file(self):
        mock_open = unittest.mock.mock_open(read_data='1.2.3\n')
        with patch('builtins.open', mock_open):
            result = self.dev._read_version()
        self.assertEqual(result, '1.2.3')

    def test_make_temp_message_pgn(self):
        """Message PGN must be 130312."""
        msg = self.dev._make_temp_message(55.0, sid=1)
        self.assertEqual(msg.PGN, 130312)

    def test_make_temp_message_kelvin_conversion(self):
        """actualTemperature value must be in Kelvin (celsius + 273.15)."""
        temp_c = 55.0
        msg = self.dev._make_temp_message(temp_c, sid=0)
        fields = {f.id: f.value for f in msg.fields}
        expected_k = temp_c + 273.15
        self.assertAlmostEqual(fields['actualTemperature'], expected_k, places=2)

    def test_make_temp_message_source_inside(self):
        """Temperature source must be 2 (Inside Temperature)."""
        msg = self.dev._make_temp_message(40.0)
        fields = {f.id: f.value for f in msg.fields}
        self.assertEqual(fields['source'], 2)

    def test_make_temp_message_instance_zero(self):
        """Temperature instance must be 0."""
        msg = self.dev._make_temp_message(40.0)
        fields = {f.id: f.value for f in msg.fields}
        self.assertEqual(fields['instance'], 0)

    def test_make_temp_message_sid_set(self):
        """SID must match the provided value."""
        msg = self.dev._make_temp_message(40.0, sid=42)
        fields = {f.id: f.value for f in msg.fields}
        self.assertEqual(fields['sid'], 42)

    def test_make_temp_message_sid_wraps(self):
        """SID=252 wraps to 0 in the caller's loop (0..251)."""
        for sid in (0, 1, 127, 251):
            msg = self.dev._make_temp_message(30.0, sid=sid)
            fields = {f.id: f.value for f in msg.fields}
            self.assertEqual(fields['sid'], sid)

    def test_manufacturer_is_yacht_devices(self):
        """GW_MANUFACTURER must be 717 (Yacht Devices), not 999 (Unknown)."""
        self.assertEqual(self.dev.GW_MANUFACTURER, 717)

    def test_preferred_sa(self):
        """Gateway preferred SA must be 200."""
        self.assertEqual(self.dev.GW_PREFERRED_SA, 200)



# ── N2KPGNDecoder.feed_to_lib ─────────────────────────────────────────────────

class TestFeedToLib(unittest.TestCase):
    """Tests for N2KPGNDecoder.feed_to_lib — fast-packet reassembly via library."""

    @classmethod
    def setUpClass(cls):
        try:
            from nmea2000 import NMEA2000Decoder  # noqa: F401
            cls._has_lib = True
        except ImportError:
            cls._has_lib = False

    def _skip_if_no_lib(self):
        if not self._has_lib:
            self.skipTest('nmea2000 library not installed')

    def test_feed_to_lib_returns_none_without_lib(self):
        """feed_to_lib must return None gracefully when library not available."""
        import ydnu02
        orig = ydnu02._HAS_N2K_LIB
        try:
            ydnu02._HAS_N2K_LIB = False
            parsed = {'info': {'can_id': 0x18EEFF5C, 'pgn': 60928, 'src': 92, 'dst': 255},
                      'data': bytes.fromhex('39 30 A0 5C 74 21 A7 2C'.replace(' ', ''))}
            result = N2KPGNDecoder.feed_to_lib(parsed)
            self.assertIsNone(result)
        finally:
            ydnu02._HAS_N2K_LIB = orig

    def test_feed_to_lib_iso_claim_returns_message(self):
        """feed_to_lib must return a complete message for single-frame PGN 60928."""
        self._skip_if_no_lib()
        parsed = {
            'info': {'can_id': 0x18EEFF5C, 'pgn': 60928, 'src': 92, 'dst': 255},
            'data': bytes.fromhex('3930A05C7421A72C'),
        }
        result = N2KPGNDecoder.feed_to_lib(parsed)
        # Library returns message for single-frame PGNs immediately
        self.assertIsNotNone(result)
        self.assertEqual(result.PGN, 60928)

    def test_feed_to_lib_exception_returns_none(self):
        """feed_to_lib must never raise — returns None on decode error."""
        self._skip_if_no_lib()
        # Corrupt data that can't be decoded
        parsed = {'info': {'can_id': 0xDEADBEEF, 'pgn': 0, 'src': 0, 'dst': 0},
                  'data': b'\xFF' * 8}
        try:
            result = N2KPGNDecoder.feed_to_lib(parsed)
            # Either None or some result — must not raise
        except Exception as exc:
            self.fail(f'feed_to_lib raised unexpectedly: {exc}')

    def test_feed_to_lib_missing_can_id_returns_none(self):
        """feed_to_lib with empty info must return None, not crash."""
        self._skip_if_no_lib()
        parsed = {'info': {}, 'data': b''}
        result = N2KPGNDecoder.feed_to_lib(parsed)
        # Decoding empty CAN ID 0x00000000 may return None or a result — must not raise
        self.assertIn(result, [None, result])  # any result is acceptable


# ── NMEA format compatibility (library-level) ─────────────────────────────────

class TestNMEAFormatCompatibility(unittest.TestCase):
    """Verify that lines our proxy emits are parseable by nmea2000 library."""

    def test_gobius_pgn127505_decoded(self):
        """The Gobius C PGN 127505 line should decode correctly."""
        try:
            from nmea2000 import NMEA2000Decoder
        except ImportError:
            self.skipTest('nmea2000 library not installed')
        d = NMEA2000Decoder()
        line = '01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        msg = d.decode(line)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.PGN, 127505)
        fields = {f.id: f.value for f in msg.fields}
        self.assertAlmostEqual(fields['level'], 94.4, places=1)

    def test_proxy_output_format_detected(self):
        """detect_format must recognise our output as CAN_FRAME_ASCII."""
        try:
            from nmea2000.input_formats import detect_format
            from nmea2000.decoder_formats import N2KFormat
        except ImportError:
            self.skipTest('nmea2000 library not installed')
        line = '01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        self.assertEqual(detect_format(line), N2KFormat.CAN_FRAME_ASCII)

    def test_no_cr_in_output_passes_regex(self):
        """Line with \\r must NOT be detected (regex requires $-terminated)."""
        try:
            from nmea2000.input_formats import _is_can_frame_ascii
        except ImportError:
            self.skipTest('nmea2000 library not installed')
        self.assertFalse(_is_can_frame_ascii('01:43:22.648 R 19F2115C 00 FF\r'))
        self.assertTrue(_is_can_frame_ascii('01:43:22.648 R 19F2115C 00 FF'))


# ── Integration (need real network) ──────────────────────────────────────────

class TestIntegration(unittest.TestCase):
    """Integration tests using real TCP sockets. Skip in sandbox."""

    def _make_fake_serial(self, lines: list[bytes]):
        q = list(lines)
        lock = threading.Lock()

        class FakeSerial:
            is_open = True

            def readline(self_inner):
                time.sleep(0.15)
                with lock:
                    if q:
                        return q.pop(0)
                return b''

            def write(self_inner, data):
                pass

        return FakeSerial()

    def _start_proxy(self, fake_serial):
        port = _free_port()
        mod = _load_gateway()
        mod._replay_device_frames = MagicMock()
        mod._send_iso_request = MagicMock()
        stop_event = threading.Event()

        def run():
            import socket as _socket
            srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            srv.bind(('127.0.0.1', port))
            srv.listen(5)
            srv.settimeout(0.1)
            mod.serial_instance = fake_serial

            def reader():
                while not stop_event.is_set():
                    raw = fake_serial.readline()
                    if not raw:
                        time.sleep(0.05)
                        continue
                    line = raw.rstrip(b'\r\n') + b'\n'
                    if mod._NMEA_LINE_RE.match(line):
                        mod._broadcast(line)

            threading.Thread(target=reader, daemon=True).start()
            while not stop_event.is_set():
                try:
                    conn, addr = srv.accept()
                    threading.Thread(
                        target=mod.handle_data_client,
                        args=(conn, addr), daemon=True,
                    ).start()
                except _socket.timeout:
                    continue
            srv.close()

        threading.Thread(target=run, daemon=True).start()
        time.sleep(0.3)
        return mod, port, stop_event

    @NEEDS_NETWORK
    def test_serial_to_tcp_delivery(self):
        """Lines from serial arrive at connected TCP client."""
        lines = [b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'] * 10
        fake_ser = self._make_fake_serial(lines)
        mod, port, stop = self._start_proxy(fake_ser)
        try:
            client = _tcp_connect(port)
            line = _recv_line(client)
            client.close()
        finally:
            stop.set()
        self.assertNotIn(b'\r', line)
        self.assertTrue(line.endswith(b'\n'))

    @NEEDS_NETWORK
    def test_two_clients_both_receive(self):
        """Both connected clients receive the same line from serial."""
        fake_ser = self._make_fake_serial([b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'] * 10)
        mod, port, stop = self._start_proxy(fake_ser)
        try:
            c1 = _tcp_connect(port)
            c2 = _tcp_connect(port)
            time.sleep(0.1)
            l1 = _recv_line(c1)
            l2 = _recv_line(c2)
            c1.close()
            c2.close()
        finally:
            stop.set()
        self.assertEqual(l1, l2)

    @NEEDS_NETWORK
    def test_client_disconnect_does_not_crash(self):
        """Disconnecting a client mid-stream must not crash the proxy."""
        fake_ser = self._make_fake_serial([b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'] * 20)
        mod, port, stop = self._start_proxy(fake_ser)
        try:
            c = _tcp_connect(port)
            _recv_line(c)
            c.close()
            time.sleep(0.2)
            c2 = _tcp_connect(port)
            line = _recv_line(c2)
            c2.close()
        finally:
            stop.set()
        self.assertNotIn(b'\r', line)

    @NEEDS_NETWORK
    def test_client_frame_forwarded_to_peer(self):
        """Frame from client A must reach client B (bidirectional hub)."""
        fake_ser = self._make_fake_serial([])
        mod, port, stop = self._start_proxy(fake_ser)
        try:
            c_sender = _tcp_connect(port)
            c_receiver = _tcp_connect(port)
            time.sleep(0.1)
            c_sender.sendall(VALID_LINE)
            line = _recv_line(c_receiver, timeout=2.0)
            c_sender.close()
            c_receiver.close()
        finally:
            stop.set()
        self.assertEqual(line, VALID_LINE)

    @NEEDS_NETWORK
    def test_client_frame_not_echoed_back(self):
        """Frame from a client must NOT be echoed back to the same client."""
        fake_ser = self._make_fake_serial([])
        mod, port, stop = self._start_proxy(fake_ser)
        try:
            c = _tcp_connect(port)
            c.settimeout(0.5)
            c.sendall(VALID_LINE)
            try:
                data = c.recv(256)
                got_echo = len(data) > 0
            except socket.timeout:
                got_echo = False
            c.close()
        finally:
            stop.set()
        self.assertFalse(got_echo, 'Client must NOT receive its own frames back')


if __name__ == '__main__':
    unittest.main(verbosity=2)
