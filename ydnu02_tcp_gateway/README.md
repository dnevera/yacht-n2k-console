# ydnu02_tcp_gateway — NMEA 2000 TCP Gateway for YDNU-02

Standalone Python TCP proxy and device gateway. Manages the hardware serial interface to the Yacht Devices YDNU-02 USB gateway (`/dev/ttyACM0`) and exposes TCP ports for multi-client NMEA 2000 data streaming, remote hardware management, and virtual N2K node identity.

---

## Architecture & Hardware Isolation

**Golden Rule**: Only `ydnu02_tcp_gateway.py` ever opens `/dev/ttyACM0`. All application services (`ydnu02-web`, Home Assistant, Signal K) connect exclusively over local or network TCP sockets.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Raspberry Pi 5 (gateway.local)                       │
│                                                                             │
│  ┌─────────────────────────┐               ┌─────────────────────────────┐  │
│  │ Home Assistant /        │               │ ydnu02-web                  │  │
│  │ Signal K / External     │               │ (FastAPI App)               │  │
│  └────────────┬────────────┘               └──────────────┬──────────────┘  │
│               │ DATA :4001                          DATA :4001 / CTRL :4002 │
│               ▼                                           ▼                 │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                ydnu02_tcp_gateway.py (Proxy Service)                  │  │
│  │                (systemd: ydnu02-tcp-gateway.service)                  │  │
│  │                ├── DATA Hub :4001 (multi-client broadcast)            │  │
│  │                ├── CTRL Server :4002 (exclusive session)              │  │
│  │                ├── Device Frame Cache (ISO Claim + Product Info)      │  │
│  │                └── Virtual Node Thread (ydnu02_gateway_device, SA=200)│  │
│  └─────────────────────────────────┬─────────────────────────────────────┘  │
│                                    │ Serial (/dev/ttyACM0 @ 115200 baud)    │
│                                    ▼                                        │
│                          ┌───────────────────┐                              │
│                          │ YDNU-02 USB CAN   │                              │
│                          └─────────┬─────────┘                              │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     ▼
                               NMEA 2000 CAN Bus
```

---

## Network Ports & Communication Protocols

| Port | Service | Protocol | Direction | Description |
|:---|:---|:---|:---|:---|
| **`4001`** | **DATA** | ASCII N2K Stream | Bidirectional | Multi-client NMEA 2000 broadcast server & CAN write interface |
| **`4002`** | **CTRL** | UTF-8 Control Line | Bidirectional | Single-client exclusive control channel (service mode & DTR toggle) |

---

### DATA Port (`4001`) — Broadcast, CAN Hub & Write

- **Multi-Client Broadcast**: Concurrently streams `\n`-terminated ASCII CAN frames to all connected TCP clients.
- **Frame format** (YDNU-02 RAW ASCII, serial→TCP direction):
  ```text
  HH:MM:SS.mmm R CAN_ID_HEX DD DD DD DD DD DD DD DD\n

  Example:
  16:42:15.123 R 0DF0042C FF 1A 00 00 00 00 00 00
  ```
- **TX format** (no timestamp, TCP client→serial, e.g. `N2KDevice` output):
  ```text
  CAN_ID_HEX DD DD DD DD DD DD DD DD\r\n

  Example:
  18EAFFFE 00 EE 00\r\n
  ```
- **N2K Bus Hub (bidirectional TCP-only)**:
  - Frames arriving from TCP clients are forwarded to all **other** TCP clients (not looped back to sender).
  - Only **PGN 59904 (ISO Request)** frames are also forwarded to the serial port so physical devices on the CAN bus respond. All other TX frames (e.g. ISO Claims and Product Info from virtual `N2KDevice`) stay within the TCP hub.
- **On Client Connect**:
  1. Cached device frames (ISO Address Claims + Product Info) are **replayed** to the new client immediately — no hardware rescan required.
  2. An **ISO Request** is sent to serial (rate-limited to ≥5s between requests) so any devices that came up after last scan announce themselves.
- **Client Disconnect & EOF**: When `recv()` returns `b""`, the connection is closed immediately — no spin-loop.

---

### CTRL Port (`4002`) — Exclusive Remote Control

- **Single-Client Exclusive Session**: Only one active control session allowed. A second connection receives `ERROR: another control session is active\n` and is disconnected.
- **Line-Oriented UTF-8 Protocol** (100ms poll interval for serial→client data push):

#### Service Terminal Sequence (`SERVICE_START` / `SERVICE_END`)

Triggers full DTR-toggle mode switch from RAW CAN to interactive YDNU-02 terminal:

```text
Client → Proxy:   SERVICE_START\n
Proxy  → Client:  READY\n

