#!/usr/bin/env python3
"""
ydnu02_tcp_gateway.py — NMEA 2000 TCP Gateway for YDNU-02
==========================================================

OVERVIEW
  This is the central multiplexer between the YDNU-02 USB-to-N2K adapter
  (/dev/ttyACM0) and all software consumers on the Raspberry Pi 5. It is
  the ONLY process that opens the physical serial port. All other services
  communicate via TCP.

DEPLOYMENT
  File:    /opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py    (<gateway-host>)
  Service: ydnu02-tcp-gateway.service  (starts BEFORE ydnu02-web.service)
  Deploy:  ./deploy.sh  or  ./deploy.sh user@gateway-host --proxy

KEY INVARIANT — only this process ever opens /dev/ttyACM0.
  All other services (ydnu02-web, Home Assistant, Signal K) connect via TCP.
  HA connects to :4001. ydnu02-web connects to :4001 (data) + :4002 (ctrl).

ARCHITECTURE
  ::

      /dev/ttyACM0  (YDNU-02, 115200 baud, CAN_FRAME_ASCII)
            │
      serial_reader thread  (owns serial port)
            │
            ▼
      ┌─ port :4001 ─ DATA ─ Bidirectional TCP Hub ─┐
      │                                              │
      │  Serial→TCP:  broadcast to all TCP clients   │
      │  TCP→TCP:     forward to all OTHER clients   │
      │  TCP→Serial:  ISO Requests ONLY (PGN 59904)  │
      │                                              │
      ├── Home Assistant  (nmea2000 IOClient)         │
      ├── Signal K        (optional)                  │
      ├── ydnu02-web      (monitor tab)               │
      └── N2KDevice SA=200 (virtual gateway device)   │
                                                      │
      ┌─ port :4002 ─ CTRL ─ Exclusive Passthrough ──┐
      │                                              │
      │  Single client at a time (ydnu02-web admin)  │
      │  SERVICE_START → DTR toggle → service mode   │
      │  FIRMWARE_START → raw passthrough (no switch) │
      └──────────────────────────────────────────────┘

PORTS
  :4001  DATA  — Serial→TCP broadcast of NMEA 2000 ASCII frames. Multiple clients.
  :4002  CTRL  — Exclusive passthrough session for service terminal / firmware flash.

FRAME FORMATS
  RX format (from YDNU-02):   "HH:MM:SS.mmm R XXXXXXXX XX XX ...\\n"
  TX format (to YDNU-02):     "HH:MM:SS.mmm T XXXXXXXX XX XX ...\\r\\n"
  Hub format (virtual N2K):   "XXXXXXXX XX XX ...\\r\\n"  (no timestamp)

  The gateway normalizes all frames to RX format before broadcasting.
  TX-format frames from virtual N2KDevice clients are converted to RX format
  via _fmt_frame() before entering the cache and broadcast pipeline.

CTRL PROTOCOL (line-oriented UTF-8)
  → SERVICE_START   proxy does: serial.close() → stty hupcl → echo → serial.open()
  ← READY           YDNU-02 is now in service terminal mode
  → <cmd>\\r\\n       forwarded verbatim to serial
  ← <response>      pushed to client on each 100ms poll
  → SERVICE_END     proxy does: serial.write("MODE RAW\\r\\n") → reset timeout
  ← OK

CRITICAL DESIGN DECISION — DTR toggle required for service mode entry
  serial.write("YDNU MODE SERVICE") is SILENTLY IGNORED while port is held open.
  YDNU-02 only processes the command when it sees a DTR low→high transition,
  which only happens when the port is CLOSED and then REOPENED (or via OS echo).
  Therefore: _enter_service_mode_on_device() closes the port, uses subprocess echo,
  then reopens. The ctrl client (ProxyControlClient / ydnu02.py) does NOT send
  "YDNU MODE SERVICE" — the gateway handles this entirely internally.
  See also: ydnu02.py::enter_service_mode() — passthrough path reads welcome only.

FIRMWARE_START vs SERVICE_START
  SERVICE_START: full DTR toggle mode switch (YDNU-02 → service terminal)
  FIRMWARE_START: raw passthrough only, no mode switch (used for firmware flash)

THREAD MODEL
  ::

      Main thread        → _accept_loop(data_srv)  — accepts DATA clients
      serial_reader      → owns serial port, broadcasts to DATA clients
      ctrl_accept        → _accept_loop(ctrl_srv)  — accepts CTRL clients
      handle_data_client → one per DATA client (bidirectional hub)
      handle_ctrl_client → one CTRL client at a time (service/firmware mode)
      gateway-n2k-device → N2KDevice async loop (ydnu02_gateway_device.py)

  Lock hierarchy (always acquire in this order to prevent deadlocks):
    1. serial_lock
    2. clients_lock
    3. _device_frame_lock
    4. _fp_lock
    5. _iso_request_lock
    6. service_conn_lock

ACTIVE ONBOARDING (NO CACHING ARCHITECTURE)
  We do NOT use a passive frame cache for device discovery.
  Instead, on every new TCP client connection:
    1. Virtual Identity: N2KDevice (SA=200) broadcasts its PGN 60928 + PGN 126996.
    2. Physical Bus Prompt: The gateway sends PGN 59904 (ISO Request) to the serial bus.
       Physical devices (like YDNU-02) reply with their own authentic PGN 60928/126996.

  This guarantees Zero Stale Data and eliminates entity duplication in Home Assistant.

SKILLS (DIAGNOSTIC MINI-PROMPTS)
================================
  Skill — monitor raw frame traffic on TCP port 4001::

      ssh user@localhost 'nc localhost 4001 | head -n 30'

  Skill — inspect gateway systemd logs::

      ssh user@localhost 'journalctl -u ydnu02-tcp-gateway -n 50 --no-pager'

  Skill — test client connection and ISO Request trigger::

      python3 -c "
      import socket
      s = socket.create_connection(('localhost', 4001))
      print('Connected to 4001')
      s.close()
      "

GATEWAY VIRTUAL IDENTITY
  The gateway registers itself as a virtual N2K device (SA=200) so Home Assistant
  and Signal K can track its liveness.  All identity and telemetry is handled by
  ydnu02_gateway_device.py which uses the nmea2000 Python library's N2KDevice,
  connecting back to our own port 4001 (CAN_FRAME_ASCII, bidirectional hub).

  Port 4001 is a bidirectional N2K bus hub:
    serial reader → broadcast to all TCP clients
    TCP client frame → broadcast to all OTHER TCP clients (not to serial)
  This lets the N2KDevice's frames (ISO Claim, Product Info, HB, Temp) reach HA.

ISO REQUEST MECHANISM
  On each new TCP client connect, the gateway sends ISO Requests (PGN 59904)
  to the physical N2K bus via serial, requesting:
    - PGN 60928  (ISO Address Claim) from all devices (destination 0xFE = global)
    - PGN 126996 (Product Information) from all devices
  This triggers physical devices to re-announce, populating the cache for
  future client connections. Rate-limited to 1 request per 5 seconds.

  ISO Requests are also broadcast to TCP clients so virtual devices (SA=200)
  receive them and respond via the bidirectional hub.

TODO:
  - TODO(ipv6): TCP servers bind to "" (all interfaces). Consider IPv6 support
    or restricting to localhost for security on untrusted networks.
  - TODO(max-clients): No limit on simultaneous DATA clients. A flood of
    connections could exhaust file descriptors. Add a configurable max.
  - TODO(health-endpoint): Add a simple HTTP health check endpoint or a
    TCP "PING" command on CTRL port for monitoring integration.
  - TODO(metrics): Track total frames processed, cache hits/misses,
    client connect/disconnect counts, serial reconnections.
  - TODO(cache-expiry): Device frame cache entries never expire. If a device
    is permanently removed from the bus, its stale cache entry persists.
    Consider TTL-based expiration (e.g. no heartbeat for 60s → evict).

ISSUES:
  - ISSUE(serial-contention): During service mode, serial_reader busy-waits
    with time.sleep(0.05) polling. This is 20 wakeups/sec doing no useful work.
    Consider using service_mode.wait() with a timeout instead.
  - ISSUE(broadcast-blocking): _broadcast() iterates all clients with sendall()
    under clients_lock. A slow/stalled client blocks broadcast to all others.
    Consider per-client send queues with async drain.
  - ISSUE(no-client-auth): Any TCP client can connect to :4001 or :4002 with
    no authentication. On a boat network this is usually acceptable, but
    :4002 (CTRL) allows firmware flashing — consider IP-based ACL.
  - ISSUE(init-race): serial_reader sends "YDNU MODE RAW" + "0\\n" but doesn't
    verify the device responded correctly. If the YDNU-02 is in an unexpected
    state, the initialization may silently fail.
  - ISSUE(frame-ordering): Frames from serial and virtual devices arrive on
    different threads. No global ordering guarantee exists, though N2K does
    not require strict ordering for most PGNs.
"""
import os
import socket
import serial
import sys
import threading
import time

