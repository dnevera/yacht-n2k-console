"""Tests for DataHub serial-forwarding guards:

1. SA-guard — TX frames whose source address belongs to the gateway's own
   virtual devices (SA=64 YDNU-02, SA=200 TCP-GW) must never be forwarded to
   the physical serial bus, except the legitimate ISO Request (PGN 59904).
2. Independent temperature (PGN 130312) throttling of physical serial
   forwarding via ``n2k_serial_temp_interval_s``, decoupled from how often
   the TCP hub itself broadcasts the same PGN (``n2k_tcp_temp_interval_s``).
3. Diagnostic echo-logging: recognizing a TX frame read back from the
   physical bus as a pseudo-ACK for testing/troubleshooting purposes.
"""
import threading

import pytest

import ydnu02_tcp_gateway.gateway_settings as _gs_module
from ydnu02_tcp_gateway.gateway_settings import GatewaySettings
from ydnu02_tcp_gateway.data_hub import DataHub, _VIRTUAL_DEVICE_SA, _TX_ECHO_WINDOW_S


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Redirect settings file to a temp dir and reset the singleton before each test."""
    monkeypatch.setattr(_gs_module, '_SETTINGS_DIR', str(tmp_path))
    monkeypatch.setattr(_gs_module, '_SETTINGS_FILE', str(tmp_path / 'gateway_settings.json'))
    monkeypatch.setattr(_gs_module, '_instance', None)
    yield
    monkeypatch.setattr(_gs_module, '_instance', None)


@pytest.fixture
def hub():
    return DataHub(get_serial_instance=lambda: None, get_serial_ready=lambda: True,
                   clients_lock=threading.Lock())


class TestVirtualDeviceSAConstant:
    def test_expected_sa_values(self):
        """Verify the virtual-device SA whitelist matches YDNU-02 (64) and TCP-GW (200)."""
        assert _VIRTUAL_DEVICE_SA == {64, 200}


class TestSAGuard:
    def test_blocks_frames_from_virtual_sa_200(self, hub):
        """TX frames with SA=200 (TCP-GW) must not be forwarded, even when enabled."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        assert hub._should_forward_to_serial(130312, 200, settings) is False

    def test_blocks_frames_from_virtual_sa_64(self, hub):
        """TX frames with SA=64 (YDNU-02) must not be forwarded, even when enabled."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        assert hub._should_forward_to_serial(127490, 64, settings) is False

    def test_allows_frames_from_physical_sa(self, hub):
        """TX frames from an ordinary physical SA (e.g. 92) are forwarded as before."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        assert hub._should_forward_to_serial(127490, 92, settings) is True

    def test_physical_sa_blocked_when_tx_disabled(self, hub):
        """Ordinary physical SA frames still respect n2k_serial_tx_enabled=False."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': False})
        assert hub._should_forward_to_serial(127490, 92, settings) is False

    def test_iso_request_forwarded_regardless_of_enabled(self, hub):
        """ISO Request (PGN 59904) is forwarded even when n2k_serial_tx_enabled=False."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': False})
        assert hub._should_forward_to_serial(59904, 254, settings) is True

    def test_iso_request_forwarded_even_from_virtual_sa(self, hub):
        """ISO Request bypasses the SA-guard too (legitimate regardless of source)."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        assert hub._should_forward_to_serial(59904, 200, settings) is True


class TestVirtualBroadcastForwarding:
    """NMEA_LINE_RE (format A) broadcasts: only forwarded for OUR OWN virtual
    devices (SA in _VIRTUAL_DEVICE_SA) — opposite guard from TX_LINE_RE."""

    def test_virtual_sa_broadcast_forwarded(self, hub):
        """A broadcast from our own virtual device (SA=200) is forwarded when enabled."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        assert hub._should_forward_virtual_broadcast_to_serial(126996, 200, settings) is True

    def test_physical_sa_broadcast_not_forwarded(self, hub):
        """A broadcast from an ordinary physical SA is never forwarded via this path."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        assert hub._should_forward_virtual_broadcast_to_serial(127505, 92, settings) is False

    def test_virtual_sa_broadcast_blocked_when_disabled(self, hub):
        """Virtual-device broadcasts still respect n2k_serial_tx_enabled=False."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': False})
        assert hub._should_forward_virtual_broadcast_to_serial(126996, 200, settings) is False


