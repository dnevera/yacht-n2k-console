"""Collect all test specs in execution order."""

from specs.device import TESTS as DEVICE
from specs.maintenance import TESTS as MAINTENANCE
from specs.service import TESTS as SERVICE
from specs.firmware import TESTS as FIRMWARE
from specs.static import TESTS as STATIC

ALL = DEVICE + MAINTENANCE + SERVICE + FIRMWARE + STATIC
