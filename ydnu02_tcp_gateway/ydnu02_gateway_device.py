#!/usr/bin/env python3
"""
ydnu02_gateway_device.py — Virtual N2K device identity for the YDNU-02 TCP Gateway.
=====================================================================================

PURPOSE
  Registers the Raspberry Pi 5 gateway as a first-class NMEA 2000 device (SA=200)
  on the N2K network so Home Assistant and Signal K can track its liveness,
  CPU temperature, and firmware/software version.

ARCHITECTURE
  This module runs as a daemon thread spawned from ``ydnu02_tcp_gateway.py::main()``.
  It creates an ``nmea2000.device.N2KDevice`` instance that connects back to the
  gateway's own TCP port 4001 (CAN_FRAME_ASCII, bidirectional hub).  Because port
  4001 is a bidirectional hub, frames emitted by N2KDevice are forwarded to all
  other connected TCP clients (HA, Signal K, ydnu02-web) but NOT echoed back to
  the sender or forwarded to serial.

  Data flow::

      N2KDevice (SA=200, port 4001)
            │
            ▼
      ydnu02_tcp_gateway  port 4001  (bidirectional hub)
            │
            ├──►  Home Assistant  (via nmea2000 IOClient)
            ├──►  Signal K        (if connected)
            └──►  ydnu02-web      (monitor tab, device list)

IDENTITY (ISO 11783 NAME)
  +---------------------+--------+-------------------------------------------------+
  | Field               | Value  | Notes                                           |
  +---------------------+--------+-------------------------------------------------+
  | Source Address (SA)  | 200    | Preferred; may change after ISO claim arbitration|
  | Unique Number        | 12345  | Arbitrary 21-bit; unique within manufacturer    |
  | Manufacturer Code    | 717    | Yacht Devices (maker of the YDNU-02 hardware)   |
  | Device Class         | 25     | Internetwork Device                             |
  | Device Function      | 130    | PC Gateway                                      |
  | Industry Group       | 4      | Marine                                          |
  | Model ID             | str    | "YDNU-02 TCP-GW"                                |
  | Model Version        | str    | "yacht-n2k-console"                             |
  | Software Version     | str    | Read from VERSION file at startup                |
  +---------------------+--------+-------------------------------------------------+

BROADCASTS (periodic)
  +--------------------+----------+--------------------------------------------+
  | PGN                | Interval | Description                                |
  +--------------------+----------+--------------------------------------------+
  | 60928 (ISO Claim)  | on start | Address claim — handled by N2KDevice lib   |
  | 126996 (Prod Info) | 60s      | Product Info — startup + periodic rebrcast  |
  | 126993 (Heartbeat) | 10s      | Liveness heartbeat — managed by library    |
  | 130312 (Temp)      | cfg 3s   | CPU temperature from sysfs thermal_zone;   |
  |                    |          | TCP hub interval = settings.n2k_tcp_temp_  |
  |                    |          | interval_s; forwarding to physical serial   |
  |                    |          | is throttled independently by data_hub.py  |
  |                    |          | via settings.n2k_serial_temp_interval_s     |
  +--------------------+----------+--------------------------------------------+

STARTUP SEQUENCE
  1. ``start_in_thread()`` launches a daemon thread that sleeps 5s for port 4001
     to become ready (serial_reader + TCP accept loop must be running).
  2. ``_run_device()`` creates N2KDevice, calls ``device.start()`` → TCP connect.
  3. ``device.wait_ready(timeout=15)`` blocks until ISO Address Claim completes.
  4. Product Info (PGN 126996) is explicitly broadcast once.
  5. Temperature loop broadcasts PGN 130312 every ``settings.n2k_tcp_temp_interval_s``
     seconds (read dynamically from ``GatewaySettings``; default 3.0s).

RECONNECTION
  If ``_run_device()`` raises (TCP disconnect, address claim timeout), the outer
  ``_loop()`` wrapper catches the exception and restarts after 15s. This covers:
  - Gateway restart (port 4001 temporarily unavailable)
  - N2KDevice internal TCP connection drop
  - Address claim collision (unlikely with SA=200, but handled)

TEMPERATURE ENCODING (PGN 130312)
  NMEA 2000 has no "CPU Temperature" source type. We use:
    source=2 ("Inside Temperature") as the closest semantic match.
  Temperature value is converted from Celsius to Kelvin (T_K = T_C + 273.15).
  The nmea2000 library encoder expects ``raw_value=None, value=<float in K>``
  to trigger its ``encode_number(value, 16, False, 0.01)`` path.

THREAD SAFETY
  This module runs entirely in its own daemon thread with its own asyncio event
  loop (``asyncio.run()``). It does NOT share any mutable state with the gateway
  module (no locks needed). Communication is exclusively via the TCP socket to
  port 4001.

TODO:
  - TODO(registration): Manufacturer 717 (Yacht Devices) is used because the
    gateway runs on YD hardware. If this device needs its own identity, register
    a manufacturer code with NMEA or use 999 (unregistered dev/test).
  - TODO(health): Consider broadcasting PGN 127508 (Battery Status) with the
    Raspberry Pi supply voltage from vcgencmd if available.
  - TODO(metrics): Track and expose reconnection count, uptime, and last
    successful temperature broadcast timestamp for monitoring.

ISSUES:
  - ISSUE(private-api): ``device._build_product_information_message()`` uses a
    private method of N2KDevice. If the nmea2000 library changes its internal API,
    this call will break silently. Pin the library version or add a try/except.
    The N2KDevice library handles ISO Request (PGN 59904) automatically via its
    internal receive loop started in connect(). Our periodic broadcast is a
    proactive measure for passive listeners (e.g. HA) that do not send ISO Requests.
  - ISSUE(claim-timeout): If address claim times out (15s), ``_run_device()``
    returns immediately without entering the temperature loop. The 15s restart
    delay means up to 30s of downtime. Consider retrying the claim instead.
  - ISSUE(sid-reset): The SID counter resets to 0 on every reconnect (crash +
    restart). While N2K allows SID discontinuities, rapid restarts could confuse
    consumers that track SID monotonicity within a session.
  - ISSUE(temp-unavailable): On non-Linux platforms (macOS dev), _read_cpu_temp()
    always returns None. The temperature loop logs a warning every 3s indefinitely.
    Consider a backoff or one-time log after initial detection.

KNOWN ISSUES / WORKAROUNDS
==========================

  KI-001 (ha-format-none) — Home Assistant drops all N2K PGNs on restart
  -----------------------------------------------------------------------
  Root cause (two cooperating bugs):

    1. HA ``hub.py`` line ~241 creates ``TextNmea2000Gateway(host, port)`` WITHOUT
       the ``format`` parameter → gateway is in auto-sense mode (``format=None``).
       In auto-sense mode the gateway can DECODE incoming frames (format is detected
       from the first received byte) but cannot ENCODE outgoing frames.

    2. ``AsyncIOClient._seed_network_map()`` runs on every ``connect()`` and tries to
       send three ISO Request frames (PGN 59904) for PGN 60928 / 126996 / 126998.
       It calls ``self.send()`` → ``_encode_impl()`` → raises ``ValueError:
       "Cannot encode: this gateway was created with format=None (auto-sense mode)"``.
       The ISO Requests never leave HA.

    3. ``nmea2000.decoder.py`` has a 10-minute window: if ``build_network_map=True``
       (HA default) and no PGN 60928 has been received for a source address SA, the
       decoder silently returns ``None`` for ALL other PGNs from that SA for the first
       10 minutes after startup (``started_at < now - 10min``). Result: PGN 130312
       (temperature), PGN 126996 (Product Info), etc. are all dropped before the
       ``receive_callback`` is ever called.

  Correct fix (in HA integration):
    Edit ``/config/custom_components/nmea2000/hub.py``, line ~241::

        from nmea2000.ioclient import N2KFormat
        self.gateway = TextNmea2000Gateway(
            host=ip, port=port,
            format=N2KFormat.CAN_FRAME_ASCII,   # ← add this
            **common_kwargs,
        )

  Workaround (implemented here — controlled via ``GatewaySettings``):
    On startup and periodically (default 60s, configurable), re-broadcast:
      • PGN 60928 (ISO Address Claim) for every known SA on the bus
      • PGN 126996 (Product Information) for our own virtual device SA=200
    HA receives these passively, populates ``source_to_iso_name[sa]``, and
    stops dropping PGNs from those source addresses.

    Toggle via web UI → Service tab → «NMEA Gateway Settings».
    API: ``GET/POST /api/gw-settings``

  Skill — check if HA is logging the encode error::

      sudo docker exec homeassistant \
          grep "Cannot encode" /config/home-assistant.log | tail -5

  Skill — check which SA HA has in its iso-name map (via HA logs)::

      sudo docker exec homeassistant \
          grep "No ISO name found" /config/home-assistant.log | tail -20

  Skill — manually trigger ISO replay via API::

      curl -s http://localhost:8080/api/gw-settings | python3 -m json.tool
      curl -X POST http://localhost:8080/api/gw-settings \
           -H 'Content-Type: application/json' \
           -d '{"ha_iso_replay_enabled": true, "ha_iso_replay_interval_s": 30}'

  Skill — disable ISO replay (e.g. after proper hub.py fix is deployed)::

      curl -X POST http://localhost:8080/api/gw-settings \
           -H 'Content-Type: application/json' \
           -d '{"ha_iso_replay_enabled": false}'

  Skill — watch ISO replay events in gateway logs::

      ssh user@localhost \
          'journalctl -u ydnu02-tcp-gateway -f | grep "ISO replay"'

SKILLS (diagnostic mini-prompts)
================================

  Skill — verify virtual device is visible on N2K bus::

      # Check that SA=200 appears in the CAN frame stream:
      ssh user@localhost 'nc -q1 localhost 4001 | grep -m5 "200"'

  Skill — check CPU temperature broadcast from sysfs::

      ssh user@localhost \
          'cat /sys/class/thermal/thermal_zone0/temp'
      # Output: 52300  → 52.3 °C. Divide by 1000.

  Skill — verify Product Info is on the bus::

      # Decode PGN 126996 frames from TCP hub (CAN_FRAME_ASCII format):
      ssh user@localhost \
          'nc -q5 localhost 4001 | grep -m3 "^:126996:"'

  Skill — check N2K address claim of virtual device::

      # PGN 60928 frames contain the ISO NAME; SA=200 → source field = C8 hex:
      ssh user@localhost \
          'nc -q5 localhost 4001 | grep -m3 "^:60928:"'

  Skill — read current GatewaySettings from disk::

      ssh user@localhost \
          'cat ~/.config/ydnu02/gateway_settings.json'

  Skill — watch gateway device daemon logs in real time::

      ssh user@localhost \
          'journalctl -u ydnu02-tcp-gateway -f -n 40'

  Skill — restart gateway daemon to force address re-claim::

      ssh user@localhost \
          'sudo systemctl restart ydnu02-tcp-gateway'

  Skill — check heartbeat interval (PGN 126993) on bus::

      # Heartbeat appears every GW_HEARTBEAT_S (10s); SA=200:
      ssh user@localhost \
          'nc -q15 localhost 4001 | grep "^:126993:" | head -3'
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


from ydnu02_tcp_gateway.gateway_settings import GatewaySettings
from ydnu02_tcp_gateway.data_hub import DataHub

logger = logging.getLogger(__name__)

# Module-level reference to DataHub injected by ydnu02_tcp_gateway.py on startup.
# DataHub.get_physical_devices() returns ISO 60928+126996 data collected from
# every frame that passed through broadcast() — no extra decoder needed.
_data_hub_ref: DataHub | None = None


def set_data_hub(hub: DataHub) -> None:
    """Wire the DataHub instance so _replay_iso_presence() can read physical device data."""
    global _data_hub_ref
    _data_hub_ref = hub

# ── Gateway device identity ───────────────────────────────────────────────────
#
# These constants define the N2K NAME fields used in ISO Address Claim (PGN 60928)
# and Product Information (PGN 126996). They are passed directly to the N2KDevice
# constructor and determine how HA, Signal K, and other chart plotters identify
# the gateway on the N2K network.

GW_HOST            = '127.0.0.1'
"""Loopback address — connects to our own port 4001 bidirectional hub."""

GW_PORT            = 4001
"""TCP port of the gateway data hub. Must match DATA_PORT in ydnu02_tcp_gateway.py."""

GW_PREFERRED_SA    = 200
"""Preferred NMEA 2000 Source Address. High value (200) avoids collision with
physical devices which typically use SA 0–99. If another device claims SA=200,
the N2KDevice library performs ISO address claim arbitration per J1939-81."""

GW_UNIQUE_NUMBER   = 902047
"""21-bit unique number for ISO NAME address claim arbitration (902047 = Virtual TCP Gateway)."""

GW_MANUFACTURER    = 2047
"""Manufacturer code: 2047 = Custom / Experimental (NMEA 2000 reserved for virtual/custom software devices)."""

GW_PRODUCT_CODE    = 200
"""Product code assigned to YDNU-02 TCP Gateway (200)."""

GW_DEVICE_CLASS    = 25
"""N2K device class: 25 = Internetwork Device (ISO 11783-5 Table B.4)."""

GW_DEVICE_FUNCTION = 130
"""N2K device function: 130 = PC Gateway (ISO 11783-5 Table B.5.25)."""

GW_INDUSTRY_GROUP  = 4
"""Industry group: 4 = Marine Industry."""

GW_MODEL_ID        = 'YDNU-02 TCP-GW'
"""Model ID string broadcast in PGN 126996 Product Information (max 16 chars)."""

GW_MODEL_VERSION   = 'yacht-n2k-console @dnevera'
"""Model version string broadcast in PGN 126996 (max 32 chars)."""

GW_HEARTBEAT_S     = 10.0
"""Heartbeat interval in seconds. The N2KDevice library automatically broadcasts
PGN 126993 at this interval. HA uses heartbeat absence to detect device offline."""

GW_TEMP_INTERVAL_S = 3.0
"""Legacy fallback tick length (seconds) for the main daemon loop.

