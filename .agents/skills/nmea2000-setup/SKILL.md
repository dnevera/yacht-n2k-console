---
name: nmea2000-setup
description: >-
  Полное руководство и база знаний по проекту yacht-n2k-console:
  TCP прокси архитектура (nmea_tcp_proxy.py), YDNU-02, Gobius C, Mopeka Pro 200 BLE,
  TCPProxyConnection, ProxyControlClient, DeviceManager, IO Stop/Resume,
  деплой на Raspberry Pi 5 (gateway.local), HA интеграция.
---

# yacht-n2k-console — NMEA 2000 Web Console

## Расположение проекта

- **Локально:** `/Users/denn/Develop/yacht/yacht-n2k-console/`
- **На gateway.local:** `/opt/nmea2000/ydnu02-web/`
- **Сервис:** `ydnu02-web.service` (systemd, port 8080)
- **Деплой:** `cd /Users/denn/Develop/yacht/yacht-n2k-console && ./deploy.sh`
- **URL:** `http://gateway.local:8080`

---

## TCP Proxy Архитектура

### ГЛАВНОЕ ПРАВИЛО: только прокси держит /dev/ttyACM0

```
YDNU-02 /dev/ttyACM0
       |
  nmea_tcp_proxy.py  (systemd: nmea-tcp-proxy.service)
       |-- :4001  DATA port   -> broadcast NMEA строк всем клиентам
       |          <- принимает ISO Request команды от клиентов (scan_bus)
       +-- :4002  CTRL port   -> эксклюзивный serial passthrough (service/firmware)
       |
       |-- HA homeassistant container (nmea2000 custom integration -> :4001)
       +-- ydnu02-web DeviceManager._bus_worker (TCPProxyConnection -> :4001)
                                                (ProxyControlClient -> :4002)
```

**Никто кроме прокси не открывает `/dev/ttyACM0` напрямую.**

### nmea_tcp_proxy.py — ключевые детали

**Env vars:**
- `NMEA_SERIAL_PORT` (default `/dev/ttyACM0`)
- `NMEA_SERIAL_BAUD` (default `115200`)
- `NMEA_PROXY_HOST` (default `""` = all interfaces)
- `NMEA_PROXY_PORT` (default `4001`) — DATA
- `NMEA_CTRL_PORT` (default `4002`) — CTRL

**Init при старте прокси (serial_reader thread):**
```python
ser.write(b"YDNU MODE RAW\r\n")  # переключение в RAW mode
time.sleep(2.0)
ser.read(ser.in_waiting)          # flush echo
ser.write(b"0\n")                 # сбрасываем фильтры
time.sleep(0.5)
ser.read(ser.in_waiting)          # flush echo
```

**NMEA frame filter (ОБЯЗАТЕЛЬНЫЙ):**
```python
_NMEA_LINE_RE = re.compile(
    rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"
)
# Только строки совпадающие с этим regex идут в broadcast.
# Текстовые ответы YDNU-02 (init echo, mode-switch ack) — отбрасываются.
```

**КРИТИЧЕСКИ ВАЖНО:** Без этого фильтра init-эхо YDNU-02 попадает в broadcast,
HA библиотека получает нечитаемые строки и спинится на 100% CPU.

**Control API (:4002) протокол:**
```
Client -> Proxy:  SERVICE_START\n    -> Proxy pauses broadcast -> ответ READY\n
Client -> Proxy:  <serial commands>  -> Proxy forwards to serial (passthrough)
Proxy  -> Client: <serial responses> -> from serial to client
Client -> Proxy:  SERVICE_END\n      -> Proxy resumes broadcast -> ответ OK\n

FIRMWARE_START / FIRMWARE_END — alias для SERVICE_START/END
```

---

## TCPProxyConnection и ProxyControlClient

### TCPProxyConnection (device_manager.py)

```python
class TCPProxyConnection:
    """DATA port (:4001) — читает NMEA broadcast от прокси."""

    def connect(self) -> None:
        """Открывает TCP; raises ConnectionRefusedError если прокси не запущен."""

    def readline(self) -> str:
        """
        Читает одну \n-terminated NMEA строку.
        Returns "" на socket.timeout (нормально — шина медленная ~2.5s/frame).
        Raises ConnectionResetError если прокси закрыл соединение (рестарт прокси).
        Использует makefile("rb") для буферизированного readline().
        """

    def write(self, data: bytes) -> None:
        """Отправляет ISO Request команды через DATA port (для scan_bus)."""

    @property
    def is_connected(self) -> bool: ...
```

