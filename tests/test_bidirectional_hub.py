"""Tests for handle_data_client: bidirectional TCP hub.

Tests require real TCP sockets — skipped in macOS sandbox.
Mini-prompt: covers client frame forwarding, TX→RX conversion, ISO Claim broadcast.
"""
import socket, threading, time, unittest
from unittest.mock import MagicMock
from tests.gw_test_helpers import load_gateway, VALID_LINE, ISO_CLAIM_LINE, NEEDS_NETWORK, make_pipe

class TestBidirectionalHub(unittest.TestCase):
    """handle_data_client forwards frames to OTHER clients, NOT to serial."""

    def setUp(self):
        self.mod = load_gateway()
        # Patch _replay_device_frames and _send_iso_request to no-ops
        self.mod._replay_device_frames = MagicMock()
        self.mod._send_iso_request = MagicMock()

    @NEEDS_NETWORK
    def test_client_frame_not_forwarded_to_serial(self):
        """A valid N2K frame from a client must NOT be written to serial."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.mod.serial_instance = mock_serial

        conn, client = make_pipe()
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

        conn, client = make_pipe()
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
        conn, client = make_pipe()
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
        conn, client = make_pipe()
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
    def test_tx_iso_claim_broadcast(self):
        """ISO Claim in TX format from N2KDevice is broadcast to TCP clients."""
        c_conn, c_client = make_pipe()
        self.mod.clients = {c_conn}
        conn, client = make_pipe()
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

        received = c_client.recv(4096)
        c_conn.close()
        c_client.close()

        # Verified: TX ISO Claim frame is broadcast to other clients in formatted RX line
        self.assertTrue(len(received) > 0)
        self.assertIn(b'18EEFFC8', received)
