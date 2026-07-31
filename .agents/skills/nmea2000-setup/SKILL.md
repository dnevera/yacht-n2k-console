---
name: nmea2000-setup
description: >-
  Полное руководство и база знаний по yacht-n2k-console: TCP Gateway архитектура
  (data_hub, serial_reader, service_mode), все баги nmea2000 lib и их патчи,
  идемпотентный деплой HA, nmea2000 fork (unique_number fix), оборудование
  (YDNU-02, Gobius C BLE GATT, Mopeka Pro 200), Signal K. Активируй при любых
  задачах связанных с: N2K gateway, data_hub, serial_reader, HA patches, deploy,
  YDNU-02, Gobius, Mopeka, ioclient, message.py hash collision.
---

# NMEA 2000 Gateway — База знаний (yacht-n2k-console)

## 📁 Структура проекта

```
yacht-n2k-console/
├── ydnu02_tcp_gateway/           # TCP Gateway пакет (основной)
│   ├── data_hub.py               # DataHub: broadcast, announce, ISO request, handle_client
│   ├── serial_reader.py          # SerialReader: читает /dev/ttyACM0, пишет в DataHub
│   ├── ydnu02_tcp_gateway.py     # Точка входа, запуск всего
│   ├── ydnu02_gateway_device.py  # N2KDeviceRegistry, N2KDeviceEncoder, DEFAULT_* devices
│   └── gateway.py / gateway_settings.py
├── ydnu02/                       # YDNU-02 hardware controller (BLE + Service mode)
├── device_manager/               # ProxyControlClient, ServiceModeManager
├── scripts/
│   └── patch_ha_nmea2000_message.py  # Идемпотентный патч message.py (v2)
├── patches/
│   └── nmea2000_ioclient.py      # Пропатченный ioclient.py (EOF fix)
├── tests/
│   ├── test_ha_gateway.py        # Unit тесты DataHub + registry (221+ тестов)
│   ├── test_live_ha_integration.py  # Live тесты против реального HA (7 тестов)
│   ├── test_service_mode.py      # Service mode тесты (TCP socket — sandbox-only)
│   └── ...
├── deploy.sh                     # Деплой на Pi + HA патчи (идемпотентный)
├── requirements.txt              # Зависимости + nmea2000 из нашего git форка
└── pyproject.toml

Смежный репозиторий:
/path/to/yacht/nmea2000/   # форк tomer-w/nmea2000
  ветка: fix/pgn-126996-hash-collision-per-source
  nmea2000/message.py  — исправлен primary_key (unique_number fix)
  nmea2000/ioclient.py — исправлен EOF spin-loop (PR уже merged в upstream)
```

---

## 🏗️ TCP Gateway Архитектура

### Схема потоков данных

```
NMEA 2000 Bus
    │
YDNU-02 /dev/ttyACM0
    │
SerialReader (serial_reader.py)
    │  readline() → парсит NMEA ASCII фреймы
    │  фильтр: _NMEA_LINE_RE — только валидные фреймы
    ▼
DataHub (data_hub.py)
    ├── broadcast(line) → все TCP clients (set[socket])
    ├── _track_physical_device(line) → N2KDeviceRegistry (PGN 60928 + 126996)
    │
    ├── :4001 DATA port — NMEA broadcast (read-only для клиентов)
    │       └── HA ha-nmea2000 integration (auto-connect)
    │       └── ydnu02-web (TCPProxyConnection)
    │
    └── :4002 CTRL port — service/firmware passthrough (эксклюзивный)
            └── ProxyControlClient (service mode, firmware flash)

N2KDeviceRegistry:
    DEFAULT_PHYSICAL_DEVICE  SA=64 (0x40)  unique_number=402047  ISO NAME=YDNU-02
    DEFAULT_VIRTUAL_DEVICE   SA=200 (0xC8) unique_number=902047  ISO NAME=TCP-GW

На connect нового клиента: handle_client() → send_iso_request()
  Phase 1: broadcast ISO Claim (PGN 60928) для обоих устройств  [немедленно]
  Phase 2: broadcast Product Info (PGN 126996) после задержки    [+0.6s Timer]
```

### Ключевые константы (data_hub.py)

```python
ANNOUNCE_PRODUCT_INFO_DELAY = 0.6   # секунды между Phase 1 и Phase 2
DATA_PORT  = 4001
CTRL_PORT  = 4002
```

### announce_all_devices() — двухфазный анонс

```python
def announce_all_devices(self, product_info_delay: float = 0.0) -> None:
    """
    product_info_delay=0   → синхронный broadcast (unit tests, прямые вызовы)
    product_info_delay=0.6 → Timer (production: send_iso_request вызывает с ANNOUNCE_PRODUCT_INFO_DELAY)
    """
```

