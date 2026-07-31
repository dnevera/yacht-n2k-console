# Technical Reference

## Project Structure

```
yacht-n2k-console/
├── app.py                  # FastAPI application entry point
├── device_manager.py       # CAN bus worker thread, device discovery, sensor state
├── ydnu02.py               # YDNU-02 serial protocol, N2KPGNDecoder, service mode
├── n2k_meta.py             # Dynamic PGN field metadata extraction from nmea2000 lib
├── n2k_command_builder.py  # Legacy hardcoded command builder (deprecated by n2k_meta)
├── models.py               # Pydantic request/response models
├── scan_gobius.py          # CLI utility for direct Gobius C BLE scanning
│
├── routes/
│   ├── __init__.py         # Lazy singleton getters (avoid circular imports)
│   ├── device.py           # GET /api/info, /api/sensors, /api/dashboard/sensors
│   ├── n2k_config.py       # Dynamic device config API (PGN 126208)
│   ├── n2k.py              # Legacy N2K command endpoint
│   ├── gobius.py           # Gobius C BLE sensor management & config
│   ├── mopeka.py           # Mopeka Pro BLE sensor management
│   ├── ble.py              # BLE device registry (scan, bind, delete)
│   ├── service.py          # YDNU-02 service mode, I/O pause/resume, diagnostics
│   ├── maintenance.py      # Backup, factory reset, MCU/hardware reset
│   ├── firmware.py         # Firmware download, upload, and flash
│   └── websockets.py       # /ws/monitor, /ws/scan
│
├── sensors/
│   ├── base_sensor.py      # BaseSensor ABC with HA MQTT publishing
│   ├── gobius_sensor.py    # GobiusCSensor — NMEA + BLE dual-source
│   └── mopeka_sensor.py    # MopekaSensor — BLE advertisement parsing
│
├── gobius_ble_poller.py    # Gobius C BLE GATT client (calibration, config)
├── gobius_parsers.py       # Gobius BLE characteristic parsers
├── ble_registry.py         # Persistent BLE MAC ↔ sensor binding
├── mopeka_scanner.py       # Mopeka Pro BLE advertisement scanner
├── mopeka_parsers.py       # Mopeka BLE advertisement data parsers
│
├── ydnu02_tcp_gateway/     # Standalone TCP Gateway module (see ydnu02_tcp_gateway/README.md)
│   ├── ydnu02_tcp_gateway.py      # Proxy server — exclusive /dev/ttyACM0 owner
│   ├── ydnu02_gateway_device.py   # Virtual N2K node SA=200 (ISO Claim, HB, CPU Temp)
│   └── ydnu02-tcp-gateway.service # systemd unit (starts before ydnu02-web)
│
├── patches/                # Runtime patches for third-party libraries
│   ├── nmea2000_ioclient.py       # EOF fix — prevents 100% CPU spin in TextNmea2000Gateway
│   └── README.md                  # Patch documentation and apply instructions
│
├── static/
│   ├── index.html          # Shell with tab navigation
│   ├── css/style.css       # Dark theme UI styles
│   ├── js/
│   │   ├── core.js         # App object, API helpers, toast, WebSocket
│   │   ├── dashboard.js    # Dashboard tab — sensor cards, auto-refresh
│   │   ├── network.js      # Network tab — CAN bus scan, device cards
│   │   ├── n2k_config.js   # Dynamic N2K config modal (from API metadata)
│   │   ├── monitor.js      # Monitor tab — live CAN frame viewer
│   │   ├── gobius.js       # Gobius tab — BLE pairing, calibration, config
│   │   ├── mopeka.js       # Mopeka tab — BLE scan, binding
│   │   ├── service.js      # Service tab — YDNU-02 terminal
│   │   └── maintenance.js  # Maintenance tab — reset, diagnostics, firmware
│   └── tabs/               # HTML fragments loaded into tab sections
│
├── tests/                  # API and unit tests
├── deploy.sh               # SCP deploy to gateway-host + service restart + HA patch
├── build_bundle.sh         # Build tarball for offline deployment
├── setup_gateway.sh      # Initial Raspberry Pi setup script
├── ydnu02-web.service      # Systemd unit for ydnu02-web (depends on tcp-gateway)
├── docker-compose.yml      # Signal K server (optional)
└── pyproject.toml          # Python project metadata
```