from ydnu02_tcp_gateway.frame_utils import (
    NMEA_LINE_RE as _NMEA_LINE_RE,
    TX_LINE_RE as _TX_LINE_RE,
    fmt_frame as _fmt_frame,
    get_pgn_sa as _get_pgn_sa,
)
from ydnu02_tcp_gateway.data_hub import DataHub
from ydnu02_tcp_gateway.ctrl_handler import CtrlHandler
from ydnu02_tcp_gateway.serial_reader import SerialReader

# ── Configuration (env vars) ──────────────────────────────────────────────────
#
# All settings are configurable via environment variables with sensible defaults.
# Override in ydnu02-tcp-gateway.service [Service] Environment= directives.

SERIAL_PORT = os.getenv("NMEA_SERIAL_PORT", "/dev/ttyACM0")
"""Path to the YDNU-02 serial device. Default: /dev/ttyACM0 (USB CDC ACM)."""

SERIAL_BAUD = int(os.getenv("NMEA_SERIAL_BAUD", "115200"))
"""Serial baud rate. YDNU-02 operates at 115200 baud in both RAW and service modes."""

TCP_HOST    = os.getenv("NMEA_PROXY_HOST", "")
"""TCP bind address. Empty string = bind to all interfaces (0.0.0.0).
TODO(security): Consider defaulting to '127.0.0.1' for localhost-only access."""