### ProxyControlClient (device_manager.py)

```python
class ProxyControlClient:
    """CTRL port (:4002) — эксклюзивный passthrough для service/firmware операций."""

    def enter_service(self) -> None:
        """Sends SERVICE_START -> прокси паузирует broadcast -> READY."""

    def exit_service(self) -> None:
        """Sends SERVICE_END -> прокси возобновляет broadcast."""

    def enter_firmware(self) -> None:
        """Alias — sends FIRMWARE_START."""

    def exit_firmware(self) -> None:
        """Sends FIRMWARE_END."""

    def passthrough_write(self, data: bytes) -> None:
        """Пишет в serial через passthrough (используется YDNU02Controller._write())."""

    def passthrough_readline(self, timeout: float = 3.0) -> str:
        """
        Читает строку через passthrough.
        ВАЖНО: Использует raw socket.recv() а НЕ makefile().readline()
        — после socket.timeout makefile входит в broken state (Python баг
        "cannot read from timed out object").
        Returns "" на timeout.
        """

    def passthrough_read_for(self, duration: float) -> str:
        """Читает все строки за duration секунд. Используется _read_response()."""
```

---

## DeviceManager

### Архитектура

```
DeviceManager
  _bus_worker (thread)
    TCPProxyConnection._tcp -> :4001 (DATA)
      readline() -> N2KPGNDecoder.parse_raw_line()
        -> _update_sensor_state()   (PGN 127505, 60928)
        -> _broadcast_frame()       (WS monitor queues)

  _service_operation / _locked_operation / _raw_locked_operation
    1. _pause_event.SET()    <- bus worker exits inner loop
    2. sleep(0.2)            <- worker finishes readline()
    3. ProxyControlClient.enter_service()
    4. _lock.acquire()
    5. ctrl._passthrough = pcc  <- прозрачный passthrough
    6. func(ctrl)               <- YDNU02Controller methods
    7. pcc.exit_service()   (finally)
    8. _pause_event.CLEAR() (finally) -> worker reconnects
```

### Состояния DeviceManager._state

| Состояние | Описание |
|:---|:---|
| `IDLE` | Не подключён к прокси |
| `LISTENING` | Читает NMEA frames из :4001 |
| `SERVICE` | В service mode (enter_service вызван вручную) |
| `NO_DEVICE` | Прокси недоступен (CONNECTION_REFUSED) |
| `STOPPED` | IO остановлен через /api/io/pause |

### Три паттерна операций

| Паттерн | Использование |
|:---|:---|
| `_service_operation(func)` | enter service -> func(ctrl) с YDNU terminal -> exit |
| `_locked_operation(func)` | enter service -> func(ctrl) OS shell команды -> exit |
| `_raw_locked_operation(func)` | enter service -> func(ctrl) без авто-exit (MCU reset, firmware) |

Все три: `_pause_event.SET -> sleep(0.2) -> pcc.enter_service() -> _lock -> ctrl._passthrough=pcc -> func -> pcc.exit_service() -> _pause_event.CLEAR()` (в finally).

### IO Stop/Resume

```python
# routes/service.py
_IO_STATE_FILE = "/opt/nmea2000/ydnu02-web/io_state.json"  # persisted

# POST /api/io/pause:
dm.stop_bus_worker() + gobius.stop() + mopeka.stop()
dm._state = "STOPPED"
_save_io_state(True)   # {"paused": true}

# POST /api/io/resume:
mopeka.start() + gobius.start() + dm.start_bus_worker()
_save_io_state(False)

# GET /api/io/state -> {paused, port, serial, gobius, mopeka}
```

**При рестарте сервиса:** `app.py lifespan` читает `io_state.json` через `is_io_paused()`.
Если `paused=True` — `start_bus_worker()` / `mopeka.start()` / `gobius.start()` **не вызываются**.

**Гарды:** `routes/device.py` и `routes/service.py` проверяют `is_io_paused()` перед каждой операцией -> возвращают 503 если IO остановлен.

---

## Bus worker reconnect логика