## Core Modules

### n2k_meta.py — PGN Metadata Engine

The heart of the dynamic configuration system. Extracts field metadata for **any** PGN from the `nmea2000` library at runtime — no hardcoded registries.

#### Key Functions

| Function | Purpose |
|----------|---------|
| `get_pgn_field_metadata(pgn)` | Returns field list with types, enums, units, configurable flag |
| `get_pgn_name(pgn)` | Human-readable PGN name (e.g., "Fluid Level") |
| `build_iso_request_frame(pgn)` | PGN 59904 ISO Request → CAN RAW frame string |
| `build_read_fields_frame(src, pgn)` | PGN 126208 Read Fields Request → CAN RAW frame |
| `build_write_fields_frame(src, pgn, pairs)` | PGN 126208 Write Fields → CAN RAW frame |
| `build_command_frame(src, pgn, pairs)` | PGN 126208 Command Group Function → CAN RAW frame |
| `parse_device_info(raw_line)` | Decode PGN 60928/126996 → structured device info |
| `parse_pgn_list(raw_line)` | Decode PGN 126464 → list of supported PGNs |
| `decode_raw_line(raw_line)` | Generic CAN frame → dict of decoded fields |

#### How Field Metadata Works

1. A dummy CAN frame is constructed for the target PGN and decoded via `NMEA2000Decoder`
2. Each `NMEA2000Field` in the decoded message provides: `id`, `name`, `type`, `unit_of_measurement`
3. For `LOOKUP` type fields, the decode function source is parsed via `inspect.getsource()` to extract the `master_dict` key (e.g., `TANK_TYPE`)
4. Enum options are resolved from `pgns.master_dict['TANK_TYPE']` → `{0: "Fuel", 1: "Water", ...}`
5. Read-only fields (sensor measurements like `level`, `temperature`) are marked `configurable: false`

#### Example Output

```json
// GET /api/n2k/pgn/127505/metadata
{
  "pgn": 127505,
  "name": "Fluid Level",
  "fields": [
    {"id": "instance", "name": "Instance", "type": "number", "unit": null, "options": null, "configurable": true},
    {"id": "type", "name": "Type", "type": "lookup", "unit": null, "configurable": true,
     "options": {"0": "Fuel", "1": "Water", "2": "Gray water", "3": "Live well", "4": "Oil", "5": "Black water"}},
    {"id": "level", "name": "Level", "type": "number", "unit": "%", "options": null, "configurable": false},
    {"id": "capacity", "name": "Capacity", "type": "number", "unit": "L", "options": null, "configurable": true}
  ]
}
```

### ydnu02_tcp_gateway.py — Standalone TCP Gateway Proxy

- **Exclusive Serial Ownership**: Holds `/dev/ttyACM0` at 115200 baud. No other application opens the USB serial device directly.
- **Port 4001 (DATA)**: Multi-client broadcast server. Serial frames are broadcast to all TCP clients. TCP client frames are forwarded to all **other** TCP clients. Only ISO Request (PGN 59904) frames are additionally forwarded to serial — all other TX frames (virtual device ISO Claim, Product Info) stay in the TCP hub.
- **Port 4002 (CTRL)**: Exclusive control channel. Supports `SERVICE_START`/`SERVICE_END` (DTR toggle + service terminal) and `FIRMWARE_START`/`FIRMWARE_END` (raw passthrough for firmware flash).
- **Device Frame Cache**: ISO Address Claims (PGN 60928) and Product Info (PGN 126996) are cached per SA and replayed to every new TCP client on connect — no hardware rescan needed.
- **TCP Disconnect / EOF Protection**: `ConnectionError` on `b""` (EOF) prevents spin-loops in `TextNmea2000Gateway`.

For full technical specifications, DTR state machines, and systemd service setup, see [ydnu02_tcp_gateway/README.md](ydnu02_tcp_gateway/README.md).

