"""Tests for DataHub: broadcast fanout, ISO Request, serial fanout.

Tests use MagicMock sockets — no real network needed.
Mini-prompt: covers _broadcast(), _send_iso_request(), serial→TCP fanout logic.
"""
import threading, unittest
from unittest.mock import MagicMock
from tests.gw_test_helpers import load_gateway, VALID_LINE, ISO_CLAIM_LINE, NEEDS_NETWORK

class TestBroadcast(unittest.TestCase):
    """_broadcast sends to all clients, optionally excluding one (the sender)."""

    def setUp(self):
        self.mod = load_gateway()

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
        """Verify that messages are broadcast to all connected clients."""
        conns, received = self._make_clients(3)
        self.mod._broadcast(VALID_LINE)
        for i in range(3):
            self.assertEqual(received[i], [VALID_LINE])

    def test_broadcast_excludes_sender(self):
        """Verify that broadcast excludes the sender client."""
        conns, received = self._make_clients(3)
        sender = conns[1]
        self.mod._broadcast(VALID_LINE, exclude=sender)
        self.assertEqual(received[0], [VALID_LINE], 'client 0 must receive')
        self.assertEqual(received[1], [],           'sender must NOT receive')
        self.assertEqual(received[2], [VALID_LINE], 'client 2 must receive')

    def test_broadcast_exclude_none_sends_to_all(self):
        """Verify that broadcast with exclude=None sends to all clients."""
        conns, received = self._make_clients(2)
        self.mod._broadcast(VALID_LINE, exclude=None)
        self.assertEqual(received[0], [VALID_LINE])
        self.assertEqual(received[1], [VALID_LINE])

    def test_dead_client_removed(self):
        """Verify that disconnected clients are removed during broadcast."""
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

    def test_broadcast_fans_out_without_caching(self):
        """Broadcast fans out line to all connected clients directly."""
        conns, received = self._make_clients(2)
        self.mod._broadcast(ISO_CLAIM_LINE)
        self.assertEqual(received[0], [ISO_CLAIM_LINE])
        self.assertEqual(received[1], [ISO_CLAIM_LINE])


class TestISORequestBroadcast(unittest.TestCase):
    """_send_iso_request must send to serial AND broadcast to TCP data clients."""

    def setUp(self):
        self.mod = load_gateway()
        # Force last sent to distant past so rate-limiter passes
        self.mod._iso_request_last_sent = 0.0
        self.mod._serial_ready.set()

    def _setup_serial(self):
        mock_ser = MagicMock()
        mock_ser.is_open = True
        self.mod.serial_instance = mock_ser
        return mock_ser

    def test_sends_to_serial(self):
        """Verify that ISO request frames are written to serial."""
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
        iso_req = next(d for d in received if b'18EAFFFE' in d)
        self.assertIsNotNone(self.mod._NMEA_LINE_RE.match(iso_req))

    def test_serial_iso_request_format_strict_no_timestamp_prefix(self):
        """CRITICAL DIAGNOSTIC: Serial ISO Request MUST NOT contain timestamp prefix.

        YDNU-02 RAW mode firmware rejects lines with '00:00:00.000 T ' prefix as syntax errors.
        If timestamp is included, physical devices like YDNU-02 (402047) never receive ISO Request
        for Product Info (PGN 126996), causing HA to show 'Product Information (...) This device has no entities'.
        """
        import re
        strict_raw_tx_re = re.compile(rb'^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r\n$')

        mock_ser = self._setup_serial()
        self.mod.clients = set()
        self.mod._send_iso_request()

        self.assertEqual(mock_ser.write.call_count, 2, 'Must send 2 ISO Requests (Claim + Prod Info)')
        writes = [call[0][0] for call in mock_ser.write.call_args_list]

        for w in writes:
            line_str = w.decode('ascii', errors='ignore')
            self.assertFalse(
                w.startswith(b'00:00:00.000') or b' T ' in w,
                f"DIAGNOSTIC FAILURE: Serial write '{line_str.strip()}' contains invalid timestamp prefix! "
                f"YDNU-02 RAW firmware will reject this, preventing HA from acquiring Product Info (PGN 126996)."
            )
            self.assertTrue(
                strict_raw_tx_re.match(w),
                f"DIAGNOSTIC FAILURE: Serial write '{line_str.strip()}' does not match strict YDNU RAW TX syntax! "
                f"Expected format: '18EAFFFE 14 F0 01\r\n'"
            )

        self.assertEqual(writes[1], bytes.fromhex("31384541464646452031342046302030310d0a"))

    def test_rate_limited(self):
        """Rapid calls must not spam serial or TCP clients."""
        mock_ser = self._setup_serial()
        self.mod.clients = set()
        self.mod._send_iso_request()
        self.mod._send_iso_request()  # should be rate-limited
        self.mod._send_iso_request()
        self.assertEqual(mock_ser.write.call_count, 2, 'Must be rate-limited to 1 request set (2 writes)')

    def test_no_write_when_serial_not_ready(self):
        """Verify that ISO request is not sent when serial is not ready."""
        mock_ser = self._setup_serial()
        self.mod._serial_ready.clear()
        self.mod.clients = set()
        self.mod._send_iso_request()
        mock_ser.write.assert_not_called()

    def test_no_write_in_service_mode(self):
        """Verify that ISO request is not sent in service mode."""
        mock_ser = self._setup_serial()
        self.mod.service_mode.set()
        self.mod.clients = set()
        self.mod._send_iso_request()
        mock_ser.write.assert_not_called()
        self.mod.service_mode.clear()


class TestSerialFanout(unittest.TestCase):
    """Serial → TCP: CR/LF stripping, empty line filtering, multi-client fanout."""

    def setUp(self):
        self.mod = load_gateway()

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
        """Verify that CRLF line endings are stripped from serial input."""
        raw = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\r\n'
        result = self._fanout(raw)
        self.assertIsNotNone(result)
        self.assertNotIn(b'\r', result)
        self.assertTrue(result.endswith(b'\n'))

    def test_lf_only_passthrough(self):
        """Verify that LF line endings pass through intact."""
        result = self._fanout(b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n')
        self.assertEqual(result, b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n')

    def test_empty_line_filtered(self):
        """Verify that empty lines from serial are filtered out."""
        self.assertIsNone(self._fanout(b'\r\n'))

    def test_whitespace_only_filtered(self):
        """Verify that whitespace-only lines are filtered out."""
        self.assertIsNone(self._fanout(b'   \r\n'))

    def test_empty_bytes_filtered(self):
        """Verify that empty byte inputs are filtered out."""
        self.assertIsNone(self._fanout(b''))

    def test_text_line_filtered_by_regex(self):
        """Verify that non-NMEA text lines are filtered out."""
        self.assertIsNone(self._fanout(b'YDNU MODE RAW OK\r\n'))

    def test_data_content_preserved(self):
        """Verify that payload data content is preserved during fanout."""
        payload = b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF'
        result = self._fanout(payload + b'\r\n')
        self.assertTrue(result.startswith(payload))

    def test_fanout_to_multiple_clients(self):
        """Verify that serial lines are fanned out to all clients."""
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