class TestSerialTempThrottle:
    def test_first_temp_frame_forwarded(self, hub):
        """The first CPU-temp (PGN 130312) broadcast from our virtual SA is forwarded."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True, 'n2k_serial_temp_interval_s': 5.0})
        assert hub._should_forward_virtual_broadcast_to_serial(130312, 200, settings) is True

    def test_second_temp_frame_within_interval_blocked(self, hub, monkeypatch):
        """A second temp frame arriving before the serial interval elapses is dropped."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True, 'n2k_serial_temp_interval_s': 5.0})

        t = [100.0]
        monkeypatch.setattr('ydnu02_tcp_gateway.data_hub.time.monotonic', lambda: t[0])

        assert hub._should_forward_virtual_broadcast_to_serial(130312, 200, settings) is True   # t=100 -> sent
        t[0] = 102.0
        assert hub._should_forward_virtual_broadcast_to_serial(130312, 200, settings) is False  # t=102, only 2s elapsed

    def test_temp_frame_forwarded_again_after_interval_elapses(self, hub, monkeypatch):
        """Once n2k_serial_temp_interval_s has elapsed, the next temp frame is forwarded."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True, 'n2k_serial_temp_interval_s': 5.0})

        t = [100.0]
        monkeypatch.setattr('ydnu02_tcp_gateway.data_hub.time.monotonic', lambda: t[0])

        assert hub._should_forward_virtual_broadcast_to_serial(130312, 200, settings) is True   # t=100 -> sent
        t[0] = 106.0
        assert hub._should_forward_virtual_broadcast_to_serial(130312, 200, settings) is True  # t=106, 6s elapsed -> sent

    def test_non_temp_pgn_not_throttled(self, hub, monkeypatch):
        """Non-temperature PGNs from our virtual SA are unaffected by the temp throttle timer."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True, 'n2k_serial_temp_interval_s': 5.0})

        t = [100.0]
        monkeypatch.setattr('ydnu02_tcp_gateway.data_hub.time.monotonic', lambda: t[0])

        assert hub._should_forward_virtual_broadcast_to_serial(126996, 200, settings) is True
        t[0] = 100.5
        # Same virtual SA, different PGN, back-to-back — no throttling applies (only PGN 130312 is throttled)
        assert hub._should_forward_virtual_broadcast_to_serial(126996, 200, settings) is True


