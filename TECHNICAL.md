# Technical Reference

## Project Structure

```
yacht-n2k-console/
├── app.py                  # FastAPI application entry point
├── device_manager.py       # CAN bus worker thread, device discovery, sensor state
├── ydnu02.py               # YDNU-02 serial protocol, N2KPGNDecoder, service mode
├── n2k_meta.py             # Dynamic PGN field metadata extraction from nmea2000 lib
├── n2k_command_builder.py  # Legacy hardcoded command builder (deprecated by n2k_meta)
│
├── routes/
│   ├── __init__.py         # Lazy singleton getters (avoid circular imports)
│   ├── device.py           # GET /api/device/status, /api/sensors
│   ├── n2k_config.py       # Dynamic device config API (PGN 126208)
│   ├── n2k.py              # Legacy N2K command endpoint
│   ├── gobius.py           # Gobius C sensor management
│   ├── mopeka.py           # Mopeka Pro BLE sensor management
│   ├── ble.py              # BLE device registry
│   ├── service.py          # YDNU-02 service mode, I/O pause
│   ├── maintenance.py      # Factory reset, diagnostics
│   ├── firmware.py         # Firmware update flow
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
├── static/
│   ├── index.html          # Shell with tab navigation
│   ├── css/style.css       # Dark theme UI styles
│   ├── js/
│   │   ├── core.js         # App object, API helpers, toast, WebSocket
│   │   ├── dashboard.js    # Dashboard tab — sensor cards, auto-refresh
│   │   ├── network.js      # Network tab — CAN bus scan, device cards
│   │   ├── n2k_config.js   # Dynamic N2K config modal (from API metadata)
│   │   ├── monitor.js      # Monitor tab — live CAN frame viewer
│   │   ├── gobius.js       # Gobius tab — BLE pairing, calibration
│   │   ├── mopeka.js       # Mopeka tab — BLE scan, binding
│   │   ├── service.js      # Service tab — YDNU-02 terminal
│   │   └── maintenance.js  # Maintenance tab — reset, diagnostics
│   └── tabs/               # HTML fragments loaded into tab sections
│
├── tests/                  # API and unit tests
├── deploy.sh               # SCP deploy to gateway.local + service restart
├── build_bundle.sh         # Build tarball for offline deployment
├── setup_gateway.local.sh      # Initial Raspberry Pi setup script
├── ydnu02-web.service      # Systemd service unit file
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

### device_manager.py — Bus Worker & Discovery

- Owns the serial port via `_bus_worker` daemon thread
- Parses raw CAN frames using `N2KPGNDecoder` (from `ydnu02.py`)
- Maintains `_discovered_bus_devices` dict keyed by source address
- Uses `N2KPGNDecoder.parse_device_info()` for library-based manufacturer/model resolution
- Tracks `active_pgns` per device for dynamic config UI
- Manages sensor instances (`GobiusCSensor`) updated from PGN 127505
- Thread-safe via `_lock` (serial access) and `_sensors_lock` (state)

### ydnu02.py — YDNU-02 Protocol

- `N2KPGNDecoder`: CAN frame parsing, device info extraction, PGN decode
- `YDNU02Controller`: serial port management, read/write, service mode commands
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

## BLE Integration

### Gobius C

- **NMEA 2000**: Publishes PGN 127505 (Fluid Level) on the CAN bus
- **BLE GATT**: Configuration only — calibration, fluid type, instance assignment
- BLE poller connects periodically to read calibration state and sensor diagnostics
- Tank level data comes exclusively from NMEA 2000

### Mopeka Pro 200

- **BLE only**: Broadcasts tank level via BLE advertisements (no NMEA 2000)
- Scanner runs continuously, parsing advertisement payloads
- Supports ultrasonic and pressure-based tank measurement

## Deployment

### Target: Raspberry Pi 5 (`gateway.local`)

```bash
# Deploy all files
./deploy.sh

# Manual deploy
scp *.py user@<gateway-host>:/opt/nmea2000/ydnu02-web/
scp routes/*.py user@<gateway-host>:/opt/nmea2000/ydnu02-web/routes/
scp static/js/*.js user@<gateway-host>:/opt/nmea2000/ydnu02-web/static/js/

# Restart service
ssh user@<gateway-host> 'sudo systemctl restart ydnu02-web'
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
| `setup_gateway.local.sh` | First-time Raspberry Pi setup (packages, permissions, venv) |