```
outer loop:
  1. if _pause_event.is_set() -> sleep(0.1), continue
  2. if not tcp.is_connected:
       tcp.connect() с exponential backoff (1s -> 2s -> 4s -> ... -> 30s max)
       state = "LISTENING"
  3. inner loop (while running and not paused):
       line = tcp.readline()
       if line == "" -> continue (timeout, bus тихий — нормально)
       parse -> _update_sensor_state + _broadcast_frame
     on ConnectionResetError/OSError -> tcp.close(), state=IDLE, sleep(1s)
  4. while paused: sleep(0.1)  # ждём unpause -> идём в step 1 -> reconnect
```

**После прокси рестарта:** bus worker детектирует `ConnectionResetError`, переподключается через backoff.
HA docker тоже надо рестартить (HA lib spin loop баг).

---

## HA nmea2000 Library — CPU Spin Loop Bug

**Симптом:** `python3 -m homeassistant 100%+ CPU`
HA log: `decoding failed. Invalid CAN Frame ASCII string format`

**Причина:** Баг в `nmea2000` lib v2026.5.2 (`ioclient.py`).
При EOF (`b""`) или decode exception — немедленный return без sleep -> infinite spin.

**Когда:** прокси рестартовал -> TCP соединение разорвано -> HA получает EOF -> spin forever.

**ОБЯЗАТЕЛЬНЫЙ restart sequence:**
```bash
sudo systemctl restart nmea-tcp-proxy
sudo docker restart homeassistant   # <- ОБЯЗАТЕЛЬНО после КАЖДОГО рестарта прокси

# Проверить через 60 сек:
ssh user@<gateway-host> 'ps aux --sort=-%cpu | head -4 && ss -tnp | grep 4001'
# HA должна быть <10% CPU и ESTAB соединение на :4001
```

---

## Оборудование

### YDNU-02 USB Gateway
- **Linux порт:** `/dev/ttyACM0` (CDC ACM, USB VID `0x0483` / PID `0xA217`)
- Baud rate **не важен** для USB CDC ACM (виртуальный порт)
- ~0.4 фрейма/сек на тихой шине, ~2.5s interval от Gobius C
- **Шина:** 60 Ом (два терминатора 120 Ом), 12V Power Tee

### Gobius C NMEA 2000
- **BLE Name:** `GOBIUS C`, **MAC:** `2C:A7:74:21:56:D8`
- **Model:** 970534, HW: V4, FW: 4.1.0, Serial: 697207
- **N2K PGN 127505:** SRC 92, Instance 0, ~2.5s interval

**Ключевая проблема fluid_type:**
Прошивка Gobius C: PGN 127505 byte[0] **всегда 0x00 (Fuel)** — баг прошивки.
Workaround: `fluid_type` хранится в `ble_registry.py` как user-configured override.
**BLE — ТОЛЬКО настройка. NMEA 2000 — ВСЕГДА основной источник данных.**

#### BLE GATT Map (Protocol Spec Issue 3, 2023-08-08)

| UUID | Имя | R/W | Описание |
|:---|:---|:---|:---|
| `0xFFE6` | **User Config** | RW | distance_empty_mm, distance_full_mm, LP filter |
| `0xFFE7` | **Command** | W | calibrate, start/stop, adv, initialize, write_info |
| `0xFFE8` | **Status** | R | temp, voltage, MAC, uptime, range, state |
| `0xFFE9` | **Measurement** | R | fill_pct (permille), distance_mm, inclination_deg |
| `0xFFEB/EC` | Info 1/2 | RW | Имя бака / комментарий (ASCII, 20 chars) |
| `0xFFF2` | **N2K Config** | RW | N2K enable, instance, fluid type, volume |
| `0xFFF3` | **N2K Status** | R | n2k_state, n2k_src |

0xFFE8 Status (20 bytes):
```
Byte 0:    State (5=Measuring, 4=Standby, 3=Calibrate, 2=Sleep, 1=Advertise)
Byte 4:    Temperature C (signed)
Byte 5-6:  Voltage (big-endian, raw -> voltage_v = raw * 3.6 * 3.324 / 4096)
Byte 7-12: MAC address (6 bytes)
Byte 14-17: Uptime (big-endian, seconds)
```

0xFFE9 Measurement (20 bytes):
```
Byte 0-1:  Fill level permille (big-endian, /10 -> %)
Byte 3-4:  Distance mm (big-endian)
Byte 5:    Inclination degrees
```

