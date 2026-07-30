# ydnu02_tcp_gateway — NMEA 2000 TCP Gateway for YDNU-02

Standalone Python TCP proxy and device gateway. Manages the hardware serial interface to the Yacht Devices YDNU-02 USB gateway (`/dev/ttyACM0`) and exposes TCP ports for multi-client NMEA 2000 data streaming and remote hardware management.

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
│               │ DATA :4001                                │ DATA :4001 /│ CTRL :4002
│               ▼                                           ▼             │   │
│  ┌──────────────────────────────────────────────────────────────────┐   │   │
│  │                ydnu02_tcp_gateway.py (Proxy Service)              │◄──┘   │
│  │                (systemd: ydnu02-tcp-gateway.service)             │◄──────┘
│  └──────────────────────────────────┬───────────────────────────────┘
│                                     │ Serial (/dev/ttyACM0 @ 115200 baud)
│                                     ▼
│                           ┌───────────────────┐
│                           │ YDNU-02 USB CAN   │
│                           └─────────┬─────────┘
└─────────────────────────────────────┼───────────────────────────────────────┘
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

### DATA Port (`4001`) — Broadcast & CAN Write

- **Multi-Client Broadcast**: Concurrently streams `\n`-terminated ASCII CAN frames to all connected TCP clients.
- **Frame Format**: Standard YDNU-02 RAW ASCII format:
  ```text
  HH:MM:SS.mmm R|T CAN_ID_HEX DATA_BYTES_HEX
  
  Example:
  16:42:15.123 R 0DF0042C FF 1A 00 00 00 00 00 00
  ```
- **Bidirectional CAN Bus Writing**: Any TCP client can write a `\n`-terminated RAW ASCII frame to port `4001`. The proxy forwards the line directly to the serial port.
- **Client Disconnect & EOF Handling**: When a client disconnects, socket read returns `b""` (EOF). The proxy closes the connection immediately, preventing infinite spin-loops across downstream clients.

---

### CTRL Port (`4002`) — Exclusive Remote Control

- **Single-Client Exclusive Access**: Only one active control session is allowed at any time. A second concurrent connection receives `ERROR: another session active\n` and is disconnected.
- **Line-Oriented UTF-8 Protocol**:

#### Service Terminal Sequence

```text
Client → Proxy:   SERVICE_START\n
Proxy  → Client:  READY\n

Client → Proxy:   <terminal command>\r\n   (e.g., HELP, SET, FILTER)
Proxy  → Client:  <terminal response bytes pushed on each poll>

Client → Proxy:   SERVICE_END\n
Proxy  → Client:  OK\n
```

#### Pause / Resume Sequence (for non-disruptive hardware operations)

```text
Client → Proxy:   PAUSE_IO\n
Proxy  → Client:  PAUSED\n

Client → Proxy:   RESUME_IO\n
Proxy  → Client:  RESUMED\n
```

---

## Hardware Service Terminal & DTR Toggle Logic

The YDNU-02 USB gateway requires a physical **DTR line drop** to switch from RAW CAN mode into its interactive service configuration terminal. A standard `serial.write()` does NOT trigger this mode switch.

### `SERVICE_START` State Machine

When a client sends `SERVICE_START` to port `4002`:

1. `serial.close()` — Closes serial handle and drops DTR line.
2. `stty -F /dev/ttyACM0 hupcl` — Sets OS terminal flag to force DTR hangup on close.
3. `echo "YDNU MODE SERVICE" > /dev/ttyACM0` — Opens port (DTR↑), writes command, closes port (DTR↓).
4. `sleep(1.5)` — Waits 1.5 seconds for YDNU-02 internal microcontroller to complete mode transition.
5. `serial.Serial(port, timeout=2.0)` — Re-opens serial port at 115200 baud for terminal I/O.
6. `Proxy → Client`: Sends `READY\n`.

### `SERVICE_END` State Machine

When a client sends `SERVICE_END` to port `4002`:

1. Sends `MODE RAW\r\n` to the YDNU-02 terminal.
2. `serial.timeout = 0.1` — Restores fast polling timeout.
3. `service_mode.clear()` — Resumes DATA port (`4001`) broadcasting.
4. `Proxy → Client`: Sends `OK\n`.

---

## Deployment & Systemd Configuration

### Deployment Targets

- **Host**: `gateway.local.local` (Raspberry Pi 5)
- **User**: `denn`
- **Script Path**: `/opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py`
- **Systemd Service**: `/etc/systemd/system/ydnu02-tcp-gateway.service`

---

### Deploying via `deploy.sh`

From project root:

```bash
# Full deploy (web app + TCP gateway service)
./deploy.sh

# Proxy-only deploy
./deploy.sh --proxy-only
```

---

### Manual Deployment Commands

```bash
# 1. Copy script to gateway.local
scp ydnu02_tcp_gateway/ydnu02_tcp_gateway.py user@<gateway-host>.local:/opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py

# 2. Copy and activate systemd service unit
scp ydnu02_tcp_gateway/ydnu02-tcp-gateway.service user@<gateway-host>.local:/tmp/ydnu02-tcp-gateway.service
ssh user@<gateway-host>.local "sudo mv /tmp/ydnu02-tcp-gateway.service /etc/systemd/system/ydnu02-tcp-gateway.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable ydnu02-tcp-gateway \
  && sudo systemctl restart ydnu02-tcp-gateway"

# 3. Verify service health & logs
ssh user@<gateway-host>.local "systemctl status ydnu02-tcp-gateway --no-pager && \
  sudo journalctl -u ydnu02-tcp-gateway -n 20 --no-pager"
```

---

## Environment Variables & Configuration

The proxy reads environment variables on startup (with sensible defaults for YDNU-02):

| Variable | Default | Description |
|:---|:---|:---|
| `NMEA_SERIAL_PORT` | `/dev/ttyACM0` | Path to USB CDC-ACM serial device |
| `NMEA_SERIAL_BAUD` | `115200` | Serial baud rate |
| `NMEA_PROXY_HOST` | `""` (all interfaces) | Bind address for TCP servers |
| `NMEA_PROXY_PORT` | `4001` | DATA broadcast TCP port |
| `NMEA_CTRL_PORT` | `4002` | CTRL control TCP port |

---

## YDNU-02 Initialization Sequence

On daemon startup or USB reconnect, `ydnu02_tcp_gateway.py` initializes the hardware:

```python
ser.write(b"YDNU MODE RAW\r\n")   # Ensure gateway is in RAW CAN ASCII mode
time.sleep(2.0)
ser.read(ser.in_waiting)           # Flush startup banner
ser.write(b"0\n")                  # Set pass-all CAN message filter
time.sleep(0.5)
ser.read(ser.in_waiting)           # Flush filter response
```

---

## Testing & Diagnostics

### Automated Tests (Pytest)

Run unit tests locally before deployment:

```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console
python3 -m pytest tests/test_ydnu02_tcp_gateway.py tests/test_service_mode.py -v
```

### Manual Control Port Diagnostic Script

```python
import socket
import time

s = socket.socket()
s.connect(('gateway.local.local', 4002))
s.settimeout(5.0)

# Enter service mode
s.sendall(b'SERVICE_START\n')
print("CTRL Response:", s.recv(1024).decode())   # Expect: READY\n

# Send HELP command to YDNU-02 terminal
s.sendall(b'HELP\r\n')
time.sleep(2.0)
print("Terminal Help:\n", s.recv(4096).decode())

# Return to RAW CAN mode
s.sendall(b'SERVICE_END\n')
print("CTRL Response:", s.recv(1024).decode())   # Expect: OK\n
s.close()
```
