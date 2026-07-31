"""Tests for frame_utils: _fmt_frame, NMEA regex, TX regex, get_pgn_sa.

Unit tests only — no network, no serial, no threading.
Mini-prompt: these tests cover pure parsing/formatting logic in frame_utils.py.
"""
import unittest
from tests.gw_test_helpers import load_gateway, VALID_LINE, ISO_CLAIM_LINE

class TestFmtFrame(unittest.TestCase):
    """_fmt_frame must produce a valid CAN_FRAME_ASCII line."""

    def setUp(self):
        self.mod = load_gateway()

    def test_output_ends_with_lf(self):
        """Verify that formatted output ends with a newline."""
        result = self.mod._fmt_frame('18EEFFC8', b'\x39\x30\xA0\x5C\x74\x21\xA7\x2C')
        self.assertTrue(result.endswith(b'\n'))

    def test_no_cr_in_output(self):
        """Verify that formatted output contains no carriage return."""
        result = self.mod._fmt_frame('18EEFFC8', b'\x39\x30\xA0\x5C')
        self.assertNotIn(b'\r', result)

    def test_format_contains_r_direction(self):
        """Verify that formatted output includes the R direction indicator."""
        result = self.mod._fmt_frame('18EEFFC8', b'\x00\x01')
        self.assertIn(b' R ', result)

    def test_can_id_hex_present(self):
        """Verify that formatted output includes the CAN ID in hex."""
        result = self.mod._fmt_frame('18EEFFC8', b'\x00')
        self.assertIn(b'18EEFFC8', result)

    def test_data_bytes_hex_present(self):
        """Verify that formatted output includes the data bytes in hex."""
        result = self.mod._fmt_frame('00000000', b'\xAB\xCD')
        self.assertIn(b'AB', result)
        self.assertIn(b'CD', result)

    def test_matches_nmea_regex(self):
        """Verify that formatted frame matches the NMEA regex."""
        result = self.mod._fmt_frame('18EEFFC8', b'\x39\x30\xA0\x5C\x74\x21\xA7\x2C')
        self.assertIsNotNone(self.mod._NMEA_LINE_RE.match(result))


class TestNMEARegex(unittest.TestCase):
    """_NMEA_LINE_RE must accept valid frames and reject invalid ones."""

    def setUp(self):
        self.mod = load_gateway()
        self.re = self.mod._NMEA_LINE_RE

    def test_valid_r_frame_matches(self):
        """Verify that valid received frames match the regex."""
        self.assertIsNotNone(self.re.match(VALID_LINE))

    def test_valid_t_frame_matches(self):
        """Verify that valid transmitted frames match the regex."""
        line = b'00:00:00.000 T 18EAFFFE 00 EE 00\n'
        self.assertIsNotNone(self.re.match(line))

    def test_cr_in_frame_rejected(self):
        """Verify that frames containing carriage returns are rejected."""
        line = b'01:43:22.648 R 19F2115C 00 30 5C 64\r\n'
        self.assertIsNone(self.re.match(line))

    def test_empty_line_rejected(self):
        """Verify that empty lines are rejected."""
        self.assertIsNone(self.re.match(b'\n'))

    def test_text_line_rejected(self):
        """Verify that plain text lines are rejected."""
        self.assertIsNone(self.re.match(b'YDNU MODE RAW\n'))

    def test_truncated_can_id_rejected(self):
        """Verify that frames with truncated CAN IDs are rejected."""
        self.assertIsNone(self.re.match(b'01:23:45.678 R 1F2115C 00\n'))

    def test_valid_8byte_payload_matches(self):
        """Verify that valid 8-byte payload frames match the regex."""
        line = b'00:00:01.000 R 18EEFF5C 39 30 A0 5C 74 21 A7 2C\n'
        self.assertIsNotNone(self.re.match(line))

    def test_tx_format_rejected_by_rx_regex(self):
        """TX format (no timestamp) must NOT match _NMEA_LINE_RE."""
        tx = b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n'
        self.assertIsNone(self.re.match(tx))


class TestTXLineRegex(unittest.TestCase):
    """_TX_LINE_RE must match YDNU-02 TX (outgoing) frames from nmea2000 N2KDevice."""

    def setUp(self):
        self.mod = load_gateway()
        self.re = self.mod._TX_LINE_RE

    def test_tx_frame_crlf_matches(self):
        """Verify that CRLF-terminated TX frames match the regex."""
        self.assertIsNotNone(self.re.match(b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n'))

    def test_tx_frame_lf_only_matches(self):
        """Verify that LF-terminated TX frames match the regex."""
        self.assertIsNotNone(self.re.match(b'18EEFFC8 39 30 A0 5C\n'))

    def test_tx_single_byte_matches(self):
        """Verify that single-byte TX frames match the regex."""
        self.assertIsNotNone(self.re.match(b'18EAFFFE 00\r\n'))

    def test_rx_format_rejected(self):
        """Verify that RX-formatted frames are rejected by TX regex."""
        self.assertIsNone(self.re.match(b'01:43:22.648 R 19F2115C 00 30\n'))

    def test_text_rejected(self):
        """Verify that plain text is rejected by TX regex."""
        self.assertIsNone(self.re.match(b'YDNU MODE OK\r\n'))

    def test_truncated_can_id_rejected(self):
        """Verify that truncated CAN IDs are rejected by TX regex."""
        self.assertIsNone(self.re.match(b'18EEFFC 39 30\r\n'))  # 7 hex chars

    def test_iso_claim_tx_format(self):
        """Verify that ISO claim frames in TX format match the regex."""
        self.assertIsNotNone(self.re.match(b'18EEFFC8 39 30 A0 5C 74 21 A7 2C\r\n'))


class TestGetPgnSa(unittest.TestCase):
    """_get_pgn_sa must decode PGN and SA from hex CAN ID string."""

    def setUp(self):
        self.mod = load_gateway()

    def test_iso_claim_pgn_and_sa(self):
        """Verify extraction of PGN and SA from an ISO claim CAN ID."""
        # CAN ID 18EEFF5C: PGN=60928 (0xEE00), SA=0x5C=92
        pgn, sa = self.mod._get_pgn_sa(b'18EEFF5C')
        self.assertEqual(pgn, 60928)
        self.assertEqual(sa, 92)

    def test_product_info_pgn(self):
        """Verify extraction of PGN and SA from a Product Info CAN ID."""
        # PGN 126996 = 0x1F014, CAN ID with PF=0xF0, PS=0x14
        # CAN ID: priority=6, DP=1, PF=0xF0, PS=0x14, SA=0xC8
        # = (6<<26)|(1<<24)|(0xF0<<16)|(0x14<<8)|0xC8 = 0x19F014C8
        pgn, sa = self.mod._get_pgn_sa(b'19F014C8')
        self.assertEqual(pgn, 126996)
        self.assertEqual(sa, 0xC8)

    def test_temperature_pgn(self):
        """Verify extraction of PGN from a Temperature CAN ID."""
        # PGN 130312 = 0x1FD08, CAN ID 19FD08C8
        pgn, sa = self.mod._get_pgn_sa(b'19FD08C8')
        self.assertEqual(pgn, 130312)

    def test_invalid_raises(self):
        """Verify that invalid CAN ID input raises an exception."""
        with self.assertRaises((ValueError, IndexError)):
            self.mod._get_pgn_sa(b'ZZZZZZZZ')