0xFFE6 User Config (20 bytes):
```
Byte 0-1:  Distance empty mm (big-endian)
Byte 2-3:  Distance full mm (big-endian)
Byte 4:    LP filter N (default 3)
Byte 5:    LP filter K (default 10-21)
```
Geometry: `tank_depth = distance_empty + distance_full`
Fill%: `(distance_empty - distance_mm) / (distance_empty - distance_full) * 100`

0xFFF2 N2K Config (20 bytes):
```
Byte 0: N2K enable (0=off, 1=on)
Byte 1: Fluid Instance (0-15)
Byte 2: Fluid Type (0=Fuel, 1=Fresh Water, 2=Waste, 3=Live Well, 4=Oil, 5=Black Water)
Byte 9: Volume (litres, uint8, max 255L)
```

0xFFE7 Command:
```
0x10=Calibrate  0x20=Initialize  0x30=Start  0x40=Stop
0x50=Adv Normal  0x60=Adv Off  0x70=Write Info
```

### Mopeka Pro 200 (BLE)
- **MAC:** `F1:FD:CB:6C:B2:CC`, passive advertisement only
- **BLE Manufacturer ID:** `0x0059` (Nordic Semiconductor), HW bit `0x04`
- `fill_level_pct = ((tank_depth - distance_mm) / tank_depth) * 100`
- Tank config в `ble_sensors.json` (на gateway.local, НЕ на сенсоре)

Advertisement Payload (10 bytes):
```
Byte 0:   Hardware ID (0x04 = Pro 200)
Byte 1:   Battery raw (voltage_v = raw / 32.0 * 2.0 + 1.5)
Byte 2:   Temperature raw (temp_c = raw - 40), bit7 = sync button
Byte 3-4: ToF us little-endian 14 bits; top 2 bits = quality (0-3)
Byte 5:   Distance mm (tof_us * 0.06875)
Byte 6-7: Accelerometer X, Y
```

---

## Структура проекта

```
yacht-n2k-console/
├── nmea_tcp_proxy.py     # ПРОКСИ: держит /dev/ttyACM0, broadcast :4001, ctrl :4002
├── device_manager.py     # TCPProxyConnection + ProxyControlClient + DeviceManager
├── ydnu02.py             # YDNU02Controller (serial protocol, passthrough поддержка)
├── n2k_meta.py           # PGN metadata из nmea2000 lib, frame builders (PGN 126208)
├── n2k_command_builder.py# PGN 126208 Group Function command builder
├── ble_registry.py       # BLE sensor registry (сохраняет в ble_sensors.json)
├── gobius_ble_poller.py  # GobiusBLEPoller — persistent GATT connection, polling
├── gobius_parsers.py     # Gobius BLE byte parsers (Protocol Spec Issue 3)
├── mopeka_scanner.py     # MopekaScanner — passive BLE advertisement scanner
├── mopeka_parsers.py     # Mopeka advertisement parser (10 bytes)
├── app.py                # FastAPI + lifespan + IO Stop/Resume startup guard
├── models.py             # Pydantic models (CmdRequest)
├── sensors/
│   ├── base_sensor.py    # NMEAData + BLEData dataclasses, BaseSensor
│   ├── gobius_sensor.py  # GobiusCSensor
│   └── mopeka_sensor.py  # MopekaSensor + MopekaAdvData
├── routes/
│   ├── __init__.py       # get_device_mgr / get_mopeka_scanner / get_ble_registry / get_gobius_poller
│   ├── device.py         # /api/info, /api/mode, /api/silent, /api/sensors, /api/dashboard/sensors
│   ├── service.py        # IO Stop/Resume + /api/io/*, /api/filters, /api/settings, /api/diag, /api/service/*
│   ├── maintenance.py    # /api/backups, /api/backup, /api/reset/*
│   ├── firmware.py       # /api/firmware/latest|download|flash|progress
│   ├── gobius.py         # /api/gobius/status|n2k|user_config|info|command
│   ├── mopeka.py         # /api/mopeka/sensors|sensor|config
│   ├── ble.py            # /api/ble/* (registry scan)
│   ├── n2k.py            # /api/n2k/command (PGN 126208 universal sender)
│   ├── n2k_config.py     # /api/n2k/devices/* (dynamic device config via PGN 126208)
│   └── websockets.py     # WS /ws/monitor, /ws/scan
├── static/
│   ├── index.html        # SPA shell, 8 tabs
│   ├── css/style.css     # Dark theme + .panel.busy overlay
│   └── js/
│       ├── core.js       # App, api(), withButton, setFields/loadInputs/readInputs
│       ├── dashboard.js  # Device info, IO Stop/Resume card, sensor cards
│       ├── monitor.js    # WS CAN bus live monitor
│       ├── network.js    # Network tab (N2K device discovery)
│       ├── service.js    # Terminal, filters, diag
│       ├── maintenance.js# Backups, reset, firmware OTA
│       ├── gobius.js     # Gobius C BLE tab
│       ├── mopeka.js     # Mopeka BLE tab
│       └── n2k_config.js # Dynamic N2K device config (PGN 126208)
│   └── tabs/
│       ├── dashboard.html, monitor.html, network.html, service.html
│       ├── gobius.html, mopeka.html, maintenance.html, modal_ble_scan.html
├── tests/
│   ├── test_nmea_tcp_proxy.py  # TCP proxy tests
│   ├── test_sensors_service.py
│   ├── test_gobius_parsers.py
│   ├── test_mopeka_parsers.py
│   ├── test_gobius_ble_nmea.py
│   ├── test_gobius_n2k_protocol.py
│   ├── test_ble_registry.py
│   ├── test_n2k_commands.py
│   ├── test_api.py
│   └── test_ble_api.py
├── deploy.sh             # scp + systemctl restart + healthcheck -> gateway.local
└── ydnu02-web.service    # systemd unit (/opt/nmea2000/ydnu02-web/)
```

