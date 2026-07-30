#!/usr/bin/env python3
"""
ydnu02_gateway_device.py — Virtual N2K device identity for the YDNU-02 TCP Gateway.

PURPOSE
  Registers the Raspberry Pi 5 gateway as a first-class NMEA 2000 device (SA=200)
  on the N2K network so Home Assistant and Signal K can track its liveness.

HOW IT WORKS
  Uses the nmea2000 Python library's N2KDevice.for_text_gateway() to connect to
  the gateway's own port 4001 (CAN_FRAME_ASCII format). The gateway port-4001 hub
  (bidirectional) relays these frames to all other connected clients (HA, SignalK).
  N2K bus semantics: every participant sees all frames.

BROADCASTS
  ISO Address Claim (PGN 60928)   — on startup + address claim events (library)
  Product Information (PGN 126996) — on startup + on ISO Request (library)
  Heartbeat (PGN 126993)          — every GW_HEARTBEAT_S seconds (library)
  Temperature (PGN 130312)        — CPU temp every GW_TEMP_INTERVAL_S seconds

IDENTITY
  SA preferred:    200
  Manufacturer:    999  (unregistered → shown as "Unknown" in HA/Signal K)
  Device Class:     25  (Internetwork Device)
  Device Function: 130  (PC Gateway)
  Industry Group:    4  (Marine)
  Model ID:        "YDNU-02 TCP-GW"

INTEGRATION
  Called via start_in_thread() from ydnu02_tcp_gateway.py main().
  Requires port 4001 to be a bidirectional hub (client frames forwarded to others).

NOTE on temperature source:
  NMEA 2000 has no "CPU Temperature" type. We use source=2 "Inside Temperature"
  as the closest match for a device/board temperature reading.
"""

import asyncio
import logging
import os
import time
import threading
from datetime import datetime

from nmea2000.device import N2KDevice
from nmea2000.input_formats import N2KFormat
from nmea2000.message import NMEA2000Message, NMEA2000Field
from nmea2000.consts import FieldTypes

logger = logging.getLogger(__name__)

# ── Gateway device identity ───────────────────────────────────────────────────

GW_HOST            = '127.0.0.1'
GW_PORT            = 4001
GW_PREFERRED_SA    = 200
GW_UNIQUE_NUMBER   = 12345       # arbitrary 21-bit unique number
GW_MANUFACTURER    = 717         # Yacht Devices — manufacturer of the YDNU-02 hardware
GW_DEVICE_CLASS    = 25          # Internetwork Device
GW_DEVICE_FUNCTION = 130         # PC Gateway
GW_INDUSTRY_GROUP  = 4           # Marine Industry
GW_MODEL_ID        = 'YDNU-02 TCP-GW'
GW_MODEL_VERSION   = 'yacht-n2k-console'
GW_HEARTBEAT_S     = 10.0        # heartbeat interval (seconds) — managed by library
GW_TEMP_INTERVAL_S = 3.0         # CPU temperature broadcast interval (seconds)

# PGN 130312 temperature source: 2 = "Inside Temperature"
# (closest N2K type for a device/board/CPU temperature)
_TEMP_SOURCE_RAW = 2


def _read_cpu_temp() -> float | None:
    """Read CPU temperature in Celsius from Linux sysfs thermal zone."""
    for zone in ('thermal_zone0', 'thermal_zone1'):
        try:
            with open(f'/sys/class/thermal/{zone}/temp') as f:
                return int(f.read().strip()) / 1000.0   # millidegrees → Celsius
        except (OSError, ValueError):
            continue
    return None