The actual CPU temperature (PGN 130312) broadcast rate is now configurable
via ``GatewaySettings.n2k_tcp_temp_interval_s`` (default 3.0s, read dynamically
on every loop iteration) — see ``_run_device()``. Physical serial forwarding of
the same PGN is throttled independently via ``n2k_serial_temp_interval_s`` in
``data_hub.py``. This constant only bounds the loop's sleep granularity."""

GW_PRODUCT_INFO_INTERVAL_S = 60.0
"""Interval for periodic re-broadcast of Product Information (PGN 126996) in seconds.

HA and other passive listeners do not send ISO Request (PGN 59904) on reconnect
(KI-001: TextNmea2000Gateway created with format=None → cannot encode ISO Requests).
We proactively re-broadcast PGN 60928 + PGN 126996 so HA populates
``source_to_iso_name`` and stops dropping PGNs from our SA.

This value is the DEFAULT. At runtime the value from ``GatewaySettings`` takes precedence
and can be changed without restarting the daemon via the web UI or API.

Skill — change interval at runtime without daemon restart::

    curl -X POST http://localhost:8080/api/gw-settings \\
         -H 'Content-Type: application/json' \\
         -d '{"ha_iso_replay_enabled": true, "ha_iso_replay_interval_s": 30}'

Skill — verify current runtime value::

    curl -s http://localhost:8080/api/gw-settings | python3 -m json.tool
"""

