"""Frame format utilities for YDNU-02 TCP Gateway.
=================================================

NMEA 2000 CAN FRAME ASCII NORMALIZATION SPECIFICATION:
------------------------------------------------------
1. PHYSICAL HARDWARE RESPONSE BEHAVIOR:
   - The physical YDNU-02 USB gateway sends outgoing CAN frames (transmitted to the N2K bus
     in response to ISO Requests PGN 59904) with direction flag 'T' (Transmit), e.g.:
       '18:48:47.064 T 19F01440 C0 86 15 05...'
   - FastPacket PGN 126996 (Product Information) is emitted by the physical YDNU-02 hardware
     as a 19-frame sequence (sequence counters C0..D3) marked with flag 'T'.

2. HA-NMEA2000 & DECODER REQUIREMENT:
   - The `nmea2000` library (used by `ha-nmea2000` in Home Assistant) and our internal
     `N2KDeviceRegistry` parser STRICTLY decode ONLY lines with direction flag 'R' (Receive).
   - Any frame with flag 'T' is rejected by `NMEA2000Decoder.decode()` returning None!
   - Without converting ' T ' -> ' R ', Home Assistant ignores all physical PGN 126996 frames
     and physical ISO Address Claims (PGN 60928), causing physical devices to disappear.

3. UNIFORM NORMALIZATION:
   - `normalize_frame()` is the single canonical transformer for all incoming ASCII lines.
   - It replaces ' T ' with ' R ' safely (since hex CAN data consists strictly of 2-char hex pairs,
     the single-character flag ' T ' surrounded by spaces cannot false-positive in CAN payload).
   - Strips trailing CR/LF and guarantees clean '\n' line termination.

DIAGNOSTIC SKILL / MINI-PROMPTS:
================================
  Skill — test frame normalization via Python CLI::

      python3 -c "
      from ydnu02_tcp_gateway.frame_utils import normalize_frame
      raw = b'18:48:47.064 T 19F01440 C0 86 15 05\\r\\n'
      norm = normalize_frame(raw)
      assert b' R ' in norm
      print('Normalized frame:', norm)
      "
"""

import re
from typing import Tuple, Union
from ydnu02.pgn_decoder import N2KPGNDecoder


# RX format regex: "HH:MM:SS.mmm R XXXXXXXX XX XX ...\n"
NMEA_LINE_RE = re.compile(
    rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"
)

# TX format regex: "XXXXXXXX XX XX ...\r?\n"
TX_LINE_RE = re.compile(
    rb"^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r?\n$"
)


def normalize_frame(line: bytes) -> bytes:
    """Normalize a YDNU-02 ASCII line into canonical RX (R-frame) format.

    Replaces outgoing ' T ' flags with ' R ' so nmea2000 decoders process them correctly,
    and strips trailing carriage returns while guaranteeing a single '\n' terminator.

    Args:
        line: Raw line bytes from serial or socket.

    Returns:
        Normalized ASCII line bytes ending with b'\\n'.

    Skill — verify T-to-R conversion::

        assert normalize_frame(b'00:00:00.000 T 18EEFF40 00\\r\\n') == b'00:00:00.000 R 18EEFF40 00\\n'
    """
    if b" T " in line:
        line = line.replace(b" T ", b" R ", 1)
    return line.rstrip(b"\r\n") + b"\n"


def fmt_frame(can_id_hex: str, data: bytes) -> bytes:
    """Format raw CAN data as a YDNU-02 ASCII RX-format text line.

    Output format: 00:00:00.000 R XXXXXXXX XX XX XX ...\n
    """
    return f'00:00:00.000 R {can_id_hex} {" ".join(f"{b:02X}" for b in data)}\n'.encode('ascii')


def get_pgn_sa(can_id: Union[bytes, str]) -> Tuple[int, int]:
    """Decode (PGN, SourceAddress) from an 8-char hex CAN ID.

    Delegates to N2KPGNDecoder.parse_can_id() — the single canonical
    CAN ID bit-math implementation — to prevent divergence.

    Args:
        can_id: 8-char hex CAN ID as str or ASCII bytes (e.g. b'18EEFF5C').

    Returns:
        (pgn, sa) tuple.
    """
    can_id_str = can_id.decode("ascii") if isinstance(can_id, bytes) else can_id
    parsed = N2KPGNDecoder.parse_can_id(can_id_str)
    return parsed["pgn"], parsed["src"]
