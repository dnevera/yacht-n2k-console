# yacht-n2k-console

NMEA 2000 Web Console for Yacht Devices — a self-hosted web application for managing marine electronics over a CAN bus network.

## Overview

A FastAPI + WebSocket application that runs on a Raspberry Pi 5 and provides:

- **Device Discovery** — automatic detection of all NMEA 2000 devices on the CAN bus via ISO Address Claim (PGN 60928) and Product Information (PGN 126996)
- **Dynamic Configuration** — read and write device parameters using PGN 126208 Group Functions (Read/Write Fields, Command) with field metadata extracted dynamically from the `nmea2000` Python library
- **Tank Level Monitoring** — Gobius C (NMEA 2000 data + BLE configuration) and Mopeka Pro 200 (BLE) fluid sensor support
- **BLE Configuration Management** — centralized confirmation dialogs, change-detection DOM safety guards, dangerous action warnings (`adv_off`, `initialize`, N2K disabling)
- **Live Bus Monitor** — real-time CAN frame viewer via WebSocket
- **YDNU-02 Gateway Management** — serial protocol control, service mode, firmware updates

## Screenshots

| Dashboard | Monitor |
|:---:|:---:|
| ![Dashboard](screenshots/Screenshot%202026-07-30%20at%2017.08.13.png) | ![Monitor](screenshots/Screenshot%202026-07-30%20at%2017.08.24.png) |

| Network — Device Discovery | Gobius C — BLE Config |
|:---:|:---:|
| ![Network](screenshots/Screenshot%202026-07-30%20at%2017.08.36.png) | ![Gobius C](screenshots/Screenshot%202026-07-30%20at%2017.08.44.png) |

| Mopeka Pro 200 | Service Terminal |
|:---:|:---:|
| ![Mopeka](screenshots/Screenshot%202026-07-30%20at%2017.08.51.png) | ![Service](screenshots/Screenshot%202026-07-30%20at%2017.09.20.png) |

| Maintenance — Backup, Reset & Firmware |
|:---:|
| ![Maintenance](screenshots/Screenshot%202026-07-30%20at%2017.09.36.png) |



## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Raspberry Pi 5                                │
│                                                                        │
│  ┌────────────-─┐   ┌──────────────┐   ┌──────────────┐                │
│  │  FastAPI     │   │  Device      │   │  BLE         │                │
│  │  + WebSocket │◄──│  Manager     │◄──│  Poller /    │                │
│  │  (app.py)    │   │  (bus_worker)│   │  Scanner     │                │
│  └──────┬───────┘   └──────┬───────┘   │  (Mopeka/    │                │
│         │                  │           │   Gobius)    │                │
│         │      TCP :4001 / │ :4002     └──────────────┘                │
│         │           ┌──────┴─────────────────────┐                     │
│         │           │  TCP Proxy / Gateway       │                     │
│         │           │  (ydnu02_tcp_gateway.py)   │                     │
│         │           │  Holds /dev/ttyACM0 exclusively                  │
│         │           └─────────────┬──────────────┘                     │
└─────────┼─────────────────────────┼────────────────────────────────────┘
          │                         │
          ▼                         ▼
    ┌──────────┐             ┌─────────────┐
    │ Browser  │             │  NMEA 2000  │
    │ Web UI   │             │  CAN Bus    │
    └──────────┘             └──┬──────┬───┘
                                │      │
                          ┌─────┴┐  ┌──┴─────┐
                          │Tank  │  │Battery │
                          │Sensor│  │Monitor │
                          └──────┘  └────────┘
```

### TCP Gateway Architecture

The hardware serial port (`/dev/ttyACM0`) is managed **exclusively** by the standalone TCP Proxy (`ydnu02_tcp_gateway.py` / `ydnu02-tcp-gateway.service`).

| Port | Mode | Description |
|------|------|-------------|
| **`4001`** | **DATA** | Broadcasts `\n`-terminated NMEA 2000 ASCII frames to all connected TCP clients (`ydnu02-web`, Home Assistant, Signal K). Supports bidirectional writing for N2K bus commands. |
| **`4002`** | **CTRL** | Exclusive control channel for YDNU-02 service mode, serial passthrough, and firmware upload via `ProxyControlClient`. |

For complete documentation, DTR state machine details, and standalone deployment options, see [ydnu02_tcp_gateway/README.md](ydnu02_tcp_gateway/README.md).

## Hardware

| Component | Model | Interface |
|-----------|-------|-----------|
| USB-CAN Gateway | Yacht Devices YDNU-02 | USB Serial (`/dev/ttyACM0`) |
| Fluid Sensor | Gobius C | NMEA 2000 (telemetry) + BLE (configuration) |
| Tank Sensor | Mopeka Pro 200 | BLE Advertisement |
| Battery Monitor | Victron SmartShunt | NMEA 2000 |
| Solar Charger | Victron MPPT | NMEA 2000 |
| Host | Raspberry Pi 5 | — |

## Quick Start

### On the Raspberry Pi

```bash
# Initial setup
./setup_gateway.local.sh

# Run the service
python3 app.py --port 8080
```

### As a systemd service

```bash
sudo cp ydnu02-web.service /etc/systemd/system/
sudo systemctl enable --now ydnu02-web.service
```

### Access the Web UI

```
http://<raspberry-pi-ip>:8080
```

## API Endpoints

### Device Configuration (Dynamic)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/n2k/devices` | List all discovered N2K devices |
| `GET` | `/api/n2k/devices/{src}/config/{pgn}` | Read current field values from device |
| `POST` | `/api/n2k/devices/{src}/config/{pgn}` | Write fields, verify with read-back diff |
| `GET` | `/api/n2k/pgn/{pgn}/metadata` | Get field metadata (types, enums, units) |

### Gobius C BLE Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/gobius/status` | Full BLE sensor read (Status, Measurement, Config, Info) |
| `GET` | `/api/gobius/live` | Fast live telemetry poller status |
| `POST` | `/api/gobius/n2k` | Write N2K Config GATT `0xFFF2` (enable, instance, fluid type, volume) |
| `POST` | `/api/gobius/user_config` | Write User Config GATT `0xFFE6` (geometry & LP filters) |
| `POST` | `/api/gobius/info` | Write Tank Info GATT `0xFFEB/0xFFEC` (name & comment) |
| `POST` | `/api/gobius/command` | Send command GATT `0xFFE7` (start/stop/calibrate/initialize/write_info) |

### Sensors & Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/sensors` | All sensor readings |
| `GET` | `/api/dashboard/sensors` | Unified sensor cards with NMEA, BLE, and Registry channels |
| `GET` | `/api/info` | Gateway status and bus health |
| `POST` | `/api/mode/{mode}` | Set YDNU-02 operating mode |
| `POST` | `/api/silent/{state}` | Enable/disable silent mode |
| `WS` | `/ws/monitor` | Live CAN frame stream |
| `WS` | `/ws/scan` | Device discovery scan |

See [TECHNICAL.md](TECHNICAL.md) for full API reference and protocol details.

## Development

```bash
# Deploy changes to Raspberry Pi
./deploy.sh

# Run tests
python3 tests/run.py
```

## License

Private. All rights reserved.