Client → Proxy:   <terminal command>\r\n   (e.g., HELP, SET, FILTER, INFO)
Proxy  → Client:  <terminal response bytes — pushed on each 100ms poll>

Client → Proxy:   SERVICE_END\n
Proxy  → Client:  OK\n
```

#### Firmware Passthrough Sequence (`FIRMWARE_START` / `FIRMWARE_END`)

Raw serial passthrough without DTR toggle (used for firmware flashing). No mode switch is performed — serial input buffer is flushed and exclusive serial access is granted:

```text
Client → Proxy:   FIRMWARE_START\n
Proxy  → Client:  READY\n

<raw firmware data exchange>

Client → Proxy:   FIRMWARE_END\n
Proxy  → Client:  OK\n
```

#### Error Responses

| Situation | Response |
|:---|:---|
| Second client connects while session active | `ERROR: another control session is active` |
| Command sent while not in service mode | `ERROR: not in service mode` |
| Serial write fails | `ERROR: serial write: <exception>` |

---

## Device Frame Cache & New Client Replay

The gateway maintains an in-memory cache of all N2K device identification frames seen in live traffic:

```python
_device_frame_cache: dict[int, dict] = {}
# Structure: {sa_int: {'iso_claim': bytes, 'product_info': [bytes, ...]}}
```

- **PGN 60928 (ISO Address Claim)**: Cached as single frame per SA, overwritten on each new claim.
- **PGN 126996 (Product Information)**: Reassembled from fast-packet multi-frame sequence, stored once complete.
- **Pre-seeded at startup**: The gateway's own virtual identity (SA=200) is pre-seeded in the cache before any physical traffic arrives, ensuring HA always has at least one visible device.
- **On new TCP client connect**: Full cache snapshot is replayed immediately, so Home Assistant builds its N2K network map without requiring a manual rescan or device power cycle.

---

## Virtual N2K Gateway Device (`ydnu02_gateway_device.py`)

The proxy launches a background thread (`start_in_thread()`) that registers the Raspberry Pi 5 host as a first-class participant on the NMEA 2000 network using `N2KDevice.for_text_gateway()` — connecting back to the gateway's own port `4001` (5-second startup delay to ensure server is ready).

### Gateway N2K Identity

| Parameter | Value | Description |
|:---|:---|:---|
| **Preferred SA** | `200` | Preferred NMEA 2000 Source Address |
| **Manufacturer** | `717` | Yacht Devices (hardware manufacturer of YDNU-02) |
| **Device Class** | `25` | Internetwork Device |
| **Device Function** | `130` | PC Gateway |
| **Industry Group** | `4` | Marine Industry |
| **Model ID** | `YDNU-02 TCP-GW` | Shown in HA / Signal K device list |

### Transmitted PGNs

| PGN | Name | Interval | Notes |
|:---|:---|:---|:---|
| **60928** | ISO Address Claim | On startup, on request | Auto-handled by `N2KDevice` library |
| **126996** | Product Information | On startup, on ISO Request | Sent once after address claim completes |
| **126993** | Heartbeat | Every 10 seconds | Auto-handled by `N2KDevice` library |
| **130312** | Temperature | Every 3 seconds | CPU temp from `/sys/class/thermal/thermal_zone*/temp`, Source=2 ("Inside Temperature") in Kelvin |

The thread restarts automatically after any crash with a 15-second backoff.

---

## Hardware Service Terminal & DTR Toggle Logic

The YDNU-02 USB gateway requires a physical **DTR line drop** to switch from RAW CAN mode into its interactive service terminal. `serial.write("YDNU MODE SERVICE")` is **silently ignored** while the port is held open — the mode switch only happens on a DTR low→high transition (port close → reopen, or OS `echo`).

**The ctrl client (`ProxyControlClient` / `ydnu02.py`) does NOT send `"YDNU MODE SERVICE"` itself** — the gateway handles this entirely internally on `SERVICE_START`.

### `SERVICE_START` Internal Sequence

1. `serial_instance = None` + `serial.close()` — Releases DTR line.
2. `stty -F /dev/ttyACM0 hupcl` — Arms OS terminal hangup-on-close flag.
3. `echo "YDNU MODE SERVICE" > /dev/ttyACM0` — Opens port (DTR↑), writes command, closes port (DTR↓).
4. `sleep(1.5)` — Waits for YDNU-02 microcontroller mode transition.
5. `serial.Serial(port, baud, timeout=2.0, dsrdtr=True)` + `dtr = True` — Re-opens in service terminal mode.
6. Flush any pending bytes.
7. `Proxy → Client`: Sends `READY\n`.

### `SERVICE_END` Internal Sequence

1. `serial.write(b"MODE RAW\r\n")` — Sends terminal command to return to RAW CAN mode.
2. `sleep(0.5)` — Waits for mode switch.
3. `serial.timeout = 0.1` — Restores fast-polling timeout.
4. `service_mode.clear()` — Resumes DATA port (`4001`) broadcast.
5. `Proxy → Client`: Sends `OK\n`.

### Thread Model

| Thread | Role |
|:---|:---|
| `serial_reader` | Owns `serial_instance`, broadcasts to DATA clients, adopts new serial instance every 50ms during service mode |
| `ctrl handler` | Takes over `serial_instance` exclusively during `SERVICE_START/END` |
| `gateway-n2k-device` | Daemon thread for virtual N2K device (runs its own asyncio event loop) |

---

## Deployment & Systemd Configuration

### Target Paths on `gateway.local`

| File | Remote Path |
|:---|:---|
| `ydnu02_tcp_gateway.py` | `/opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py` |
| `ydnu02_gateway_device.py` | `/opt/nmea2000/ydnu02-web/ydnu02_gateway_device.py` |
| Systemd service | `/etc/systemd/system/ydnu02-tcp-gateway.service` |

The gateway service starts **before** `ydnu02-web.service` (declared in systemd unit).

### Deploy via `deploy.sh`

```bash
# Full deploy (web app + TCP gateway)
./deploy.sh

