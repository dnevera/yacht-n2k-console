# ydnu02_tcp_gateway — NMEA 2000 TCP Gateway for YDNU-02

Independent module. Can be deployed and operated without the rest of the ydnu02-web app.

## Purpose

`ydnu02_tcp_gateway.py` is a standalone Python TCP proxy that sits between the YDNU-02 USB
gateway (`/dev/ttyACM0`) and all clients (Home Assistant, ydnu02-web, Signal K, etc.).

**Only the proxy ever opens `/dev/ttyACM0`.** All other services connect via TCP.

---

## Ports

| Port | Name | Direction | Description |
|------|------|-----------|-------------|
| 4001 | DATA | Serial → TCP | Broadcasts `\n`-terminated NMEA 2000 ASCII frames to all clients |
| 4002 | CTRL | Bidirectional | Exclusive passthrough for service terminal / firmware flash |

### DATA port (4001)

- Read-only broadcast: connects, receives NMEA lines, disconnects.
- Clients can write to the port (TCP→Serial) for N2K bus commands.
- Multiple concurrent clients supported.
- Only valid NMEA lines are forwarded (format: `HH:MM:SS.mmm R|T CANID DD DD...`).

### CTRL port (4002)

- Single-client exclusive session (second client gets `ERROR: another session active`).
- Protocol (line-oriented UTF-8):

```
Client → Proxy:   SERVICE_START\n
Proxy  → Client:  READY\n
Client → Proxy:   <terminal command>\r\n   (e.g. HELP, SET, FILTER)
Proxy  → Client:  <terminal response bytes> (pushed on each poll)
Client → Proxy:   SERVICE_END\n
Proxy  → Client:  OK\n
```

---

## Service Terminal Gateway (how mode switching works)

YDNU-02 requires a **DTR toggle** to enter service terminal mode. `serial.write()` does NOT
trigger this — the port must be fully closed, the command written via OS echo, then reopened.

On `SERVICE_START`, the proxy does this internally:
1. `serial.close()` — releases DTR
2. `stty -F /dev/ttyACM0 hupcl` — arm DTR hangup-on-close
3. `echo "YDNU MODE SERVICE" > /dev/ttyACM0` — open (DTR↑), write, close (DTR↓)
4. `sleep(1.5)` — wait for YDNU-02 to switch modes
5. `serial.Serial(port, timeout=2.0)` — reopen for service terminal I/O
6. Send `READY` to ctrl client

On `SERVICE_END`:
1. `serial.write(b"MODE RAW\r\n")` — service terminal command (works because YDNU-02 is in terminal mode)
2. `serial.timeout = 0.1` — reset to fast NMEA polling
3. `service_mode.clear()` — resume broadcast

The `serial_reader` thread adopts the new serial instance automatically (checks `serial_instance`
every 50ms while in service_mode sleep loop).

---

## Deployment

### Paths on gateway.local

| File | Path |
|------|------|
| Proxy script | `/opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py` |
| Service unit | `/etc/systemd/system/ydnu02-tcp-gateway.service` |

Same directory as `ydnu02-web` so both services share one deploy target.

### Install from scratch

```bash
# 1. Copy script
scp ydnu02_tcp_gateway/ydnu02_tcp_gateway.py user@<gateway-host>.local:/opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py

# 2. Copy service unit
scp ydnu02_tcp_gateway/ydnu02_tcp_gateway.service user@<gateway-host>.local:/tmp/ydnu02_tcp_gateway.service
ssh user@<gateway-host>.local "sudo mv /tmp/ydnu02_tcp_gateway.service /etc/systemd/system/ydnu02-tcp-gateway.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable ydnu02-tcp-gateway \
  && sudo systemctl start ydnu02-tcp-gateway"

# 3. Verify
ssh user@<gateway-host>.local "systemctl status ydnu02-tcp-gateway --no-pager && \
  sudo journalctl -u ydnu02-tcp-gateway -n 10 --no-pager"
```

### Update (via deploy.sh)

```bash
./deploy.sh              # deploys both ydnu02-web and ydnu02_tcp_gateway
./deploy.sh --proxy-only # deploys only the proxy
```

### Manual service management

```bash
ssh user@<gateway-host>.local "sudo systemctl restart ydnu02-tcp-gateway"
ssh user@<gateway-host>.local "sudo systemctl status ydnu02-tcp-gateway --no-pager"
ssh user@<gateway-host>.local "sudo journalctl -u ydnu02-tcp-gateway -n 30 --no-pager"
```

---

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `NMEA_SERIAL_PORT` | `/dev/ttyACM0` | YDNU-02 USB serial device |
| `NMEA_SERIAL_BAUD` | `115200` | Baud rate |
| `NMEA_PROXY_HOST` | `""` (all interfaces) | Bind host |
| `NMEA_PROXY_PORT` | `4001` | DATA broadcast port |
| `NMEA_CTRL_PORT` | `4002` | CTRL exclusive port |

---

## YDNU-02 Init Sequence (on startup / reconnect)

```python
ser.write(b"YDNU MODE RAW\r\n")   # switch to RAW mode
time.sleep(2.0)
ser.read(ser.in_waiting)           # flush mode-switch echo
ser.write(b"0\n")                  # set filter: show all frames
time.sleep(0.5)
ser.read(ser.in_waiting)           # flush filter echo
```

Note: `YDNU MODE RAW\r\n` works here because it's sent immediately after opening a fresh
serial connection (DTR just went high). This is different from the service mode switch
which requires the port to be closed first.

---

## Testing

```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console
python3 -m pytest tests/test_ydnu02_tcp_gateway.py tests/test_service_mode.py -v
```

### Manual ctrl port test

```python
import socket, time
s = socket.socket(); s.connect(('gateway.local.local', 4002)); s.settimeout(5)
s.sendall(b'SERVICE_START\n')
print(s.recv(1024))   # READY
s.sendall(b'HELP\r\n')
time.sleep(3)
print(s.recv(4096).decode())   # firmware version + command list
s.sendall(b'SERVICE_END\n')
print(s.recv(1024))   # OK
s.close()
```
