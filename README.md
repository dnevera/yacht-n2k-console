# yacht-n2k-console

NMEA 2000 Web Console for Yacht Devices — a self-hosted web application for managing marine electronics over a CAN bus network.

## Overview

A FastAPI + WebSocket application that runs on a Raspberry Pi 5 and provides:

- **Device Discovery** — automatic detection of all NMEA 2000 devices on the CAN bus via ISO Address Claim (PGN 60928) and Product Information (PGN 126996)
- **Dynamic Configuration** — read and write device parameters using PGN 126208 Group Functions (Read/Write Fields, Command) with field metadata extracted dynamically from the `nmea2000` Python library
- **Tank Level Monitoring** — Gobius C (NMEA 2000 + BLE) and Mopeka Pro 200 (BLE) fluid sensor support
- **Live Bus Monitor** — real-time CAN frame viewer via WebSocket
- **YDNU-02 Gateway Management** — serial protocol control, service mode, firmware updates

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5                        │
│                                                          │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │  FastAPI     │   │  Device      │   │  BLE         │  │
│  │  + WebSocket │◄──│  Manager     │◄──│  Scanner     │  │
│  │  (app.py)    │   │  (bus_worker)│   │  (Mopeka/    │  │
│  └──────┬───────┘   └──────┬───────┘   │   Gobius)    │  │
│         │                  │           └──────────────┘  │
│         │           ┌──────┴───────┐                     │
│         │           │  YDNU-02     │                     │
│         │           │  Serial Port │                     │
│         │           │  /dev/ttyACM0│                     │
│         │           └──────┬───────┘                     │
└─────────┼──────────────────┼─────────────────────────────┘
          │                  │
          ▼                  ▼
    ┌──────────┐      ┌─────────────┐
    │ Browser  │      │  NMEA 2000  │
    │ Web UI   │      │  CAN Bus    │
    └──────────┘      └──┬──────┬───┘
                         │      │
                    ┌────┴┐  ┌──┴─────┐
                    │Tank │  │Battery │
                    │Sensor│  │Monitor │
                    └─────┘  └────────┘
```

## Hardware

| Component | Model | Interface |
|-----------|-------|-----------|
| USB-CAN Gateway | Yacht Devices YDNU-02 | USB Serial (`/dev/ttyACM0`) |
| Fluid Sensor | Gobius C | NMEA 2000 + BLE |
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

### Sensors & Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/sensors` | All sensor readings |
| `GET` | `/api/device/status` | Gateway status and bus health |
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