# Proxy-only deploy
./deploy.sh user@<gateway-host> --proxy
```

### Manual Deployment

```bash
# 1. Copy scripts to gateway.local
scp ydnu02_tcp_gateway/ydnu02_tcp_gateway.py user@<gateway-host>.local:/opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py
scp ydnu02_tcp_gateway/ydnu02_gateway_device.py user@<gateway-host>.local:/opt/nmea2000/ydnu02-web/ydnu02_gateway_device.py

# 2. Install and activate systemd service unit (first time only)
scp ydnu02_tcp_gateway/ydnu02-tcp-gateway.service user@<gateway-host>.local:/tmp/ydnu02-tcp-gateway.service
ssh user@<gateway-host>.local "sudo mv /tmp/ydnu02-tcp-gateway.service /etc/systemd/system/ydnu02-tcp-gateway.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable ydnu02-tcp-gateway \
  && sudo systemctl restart ydnu02-tcp-gateway"

# 3. Verify
ssh user@<gateway-host>.local "systemctl status ydnu02-tcp-gateway --no-pager && \
  sudo journalctl -u ydnu02-tcp-gateway -n 20 --no-pager"
```

### Service Management

```bash
# Restart
ssh user@<gateway-host>.local "sudo systemctl restart ydnu02-tcp-gateway"

# Status
ssh user@<gateway-host>.local "systemctl status ydnu02-tcp-gateway --no-pager"

# Logs
ssh user@<gateway-host>.local "sudo journalctl -u ydnu02-tcp-gateway -n 50 --no-pager"
```

---

## Environment Variables

| Variable | Default | Description |
|:---|:---|:---|
| `NMEA_SERIAL_PORT` | `/dev/ttyACM0` | Path to USB CDC-ACM serial device |
| `NMEA_SERIAL_BAUD` | `115200` | Serial baud rate |
| `NMEA_PROXY_HOST` | `""` (all interfaces) | Bind address for TCP servers |
| `NMEA_PROXY_PORT` | `4001` | DATA broadcast TCP port |
| `NMEA_CTRL_PORT` | `4002` | CTRL control TCP port |

---

## YDNU-02 Initialization Sequence

On startup or USB reconnect, the serial reader initializes the hardware:

```python
ser.write(b"YDNU MODE RAW\r\n")   # Switch to RAW CAN ASCII mode
time.sleep(2.0)
ser.read(ser.in_waiting)           # Flush startup banner / echo
ser.write(b"0\n")                  # Set pass-all CAN frame filter
time.sleep(0.5)
ser.read(ser.in_waiting)           # Flush filter response
# → _serial_ready.set() — unlocks ISO Request sending
```

Any valid NMEA frames received during init are pre-loaded into the device cache before `_serial_ready` is set.

---

## Testing & Diagnostics

### Automated Tests

```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console
python3 -m pytest tests/test_ydnu02_tcp_gateway.py tests/test_service_mode.py -v
```

### Manual CTRL Port Diagnostic

```python
import socket, time

s = socket.socket()
s.connect(('gateway.local.local', 4002))
s.settimeout(5.0)

s.sendall(b'SERVICE_START\n')
print(s.recv(1024).decode())   # → READY

s.sendall(b'HELP\r\n')
time.sleep(2.0)
print(s.recv(4096).decode())   # → YDNU-02 terminal help output

s.sendall(b'SERVICE_END\n')
print(s.recv(1024).decode())   # → OK
s.close()
```