### device_manager.py — Bus Worker & TCP Connection

- Manages the TCP stream via `TCPProxyConnection` (port 4001) with exponential backoff reconnect
- Uses `ProxyControlClient` (port 4002) for atomic hardware pause/resume
- Parses raw CAN frames using `N2KPGNDecoder` (from `ydnu02.py`)
- Maintains `_discovered_bus_devices` dict keyed by source address
- Uses `N2KPGNDecoder.parse_device_info()` for library-based manufacturer/model resolution
- Tracks `active_pgns` per device for dynamic config UI
- Manages sensor instances (`GobiusCSensor`) updated from PGN 127505
- Thread-safe via `_lock` (socket access) and `_sensors_lock` (state)

### ydnu02.py — YDNU-02 Protocol

- `N2KPGNDecoder`: CAN frame parsing, device info extraction, PGN decode
- `YDNU02Controller`: serial/socket port management, read/write, service mode commands
- Service mode operations: `INFO`, `STATUS`, `MODE`, `RESET`, firmware upload

## NMEA 2000 Protocol Reference

### Discovery Flow

```
Our Gateway                    Target Device
    │                                │
    ├── ISO Request (PGN 59904) ────►│  "Send me your Address Claim"
    │◄── Address Claim (PGN 60928) ──┤  manufacturer, function, class, instance
    │                                │
    ├── ISO Request (PGN 59904) ────►│  "Send me your Product Info"
    │◄── Product Info (PGN 126996) ──┤  model, serial, firmware version
    │                                │
    ├── ISO Request (PGN 59904) ────►│  "Send me your PGN list"
    │◄── PGN List (PGN 126464) ─────┤  list of PGNs this device transmits
```

### Configuration Flow (PGN 126208)

```
Our Gateway                    Target Device
    │                                │
    ├── Read Fields Request ────────►│  function_code=3, target_pgn=127505
    │◄── Read Fields Reply ──────────┤  current field values
    │                                │
    ├── Command Group Function ─────►│  function_code=1, field pairs
    │◄── Acknowledge ────────────────┤  accept / reject
    │                                │
    ├── Read Fields Request ────────►│  verify written values
    │◄── Read Fields Reply ──────────┤  new field values (diff)
```

### CAN Frame Format (YDNU-02 RAW mode)

```
<CAN_ID_hex> <data_byte_1> <data_byte_2> ... <data_byte_N>

Example: 18EAFF10 00 EE 00
         ^^^^^^^^ ^^^^^^^^
         CAN ID   Data (3 bytes: PGN 60928 in little-endian)

29-bit CAN ID structure:
  Bits 28-26: Priority (0-7)
  Bits 25-16: PDU Format (PF) — determines PGN
  Bits 15-8:  PDU Specific (PS) — destination or group extension
  Bits 7-0:   Source Address
```

## BLE Integration & Dual-Source Architecture

### Gobius C Sensor

- **NMEA 2000 (`nmea_raw`)**: Primary live telemetry source (publishes PGN 127505 Fluid Level on CAN bus every ~2.5s).
- **BLE GATT (`ble_raw`)**: Configuration & sensor diagnostics (geometry, LP filters, N2K settings, commands, info labels).
- **BLE and NMEA are strictly independent channels** — BLE reads/writes do NOT alter NMEA 2000 bus telemetry decoding.

#### GATT Characteristic Reference (Spec Issue 3, 2023-08-08)
All multi-byte integer values are Big-Endian (MSB first) per spec §8.2.4.