DATA_PORT   = int(os.getenv("NMEA_PROXY_PORT", "4001"))
"""TCP port for the DATA hub (bidirectional N2K frame bus)."""

CTRL_PORT   = int(os.getenv("NMEA_CTRL_PORT",  "4002"))
"""TCP port for the CTRL channel (exclusive service/firmware mode)."""

# ── Shared state ──────────────────────────────────────────────────────────────
#
# Module-level mutable state shared between threads.
# All access MUST be guarded by the corresponding lock.
#
# ISSUE(module-globals): Using module-level globals makes unit testing difficult
# (tests must patch module attributes). Consider refactoring into a Gateway class
# in the future.

# Data clients (port DATA_PORT)
clients: set = set()
"""Set of connected DATA client sockets. Protected by clients_lock."""
clients_lock = threading.Lock()
"""Guards the clients set. Acquired during broadcast and client connect/disconnect."""

# Serial instance — owned by serial_reader, temporarily replaced by ctrl handler
# during service mode (close for mode switch → reopen in service terminal mode).
# Always access under serial_lock. serial_reader adopts changes via its sleep loop.
serial_instance: serial.Serial | None = None
"""The active pyserial Serial object. None when disconnected or during mode switch.
INVARIANT: Only serial_reader creates new Serial instances (except during
_enter_service_mode_on_device which reopens after DTR toggle)."""
serial_lock = threading.Lock()
"""Guards serial_instance reads and writes. Must be held for any serial access."""

