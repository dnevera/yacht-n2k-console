"""Frame format utilities for YDNU-02 TCP Gateway.

Formatting synthetic NMEA ASCII lines and line regexes.
CAN ID decoding is delegated to N2KPGNDecoder.parse_can_id() — the
single canonical implementation — to avoid duplicated bit-math.
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