---

## Двухуровневая система команд YDNU-02

**КРИТИЧЕСКИ ВАЖНО:** У YDNU-02 ДВА ОТДЕЛЬНЫХ УРОВНЯ управления:
1. **OS Shell** — `YDNU MODE ...` через serial (`DeviceManager._locked_operation`)
2. **Service Menu** — только после `YDNU MODE SERVICE` (`_service_operation`)

**Через прокси:** все команды идут через `pcc.passthrough_write()`.
Прямой `echo > /dev/ttyACM0` работать не будет — прокси держит порт!

### OS Shell команды

| Команда | Эффект | EEPROM |
|:---|:---|:---|
| `YDNU MODE AUTO/0183/RAW/N2K` | Смена режима | да |
| `YDNU MODE SERVICE` | Вход в сервисный режим | нет |
| `YDNU SILENT ON/OFF` | Silent mode | да |

### Service Menu команды
`HELP`, `MODE`, `FILTER`, `PRINT <name>`, `SET`, `RESET SETTINGS/FILTERS`, `DIAG ALL/USB_RX/USB_TX/N2K_RX/N2K_TX`

---

## RAW Mode (Appendix E)

```
Incoming: hh:mm:ss.ddd R|T XXXXXXXX XX XX ...<CR><LF>
Outgoing: XXXXXXXX XX XX ...<CR><LF>   (без timestamp!)
```

---

## n2k_meta.py и PGN 126208

```python
# НЕ хардкодить PGN registry — всё из library decode functions
n2k_meta.get_pgn_field_metadata(pgn)     # -> [{id, name, type, configurable, ...}]
n2k_meta.get_pgn_name(pgn)               # -> "Fluid Level"
n2k_meta.build_read_fields_frame(target_src, target_pgn)   # -> RAW hex str
n2k_meta.build_command_frame(target_src, target_pgn, field_pairs)
n2k_meta.decode_raw_line(raw)            # -> {id, fields, ...}
```

### API n2k_config.py

```
GET  /api/n2k/devices              -> список всех N2K устройств на шине
GET  /api/n2k/devices/{src}/config/{pgn}  -> Read Fields + ждёт ответ (3s timeout)
POST /api/n2k/devices/{src}/config/{pgn}  -> Command Group Function + verify diff
GET  /api/n2k/pgn/{pgn}/metadata   -> field metadata (types, options, configurable)
POST /api/n2k/command              -> универсальный PGN 126208 sender
```

---

## Диагностика и команды