# Service/firmware mode flag — set while control client holds exclusive serial
service_mode = threading.Event()
"""Set when a CTRL client has exclusive serial access (SERVICE_START or FIRMWARE_START).
While set, serial_reader enters a sleep loop and does not read from serial.
DATA clients still receive frames from virtual N2K devices (TCP→TCP path)."""
service_conn: socket.socket | None = None
"""Active CTRL client socket. None when no control session is active."""
service_conn_lock = threading.Lock()
"""Guards service_conn assignment and service_mode transitions."""

# Signals that YDNU-02 has finished its init sequence and is ready for N2K frames.
# ISO Requests must NOT be sent before this is set — YDNU-02 will ignore them.
_serial_ready = threading.Event()
"""Set after serial_reader successfully initializes the YDNU-02 in RAW mode.
Cleared on serial disconnect/error. Used by _send_iso_request() to gate
ISO Request transmission until the device is ready."""






# ── Active Onboarding Data Hub (No Caching) ──────────────────────────────────
#
# DataHub handles frame broadcast to all TCP clients and triggers ISO Requests
# on new client connection. Passive frame caching is omitted in favor of active
# ISO Request prompts to prevent entity duplication in Home Assistant.


_data_hub = DataHub(
    get_serial_instance=lambda: serial_instance,
    get_serial_ready=lambda: _serial_ready.is_set(),
    get_service_mode=lambda: service_mode.is_set(),
    serial_lock=serial_lock,
    get_clients=lambda: clients,
    clients_lock=clients_lock,
)

_iso_request_lock = _data_hub._iso_request_lock


def _broadcast(line: bytes, exclude: socket.socket | None = None) -> None:
    """Thin wrapper delegating to DataHub.broadcast."""
    _data_hub.broadcast(line, exclude=exclude)


def _send_iso_request() -> None:
    """Thin wrapper delegating to DataHub.send_iso_request."""
    _data_hub.send_iso_request()


def handle_data_client(conn: socket.socket, addr) -> None:
    """Thin wrapper delegating to DataHub.handle_client."""
    _data_hub.handle_client(conn, addr)


def _set_serial_instance(ser):
    """Set the module-level serial_instance variable."""
    global serial_instance
    serial_instance = ser


_ctrl_handler = CtrlHandler(
    service_mode=service_mode,
    get_serial_instance=lambda: serial_instance,
    set_serial_instance=_set_serial_instance,
    serial_lock=serial_lock,
    serial_port=SERIAL_PORT,
    serial_baud=SERIAL_BAUD,
)


def handle_ctrl_client(conn: socket.socket, addr) -> None:
    """Thin wrapper delegating to CtrlHandler.handle_client."""
    _ctrl_handler.handle_client(conn, addr)


_serial_reader_worker = SerialReader(
    serial_port=SERIAL_PORT,
    serial_baud=SERIAL_BAUD,
    get_serial_instance=lambda: serial_instance,
    set_serial_instance=_set_serial_instance,
    serial_lock=serial_lock,
    serial_ready=_serial_ready,
    service_mode=service_mode,
    broadcast=_broadcast,
    send_iso_request=_send_iso_request,
)


def serial_reader() -> None:
    """Thin wrapper delegating to SerialReader.run."""
    _serial_reader_worker.run()


# ── TCP servers ───────────────────────────────────────────────────────────────

from ydnu02_tcp_gateway.gateway import make_server as _make_server, accept_loop as _accept_loop



