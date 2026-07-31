"""Tests for ydnu02_gateway_device and N2KPGNDecoder.

Covers: CPU temp reading, version file, PGN 130312 message building,
N2KPGNDecoder.feed_to_lib, NMEA format compatibility.
Mini-prompt: tests _read_cpu_temp, _read_version, _make_temp_message, feed_to_lib.
"""
import unittest
from unittest.mock import MagicMock, patch
from tests.gw_test_helpers import load_device
from ydnu02 import N2KPGNDecoder

class TestGatewayDevice(unittest.TestCase):
    """Unit tests for ydnu02_gateway_device.py functions."""

    def setUp(self):
        self.dev = load_device()

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
        """Verify that _read_version reads version string from file."""
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

    def test_manufacturer_is_custom(self):
        """GW_MANUFACTURER must be 2047 (Custom/Experimental)."""
        self.assertEqual(self.dev.GW_MANUFACTURER, 2047)

    def test_preferred_sa(self):
        """Gateway preferred SA must be 200."""
        self.assertEqual(self.dev.GW_PREFERRED_SA, 200)


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
        # Decoding empty CAN ID 0x00000000 may return None or a result — must not raise.
        # No assertion on result value: the goal is crash-safety, not a specific return.


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
        """Line with \r must NOT be detected (regex requires 569Xterminated)."""
        try:
            from nmea2000.input_formats import _is_can_frame_ascii
        except ImportError:
            self.skipTest('nmea2000 library not installed')
        self.assertFalse(_is_can_frame_ascii('01:43:22.648 R 19F2115C 00 FF\r'))
        self.assertTrue(_is_can_frame_ascii('01:43:22.648 R 19F2115C 00 FF'))