| UUID | Name | Access | Purpose & Byte Layout |
|:---|:---|:---|:---|
| `0xFFE6` | **User Config** | R/W | Geometry: `[0:2]` Empty mm [20..2000], `[2:4]` Full mm [20..2000], `[4]` LP Filter N [0..100], `[5]` LP Filter K % [1..100], `[18]` Adv-off time [10..255s] |
| `0xFFE7` | **Command** | W | 3-byte command frame `[0]` ASCII code, `[1:3]` BE uint16 param. Code `'i'` (0x69) = Factory Reset, `'c'` = Calibrate, `'a'`/`'b'` = Stop/Start, `'w'` = Write Info commit |
| `0xFFE8` | **Status** | R | `[0]` State (5=Active), `[2:6]` Uptime s, `[8]` Temp °C, `[9:11]` Voltage mV, `[11:17]` MAC, `[19]` Range (0..3) |
| `0xFFE9` | **Measurement**| R+Notify | `[2]` Level valid, `[3:5]` Fill level ‰ [0..1000] (÷10 → %), `[5]` Inclination °, `[6:8]` Distance mm |
| `0xFFEB` | **Info 1** | R/W | Tank Name string (20 bytes UTF-8, space-padded). Requires `0xFFE7` `'w'` command to commit |
| `0xFFEC` | **Info 2** | R/W | Tank Comment string (20 bytes UTF-8, space-padded). Requires `0xFFE7` `'w'` command to commit |
| `0xFFF2` | **N2K Config** | R/W | `[0]` Enabled (0/1), `[1]` Instance (0..15), `[2]` Fluid Type (0..6), `[9]` Volume L (uint8, max 255L!) |
| `0xFFF3` | **N2K Status** | R | `[0]` N2K State (0/2), `[1]` Source Address (default 92) |

#### Info Write Protocol Rule
Writing `0xFFEB` or `0xFFEC` MUST be immediately followed by writing `0xFFE7` with ASCII `'w'` (`0x770000`). Skipping the commit command leaves tank labels unpersisted in sensor flash memory.

#### Safety & Confirmation Rules
- All GATT writes require confirmation via `App.gobiusConfirm()` modal showing the exact change list.
- **Dangerous Operations** (red header modal): Factory Reset (`initialize`), Turning off advertising (`adv_off`), Disabling N2K output.
- `adv_off` recovery requires a power cycle → reconnect **within 10 seconds** of power-on.

### Mopeka Pro 200

- **BLE only**: Broadcasts tank level via BLE advertisements (no NMEA 2000)
- Scanner runs continuously via D-Bus (`dbus-fast`), parsing advertisement payloads
- Supports ultrasonic and pressure-based tank measurement

### Dashboard Sensor Card Channels

Each card on the Dashboard standardizes telemetry into 3 isolated channels:
1. `📡 NMEA 2000` — live CAN bus telemetry (teal header, live age)
2. `📶 BLE` — live GATT measurements / status (teal header, live age)
3. `📋 Registry` — user configuration context from `ble_sensors.json` (muted secondary styling, `config` label)

## Full API Reference

All routes are mounted under `/api` prefix (except WebSockets).

### Device (`routes/device.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/info` | Gateway status, firmware info, serial port state |
| `GET` | `/api/sensors` | All active sensor readings |
| `GET` | `/api/dashboard/sensors` | Dashboard cards (NMEA + BLE + Registry channels) |
| `POST` | `/api/mode/{mode}` | Set YDNU-02 operating mode |
| `POST` | `/api/silent/{state}` | Enable/disable silent CAN mode |

### N2K Dynamic Config (`routes/n2k_config.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/n2k/devices` | All discovered N2K devices |
| `GET` | `/api/n2k/devices/{src}/config/{pgn}` | Read field values from device |
| `POST` | `/api/n2k/devices/{src}/config/{pgn}` | Write fields + read-back diff |
| `GET` | `/api/n2k/pgn/{pgn}/metadata` | Field metadata (types, enums, units) |
| `POST` | `/api/n2k/command` | Legacy N2K command |

### Gobius C BLE (`routes/gobius.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/gobius/live` | Fast live telemetry poller status |
| `GET` | `/api/gobius/status` | Full BLE read (Status/Measurement/Config/Info) |
| `POST` | `/api/gobius/n2k` | Write N2K Config `0xFFF2` |
| `POST` | `/api/gobius/user_config` | Write User Config `0xFFE6` |
| `POST` | `/api/gobius/command` | Send command `0xFFE7` |
| `POST` | `/api/gobius/info` | Write Info `0xFFEB`+`0xFFEC` + commit |
| `POST` | `/api/gobius/n2k_command` | Send raw N2K command via BLE |