def main() -> None:
    """Gateway entry point: start all subsystems and enter the main accept loop.

    Startup order (critical for dependencies):
      1. serial_reader thread — opens /dev/ttyACM0, begins reading N2K frames.
      2. Gateway N2K device thread — waits 5s, then connects to port 4001.
         Depends on: DATA server must be accepting connections.
      3. DATA server (port 4001) — accepts HA, Signal K, ydnu02-web, N2KDevice.
      4. CTRL server (port 4002) — accepts ydnu02-web admin interface.
      5. Main thread enters DATA accept loop (blocks here until shutdown).

    Shutdown:
      KeyboardInterrupt (Ctrl+C) in the main accept loop triggers sys.exit(0).
      All daemon threads are terminated by the Python interpreter on exit.

    Skill — start the gateway manually for development::

        # On the Raspberry Pi (with YDNU-02 connected):
        cd /opt/nmea2000/ydnu02-web
        python3 -m ydnu02_tcp_gateway.ydnu02_tcp_gateway

        # With custom ports (e.g. to avoid conflict with production):
        NMEA_PROXY_PORT=4011 NMEA_CTRL_PORT=4012 python3 -m ydnu02_tcp_gateway.ydnu02_tcp_gateway

    Skill — deploy and restart via deploy.sh::

        # From the dev machine (macOS):
        ./deploy.sh user@gateway-host           # full deploy (proxy + web + HA patches)
        ./deploy.sh user@gateway-host --proxy    # proxy only (gateway + device)
        ./deploy.sh user@gateway-host --web      # web only (UI + backend)

    Skill — check all gateway components are healthy::

        ssh user@gateway-host
        systemctl status ydnu02-tcp-gateway   # gateway process
        systemctl status ydnu02-web           # web UI process
        ss -tlnp | grep -E '400[12]'          # ports 4001/4002 listening
        nc -z localhost 4001 && echo 'DATA OK' || echo 'DATA FAIL'
        nc -z localhost 4002 && echo 'CTRL OK' || echo 'CTRL FAIL'
        curl -s http://localhost:8080/api/devices | python3 -m json.tool | head

    Skill — view live gateway logs::

        ssh user@gateway-host journalctl -u ydnu02-tcp-gateway -f --no-pager
        # Key log patterns:
        #   [serial] opened /dev/ttyACM0     → serial port connected
        #   [cache] ISO Claim cached SA=X    → device discovered
        #   [data] client connected          → HA/Signal K/web connected
        #   [ctrl] SERVICE_START             → service mode entered
        #   [gwdev] Address claimed: SA=200  → virtual device online

    ISSUE(unclean-shutdown): No graceful shutdown sequence. Serial port is not
      explicitly closed, pending TCP sends are not drained, and the YDNU-02
      may be left in whatever mode it was in. On systemd restart, the next
      serial_reader will reinitialize the YDNU-02 in RAW mode.

    TODO(signal-handling): Register SIGTERM handler for systemd stop integration.
      Close serial port, drain clients, send "Cannot Claim" from N2KDevice.
    """
    # Import gateway device module (uses nmea2000 library's N2KDevice).
    # Deferred import so gateway can start without the library on non-target platforms.
    from ydnu02_gateway_device import start_in_thread as start_gw_device
    from ydnu02_gateway_device import set_data_hub as _set_gw_data_hub
    _set_gw_data_hub(_data_hub)

    # Start serial reader thread
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()

    # Start virtual N2K device (SA=200: ISO Claim, Product Info, Heartbeat, CPU Temp).
    # Connects back to our own port 4001 after a 5s startup delay.
    # The library handles all frame encoding correctly via N2KDevice.for_text_gateway().
    start_gw_device()

    # Data server
    data_srv = _make_server(TCP_HOST, DATA_PORT)
    print(f"[proxy] NMEA data  listening on :{DATA_PORT}", flush=True)

    # Control server (runs in its own thread)
    ctrl_srv = _make_server(TCP_HOST, CTRL_PORT)
    print(f"[proxy] NMEA ctrl  listening on :{CTRL_PORT}", flush=True)
    ct = threading.Thread(
        target=_accept_loop,
        args=(ctrl_srv, handle_ctrl_client, "ctrl"),
        daemon=True,
    )
    ct.start()

    # Data accept loop (main thread)
    try:
        _accept_loop(data_srv, handle_data_client, "data")
    except KeyboardInterrupt:
        print("Shutting down.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