# PGN 130312 temperature source: 2 = "Inside Temperature"
# (closest N2K type for a device/board/CPU temperature)
_TEMP_SOURCE_RAW = 2


def _read_cpu_temp() -> float | None:
    """Read CPU temperature in Celsius from Linux sysfs thermal zone.

    Scans ``/sys/class/thermal/thermal_zone{0,1}/temp`` for a valid millidegree
    reading. Returns the first successful read converted to Celsius.

    Platform behavior:
      - Linux (Raspberry Pi 5): thermal_zone0 = CPU package temperature.
      - macOS / Windows: always returns None (sysfs does not exist).

    Returns:
        CPU temperature in degrees Celsius, or None if unavailable.

    Skill — check CPU temperature from SSH::

        ssh user@gateway-host cat /sys/class/thermal/thermal_zone0/temp
        # Output: 52300  (= 52.3°C)

    Skill — monitor CPU temp in real time::

        ssh user@gateway-host 'watch -n1 cat /sys/class/thermal/thermal_zone0/temp'

    Skill — compare sysfs temp with N2K broadcast::

        # Read from sysfs:
        ssh user@gateway-host cat /sys/class/thermal/thermal_zone0/temp
        # Read from N2K bus (PGN 130312, SA=200):
        timeout 5 nc <gateway-host> 4001 | grep -m1 '09FF04C8'
        # The N2K value is in Kelvin × 100 (e.g. 52.3°C = 32545 raw)

    Skill — use vcgencmd as alternative temp source::

        ssh user@gateway-host vcgencmd measure_temp
        # Output: temp=52.3'C

    ISSUE(single-read): Only reads the first available zone. On multi-zone
      systems, thermal_zone0 may not be the CPU. Consider reading all zones
      and returning the maximum, or reading the specific bcm2712 zone.
    """
    for zone in ('thermal_zone0', 'thermal_zone1'):
        try:
            with open(f'/sys/class/thermal/{zone}/temp') as f:
                return int(f.read().strip()) / 1000.0   # millidegrees → Celsius
        except (OSError, ValueError):
            continue
    return None


