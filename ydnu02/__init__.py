"""ydnu02 package.

Yacht Devices YDNU-02 NMEA 2000 USB Gateway Controller & Monitor.
"""

import sys
import types
import ydnu02.pgn_decoder as _pgn_dec
from ydnu02.pgn_decoder import N2KPGNDecoder
from ydnu02.controller import YDNU02Controller
from ydnu02.cli import build_parser, main


class _Ydnu02Module(types.ModuleType):
    @property
    def _HAS_N2K_LIB(self):
        return _pgn_dec._HAS_N2K_LIB

    @_HAS_N2K_LIB.setter
    def _HAS_N2K_LIB(self, val):
        _pgn_dec._HAS_N2K_LIB = val


sys.modules[__name__].__class__ = _Ydnu02Module

__all__ = [
    "N2KPGNDecoder",
    "YDNU02Controller",
    "_HAS_N2K_LIB",
    "build_parser",
    "main",
]
