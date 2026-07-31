---
name: nmea2000-setup
description: >-
  Полное руководство и база знаний по настройке NMEA 2000 (Yacht Devices YDNU-02, Gobius C),
  Mopeka Pro 200 BLE, сервисного режима YDNU-02, TCP-прокси архитектуры,
  Signal K Server на Raspberry Pi 5 и интеграции с Home Assistant / Victron.
---

# NMEA 2000 + YDNU-02 + Gobius C + Mopeka BLE + Signal K (Raspberry Pi 5)

## 📁 Файлы проекта (yacht-n2k-console)

| Файл | Описание |
|:---|:---|
| [nmea_tcp_proxy.py](file:///path/to/yacht-n2k-console/nmea_tcp_proxy.py) | TCP прокси — YDNU-02 → broadcast, Control API |
| [device_manager.py](file:///path/to/yacht-n2k-console/device_manager.py) | TCPProxyConnection + ProxyControlClient |
| [ydnu02.py](file:///path/to/yacht-n2k-console/ydnu02.py) | Hardware controller (поддерживает passthrough) |
| [tests/test_nmea_tcp_proxy.py](file:///path/to/yacht-n2k-console/tests/test_nmea_tcp_proxy.py) | 18 тестов прокси |
| HA integration | `/Users/denn/Develop/3dprint/ha/nmea2000/` — FastAPI приложение |

---

## 🏗️ TCP Proxy Архитектура

### ⚠️ ГЛАВНОЕ ПРАВИЛО: только прокси держит /dev/ttyACM0

```
YDNU-02 /dev/ttyACM0
       │
  nmea_tcp_proxy.py  (systemd: nmea-tcp-proxy.service)
       ├── :4001  DATA port   → broadcast NMEA строк всем клиентам (read-only для них)
       └── :4002  CTRL port   → эксклюзивный serial passthrough (service/firmware mode)
       │
       ├── HA ha-nmea2000 integration (автоматически подключается к :4001)
       └── ydnu02-web (TCPProxyConnection → :4001, ProxyControlClient → :4002)
```

**Никто кроме прокси не открывает `/dev/ttyACM0` напрямую.**

### nmea_tcp_proxy.py — ключевые детали

**Env vars:**
- `NMEA_SERIAL_PORT` (default `/dev/ttyACM0`)
- `NMEA_SERIAL_BAUD` (default `115200`)
- `NMEA_PROXY_HOST` (default `0.0.0.0`)
- `NMEA_PROXY_PORT` (default `4001`) — DATA
- `NMEA_CTRL_PORT` (default `4002`) — CTRL

**Init при старте:**
```python
ser.write(b"YDNU MODE RAW\r\n")  # переключение в RAW mode
time.sleep(2.0)
ser.read(ser.in_waiting)         # flush echo
ser.write(b"0\n")                # сбрасываем фильтры
time.sleep(0.5)
ser.read(ser.in_waiting)         # flush echo
```

**NMEA frame filter (ОБЯЗАТЕЛЬНЫЙ):**
```python
_NMEA_LINE_RE = re.compile(
    rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"
)
# Только строки совпадающие с этим regex идут в broadcast.
# Текстовые ответы YDNU-02 (init echo, mode-switch ack) — отбрасываются.
```

**⚠️ КРИТИЧЕСКИ ВАЖНО:** Без этого фильтра init-эхо YDNU-02 попадает в broadcast,
HA библиотека получает нечитаемые строки и спинится на 100% CPU (см. баг ниже).

**Control API (:4002) команды:**
```
SERVICE_START   → ставит service_mode, паузирует broadcast
SERVICE_END     → снимает service_mode, возобновляет broadcast
FIRMWARE_START  → аналог SERVICE_START
FIRMWARE_END    → аналог SERVICE_END
(после команды клиент получает эксклюзивный serial passthrough)
```

### device_manager.py — TCPProxyConnection

```python
class TCPProxyConnection:
    """Читает NMEA строки из :4001 вместо прямого serial."""
    def readline(self) -> str: ...   # blocking, с reconnect backoff
    def write(self, data: bytes): ... # TCP write

class ProxyControlClient:
    """Подключается к :4002, управляет service/firmware mode."""
    def enter_service(self): ...     # отправляет SERVICE_START
    def exit_service(self): ...      # отправляет SERVICE_END
    def passthrough_write(self, data): ...
    def passthrough_read_for(self, duration): ...
```

### ydnu02.py — passthrough поддержка

`YDNU02Controller` поддерживает атрибут `_passthrough` (экземпляр `ProxyControlClient`):
- `_write()` → если `_passthrough` установлен, пишет через него, иначе прямой serial
- `_read_response()` → `pcc.passthrough_read_for(duration)` или прямой serial
- `_send_terminal_command()` → пропускает flush-буфера если passthrough
- `enter_service_mode()` → proxy path (через passthrough) + legacy path (shell echo)

Backward compatible: legacy direct-serial код остаётся (используется при `_passthrough=None`).

---

## 🐛 HA nmea2000 Library — CPU Spin Loop Bug

### Симптом
```
python3 -m homeassistant  100%+ CPU
HA log: decoding failed. text: , bytes: . Error: Invalid CAN Frame ASCII string format
```

### Причина
Баг в `nmea2000` lib v2026.5.2 (`nmea2000/ioclient.py`):

```python
# AsyncIOClient._receive_loop (~строка 209):
while self._state != State.CLOSED:
    await self._receive_impl()   # ← если возвращает мгновенно — спин!

# _receive_impl (~строка 535):
data = await self.reader.readline()  # EOF → b""
line = data.decode().strip()         # ""
try:
    message = self.decoder.decode(line)  # EXCEPTION
except Exception as e:
    self.logger.warning(...)
    return   # ← немедленный return, нет sleep!
```

**При EOF (`b""`):** HA библиотека не детектирует закрытие соединения,
крутится в бесконечном цикле без sleep → 100% CPU.
**При нечитаемых строках:** те же симптомы — decode exception → return → немедленный следующий вызов.

### Когда возникает
1. Прокси рестартовал → TCP соединение разорвано → HA получает EOF → **spin forever**
2. В broadcast попали не-NMEA строки (init echo) → decode fail spin

### Фикс на нашей стороне
1. **NMEA regex фильтр** в прокси — только валидные фреймы в broadcast
2. После рестарта прокси — **обязательно перезапустить HA**

### HA интеграция (ha-nmea2000, @tomer-w)
- **Custom component:** `/config/custom_components/nmea2000/` внутри HA контейнера
- **Версия:** v2026.5.0, `nmea2000==2026.5.2`
- **Config:** `gateway_type=text`, `ip=127.0.0.1`, `port=4001`
- **Процесс:** `python3 -m homeassistant --config /config` в Docker `homeassistant` на **gateway-host**

---

## 🔧 Диагностика и команды

### Проверка состояния

```bash
# CPU usage
ssh user@gateway-host 'ps aux --sort=-%cpu | head -5'

# Прокси работает?
ssh user@gateway-host 'systemctl status nmea-tcp-proxy --no-legend | head -4'

# HA подключена к прокси?
ssh user@gateway-host 'ss -tnp | grep 4001'
# Должно быть: ESTAB ... 127.0.0.1:XXXXX → 127.0.0.1:4001

# Данные из прокси (5 строк за 15 сек)
ssh user@gateway-host 'timeout 15 bash -c "nc localhost 4001" | head -5'
# Пример валидного фрейма: 03:35:31.851 R 19F2115C 00 30 5C 64 00 00 00 FF

# Ошибки HA интеграции
ssh user@gateway-host 'sudo docker exec homeassistant bash -c "grep decoding /config/home-assistant.log | tail -5"'

# Лог прокси
ssh user@gateway-host 'sudo journalctl -u nmea-tcp-proxy -n 20 --no-pager'
```

### ⚠️ Обязательный restart sequence

```bash
# После ЛЮБОГО systemctl restart nmea-tcp-proxy:
sudo systemctl restart nmea-tcp-proxy
sudo docker restart homeassistant   # ← ОБЯЗАТЕЛЬНО, иначе spin loop

# Проверка через 60 сек:
ssh user@gateway-host 'ps aux --sort=-%cpu | head -4 && ss -tnp | grep 4001'
# HA должна быть <10% CPU и ESTAB соединение на :4001
```

### Деплой прокси

```bash
scp /path/to/yacht-n2k-console/nmea_tcp_proxy.py user@gateway-host:/home/user/
ssh user@gateway-host 'sudo mv /home/user/nmea_tcp_proxy.py /usr/local/bin/nmea_tcp_proxy.py \
  && sudo systemctl restart nmea-tcp-proxy \
  && sudo docker restart homeassistant'
```

### Запуск тестов прокси

```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console
python3 -m pytest tests/test_nmea_tcp_proxy.py -v   # 18 тестов
```

---

## 🔌 Оборудование и физический слой NMEA 2000

### Параметры шины
- **Сопротивление:** 60 Ом между CAN-H и CAN-L (два терминатора по 120 Ом).
- **Питание:** 12V DC через Power Tee.

### Yacht Devices YDNU-02 USB Gateway
- **Linux порт:** `/dev/ttyACM0` (CDC ACM виртуальный COM-порт).
- **VID:** `0x0483` (STMicroelectronics), **PID:** `0xA217`.
- Скорость порта (baud rate) **не имеет значения** для USB CDC ACM.
- NMEA bus шлёт ~0.4 фрейма/сек при почти пустой шине.

### Gobius C NMEA 2000
- **BLE Name:** `GOBIUS C`, **MAC:** `2C:A7:74:21:56:D8`
- **N2K PGN 127505:** Source: 92, Instance: 0, ~2.5s interval

#### ⚠️ Ключевая проблема PGN 127505 fluid_type
**Прошивка Gobius C** PGN 127505 byte[0] **всегда передаёт 0x00 (Fuel)**, независимо от настройки FFF2.
Workaround: `fluid_type` хранится в registry как user-configured override.
**BLE — это ТОЛЬКО настройка. NMEA 2000 — ВСЕГДА основной источник данных.**

---

### 📡 Gobius C BLE GATT Protocol Reference
**Source:** "GOBIUS C Bluetooth Protocol & Functional Description", Issue 3, 2023-08-08, Gobius Sensor Technology AB.
**All multi-byte values: Big-Endian (MSB first) per spec §8.2.4.**

#### GATT Characteristic Map

| UUID     | Имя            | R/W      | Описание |
|:---------|:---------------|:---------|:---------|
| `0xFFE6` | **User Config**  | R/W    | geometry (dist_empty/full mm), LP filters |
| `0xFFE7` | **Command**      | W      | 3-byte command frame (calibrate, start/stop, adv, initialize, write_info) |
| `0xFFE8` | **Status**       | R      | state, uptime, temp, voltage, MAC, range, errors |
| `0xFFE9` | **Measurement**  | R+Notify | fill ‰, distance mm, inclination, envelopes |
| `0xFFEB` | **Info 1**       | R/W    | User label (20 bytes ASCII, space-padded) |
| `0xFFEC` | **Info 2**       | R/W    | User comment (20 bytes ASCII, space-padded) |
| `0xFFF2` | **N2K Config**   | R/W    | N2K enable, instance, fluid type, volume |
| `0xFFF3` | **N2K Status**   | R      | n2k_state, n2k_src (N2K firmware extension) |

---

#### 0xFFE8 Status (20 bytes) — Table 26
```
[0]     ST_ST   State
          0=Start-Up  1=Self-Test  2=Uninit  3=Uncalibrated
          4=Calibration  5=Active  6=Error  7=Production-Test  8=HW-Test
[1]     ST_SB   Status bits (bitmask)
[2:6]   ST_T    Uptime since power-on [s]  uint32 BE
[6]     ST_ER1  General error code
[7]     ST_ER2  Hardware error code
[8]     ST_T    Processor temperature [°C]  int8 signed
[9:11]  ST_V    Supply voltage [mV]  uint16 BE  →  voltage_v = raw_mv / 1000.0
[11:17] ST_ID   BLE MAC address (6 bytes)
[17]    ST_ER3  Extended HW error
[18]    ST_ERR  Radar comm error counter
[19]    ST_RNG  Current measurement range
          0=Zero  1=Near  2=Mid  3=Far
```
Parser: `gobius_parsers.parse_status(data)` → `GobiusCSensor.update_from_ble_status()`

---

#### 0xFFE9 Measurement (20 bytes) — Table 27
```
[0]     M_ST    State (copy of status byte[0])
[1]     M_SB    Status bits
[2]     M_VD    Level validity  0=invalid  1=valid
[3:5]   M_FL    Fill level ‰ [0-1000]  uint16 BE  →  fill_pct = raw / 10.0
[5]     M_INC   Sensor inclination [0-90°]
[6:8]   M_DIST  Distance sensor→fluid surface [mm]  uint16 BE
[8:10]  M_SZR   Envelope size Zero Range
[10:12] M_SNR   Envelope size Near Range
[12:14] M_SMR   Envelope size Mid Range
[14:16] M_SFR   Envelope size Far Range
[16:20] Reserved
```
Sensor pushes FFE9 via BLE Notify — subscribe once after connect.
Parser: `gobius_parsers.parse_measurement(data)` → `GobiusCSensor.update_from_ble_measurement()`

---

#### 0xFFE6 User Config (20 bytes) — Table 23
```
[0:2]   UC_DE    Distance for tank EMPTY [mm]  uint16 BE  clamp [20..2000]
[2:4]   UC_DF    Distance for tank FULL  [mm]  uint16 BE  clamp [20..2000]
[4]     UC_LPN   LP filter size  (0=disabled, range 0..100)
[5]     UC_LPK   LP filter threshold [%]  range [1..100]
[6]     UC_BITS  Config bits (Table 17)
[7]     UC_O1T   Output 1 threshold [%]  0..100
[8]     UC_O1H   Output 1 hysteresis [%]  0..100
[9]     UC_O2T   Output 2 threshold [%]  0..100
[10]    UC_O2H   Output 2 hysteresis [%]  0..100
[11]    UC_R0    Resistive Ω at 0%
[12]    UC_R25   Resistive Ω at 25%
[13]    UC_R50   Resistive Ω at 50%
[14]    UC_R75   Resistive Ω at 75%
[15]    UC_R100  Resistive Ω at 100%
[16]    UC_VE    Voltage empty (unit 25mV)
[17]    UC_VF    Voltage full (unit 25mV)
[18]    UC_AOF   Advertise-off time [10..255 s]
[19]    Reserved
```
**Write pattern:** `read_char(0xFFE6)` → patch bytes → `write_char(0xFFE6)` → verify with `read_char`

Parser: `gobius_parsers.parse_user_cfg(data)` → `GobiusCSensor.update_from_ble_user_cfg()`

Fill level formula:
```
fill% = (dist_empty_mm - distance_mm) / (dist_empty_mm - dist_full_mm) * 100
```

---

#### 0xFFF2 N2K Config (20 bytes) — N2K firmware extension
```
[0]     enabled      0x00=off  /  0x01=on
[1]     instance     fluid instance  nibble &0x0F  range [0..15]
[2]     fluid_type   NMEA fluid type code:
                       0=Fuel  1=Fresh Water  2=Gray Water  3=Live Well
                       4=Oil   5=Black Water  6=Gasoline
[3..8]  Reserved
[9]     volume_l     Tank volume [L]  uint8  clamp [1..255]  (max 255L!)
[10:20] Reserved
```
**Write pattern:** `read_char(0xFFF2)` → patch bytes → `write_char(0xFFF2)` → verify

> ⚠️ volume_l is a single byte — **max 255 litres.** Values > 255 must be clamped before write.

Parser: `gobius_parsers.parse_n2k_cfg(data)` → `GobiusCSensor.update_from_ble_n2k_cfg()`

---

#### 0xFFF3 N2K Status (20 bytes) — N2K firmware extension
```
[0]   n2k_state   0=off  2=active
[1]   n2k_src     NMEA 2000 source address (default 92 = 0x5C)
```

---

#### 0xFFE7 Command (3 bytes) — Table 18
```
[0]     cmd_code   ASCII character
[1:3]   param      uint16 BE  (0x0000 if unused)
```

| Code | ASCII | Command       | Danger? |
|:-----|:------|:--------------|:--------|
| 0x62 | `b`   | start         | —       |
| 0x61 | `a`   | stop          | —       |
| 0x63 | `c`   | calibrate     | —       |
| 0x69 | `i`   | initialize    | ⚠️ FACTORY RESET — all settings erased |
| 0x6E | `n`   | adv_normal    | —       |
| 0x6F | `o`   | adv_off       | ⚠️ BLE connection lost immediately. To re-enable: power cycle → reconnect **within 10 seconds** |
| 0x77 | `w`   | write_info    | MUST follow info1/info2 writes |
| 0x73 | `s`   | secure        | —       |
| 0x75 | `u`   | unsecure      | —       |

---

#### 0xFFEB / 0xFFEC Info 1/2 (20 bytes each)
- UTF-8 string, right-padded with spaces to exactly 20 bytes
- Read back: `bytes.decode('utf-8', errors='replace').strip()`

**Info write sequence — MUST follow this exact order:**
```
1. write_char(0xFFEB, info1_20_bytes)
2. write_char(0xFFEC, info2_20_bytes)
3. write_char(0xFFE7, b'\x77\x00\x00')   ← write_info commit command ('w')
```
Skipping step 3 = changes NOT persisted to sensor flash.

---

#### BLE Architecture Rules
- **BLE and NMEA are independent channels** — never mix writes
- **One BLE connection** owned by `GobiusBLEPoller` — all reads/writes go through it
- **No other code** creates BLE connections to Gobius
- `write_char` from routes goes through `poller.write_char()` under `_lock`
- Polling: FFE8+FFF3 every 30s (status), FFE9 via notify (measurement push)

#### Real Sensor Dumps (Verified)
```python
REAL_FFE8 = "05080000749a00001c2ec02ca7742156d8000001"
# State=Active(5), uptime=29850s, temp=28°C, voltage=11.968V, MAC=2C:A7:74:21:56:D8

REAL_FFE9 = "0508010318050066005e0087017902d300000000"
# fill=79.2% (792‰), dist=102mm, incl=5°, env: 94/135/377/723

REAL_FFE6 = "012c003203151032053205000000000000000a00"
# dist_empty=300mm, dist_full=50mm, LP=3/21, advertise_off=10s

REAL_FFF2_ON  = "0100010000000000009600000000000000000000"
# enabled=True, instance=0, fluid_type=1(Fresh Water), volume=150L

REAL_FFF3 = "025c000000000000000000000000000000000000"
# n2k_state=2(active), n2k_src=92(0x5C)
```


### Mopeka Pro 200 (BLE)
- **MAC:** `F1:FD:CB:6C:B2:CC`, passive advertisement only
- **BLE Manufacturer ID:** `0x0059` (Nordic Semiconductor)
- `fill_level_pct = ((tank_depth - distance_mm) / tank_depth) × 100`

---

## 💻 Двухуровневая система команд YDNU-02

### ⚠️ КРИТИЧЕСКИ ВАЖНО
У YDNU-02 **ДВА ОТДЕЛЬНЫХ УРОВНЯ** управления:
1. **OS Shell** — `echo "YDNU MODE ..." > /dev/ttyACM0`
2. **Service Menu** — только после `YDNU MODE SERVICE`, через serial terminal

**Через прокси:** OS Shell команды → `pcc.passthrough_write(b"YDNU MODE SERVICE\r\n")`
(прокси держит serial — прямой `echo > /dev/ttyACM0` работать не будет!)

#### OS Shell команды

| Команда | Эффект | EEPROM |
|:---|:---|:---|
| `YDNU MODE AUTO/0183/RAW/N2K` | Смена режима | ✅ |
| `YDNU MODE SERVICE` | Вход в сервисный режим | ❌ |
| `YDNU SILENT ON/OFF` | Silent mode | ✅ |

#### Service Menu команды
`HELP`, `MODE`, `FILTER`, `SET`, `RESET SETTINGS/FILTERS`, `DIAG ALL/USB_RX/USB_TX/N2K_RX/N2K_TX`

---

## 📡 RAW Mode (Appendix E)
```
Incoming: hh:mm:ss.ddd R msgid b0 b1 ... b7<CR><LF>
Outgoing: msgid b0 b1 ... b7<CR><LF>   (без timestamp!)
```

---

## 🌐 ydnu02-web (yacht-n2k-console)

### Структура
```
yacht-n2k-console/
├── nmea_tcp_proxy.py     # ← ОСНОВНОЙ: держит /dev/ttyACM0, broadcast :4001, ctrl :4002
├── device_manager.py     # TCPProxyConnection + ProxyControlClient
├── ydnu02.py             # Hardware controller (passthrough support)
├── tests/
│   └── test_nmea_tcp_proxy.py  # 18 тестов (pytest)

ha/nmea2000/              # FastAPI веб-приложение
├── app.py
├── device_manager.py
├── ydnu02.py
├── routes/               # device, service, maintenance, firmware, gobius, mopeka, sensors, websockets
└── static/               # SPA, 7 tabs, dark theme
```

### DeviceManager Patterns (через TCP)

| Паттерн | Использование |
|:---|:---|
| `TCPProxyConnection` | Читает NMEA из :4001, reconnect backoff |
| `ProxyControlClient` | Управляет :4002 для service/firmware |
| `_service_operation(func)` | lock → enter_service → func → exit_service |

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
