---
name: nmea2000-setup
description: >-
  Полное руководство и база знаний по настройке NMEA 2000 (Yacht Devices YDNU-02, Gobius C),
  Mopeka Pro 200 BLE, сервисного режима YDNU-02, Signal K Server на Raspberry Pi 5
  и интеграции с Home Assistant / Victron.
---

# NMEA 2000 + YDNU-02 + Gobius C + Mopeka BLE + Signal K (Raspberry Pi 5)

## 📁 Файлы проекта
- [docker-compose.yml](file:///Users/denn/Develop/3dprint/ha/nmea2000/docker-compose.yml) — Docker Compose стек Signal K.
- [README.md](file:///Users/denn/Develop/3dprint/ha/nmea2000/README.md) — Инструкция по настройке и диагностике.
- [ydnu02.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/ydnu02.py) — Python CLI-контроллер YDNU-02.

---

## 🔌 Оборудование и физический слой NMEA 2000

### Параметры шины
- **Сопротивление:** 60 Ом между CAN-H и CAN-L (два терминатора по 120 Ом).
- **Питание:** 12V DC через Power Tee. CAN-трансиверы всех устройств запитываются от шины.

### Yacht Devices YDNU-02 USB Gateway
- **Linux порт:** `/dev/ttyACM0` (CDC ACM виртуальный COM-порт).
- **VID:** `0x0483` (STMicroelectronics), **PID:** `0xA217`.
- USB-часть запитана от хоста, CAN-трансивер — от 12V шины N2K (гальваническая изоляция 2500 VRMS).
- Скорость порта (baud rate) **не имеет значения** для USB CDC ACM — это виртуальный порт.

### Gobius C NMEA 2000
- **Модель (GATT):** 970534 (NMEA 2000, чёрный корпус), HW: V4, FW: 4.1.0, Serial: 697207
- **BLE Name:** `GOBIUS C`, **MAC:** `2C:A7:74:21:56:D8`
- **Manufacturer:** Gobius Sensor Tech (BLE Manufacturer ID: `0x0F53`)
- **N2K PGN 127505:** Source: 92, Instance: 0, ~2.5s interval, Level: ~80%, Cap: 20L

#### ⚠️ Ключевая проблема PGN 127505 fluid_type
**Прошивка Gobius C** PGN 127505 byte[0] **всегда передаёт 0x00 (Fuel)**, независимо от настройки FFF2.
Это **баг прошивки**. Workaround: при сохранении конфигурации через BLE, `fluid_type` записывается
в registry как **user-configured override**. Dashboard использует этот override вместо значения из NMEA PGN.
**BLE — это ТОЛЬКО настройка. NMEA 2000 — ВСЕГДА основной источник данных.**

#### BLE GATT Map (Protocol Spec Issue 3, 2023-08-08)

| UUID | Имя | R/W | Описание |
|:---|:---|:---|:---|
| `0xFFE6` | **User Config** | RW | distance_empty_mm, distance_full_mm, LP filter |
| `0xFFE7` | **Command** | W | calibrate, start/stop, adv, initialize, write_info |
| `0xFFE8` | **Status** | R | temp, voltage, MAC, uptime, range, state |
| `0xFFE9` | **Measurement** | R | fill_pct (‰), distance_mm, inclination_deg |
| `0xFFEB/EC` | Info 1/2 | RW | Имя бака / комментарий (ASCII, 20 chars) |
| `0xFFF2` | **N2K Config** | RW | N2K enable, instance, fluid type, volume |
| `0xFFF3` | **N2K Status** | R | n2k_state, n2k_src |

#### 0xFFE8 Status (20 bytes)
```
Byte  0:    State (5=Measuring, 4=Standby, 3=Calibrate, 2=Sleep, 1=Advertise)
Byte  1:    Status bits (bit 0=measuring, bit 1=valid_range)
Byte  2:    Current range (0=Zero, 1=Near, 2=Ext1, 3=Ext2)
Byte  3:    Error code (0=OK)
Byte  4:    Temperature °C (signed)
Byte  5-6:  Voltage (big-endian, raw → voltage_v = raw * 3.6 * 3.324 / 4096)
Byte  7-12: MAC address (6 bytes)
Byte 13:    Measuring flag (0/1)
Byte 14-17: Uptime (big-endian, seconds)
```

#### 0xFFE9 Measurement (20 bytes)
```
Byte  0-1:  Fill level ‰ (big-endian, divide by 10 → %)
Byte  2:    Level valid (0/1)
Byte  3-4:  Distance mm (big-endian)
Byte  5:    Inclination degrees
```

#### 0xFFE6 User Config (20 bytes)
```
Byte  0-1:  Distance empty mm (big-endian)
Byte  2-3:  Distance full mm (big-endian)
Byte  4:    LP filter N (default 3)
Byte  5:    LP filter K (default 10-21)
```
**Geometry:** tank_depth = distance_empty + distance_full.
**Fill%:** computed = (distance_empty - distance_mm) / (distance_empty - distance_full) × 100.

#### 0xFFE7 Command
```
0x10=Calibrate  0x20=Initialize  0x30=Start  0x40=Stop
0x50=Adv Normal  0x60=Adv Off  0x70=Write Info
```

#### 0xFFF2 N2K Config (20 bytes)
```
Byte 0: N2K enable (0=off, 1=on)
Byte 1: Fluid Instance (0-15)
Byte 2: Fluid Type (0=Fuel, 1=Fresh Water, 2=Waste, 3=Live Well, 4=Oil, 5=Black Water)
Byte 9: Volume (литры, uint8, max 255L)
```

#### 0xFFF3 N2K Status
```
Byte 0: N2K state (2=active)
Byte 1: Source address (e.g. 92)
```

### Mopeka Pro 200 (BLE)
- **MAC:** `F1:FD:CB:6C:B2:CC`, **Type:** Pro 200 (top-down ultrasonic)
- **BLE Manufacturer ID:** `0x0059` (Nordic Semiconductor)
- **Hardware bit:** `0x04` in byte[0] → Pro 200
- **Протокол:** Passive advertisement only (no GATT connection)
- **Payload:** 10 bytes manufacturer data

#### Advertisement Payload (10 bytes)
```
Byte 0:    Hardware ID (0x04 = Pro 200)
Byte 1:    Battery voltage raw (voltage_v = (raw / 32.0) * 2.0 + 1.5)
Byte 2:    Temperature raw (temp_c = raw - 40)
           bit 7: sync button pressed
Byte 3-4:  Time-of-Flight μs (little-endian, 14 bits)
           top 2 bits of byte 4: quality (0-3 stars)
Byte 5:    Distance mm (computed: tof_us * 0.06875)
Byte 6-7:  Accelerometer X, Y
Byte 8-9:  Reserved
```

#### Geometry (top-down)
- Sensor measures **air gap** (distance from top to liquid surface)
- `fill_level_pct = ((tank_depth - distance_mm) / tank_depth) × 100`
- Tank depth and capacity stored in local `mopeka_config.json` (not on sensor)

---

## 🏗️ Sensor Architecture

### ⚠️ ФУНДАМЕНТАЛЬНОЕ ПРАВИЛО: Роли каналов

| Канал | Роль | Что делает |
|:------|:-----|:-----------|
| **NMEA 2000** | **ОСНОВНОЙ источник данных** | fill_level, capacity, live telemetry для dashboard |
| **BLE** | **ТОЛЬКО НАСТРОЙКА** | Подключился → записал конфиг → отключился |
| **Registry** | **User-configured overrides** | Хранит настройки пользователя (fluid_type, capacity, name) |

**BLE — это НЕ источник данных.** BLE используется ТОЛЬКО для записи настроек в сенсор.
Dashboard ВСЕГДА показывает данные из NMEA 2000. Если прошивка сенсора багует (как Gobius fluid_type=0),
workaround — хранить user-configured override в registry, а НЕ читать из BLE.

**NMEA НИКОГДА не зависит от BLE. BLE НИКОГДА не зависит от NMEA.**

```python
# sensors/base_sensor.py
@dataclass NMEAData:  fill_level_pct, capacity_l, calculated_l, fluid_type_code/name, src
@dataclass BLEData:   temp_c, voltage_v, fill_pct, distance_mm, volume_l, fluid_type_code/name, ...

class BaseSensor:
    self.nmea = NMEAData()
    self.ble = BLEData()
    # to_dict() → flat compat + nested nmea{}/ble{} structures
```

### Parsers
- Gobius BLE парсеры: [gobius_parsers.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/gobius_parsers.py)
- Mopeka BLE парсеры: [mopeka_parsers.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/mopeka_parsers.py)

### API
```
GET  /api/sensors               → {fluid_levels: [{...to_dict()...}]}
GET  /api/gobius/status          → BLE connect + read all chars
POST /api/gobius/n2k             → Write 0xFFF2
POST /api/gobius/user_config     → Write 0xFFE6
POST /api/gobius/info            → Write 0xFFEB/FFEC
POST /api/gobius/command         → Write 0xFFE7
GET  /api/mopeka/sensors         → {sensors: [{mac, fill_level_pct, distance_mm, ...}]}
GET  /api/mopeka/sensor/{mac}    → single sensor dict
POST /api/mopeka/config/{mac}    → {name, tank_depth_mm, capacity_l, fluid_type}
DELETE /api/mopeka/sensor/{mac}  → remove sensor
```

### Tests
```bash
PYTHONPATH=. python3 tests/test_sensors_service.py -v    # 15 sensor tests
PYTHONPATH=. python3 tests/test_gobius_parsers.py        # 14 parser tests
PYTHONPATH=. python3 tests/test_mopeka_parsers.py -v     # 6 parser tests
```

---

## 💻 Двухуровневая система команд YDNU-02

### ⚠️ КРИТИЧЕСКИ ВАЖНО
У YDNU-02 **ДВА ОТДЕЛЬНЫХ УРОВНЯ** управления:
1. **OS Shell** — `echo "YDNU MODE ..." > /dev/ttyACM0`
2. **Service Menu** — только после `YDNU MODE SERVICE`, через `screen /dev/ttyACM0`

#### OS Shell команды

| Команда | Эффект | EEPROM |
|:---|:---|:---|
| `YDNU MODE AUTO/0183/RAW/N2K` | Смена режима | ✅ |
| `YDNU MODE SERVICE` | Вход в сервисный режим | ❌ |
| `YDNU SILENT ON/OFF` | Silent mode | ✅ |

**⚠️ Обязательно:** `stty -F /dev/ttyACM0 hupcl` перед отправкой.

#### Service Menu команды
`HELP`, `MODE`, `FILTER`, `SET`, `RESET SETTINGS/FILTERS`, `DIAG ALL/USB_RX/USB_TX/N2K_RX/N2K_TX`

---

## 📡 RAW Mode (Appendix E)
```
Incoming: hh:mm:ss.ddd R msgid b0 b1 ... b7<CR><LF>
Outgoing: msgid b0 b1 ... b7<CR><LF>   (без timestamp!)
```

---

## 🌐 YDNU-02 Web Console

### Архитектура
```
ha/nmea2000/
├── app.py                  # FastAPI + uvicorn + MopekaScanner lifecycle
├── device_manager.py       # Thread-safe YDNU02 wrapper (3 patterns)
├── ydnu02.py               # Hardware controller
├── gobius_parsers.py        # Gobius BLE byte parsers (protocol spec Issue 3)
├── mopeka_parsers.py        # Mopeka advertisement parser (10 bytes)
├── mopeka_scanner.py        # Background bleak passive BLE scanner
├── mopeka_config.json       # Tank config (depth, capacity, fluid_type per MAC)
├── sensors/
│   ├── base_sensor.py       # NMEAData + BLEData dataclasses, BaseSensor
│   ├── gobius_sensor.py     # GobiusCSensor — BLE update methods
│   └── mopeka_sensor.py     # MopekaSensor + MopekaAdvData
├── routes/
│   ├── device.py            # /api/info, /api/mode, /api/silent
│   ├── service.py           # /api/service/cmd|enter|exit|state|filters|diag
│   ├── maintenance.py       # /api/backups, /api/backup, /api/reset/*
│   ├── firmware.py          # /api/firmware/latest|upload|download|flash|progress
│   ├── gobius.py            # /api/gobius/status|n2k|user_config|info|command
│   ├── mopeka.py            # /api/mopeka/sensors|sensor|config
│   ├── sensors.py           # /api/sensors
│   └── websockets.py        # WS /ws/monitor, /ws/scan
├── static/
│   ├── index.html           # SPA, 7 tabs
│   ├── css/style.css        # Dark theme + .panel.busy overlay spinner
│   └── js/
│       ├── core.js           # App, api(), withButton, setFields/loadInputs/readInputs
│       ├── dashboard.js      # refreshInfo, setMode, setSilent
│       ├── monitor.js        # WS CAN bus live
│       ├── scan.js           # WS device scanner
│       ├── service.js        # Terminal, filters, diag
│       ├── maintenance.js    # Backups, reset, firmware OTA
│       ├── gobius.js         # Gobius BLE tab (uses setFields/loadInputs)
│       └── mopeka.js         # Mopeka BLE tab (uses setFields/loadInputs/readInputs)
├── tests/
│   ├── test_sensors_service.py  # 15 sensor tests
│   ├── test_gobius_parsers.py   # 14 parser tests
│   └── test_mopeka_parsers.py   # 6 parser tests
├── deploy.sh                # scp + systemctl restart + healthcheck
└── ydnu02-web.service       # systemd unit
```

### Деплой
```bash
cd /Users/denn/Develop/3dprint/ha/nmea2000 && ./deploy.sh
```

### Frontend Pattern: withButton + Busy Overlay
```javascript
// core.js — все кнопки используют этот паттерн:
withButton(btnEl, label, asyncFn)
  → btn.closest('.panel').classList.add('busy')   // CSS overlay + spinner
  → all controls disabled (pointer-events:none)
  → on success: btn.result-ok (green flash 1.5s)
  → on error: btn.result-err (red flash 2s)
  → finally: remove .busy

// HTML: onclick="App.someMethod(this)"  ← передаёт кнопку
```

### Frontend Pattern: Shared Sensor Helpers (core.js)
```javascript
// Read-only fields — update textContent by ID map
App.setFields({'mop-temp': '25°C', 'mop-fill': '77.9%'})

// Config inputs — load values ONCE, never overwrite on auto-refresh
App.loadInputs({'mop-depth': 500, 'mop-cap': 20}, '_mopekaConfigLoaded')

// Read form values for POST — typed (int/float/str/bool)
App.readInputs({tank_depth_mm: {id:'mop-depth', type:'float'}})
// → {tank_depth_mm: 500.0}
```
**⚠️ ПРАВИЛО:** Gobius и Mopeka используют одни и те же хелперы. Не дублировать `getElementById` цепочки.

### DeviceManager Patterns

| Паттерн | Использование |
|:---|:---|
| `_service_operation(func)` | lock → enter service → func → exit |
| `_locked_operation(func)` | lock → func (OS commands) |
| `_raw_locked_operation(func)` | lock → func (reset/firmware) |

### Firmware OTA
1. `GET /api/firmware/latest` → парсит yachtd.com
2. `POST /api/firmware/download` → ZIP → .BIN
3. `POST /api/firmware/flash/{filename}` → validate + backup + chunked write
4. `GET /api/firmware/progress` → polling

---

## 🐳 Signal K в Docker
```bash
cd ~/ha/nmea2000 && docker compose up -d
```
Веб: `http://192.168.68.56:3000`