def _read_version() -> str:
    """Read software version from VERSION file in the project root.

    Search order:
      1. ``../VERSION``  (expected when running from ydnu02_tcp_gateway/ subdir)
      2. ``./VERSION``   (fallback if running from project root)

    Returns:
        Version string (e.g. "1.2.3"), or "0.0.0" if the file is not found.

    Skill — check current deployed version::

        ssh user@gateway-host cat /opt/nmea2000/ydnu02-web/VERSION
        # Output: 1.2.3

    Skill — verify version matches N2K Product Info broadcast::

        # The version string is embedded in PGN 126996 Product Information.
        # Check via the ydnu02-web API:
        curl -s http://<gateway-host>:8080/api/devices | python3 -c "
        import json, sys
        for d in json.load(sys.stdin):
            if d.get('source_address') == 200:
                print(f'SA=200: sw_version={d.get(\"software_version\")}')
        "

    TODO(version-source): Consider reading from ``pyproject.toml`` or git tag
      as a fallback for development environments where VERSION may not exist.
    """
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

    PGN 130312 is a single CAN frame (8 bytes, no fast-packet). Fields:

    +-------------------+-------+-------+----------------------------------------------+
    | Field             | Type  | Bytes | Description                                  |
    +-------------------+-------+-------+----------------------------------------------+
    | sid               | uint8 | 1     | Sequence ID (rolling 0-251, wraps at 252)    |
    | instance          | uint8 | 1     | Temperature instance (0 = primary/only)      |
    | source            | uint8 | 1     | Source type (2 = Inside Temperature)         |
    | actualTemperature | uint16| 2     | Kelvin × 100 (resolution 0.01 K)             |
    | setTemperature    | uint16| 2     | Set point in K (None = N/A → 0xFFFF)         |
    | reserved_56       | uint8 | 1     | Reserved = 0                                 |
    +-------------------+-------+-------+----------------------------------------------+

    Encoding path in the nmea2000 library (``encode_pgn_130312``):
      - If ``raw_value`` is int/float → ``encode_number(raw_value, 16, False, 0.01)``
      - If ``raw_value`` is None and ``value`` is float → same encoder with ``value``
    We set ``raw_value=None, value=temp_k`` to trigger the value-path encoder.

    Args:
        temp_celsius: CPU temperature in degrees Celsius (e.g. 52.3).
        sid: Sequence ID, rolling counter 0-251. Wraps at 252 per NMEA 2000 spec.

    Returns:
        Fully constructed NMEA2000Message ready for ``device.send()``.
        The ``source`` field is set to 0; N2KDevice.send() substitutes the
        claimed SA before transmission.

    Skill — build and inspect a temperature message::

        >>> msg = _make_temp_message(52.3, sid=5)
        >>> msg.PGN
        130312
        >>> msg.fields[0].value  # sid
        5
        >>> msg.fields[3].value  # actualTemperature in Kelvin
        325.45

    Skill — manually decode a PGN 130312 from raw CAN data::

        # Raw frame from nc: "00:00:00.000 R 09FF04C8 05 00 02 91 7E FF FF 00"
        # Data bytes: 05 00 02 91 7E FF FF 00
        #   sid=0x05, instance=0x00, source=0x02
        #   actualTemp = 0x7E91 = 32401 * 0.01 = 324.01 K = 50.86°C
        python3 -c "print(f'{0x7E91 * 0.01 - 273.15:.1f}°C')"  # → 50.9°C

    Skill — monitor gateway CPU temp via the ydnu02-web API::

        # The web UI exposes device data including temperature:
        curl -s http://<gateway-host>:8080/api/devices | python3 -c "
        import json, sys
        for d in json.load(sys.stdin):
            if d.get('source_address') == 200:
                temps = d.get('temperatures', [])
                for t in temps:
                    print(f'CPU: {t[\"value\"]:5.1f}°C  (source={t[\"source\"]})')
        "

    ISSUE(precision): Converting float Celsius → float Kelvin → uint16 encoding
      introduces floating-point rounding. For 52.3°C: 325.45K → raw 32545.
      Maximum error is ±0.005K (0.005°C), which is acceptable for CPU monitoring.
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
    """Async main loop: claim N2K address and broadcast telemetry continuously.

    Lifecycle:
      1. Create N2KDevice with for_text_gateway() factory (connects to port 4001).
      2. Start the device (opens TCP connection, begins ISO address claim).
      3. Wait for address claim to complete (up to 15s timeout).
      4. Broadcast Product Information (PGN 126996) once on startup.
      5. Enter infinite loop broadcasting CPU temperature every
         ``settings.n2k_tcp_temp_interval_s`` seconds (read dynamically).

    On any exception (TCP disconnect, encoding error), this function either
    raises (caught by the outer _loop() wrapper) or logs and continues.

    The N2KDevice library automatically handles:
      - ISO Address Claim (PGN 60928) on startup and address conflicts
      - ISO Request responses (PGN 59904 → replies with 60928 or 126996)
      - Heartbeat (PGN 126993) at GW_HEARTBEAT_S interval

    Skill — verify the gateway device is online and claimed::

        # Check via the web API:
        curl -s http://<gateway-host>:8080/api/devices | python3 -c "
        import json, sys
        for d in json.load(sys.stdin):
            if d.get('source_address') == 200:
                print(f'Gateway: SA={d[\"source_address\"]}, model={d.get(\"model_id\")}')
                print(f'Version: {d.get(\"software_version\")}')
        "

    Skill — watch heartbeat and temperature frames from SA=200::

        # Filter gateway frames from live TCP stream:
        nc <gateway-host> 4001 | grep 'C8'  # SA=200 = 0xC8
        # ISO Claim:   18EEFFC8 ...
        # Heartbeat:   19F11100C8 ...  (PGN 126993)
        # Temperature: 09FF04C8 ...    (PGN 130312)

    Skill — decode temperature from a raw frame::

        # Frame: 09FF04C8 05 00 02 91 7E FF FF 00
        python3 -c "
        data = bytes.fromhex('05000291 7EFFFF00'.replace(' ', ''))
        sid = data[0]
        source = data[2]
        temp_raw = int.from_bytes(data[3:5], 'little')
        temp_c = temp_raw * 0.01 - 273.15
        print(f'SID={sid} source={source} temp={temp_c:.1f}°C')
        "

    Skill — check gateway logs for claim and reconnect events::

        ssh user@gateway-host journalctl -u ydnu02-tcp-gateway -f --no-pager | grep gwdev
        # [gwdev] N2K device started, claiming address (preferred SA=200)...
        # [gwdev] Address claimed: SA=200  model="YDNU-02 TCP-GW"  version=1.2.3
        # [gwdev] Device loop crashed: ... Restarting in 15s.

    KI-001 (ha-format-none) workaround — ISO Replay:
      HA creates TextNmea2000Gateway(format=None) → cannot send ISO Requests →
      decoder.py drops all PGNs from unknown SA for 10 min. Workaround: this
      loop re-broadcasts PGN 60928 (ISO Claim) for cached physical devices and
      PGN 126996 (Product Info) for our own SA at GW_PRODUCT_INFO_INTERVAL_S.
      Controlled by GatewaySettings.ha_iso_replay_enabled (web UI toggle).
      See module-level KNOWN ISSUES for full root-cause analysis.

    ISSUE(claim-failure): If wait_ready() times out, we return immediately.
      The outer loop restarts after 15s, but there's no incremental backoff.
      Repeated claim failures could spam logs.

    TODO(graceful-shutdown): No cleanup is performed on exit. Consider calling
      device.stop() or sending a "Cannot Claim" (PGN 60928, SA=254) on shutdown.
    """
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
        product_code=GW_PRODUCT_CODE,
        device_class=GW_DEVICE_CLASS,
        device_function=GW_DEVICE_FUNCTION,
        industry_group=GW_INDUSTRY_GROUP,
        model_id=GW_MODEL_ID,
        model_version=GW_MODEL_VERSION,
        software_version_code=version,
        model_serial_code=f"SW-GW-{GW_UNIQUE_NUMBER:08d}",
        heartbeat_interval=GW_HEARTBEAT_S,
        persistence_path=os.path.expanduser('~/.config/ydnu02/n2k_gateway_device.json'),
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

    # Register virtual gateway device info in DataHub.device_registry
    if _data_hub_ref is not None:
        from ydnu02_tcp_gateway.device_contract import N2KDeviceInfo
        _data_hub_ref.device_registry.register_device(N2KDeviceInfo(
            sa=device.address,
            unique_id=GW_UNIQUE_NUMBER,
            mfg_code=GW_MANUFACTURER,
            device_class=GW_DEVICE_CLASS,
            device_function=GW_DEVICE_FUNCTION,
            industry_group=GW_INDUSTRY_GROUP,
            model_id=GW_MODEL_ID,
            model_version=GW_MODEL_VERSION,
            software_version=version,
            model_serial=f"SW-GW-{GW_UNIQUE_NUMBER:08d}",
        ))

    async def _replay_iso_presence() -> None:
        """Broadcast own presence and prompt all physical devices via ISO Request (PGN 59904).

        KI-001 workaround:
          1. Broadcast OUR virtual device PGN 60928 (Address Claim) so HA populates
             source_to_iso_name[200].
          2. Broadcast OUR virtual device PGN 126996 (Product Info).
          3. Send PGN 59904 (ISO Request) for PGN 60928 to Destination=255 (Broadcast).
             This prompts all physical devices on the bus (like YDNU-02) to re-claim
             their address and send their PGN 60928.
          4. Send PGN 59904 (ISO Request) for PGN 126996 to Destination=255 (Broadcast).
             This prompts physical devices to announce their Product Info.

          HA receives all these physical responses passively and populates its network map.
        """
        # 1. Our virtual device — PGN 60928 (Address Claim)
        try:
            claim_msg = device._build_address_claim_message()
            claim_msg.source = device.address
            await device.send(claim_msg)
            logger.warning('[gwdev] ISO replay: broadcast PGN 60928 for virtual SA=%d', device.address)
        except Exception as exc:
            logger.warning('[gwdev] ISO replay: failed PGN 60928 (own): %s', exc)

        await asyncio.sleep(0.1)

        # 3. Canonical N2K Request: Prompt all physical devices on bus to announce PGN 60928
        try:
            req_claim = device._build_iso_request_message(60928, destination=255)
            req_claim.source = device.address
            await device.send(req_claim)
            logger.warning('[gwdev] ISO Request: prompted bus for PGN 60928 (ISO Claim)')
        except Exception as exc:
            logger.warning('[gwdev] ISO Request for PGN 60928 failed: %s', exc)

        # 4. Canonical N2K Request: Prompt all physical devices on bus to announce PGN 126996
        try:
            req_prod = device._build_iso_request_message(126996, destination=255)
            req_prod.source = device.address
            await device.send(req_prod)
            logger.warning('[gwdev] ISO Request: prompted bus for PGN 126996 (Product Info)')
        except Exception as exc:
            logger.warning('[gwdev] ISO Request for PGN 126996 failed: %s', exc)

        # 5. Broadcast announcements for all registered devices (virtual + physical)
        if _data_hub_ref is not None:
            _data_hub_ref.announce_all_devices()
            logger.warning('[gwdev] Re-broadcast presence for all registered N2K devices')

    # Startup replay — run once after address claim, before entering the main loop.
    await asyncio.sleep(1.0)
    await _replay_iso_presence()


    sid = 0
    _last_replay_t = time.monotonic()  # track last ISO replay broadcast time
    _last_tcp_temp_t = time.monotonic()  # track last CPU temp broadcast time (TCP hub)
    while True:
        # Tick at a small, fixed rate so both the ISO replay timer and the
        # CPU-temp broadcast timer (now configurable via GatewaySettings)
        # can be evaluated independently and responsively.
        await asyncio.sleep(min(1.0, GW_TEMP_INTERVAL_S))

        # Periodic ISO replay (KI-001 workaround) — controlled by GatewaySettings.
        # Settings are read dynamically so changes via web UI take effect within
        # one tick (~1s) without restarting the daemon.
        settings = GatewaySettings.instance()
        now = time.monotonic()
        replay_interval = settings.ha_iso_replay_interval_s
        if settings.ha_iso_replay_enabled and (now - _last_replay_t >= replay_interval):
            _last_replay_t = now
            await _replay_iso_presence()

        # CPU temperature broadcast (PGN 130312) to the TCP hub — throttled by
        # settings.n2k_tcp_temp_interval_s (default 3.0s), read dynamically so
        # changes via the web UI take effect without restarting the daemon.
        # Physical serial forwarding of this same PGN is throttled independently
        # by data_hub.py using settings.n2k_serial_temp_interval_s.
        tcp_temp_interval = settings.n2k_tcp_temp_interval_s
        if now - _last_tcp_temp_t >= tcp_temp_interval:
            _last_tcp_temp_t = now
            temp = _read_cpu_temp()
            if temp is not None:
                try:
                    msg = _make_temp_message(temp, sid)
                    await device.send(msg)
                    sid = (sid + 1) % 252
                except Exception as exc:
                    logger.warning('[gwdev] Temperature send failed: %s', exc)
            else:
                # ISSUE(log-spam): On non-Linux, this fires every tcp_temp_interval.
                # TODO: Detect once and suppress further warnings.
                logger.warning('[gwdev] CPU temp unavailable (non-Linux platform?)')