```bash
# Прокси работает?
ssh user@<gateway-host> 'systemctl status nmea-tcp-proxy --no-legend | head -4'

# ydnu02-web работает?
ssh user@<gateway-host> 'systemctl status ydnu02-web --no-legend | head -4'

# HA подключена к прокси? (должно быть 2 ESTAB: HA + ydnu02-web)
ssh user@<gateway-host> 'ss -tnp | grep 4001'

# Данные из прокси (5 строк за 15 сек)
ssh user@<gateway-host> 'timeout 15 bash -c "nc localhost 4001" | head -5'
# Пример: 03:35:31.851 R 19F2115C 00 30 5C 64 00 00 00 FF

# Лог прокси
ssh user@<gateway-host> 'sudo journalctl -u nmea-tcp-proxy -n 20 --no-pager'

# IO state
ssh user@<gateway-host> 'cat /opt/nmea2000/ydnu02-web/io_state.json'

# CPU usage
ssh user@<gateway-host> 'ps aux --sort=-%cpu | head -5'
```

### Деплой прокси (отдельный сервис)
```bash
scp /Users/denn/Develop/yacht/yacht-n2k-console/nmea_tcp_proxy.py user@<gateway-host>:/home/denn/
ssh user@<gateway-host> 'sudo mv /home/denn/nmea_tcp_proxy.py /usr/local/bin/nmea_tcp_proxy.py \
  && sudo systemctl restart nmea-tcp-proxy \
  && sudo docker restart homeassistant'
```

### Деплой ydnu02-web
```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console && ./deploy.sh
```

### Запуск тестов
```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console
PYTHONPATH=. python3 -m pytest tests/ -v
```

---

## Sensor Architecture

### Роли каналов (ФУНДАМЕНТАЛЬНОЕ ПРАВИЛО)

| Канал | Роль | Что делает |
|:------|:-----|:-----------|
| **NMEA 2000** | **ОСНОВНОЙ источник данных** | fill_level, capacity, live telemetry |
| **BLE** | **ТОЛЬКО НАСТРОЙКА** | Подключился -> записал конфиг -> отключился |
| **BLE Registry** | **User-configured overrides** | fluid_type, capacity, name (ble_sensors.json) |

**NMEA НИКОГДА не зависит от BLE. BLE НИКОГДА не зависит от NMEA.**

```python
# sensors/base_sensor.py
# NMEAData:  fill_level_pct, capacity_l, calculated_l, fluid_type_code/name, src, age_sec
# BLEData:   temp_c, voltage_v, fill_pct, distance_mm, volume_l, n2k_state, n2k_src, ...
# BaseSensor: self.nmea = NMEAData(); self.ble = BLEData()
# to_dict() -> flat compat dict + nested nmea{} / ble{} structures
```

### API Endpoints

```
GET  /api/sensors                  -> {fluid_levels: [{...to_dict()...}]}
GET  /api/dashboard/sensors        -> unified sensor cards (merges NMEA + BLE + registry)
GET  /api/gobius/status            -> BLE connect + read all characteristics
POST /api/gobius/n2k               -> Write 0xFFF2
POST /api/gobius/user_config       -> Write 0xFFE6
POST /api/gobius/info              -> Write 0xFFEB/FFEC
POST /api/gobius/command           -> Write 0xFFE7
GET  /api/mopeka/sensors           -> {sensors: [{mac, fill_level_pct, ...}]}
POST /api/mopeka/config/{mac}      -> {name, tank_depth_mm, capacity_l, fluid_type}
DELETE /api/mopeka/sensor/{mac}    -> remove sensor
GET  /api/io/state                 -> {paused, port, serial, gobius, mopeka}
POST /api/io/pause                 -> Stop all I/O
POST /api/io/resume                -> Resume all I/O
```

---

## Frontend Patterns (JS)

### withButton + Busy Overlay
```javascript
// core.js — все кнопки используют:
withButton(btnEl, label, asyncFn)
  // -> btn.closest('.panel').classList.add('busy')  = CSS overlay + spinner
  // -> on success: btn.result-ok (green flash 1.5s)
  // -> on error:   btn.result-err (red flash 2s)
  // -> finally:    remove .busy
// HTML: onclick="App.someMethod(this)"  <- передаёт кнопку
```