**ПОЧЕМУ двухфазный:** HA nmea2000 decoder строит `source_to_iso_name` из PGN 60928.
Если PGN 126996 приходит РАНЬШЕ PGN 60928 — `source_to_iso_name[SA]` не заполнен →
decoder делает **silent drop** (строка ~339 в decoder.py: `if source_iso_name is None and build_network_map: return None`).

---

## 🐛 Известные Баги и Фиксы

### Bug 1 — HA decoder silent drop (NMEA ioclient EOF spin-loop)

**Файл:** `nmea2000/ioclient.py` в HA Docker контейнере

**Симптом:** После рестарта gateway — HA крутится на 100% CPU, не переподключается.

**Причина:**
```python
# _receive_impl() (строка ~535):
data = await self.reader.readline()  # EOF → b""
line = data.decode().strip()         # ""
message = self.decoder.decode(line)  # EXCEPTION
# except: return  ← немедленный return, цикл крутится без sleep → 100% CPU
```

**Фикс:** `patches/nmea2000_ioclient.py` — при `b""` поднимает `ConnectionError` вместо `return`.

**PR:** merged в `tomer-w/nmea2000` (PR #61).

**Идемпотентность деплоя:** MD5-сравнение remote vs local перед `docker cp`.

---

### Bug 2 — PGN 126996 hash collision (все устройства → один device в HA)

**Файл:** `nmea2000/message.py` в HA Docker контейнере

**Симптом:** Второй NMEA 2000 девайс в HA показывает «0 entities».

**Причина (оригинальный upstream код):**
```python
primary_key = f"{self.id}"    # для PGN 126996: self.id = "productInformation"
# Нет полей с part_of_primary_key=True → primary_key одинаков для ВСЕХ устройств
# MD5("productInformation") = "818d9516db08fd90ffd1967e3c403bed"  ← коллизия
```

**Фикс (наш форк + патч):**
```python
source_id = (
    self.source_iso_name.unique_number   # ← 21-бит, manufacturer-assigned, STABLE
    if self.source_iso_name is not None
    else self.source                      # ← fallback: SA byte
)
primary_key = f"{self.id}_{source_id}"
```

**ПОЧЕМУ `unique_number`, а НЕ `iso_name.name`:**
- `unique_number` = 21-бит, прошит производителем (NMEA 2000 §3.1.1), **никогда не меняется**
- `iso_name.name` = 64-бит integer, включает `device_instance` (меняется при переинициализации шины!)
- Использование `iso_name.name` → разный MD5 при каждом рестарте YDNU-02 → новый device в HA registry

**Хэши после фикса (стабильны навсегда):**
- SA=64 (YDNU-02, unique_number=402047): `ef195c7c99c762fdfda4e198aae87930`
- SA=200 (TCP-GW, unique_number=902047): `c11f5c824c71fe7e186cba56bf0f8672`

**Маркер идемпотентности:**
- `"yacht-n2k-console-patch-v1"` — использовал `.name` (нестабильный, создавал дубли)
- `"yacht-n2k-console-patch-v2"` — использует `.unique_number` (стабильный, текущий)

**Upgrade v1→v2:** автоматически через `patch_ha_nmea2000_message.py` при следующем `--patch-ha`.

**PR pending:** `dnevera/nmea2000` → `tomer-w/nmea2000`

---

### Bug 3 — HA registry накапливает мусор (старые device записи)

**Симптом:** Несколько «Product Information (Yacht Devices - PC Gateway - ...)» в HA.

**Причина:** До patch-v2 `device_instance` в `iso_name.name` менялся → другой MD5 → новая запись.
Тест `next(d for d in devices if '902047' in str(d))` брал первый попавшийся (старый, 0 entities).

**Диагностика:**
```bash
ssh user@<gateway-host> "sudo docker exec homeassistant python3 -c \"
import json
dr = json.load(open('/config/.storage/core.device_registry'))
er = json.load(open('/config/.storage/core.entity_registry'))
nmea = [d for d in dr['data']['devices'] if '402047' in str(d) or '902047' in str(d)]
print('NMEA devices:', len(nmea))
for d in nmea:
    ent = [e for e in er['data']['entities'] if e.get('device_id')==d['id']]
    print('  %s → %d entities' % (d.get('name','?')[:70], len(ent)))
\""
```

**Фикс:** `./deploy.sh --clean-ha` → удаляет все nmea2000 devices → HA пересоздаёт с нуля.

**После patch-v2:** дубли больше не создаются. Одноразовая очистка решает проблему навсегда.

---

## 🔧 Деплой (deploy.sh)

### Режимы

```bash
./deploy.sh                   # полный деплой: gateway + web + patch HA
./deploy.sh --proxy           # только gateway + patch HA (без web restart)
./deploy.sh --web             # только web (gateway и HA не трогается)
./deploy.sh --patch-ha        # только патчи HA (без деплоя кода)
./deploy.sh --clean-ha        # удалить мусорные NMEA devices из HA registry
./deploy.sh --proxy --no-test # без post-deploy тестов
./deploy.sh --no-diff         # пропустить pre-deploy diff
```

### patch_ha() — алгоритм (идемпотентный)

```
Patch 1 (ioclient EOF fix):
  md5(local patches/nmea2000_ioclient.py) == md5(remote in container)?
    YES → "already up to date — skipping"   [ha_changed остаётся false]
    NO  → docker cp → applied ✓              [ha_changed=true]

Patch 2 (message.py hash collision fix):
  запустить scripts/patch_ha_nmea2000_message.py внутри контейнера
  Сценарий A: PATCH_MARKER_V2 в файле  → "Already applied."             [ha_changed=false]
  Сценарий B: PATCH_MARKER_V1 в файле  → upgrade .name→.unique_number    [ha_changed=true]
  Сценарий C: оригинальный upstream     → fresh install                   [ha_changed=true]
  Сценарий D: файл не найден            → ERROR (динамический discovery)

HA restart: ТОЛЬКО если ha_changed=true
```

### requirements.txt — nmea2000 из git форка

```
# Устанавливается из нашего git-форка (не из PyPI!):
git+https://github.com/dnevera/nmea2000.git@fix/pgn-126996-hash-collision-per-source#egg=nmea2000
```

**После merge PR в tomer-w:** заменить на `nmea2000>=<новая версия>`.

### venv

```bash
cd /Users/denn/Develop/yacht/yacht-n2k-console
rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Верификация: .venv/bin/python -c "from nmea2000 import message; import inspect; print(inspect.getfile(message))"
# Ожидаем: .venv/lib/.../site-packages/nmea2000/message.py  (НЕ /opt/homebrew/...)
```

---

## 🧪 Тесты

### Запуск (локально)

```bash
# Unit тесты (без live HA и service_mode):
.venv/bin/python -m pytest tests/ \
  --ignore=tests/test_live_ha_integration.py \
  --ignore=tests/test_service_mode.py -q
# → 221 passed, 10 skipped

# Live тесты (требуют Pi + HA + running gateway):
.venv/bin/python -m pytest tests/test_live_ha_integration.py -v
# → 7 passed

# test_service_mode.py → PermissionError socket.bind — sandbox ограничение, не баг
```

### Ключевые тесты

| Тест | Что проверяет |
|------|--------------|
| `test_pk_hash_uniqueness_per_device_source` | SA=64 и SA=200 дают разные MD5 |
| `test_announce_all_devices_emits_both_sa64_and_sa200_frames` | оба устройства в анонсе |
| `test_ha_live_registry_strict_device_and_entities_check` | оба device в HA имеют >0 entities |
| `test_virtual_gateway_device_info_complete` | virtual gateway device правильно зарегистрирован |

### announce_all_devices() в тестах

```python
# Тесты вызывают БЕЗ аргументов (синхронный, delay=0.0 по умолчанию):
self.hub.announce_all_devices()          # виден ВЕСЬ output сразу

# Production: send_iso_request() передаёт задержку явно:
self.announce_all_devices(product_info_delay=ANNOUNCE_PRODUCT_INFO_DELAY)  # 0.6s Timer
```

---

## 📡 Живая диагностика

### Состояние

```bash
# Сервисы на Pi
ssh user@<gateway-host> 'systemctl is-active ydnu02-tcp-gateway ydnu02-web'

# Соединения (должно быть 2+ ESTAB на :4001)
ssh user@<gateway-host> 'ss -tnp | grep 4001'

# Живые NMEA фреймы
ssh user@<gateway-host> 'timeout 5 bash -c "nc localhost 4001" | head -10'

# Лог gateway (двухфазный анонс)
ssh user@<gateway-host> 'sudo journalctl -u ydnu02-tcp-gateway -n 30 --no-pager | grep -E "Phase|client|ISO"'
```

### Патчи в HA

```bash
# Какой маркер применён?
ssh user@<gateway-host> "sudo docker exec homeassistant grep 'yacht-n2k-console-patch' \
  /usr/local/lib/python3.14/site-packages/nmea2000/message.py"
# Ожидаем: yacht-n2k-console-patch-v2

# source_id использует unique_number?
ssh user@<gateway-host> "sudo docker exec homeassistant grep 'source_iso_name\.' \
  /usr/local/lib/python3.14/site-packages/nmea2000/message.py"
# Ожидаем: self.source_iso_name.unique_number
```

### Дубли в HA → clean-ha

```bash
./deploy.sh --clean-ha
# После: .venv/bin/python -m pytest tests/test_live_ha_integration.py -v
```

---

## 🔌 Оборудование

### Yacht Devices YDNU-02

- **Linux порт:** `/dev/ttyACM0` (CDC ACM)
- **SA на шине:** 64 (0x40)
- **unique_number:** 402047 (прошит, неизменный)
- **⚠️ device_instance** (в iso_name.name) меняется при каждой переинициализации шины!

### TCP-GW Virtual Device

- **SA:** 200 (0xC8) — виртуальный
- **unique_number:** 902047 (synthetic)

### NMEA 2000 ISO NAME структура (64-бит)

```
Bits 63-43: unique_number (21 bits)  ← прошит производителем, NEVER changes
Bits 42-32: manufacturer_code (11 bits)
Bits 31-28: device_instance_upper (4 bits)  ← МЕНЯЕТСЯ при рестарте!
Bits 27-21: device_instance_lower (7 bits)  ← МЕНЯЕТСЯ при рестарте!
Bits 20-16: device_function (5 bits)
...
```

**Вывод:** Использовать только `unique_number` для стабильных идентификаторов.

### Gobius C NMEA 2000

- **SA:** 92 (0x5C), **unique_number:** 697207
- **BLE:** `GOBIUS C`, MAC `2C:A7:74:21:56:D8`
- **PGN 127505:** interval ~2.5s
- **⚠️ Баг прошивки:** fluid_type в PGN 127505 всегда 0x00 (Fuel)

#### BLE GATT

| UUID | Имя | R/W | Описание |
|------|-----|-----|----------|
| `0xFFE6` | User Config | R/W | dist_empty/full mm, LP filters |
| `0xFFE7` | Command | W | 3-byte command (calibrate, reset, adv, info write) |
| `0xFFE8` | Status | R | state, uptime, temp, voltage, MAC, range |
| `0xFFE9` | Measurement | R+Notify | fill ‰, distance mm, inclination |
| `0xFFEB` | Info 1 | R/W | User label (20 bytes) |
| `0xFFEC` | Info 2 | R/W | User comment (20 bytes) |
| `0xFFF2` | N2K Config | R/W | N2K enable, instance, fluid type, volume (max 255L!) |
| `0xFFF3` | N2K Status | R | n2k_state, n2k_src |

**⚠️ Опасные команды (0xFFE7):**
- `b'\x69\x00\x00'` (`i`) → factory reset!
- `b'\x6F\x00\x00'` (`o`) → BLE off, переподключение в течение 10 сек

**Info write sequence:**
```
1. write_char(0xFFEB, info1)
2. write_char(0xFFEC, info2)
3. write_char(0xFFE7, b'\x77\x00\x00')  ← commit обязателен!
```

### Mopeka Pro 200 BLE

- **MAC:** `F1:FD:CB:6C:B2:CC`, passive advertisement
- `fill_level_pct = ((tank_depth - distance_mm) / tank_depth) × 100`

---

## 📦 Форк nmea2000

**Путь:** `/Users/denn/Develop/yacht/nmea2000`
**Ветка:** `fix/pgn-126996-hash-collision-per-source`

### Изменения относительно upstream

| Файл | Изменение | Статус PR |
|------|-----------|-----------|
| `nmea2000/message.py` | `source_id = unique_number` (было `.name`) | pending |
| `nmea2000/ioclient.py` | EOF → `ConnectionError` | merged PR #61 |
| `nmea2000/decoder.py` | архитектурная документация | — |

### Тесты форка

```bash
cd /Users/denn/Develop/yacht/nmea2000
python3 -m pytest tests/ -v   # test_decoder.py проверяет хэши SA=64 и SA=200
```

---

## ⚠️ Правила

1. **Никогда не трогать тесты** без явного подтверждения бага в тесте (из AGENTS.md)
2. **Перед деплоем** — pre_deploy_diff показывается автоматически
3. **После --patch-ha** — HA перезапускается только если что-то реально изменилось
4. **После --clean-ha** — обязательно запустить live тесты
5. **При появлении дублей** в HA → deploy.sh --clean-ha (patch-v2 предотвращает новые дубли)
6. **test_service_mode.py** падает в sandbox (socket.bind) — это нормально, не баг кода
7. **nmea2000 устанавливается из git форка** (requirements.txt) — не из PyPI upstream