def _read_version() -> str:
    """Read software version from VERSION file in the project root."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, '..', 'VERSION'),
                 os.path.join(here, 'VERSION')):
        try:
            with open(path) as fh:
                return fh.read().strip()
        except OSError:
            pass
    return '0.0.0'


def _make_temp_message(temp_celsius: float, sid: int = 0) -> NMEA2000Message:
    """Build a PGN 130312 (Temperature) NMEA2000Message from a CPU temp in Celsius.

    PGN 130312 is a single CAN frame (no fast-packet). Fields per encode_pgn_130312:
      sid               uint8   — Sequence ID (rolling counter, wraps at 251)
      instance          uint8   — Temperature instance (0 = primary/only)
      source            uint8   — Source type (2 = Inside Temperature)
      actualTemperature uint16  — Kelvin, resolution 0.01 K; pass value in K, raw=None
      setTemperature    uint16  — Set point (None = unavailable / 0xFFFF)
      reserved_56       uint8   — Reserved = 0

    The encoder (encode_pgn_130312) checks:
      - raw_value is int/float → encode_number(raw_value, 16, False, 0.01)
      - raw_value is None, value is float → encode_number(value, 16, False, 0.01)
    Setting raw_value=None and value=temp_k (float) triggers the value-path.
    """
    temp_k = temp_celsius + 273.15
    msg = NMEA2000Message(PGN=130312)
    msg.source    = 0            # N2KDevice.send() substitutes the actual claimed SA
    msg.priority  = 6
    msg.timestamp = datetime.now()
    msg.fields = [
        NMEA2000Field(id='sid',               value=sid,              raw_value=sid),
        NMEA2000Field(id='instance',          value=0,                raw_value=0),
        NMEA2000Field(id='source',            value=_TEMP_SOURCE_RAW, raw_value=_TEMP_SOURCE_RAW),
        NMEA2000Field(id='actualTemperature', value=temp_k,           raw_value=None),
        NMEA2000Field(id='setTemperature',    value=None,             raw_value=None),
        NMEA2000Field(id='reserved_56',       value=0,                raw_value=0),
    ]
    return msg


async def _run_device() -> None:
    """Async main loop: claim N2K address and broadcast telemetry continuously."""
    version = _read_version()

    # N2KDevice connects to our own port 4001 (bidirectional hub) using the same
    # CAN_FRAME_ASCII format that the YDNU-02 gateway outputs. The library handles
    # ISO Claim, Product Info, Heartbeat, and ISO Request responses automatically.
    device = N2KDevice.for_text_gateway(
        GW_HOST,
        GW_PORT,
        format=N2KFormat.CAN_FRAME_ASCII,
        preferred_address=GW_PREFERRED_SA,
        unique_number=GW_UNIQUE_NUMBER,
        manufacturer_code=GW_MANUFACTURER,
        device_class=GW_DEVICE_CLASS,
        device_function=GW_DEVICE_FUNCTION,
        industry_group=GW_INDUSTRY_GROUP,
        model_id=GW_MODEL_ID,
        model_version=GW_MODEL_VERSION,
        software_version_code=version,
        model_serial_code=str(GW_UNIQUE_NUMBER),
        heartbeat_interval=GW_HEARTBEAT_S,
    )

    await device.start()
    logger.warning('[gwdev] N2K device started, claiming address (preferred SA=%d)...',
                   GW_PREFERRED_SA)

    try:
        await device.wait_ready(timeout=15.0)
    except asyncio.TimeoutError:
        logger.warning('[gwdev] Address claim timed out — will retry on reconnect')
        return

    logger.warning('[gwdev] Address claimed: SA=%d  model="%s"  version=%s',
                   device.address, GW_MODEL_ID, version)

    # Broadcast CPU temperature every GW_TEMP_INTERVAL_S seconds.
    # Heartbeat is handled automatically by the library at GW_HEARTBEAT_S intervals.
    sid = 0
    while True:
        await asyncio.sleep(GW_TEMP_INTERVAL_S)
        temp = _read_cpu_temp()
        if temp is not None:
            try:
                msg = _make_temp_message(temp, sid)
                await device.send(msg)
                sid = (sid + 1) % 252
            except Exception as exc:
                logger.warning('[gwdev] Temperature send failed: %s', exc)
        else:
            logger.warning('[gwdev] CPU temp unavailable (non-Linux platform?)')


def start_in_thread() -> threading.Thread:
    """Start the gateway N2K device in a daemon thread with its own asyncio event loop.

    Waits 5 seconds on startup so the gateway TCP server (port 4001) is ready.
    Restarts automatically if the device loop crashes (e.g. gateway restart).
    """
    def _loop() -> None:
        time.sleep(5.0)   # wait for the gateway TCP server to start accepting connections
        while True:
            try:
                asyncio.run(_run_device())
            except Exception as exc:
                logger.warning('[gwdev] Device loop crashed: %s. Restarting in 15s.', exc)
            time.sleep(15.0)

    t = threading.Thread(target=_loop, name='gateway-n2k-device', daemon=True)
    t.start()
    logger.warning('[gwdev] Gateway device thread launched')
    return t