def start_in_thread() -> threading.Thread:
    """Start the gateway N2K device in a daemon thread with its own asyncio event loop.

    Startup delay: 5 seconds — ensures the gateway TCP server (port 4001) is
    accepting connections before N2KDevice attempts to connect. Without this
    delay, the first connection attempt would fail with ECONNREFUSED.

    Restart strategy: infinite loop with 15s delay between restarts. Covers:
      - TCP disconnect (gateway restart, port 4001 temporarily down)
      - Address claim timeout (another device contesting SA=200)
      - Unhandled exceptions in N2KDevice or asyncio internals

    Thread properties:
      - Name: "gateway-n2k-device" (visible in thread dumps and logs)
      - Daemon: True (does not prevent process exit)

    Returns:
        The started threading.Thread instance (for testing/inspection).

    Skill — verify the device thread is running::

        # Check thread list via /proc (on Raspberry Pi):
        ssh user@gateway-host ps -eLf | grep ydnu02_tcp_gateway

        # Or from within Python:
        import threading
        print([t.name for t in threading.enumerate()])
        # Expected: [..., 'gateway-n2k-device', ...]

    Skill — test N2KDevice connectivity in isolation::

        # Run _run_device() standalone (port 4001 must be available):
        python3 -c "
        import asyncio
        from ydnu02_gateway_device import _run_device
        asyncio.run(_run_device())
        "

    Skill — monitor reconnection behavior::

        # Stop and restart the gateway, watch device thread recover:
        ssh user@gateway-host journalctl -u ydnu02-tcp-gateway -f | grep gwdev
        # [gwdev] Device loop crashed: ... Restarting in 15s.
        # [gwdev] N2K device started, claiming address (preferred SA=200)...
        # [gwdev] Address claimed: SA=200

    TODO(startup-readiness): Instead of a fixed 5s sleep, consider polling
      port 4001 with exponential backoff to start faster when the gateway
      is already running.

    ISSUE(no-shutdown): There is no mechanism to cleanly stop this thread.
      On process exit, the daemon thread is killed mid-operation. Consider
      adding a threading.Event for graceful shutdown and N2KDevice cleanup.
    """
    def _loop() -> None:
        """Inner restart loop: run _run_device() forever with crash recovery.

        Each iteration:
          1. Run the async device loop until it exits (crash or timeout).
          2. Log the failure reason.
          3. Sleep 15s before retrying.

        ISSUE(backoff): Fixed 15s restart delay. If port 4001 is down for
          extended periods, this wastes resources with repeated failures.
          Consider exponential backoff with a cap (e.g. 15s → 30s → 60s → max 5m).
        """
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