### Shared Sensor Helpers
```javascript
App.setFields({'mop-temp': '25C', 'mop-fill': '77.9%'})   // read-only fields
App.loadInputs({'mop-depth': 500}, '_mopekaConfigLoaded')    // load ONCE
App.readInputs({tank_depth_mm: {id:'mop-depth', type:'float'}})  // -> typed object
```
**ПРАВИЛО:** Gobius и Mopeka используют одни хелперы. Не дублировать getElementById.

### Polling
- Dashboard: 5s polling на `/api/io/state` и `/api/dashboard/sensors`
- Non-200 HTTP (503 = IO stopped) обрабатывается в `api()` в `core.js`

---

## Home Assistant на gateway.local

### История и архитектура

**HA установлена на gateway.local** (Raspberry Pi 5) в Docker.
Конфиг: `/mnt/ssd-data/homeassistant/config`

**Первая попытка (НЕ РАБОТАЕТ):**
docker-compose давал HA `/dev/ttyACM0` напрямую через `devices:`.
HA nmea2000 custom integration **не поддерживает YDNU-02 напрямую** —
несовместимый протокол (YDNU-02 говорит ASCII RAW, интеграция ожидает другое).

**Решение — TCP прокси:**
Именно поэтому был создан `nmea_tcp_proxy.py`.
Прокси держит `/dev/ttyACM0` эксклюзивно, инициализирует YDNU-02 в RAW mode,
фильтрует NMEA строки и раздаёт их по TCP `:4001`.
HA nmea2000 интеграция подключается к `:4001` и получает чистый NMEA поток.

```
/dev/ttyACM0
    └── nmea_tcp_proxy.py (держит порт ЭКСКЛЮЗИВНО)
            ├── :4001 → HA (nmea2000 custom integration)
            ├── :4001 → ydnu02-web DeviceManager._bus_worker
            └── :4002 → ydnu02-web ProxyControlClient (service/firmware ops)
```

**⚠️ ВАЖНО:** `homeassistant/docker-compose.yml` содержит устаревшую строку
`devices: /dev/ttyACM0` — она **не используется** и может быть убрана.
HA должна подключаться ТОЛЬКО через TCP `:4001`, никогда не через USB напрямую.

### HA docker-compose (актуальный)

```yaml
# homeassistant/docker-compose.yml
services:
  homeassistant:
    container_name: homeassistant
    image: ghcr.io/home-assistant/home-assistant:stable
    restart: unless-stopped
    privileged: true
    network_mode: host          # важно: host mode чтобы видеть :4001 прокси
    volumes:
      - /mnt/ssd-data/homeassistant/config:/config
      - /etc/localtime:/etc/localtime:ro
      - /run/dbus:/run/dbus:ro
    # НЕ добавлять devices: ttyACM0 — порт держит nmea_tcp_proxy.py
    environment:
      - TZ=Europe/London
```

### HA nmea2000 custom integration

- Подключается к `127.0.0.1:4001` (TCP, DATA port прокси)
- Читает NMEA 2000 RAW frames в ASCII формате
- **Баг в lib v2026.5.2:** при EOF (прокси рестартовал) входит в infinite spin loop → 100% CPU
- **После КАЖДОГО рестарта прокси ОБЯЗАТЕЛЬНО:**
  ```bash
  sudo systemctl restart nmea-tcp-proxy
  sudo docker restart homeassistant
  ```

### Команды управления HA

```bash
# Статус
ssh user@<gateway-host> 'sudo docker ps | grep homeassistant'

# Запуск/рестарт
ssh user@<gateway-host> 'sudo docker restart homeassistant'

# Логи HA (последние 50 строк)
ssh user@<gateway-host> 'sudo docker logs homeassistant --tail 50'

# Логи nmea2000 интеграции
ssh user@<gateway-host> 'sudo docker logs homeassistant 2>&1 | grep -i nmea | tail -20'

# Полный стек (прокси + HA)
ssh user@<gateway-host> 'systemctl status nmea-tcp-proxy --no-legend | head -3 && sudo docker ps --format "{{.Names}} {{.Status}}" | grep homeassistant'
```

### Путь к HA конфигу

```
/mnt/ssd-data/homeassistant/config/
├── configuration.yaml
├── custom_components/
│   └── nmea2000/          # custom integration
└── ...
```

---

## Signal K в Docker (gateway.local)

```bash
cd ~/ha/nmea2000 && docker compose up -d
```
Веб: `http://gateway.local:3000`