class TestHandleClientIntegration:
    """End-to-end sanity check through the public handle_client() code path."""

    def _make_hub_with_serial(self):
        from unittest.mock import MagicMock
        mock_ser = MagicMock()
        mock_ser.is_open = True
        # get_serial_ready=False disables the automatic onboarding ISO Request
        # inside handle_client(), so ser.write() call counts only reflect the
        # TX-line forwarding logic under test.
        hub = DataHub(get_serial_instance=lambda: mock_ser, get_serial_ready=lambda: False,
                       clients_lock=threading.Lock())
        return hub, mock_ser

    def _run_client(self, hub, lines: bytes):
        """Feed raw bytes through handle_client() using a fake socket."""
        class FakeConn:
            def __init__(self, data):
                self._chunks = [data, b'']
                self._idx = 0

            def recv(self, _n):
                if self._idx >= len(self._chunks):
                    return b''
                chunk = self._chunks[self._idx]
                self._idx += 1
                return chunk

            def close(self):
                pass

        hub.handle_client(FakeConn(lines), ('127.0.0.1', 12345))

    def test_tx_frame_with_virtual_sa_not_forwarded(self):
        """A format-B TX frame carrying SA=200 (TCP-GW) must not reach ser.write()."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        hub, mock_ser = self._make_hub_with_serial()
        # CAN ID 09FF04C8 decodes to PGN=130312, SA=200 (see frame_utils.py docstring)
        self._run_client(hub, b'09FF04C8 00 00 00 00 00 00 00 00\r\n')
        mock_ser.write.assert_not_called()

    def test_tx_frame_with_physical_sa_forwarded(self):
        """A format-B TX frame carrying an ordinary physical SA is forwarded as before."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        hub, mock_ser = self._make_hub_with_serial()
        # CAN ID 09FD025C decodes to PGN=127490, SA=92 (see frame_utils.py docstring)
        self._run_client(hub, b'09FD025C 00 00 00 00 00 00 00 00\r\n')
        mock_ser.write.assert_called_once()

    def test_iso_request_tx_forwarded_even_when_disabled(self):
        """ISO Request (PGN 59904) TX frames still reach ser.write() regardless of the toggle."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': False})
        hub, mock_ser = self._make_hub_with_serial()
        self._run_client(hub, b'18EAFFFE 00 EE 00\r\n')
        mock_ser.write.assert_called_once()

    def test_nmea_broadcast_from_physical_sa_not_forwarded(self):
        """A regression guard: format-A broadcast from an ordinary physical SA
        (e.g. relayed by a passive TCP client) must NOT reach ser.write() —
        only our own virtual devices' broadcasts are relayed to serial."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        hub, mock_ser = self._make_hub_with_serial()
        # SA=0x5C=92 (physical), format A (NMEA_LINE_RE)
        self._run_client(hub, b'01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n')
        mock_ser.write.assert_not_called()

    def test_nmea_broadcast_from_virtual_sa_forwarded(self):
        """A format-A broadcast from our own virtual device (SA=200) IS forwarded
        to physical serial when n2k_serial_tx_enabled=True."""
        settings = GatewaySettings.instance()
        settings.apply_from_dict({'n2k_serial_tx_enabled': True})
        hub, mock_ser = self._make_hub_with_serial()
        # CAN ID 18EEFFC8 decodes to PGN=60928, SA=200 (see frame_utils.py docstring)
        self._run_client(hub, b'00:00:00.000 R 18EEFFC8 39 30 A0 C8 74 21 A7 2C\n')
        mock_ser.write.assert_called_once()


class TestTxEchoDiagnostics:
    """Diagnostic echo-logging: DataHub.record_tx_echo_candidate() /
    DataHub.check_tx_echo() — a testing/troubleshooting aid, NOT a real
    protocol-level delivery acknowledgment (YDNU-02 RAW mode has none)."""

    def test_echo_logged_when_can_id_matches(self, hub, capsys):
        """A CAN ID read back shortly after being recorded logs a pseudo-ACK."""
        hub.record_tx_echo_candidate('18EAFFFE')
        hub.check_tx_echo('18eaFFfe')  # case-insensitive match
        out = capsys.readouterr().out
        assert 'echo' in out.lower()
        assert '18EAFFFE' in out

    def test_no_echo_logged_for_unrelated_can_id(self, hub, capsys):
        """A CAN ID that was never written to serial produces no echo log."""
        hub.record_tx_echo_candidate('18EAFFFE')
        hub.check_tx_echo('09FF04C8')
        out = capsys.readouterr().out
        assert 'echo' not in out.lower()

    def test_echo_not_logged_after_window_expires(self, hub, monkeypatch, capsys):
        """A CAN ID read back after _TX_ECHO_WINDOW_S has elapsed is not logged."""
        t = [100.0]
        monkeypatch.setattr('ydnu02_tcp_gateway.data_hub.time.monotonic', lambda: t[0])

        hub.record_tx_echo_candidate('18EAFFFE')
        t[0] = 100.0 + _TX_ECHO_WINDOW_S + 1.0
        hub.check_tx_echo('18EAFFFE')
        out = capsys.readouterr().out
        assert 'echo' not in out.lower()

    def test_echo_is_consumed_only_once(self, hub, capsys):
        """Once matched, the pending entry is removed — a second identical read
        back does not produce a duplicate pseudo-ACK log."""
        hub.record_tx_echo_candidate('18EAFFFE')
        hub.check_tx_echo('18EAFFFE')
        capsys.readouterr()  # clear first log
        hub.check_tx_echo('18EAFFFE')
        out = capsys.readouterr().out
        assert 'echo' not in out.lower()

    def test_echo_never_raises_on_malformed_input(self, hub):
        """check_tx_echo must never raise, even with malformed CAN ID strings."""
        hub.check_tx_echo('')
        hub.check_tx_echo('not-hex')
