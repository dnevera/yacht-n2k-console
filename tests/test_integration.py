"""End-to-end integration tests for the TCP gateway.

Requires real TCP sockets — all tests skipped in macOS sandbox.
Run on Pi with: python3 -m pytest tests/test_integration.py -v
Mini-prompt: tests full serial→TCP delivery, multi-client fanout, client frame forwarding.
"""
import socket, threading, time, unittest
from unittest.mock import MagicMock
from tests.gw_test_helpers import load_gateway, VALID_LINE, NEEDS_NETWORK, free_port, tcp_connect, recv_line

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
        port = free_port()
        mod = load_gateway()
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
            client = tcp_connect(port)
            line = recv_line(client)
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
            c1 = tcp_connect(port)
            c2 = tcp_connect(port)
            time.sleep(0.1)
            l1 = recv_line(c1)
            l2 = recv_line(c2)
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
            c = tcp_connect(port)
            recv_line(c)
            c.close()
            time.sleep(0.2)
            c2 = tcp_connect(port)
            line = recv_line(c2)
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
            c_sender = tcp_connect(port)
            c_receiver = tcp_connect(port)
            time.sleep(0.1)
            c_sender.sendall(VALID_LINE)
            line = recv_line(c_receiver, timeout=2.0)
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
            c = tcp_connect(port)
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