### Mopeka Pro (`routes/mopeka.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/mopeka/sensors` | All registered Mopeka sensors |
| `GET` | `/api/mopeka/sensor/{mac}` | Single sensor reading |
| `POST` | `/api/mopeka/config/{mac}` | Update sensor configuration |
| `DELETE` | `/api/mopeka/sensor/{mac}` | Remove sensor binding |

### BLE Registry (`routes/ble.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/ble/sensors` | All registered BLE sensors |
| `POST` | `/api/ble/sensors` | Register new BLE sensor |
| `PUT` | `/api/ble/sensors/{mac}` | Update sensor metadata |
| `DELETE` | `/api/ble/sensors/{mac}` | Remove sensor from registry |
| `GET` | `/api/ble/scan` | Trigger BLE advertisement scan |

### Service & I/O (`routes/service.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `POST` | `/api/io/pause` | Pause serial I/O (for firmware flash / service) |
| `POST` | `/api/io/resume` | Resume serial I/O |
| `GET` | `/api/io/state` | Current I/O pause state |
| `GET` | `/api/filters` | Active CAN frame filters |
| `GET` | `/api/settings` | Gateway settings |
| `GET` | `/api/diag/{scope}` | Diagnostics for a given scope |
| `POST` | `/api/service/cmd` | Send command to YDNU-02 service terminal |
| `POST` | `/api/service/enter` | Enter YDNU-02 service terminal mode |
| `POST` | `/api/service/exit` | Exit YDNU-02 service terminal mode |
| `GET` | `/api/service/state` | Service mode state |

### Maintenance (`routes/maintenance.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `POST` | `/api/backup` | Create configuration backup |
| `GET` | `/api/backups` | List available backups |
| `GET` | `/api/backup/download/{filename}` | Download backup file |
| `POST` | `/api/reset/settings` | Reset settings to defaults |
| `POST` | `/api/reset/filters` | Reset CAN filters |
| `POST` | `/api/reset/mcu` | Software reset YDNU-02 MCU |
| `POST` | `/api/reset/hardware` | Hardware reset YDNU-02 |

### Firmware (`routes/firmware.py`)
| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/firmware/latest` | Latest available firmware info |
| `GET` | `/api/firmware/progress` | Ongoing flash progress |
| `POST` | `/api/firmware/download` | Download firmware from remote |
| `POST` | `/api/firmware/upload` | Upload firmware file |
| `POST` | `/api/firmware/flash/{filename}` | Flash firmware to YDNU-02 |
| `GET` | `/api/firmware/files` | List locally available firmware files |

### WebSockets
| Method | Endpoint | Description |
|:---|:---|:---|
| `WS` | `/ws/monitor` | Live CAN frame stream |
| `WS` | `/ws/scan` | Device discovery scan results |

## Deployment

### Target: Raspberry Pi 5 (`<gateway-host>`)

```bash
# Deploy all files
./deploy.sh

# Manual deploy
scp *.py user@gateway-host:/opt/nmea2000/ydnu02-web/
scp routes/*.py user@gateway-host:/opt/nmea2000/ydnu02-web/routes/
scp static/js/*.js user@gateway-host:/opt/nmea2000/ydnu02-web/static/js/

# Restart service
ssh user@gateway-host 'sudo systemctl restart ydnu02-web'
```

### Dependencies

- Python 3.13+
- `nmea2000` — NMEA 2000 protocol library (decode/encode)
- `fastapi` + `uvicorn` — web framework
- `pyserial` — serial port access
- `bleak` — BLE GATT client (Gobius configuration)
- `dbus-fast` — D-Bus access for BLE advertisements (Mopeka)

## Configuration Files

| File | Purpose |
|------|---------|
| `ydnu02-web.service` | Systemd unit — auto-start on boot |
| `docker-compose.yml` | Signal K server (optional, alternative to this console) |
| `setup_gateway.sh` | First-time Raspberry Pi setup (packages, permissions, venv) |
