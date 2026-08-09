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
cd <project-root>/yacht-n2k-console
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

---

## 📦 Форк nmea2000

**Путь:** `<project-root>/nmea2000`
**Ветка:** `fix/pgn-126996-hash-collision-per-source`

### Изменения относительно upstream

| Файл | Изменение | Статус PR |
|------|-----------|-----------|
| `nmea2000/message.py` | `source_id = unique_number` (было `.name`) | pending |
| `nmea2000/ioclient.py` | EOF → `ConnectionError` | merged PR #61 |
| `nmea2000/decoder.py` | архитектурная документация | — |

### Тесты форка

```bash
cd <project-root>/nmea2000
python3 -m pytest tests/ -v   # test_decoder.py проверяет хэши SA=64 и SA=200
```

---

## ⚡ FastPacket Assembly — КРИТИЧЕСКОЕ ПРАВИЛО

### _n2k_decoder singleton — stateful, НЕ thread-safe

`ydnu02/pgn_decoder.py` содержит модульный синглтон `_n2k_decoder = NMEA2000Decoder()`.
Это **stateful** объект — он accumulates FastPacket sub-frames и собирает полный пакет
только когда приходит **последний** sub-frame в последовательности.

### ⛔ ЗАПРЕТ: двойной вызов decode() на один фрейм

**НИКОГДА** не кормить один и тот же CAN frame в `_n2k_decoder.decode()` дважды.
Двойной вызов отравляет sequence counter → assembly **ВСЕГДА** ломается → 0 собранных пакетов.

Точки вызова `_n2k_decoder.decode()`:
1. `decode_pgn()` — для human-readable строк (Monitor tab)
2. `_decode_via_lib()` — для PGN 60928 field extraction
3. `feed_to_lib()` — для FastPacket assembly (PGN 126996 Product Info)

**Правило разделения:**
- `decode_pgn()` **пропускает** FastPacket PGNs и возвращает raw hex строку
- `feed_to_lib()` — **единственная** точка входа для FastPacket assembly
- `_decode_via_lib()` — только для single-frame PGNs (60928 и т.д.)

### FastPacket PGNs (multi-frame)
```python
_FAST_PACKET_PGNS = {126996, 126998, 129029, 129540, 130567, 130577}
```

### Поток данных SensorRegistry.update()
```
BusWorker → parse_raw_line(line) → update(parsed)
  ├─ PGN 60928: parse_device_info() → claimed, manufacturer, function_name, unique_id
  ├─ ALL frames: feed_to_lib(parsed) → FastPacket assembly
  │   └─ if assembled PGN 126996: → model, firmware, serial, model_version
  └─ PGN 127505: → fluid level
```

---

## 🏷️ Device Card Display — цепочка имён

### DisplayName fallback (network.js)
```
model → modelVersion → funcName → cleanMfr → "Device (SRC N)"
```

- `model` = из PGN 126996 `modelId` (например `YDNU-02 TCP-GW`)
- `modelVersion` = из PGN 126996 `modelVersion` (например `yacht-n2k-console`)
- `funcName` = из PGN 60928 `device_function` (например `PC Gateway`)
- `cleanMfr` = manufacturer, **но** отфильтрованы сырые строки `MfgCode N`, `Custom / Reserved (N)`
- Fallback = `Device (SRC N)`

### Badge (тип устройства)
```javascript
model/func contains 'tcp'     → 'TCP GW'
func contains 'gateway'       → 'USB GW'
mfr contains 'gobius'         → 'GOBIUS'
```

### unique_id=0 (SA=0)
`unique_id == 0` в ISO Address Claim (PGN 60928) означает **неинициализированный / транзитный вызов** при старте шлюза YDNU-02 (ISO 11783-5).
Настоящее N2K устройство обязано иметь `unique_id > 0`. 
`SensorRegistry` и `N2KDeviceRegistry` устанавливают `claimed = True` строго при `unique_id > 0`.
При получении валидного claim (например `src=64`, `unique_id=402047`), транзитные фантомные записи с `src=0` и `unique_id=0` автоматически зачищаются.
Проверка `fullyIdentified` на фронтенде (`network.js`) также требует `Number(uniqueId) > 0`.

---

## ✅ Аудит соответствия официальному протоколу YDNU-02

**Источник:** официальный User Manual YDNU-02 (60 стр., PDF с сайта yachtd.com,
`https://www.yachtd.com/downloads/ydnu02.pdf`), Appendix E (RAW mode) и Appendix F (N2K/DLE mode).

### RAW mode (Appendix E) — используется в проекте

Официальный формат: `hh:mm:ss.ddd D msgid b0 b1 ... b7<CR><LF>`, где `D` = `R` (из шины) или `T` (в шину),
`msgid` — 29-бит CAN ID в hex, данные — 1..8 байт в hex. Исходящие сообщения (host→устройство) —
тот же формат, но без времени/направления, обязательно завершаются `<CR><LF>`.

Сверка с кодом (`ydnu02_tcp_gateway/frame_utils.py`):
- `NMEA_LINE_RE = rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"` — **соответствует**.
- `TX_LINE_RE = rb"^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r?\n$"` — **соответствует** (мануал требует `<CR><LF>` для записи в устройство).
- `get_pgn_sa()` → `N2KPGNDecoder.parse_can_id()` корректно реализует стандартное извлечение
  PGN/SA/DST из 29-бит CAN ID (PF<240 → PDU1/destination-specific, PF≥240 → PDU2/broadcast) —
  соответствует NMEA 2000/J1939 стандарту, расхождений с мануалом нет.

**Вывод:** реализация RAW-режима в проекте полностью соответствует официальной спецификации Yacht Devices.
Расхождений между документацией, скиллом и исходным кодом не найдено.

### N2K mode (Appendix F, DLE-encoding) — НЕ используется

Бинарный режим на основе DLE/STX/ETX (совместим с ActiSense NGT / Garmin Serial Protocol) в проекте
не реализован и не требуется — гейтвей и весь стек работают исключительно в RAW-режиме (текстовый ASCII).

---

## 🖥️ Sailing Dashboard (HA, `ha/sailing-dash/`)

Source-controlled копия storage-mode Lovelace-дашборда "Sailing"
(`http://bumblebee.local:8123/dashboard-sailing/`). Полная документация —
`ha/sailing-dash/README.md`, здесь только сводка для быстрой навигации.

### Файлы
- `dashboard-sailing.yaml` — `views:` конфиг (Lovelace "sections"), source of truth.
- `sensors-sailing.yaml` — `rest:`/`template:`/`device_tracker:` энтити, которые
  НЕ публикуются `ydnu02_tcp_gateway` (open-meteo forecast, barometer_mmhg,
  boat_latitude/longitude, device_tracker.nevera).
- `deploy_dashboard.sh` / `deploy_sensors.sh` — идемпотентный деплой (round-trip
  YAML → `.storage/lovelace.dashboard_sailing` JSON; merge по `unique_id` в
  `configuration.yaml`). **Оба перезапускают HA.**
- `lovelace-resources.yaml` — HACS-карточки: `card-mod`, `compass-card`, `apexcharts-card`.
- `requirements-ha.txt` — чек-лист для установки на "пустой" HA с нуля: HACS +
  3 карточки выше, какие части уже встроены в HA core (rest/template/map/...),
  и ссылка на deploy-tooling зависимости (`pyyaml`/`websockets` в корневом
  `requirements.txt`).

### Раскладка дашборда меняется пользователем через HA UI
Пользователь несколько раз перекомпоновывал плитки прямо в интерфейсе HA
(заголовки/позиции секций Wind & Forecast / Weather & Forecast / Position
менялись). После каждой такой правки `dashboard-sailing.yaml` нужно
**забрать заново** из `.storage/lovelace.dashboard_sailing` (SSH →
`docker exec homeassistant cat /config/.storage/lovelace.dashboard_sailing`
→ `data.config` → сконвертировать в YAML), а не редактировать вручную —
файл в репо должен зеркалить текущую живую раскладку.

### ⚠️ ПРАВИЛО: всегда сравнивать HA с локальной версией ПЕРЕД деплоем (2026-08-09)
Пользователь может в любой момент поменять что-то руками через HA UI —
слепой деплой локального YAML затрёт эти изменения. Поэтому **перед любым
деплоем** дашборда/сенсоров нужно сначала сравнить живой конфиг с локальным
файлом, а не наоборот. Это уже встроено в скрипты:
- `deploy_dashboard.sh` (шаг 2b) сам тянет живой `.storage`, конвертирует в
  YAML и печатает diff с `dashboard-sailing.yaml` перед загрузкой;
  `REQUIRE_CLEAN_DIFF=1` заставляет прерваться при расхождении.
- `deploy_sensors.sh` (шаг 2) сам фетчит remote `configuration.yaml` и мержит
  с `sensors-sailing.yaml`, печатая diff перед загрузкой.
Если diff показывает неожиданные изменения — сначала забрать живую версию в
репо (как в разделе выше), потом накатывать свои правки поверх неё.

**Уточнение (2026-08-09 17:30, правило нарушено второй раз):** сравнивать
нужно НЕ только перед деплоем, а **перед началом любой правки**. Обязательный
порядок:
1. `ssh <host> "sudo docker exec homeassistant cat /config/.storage/lovelace.dashboard_sailing"`
   → `data.config` → YAML → **синхронизировать локальный файл с live** (ручные
   правки пользователя в UI всегда приоритетны, их нельзя перезаписывать);
2. только потом вносить новое изменение поверх;
3. проверить, что diff live-vs-local содержит **только** это изменение;
4. деплоить через `./deploy.sh` (никогда ad-hoc `scp`/`docker cp`).
Пример того, что было потеряно бы: заголовок секции «Wind & Forecast» → «Wind»,
title карточки «Wind — History & Forecast» → «Wind History & Forecast», удалён
подзаголовок «Wind Direction & Speed — Vector Chart».

### card-mod и `!important` (2026-08-09)
Стили `card_mod` вставляются как `<style>` в shadow root карточки, а собственный
CSS HA приходит через `adoptedStyleSheets`, которые каскад применяет **после**
обычных `<style>`. Поэтому правила с одинаковой специфичностью (например
`.entity { flex-direction: column }` у `hui-glance-card`) перебивают card-mod —
все декларации в `card_mod.style` нужно помечать `!important`. Так сделана
шапка значений над вектор-графиком ветра: `flex-direction: column-reverse` +
крупное значение 26px сверху и мелкая подпись 12px снизу (вид как у
`show_states` у apexcharts-card).

### ⚠️ Storage-mode дашборд НЕ подхватывается без рестарта HA
HA читает `.storage/lovelace.dashboard_sailing` один раз при старте и дальше
отдаёт фронтенду копию из памяти (websocket `lovelace/config`). `docker cp`
в этот файл **не влияет на то, что видит браузер**, пока HA не перезапущен
(hard-refresh не помогает, HA может ещё и перезаписать файл из памяти).
Из-за этого фикс `rangeStart` 2026-08-09 сначала «не сработал». Теперь
`deploy_dashboard.sh` делает `docker restart homeassistant` (отключается
через `SKIP_RESTART=1`). Проверка того, что реально отдаёт HA:
websocket `/api/websocket` → auth (HA_TOKEN из `.env`) →
`{"type":"lovelace/config","url_path":"dashboard-sailing"}`.

### Позиция на карте — GPS лодки, НЕ телефон
Карта использует `device_tracker.nevera` (`template: device_tracker` в
`sensors-sailing.yaml`), производный от N2K-позиции (PGN 129025/129029,
Raymarine display) — то есть **собственный GPS лодки**.
`device_tracker.iphone_17_promax_nevera` (HA Companion App, GPS телефона)
существует, но **не используется** в карточке — трекает того, у кого телефон,
а не лодку (баг был исправлен 2026-08-09: карта раньше указывала на телефон).

### Wind History & Forecast — реальный баг найден и исправлен (2026-08-09)
Цепочка: `api.open-meteo.com` (`rest: resource_template`, теперь templated
по live GPS лодки, не статические координаты) → `sensor.wind_forecast_rest`
→ `sensor.wind_forecast_flat` (`template:`, атрибуты `forecast_time`/
`forecast_wind`/`forecast_gust`) → `apexcharts-card` `data_generator` на
дашборде.

**Реальная причина** (не кэш браузера, как предполагалось изначально):
обе `data_generator`-строки объявляли `const start = ...`, а
apexcharts-card вызывает `data_generator` как
`new Function('entity','start','end','hass','moment', code)` — `start`
уже параметр функции. Повторное `const start` — это `SyntaxError`
(`Identifier 'start' has already been declared`), бросается синхронно на
КАЖДОМ вычислении → forecast/gust линии никогда не рисовались, независимо
от кэша/hard-refresh. Подтверждено воспроизведением throw через
`new Function(...)` в Node (вне HA/браузера) на реальной строке из
дашборда. Фикс — переименовать локальную переменную в `rangeStart`.

Проверено и **не** было причиной (аудит до нахождения реального бага):
доступность open-meteo API, наполненность атрибутов `sensor.wind_forecast_flat`
(48 точек, свежий `last_updated`), установка/регистрация `apexcharts-card.js`,
идентичность deployed `.storage` конфига и репозиторного YAML,
`fill_raw: 'null'` (дефолт карты). `cache: false` на карте добавлен как
доп. защита от `localStorage`-кэша apexcharts-card (hard-refresh его не
чистит), но настоящей причиной был именно баг с `start`.

HA-нативная интеграция "Open-Meteo" (config-flow) была опробована как
альтернатива, но её `hourly`-forecast не содержит `wind_speed`/`wind_gust`
(только `condition`/`precipitation`/`temperature`, ветер — только daily max
или current) → не подходит для этого графика, интеграция удалена.

### Wind Direction — windrose (история) + стрелки (форекаст), финал 2026-08-09 v3
Два предыдущих варианта убраны по фидбеку пользователя (v1: число градусов
по оси Y ApexCharts — бессмысленно для компасного направления, скачки на
360°/0°; v2: строка из 8 крупных повёрнутых `mdi:navigation` тайлов — не
"график" и иконки слишком большие). Финально — 2 части:
1. **История — `custom:windrose-card`** (github.com/aukedejong/lovelace-windrose-card
   v2.4.2) — настоящая полярная роза направления/скорости ветра из живой
   recorder-истории (`sensor.wind_direction_history` + сенсор скорости
   Raymarine), `data_period.period_back: -24h`, красная стрелка текущего
   направления (`current_direction.show_arrow`). **НЕ через HACS** (не входит
   в 3 карточки из requirements-ha.txt) — `.js` скачан вручную с GitHub
   Release, скопирован в `/config/www/windrose-card.js` на bumblebee.local,
    зарегистрирован как resource (`/local/windrose-card.js`, `type: module`)
    прямо в `.storage/lovelace_resources`.
   **Скорость ветра на розе (2026-08-09):** отображается тремя способами —
   цвет лепестков по `windspeed_entities`, вертикальная шкала-легенда
   (`windspeed_bar_location: right`) и числовое значение в углу карточки
   (`corner_info.top_right`, тот же сенсор скорости Raymarine, в узлах).
2. **Форекаст — заменён 2026-08-09 на график-таймлайн.** Строка из 8
   маленьких повёрнутых стрелок-плиток была явно отвергнута пользователем
   ("не то что нарисовал"); вместо неё добавлена (роза выше НЕ трогалась,
   пользователь явно попросил её оставить — это дополнение, а не замена)
   новая карточка `custom:apexcharts-card` **"Wind Direction & Speed —
   Timeline"** по образцу
   https://community.plotly.com/t/wind-direction-and-speed-timeline/94120/3:
   ось X — время, левая ось Y (`yaxis_id: speed`) — скорость ветра (история
   + форекаст/гасты, те же серии что и в верхнем графике "Wind — History &
   Forecast"), правая ось Y (`yaxis_id: direction`, `min:0/max:360`) —
   направление: история с `sensor.wind_direction_history` (маркеры,
   `stroke_width: 0`) и прогноз `forecast_dir` (пунктирная линия, тот же
   `data_generator`-паттерн что у `forecast_wind`/`forecast_gust`).

Подтверждено чтением исходника `apexcharts-card.js` напрямую: нет generic
API для per-point картинок/поворота маркера — только `data_generator`/
`transform` для целой серии, поэтому направление на новом графике
показывается как обычное числовое значение (0-360°), а не повёрнутыми
стрелками; визуально как роза ветров направление отображается только на
`windrose-card` выше.

**Позиция розы изменилась 2026-08-09 (~13:40) — правка пользователя в HA UI.**
Пользователь заменил `custom:compass-card` ветра (в самом верху секции
"Wind & Forecast") на `custom:windrose-card` прямо в UI и удалил
дублирующую подсекцию "Wind Direction — History (rose)" — роза теперь
одна, наверху секции; ветровой `compass-card` больше не используется
(COG `compass-card` в "Position" не затронут). `dashboard-sailing.yaml`
пере-синхронизирован из `.storage`.

**Проверка ошибки "Configuration error: value.series[0].type is none of
line/column/area" (скриншот 2026-08-09):** схема `apexcharts-card.js`
v2.2.3 (извлечена прямо с bumblebee.local) подтверждает — `type` реально
принимает только `line`/`column`/`area`, а `yaxis_id` — валидное
schema-поле серии. У обоих графиков ("Wind — History & Forecast" и "Wind
Direction & Speed — Timeline") в деплоенном конфиге все серии имеют
`type: area`/`line` — ошибка на этом конфиге не воспроизводится; если
после hard-refresh она всё ещё видна, нужен вывод браузерной консоли для
точной идентификации карточки.

**⚠️ Правило (после этого нарушения):** перед любой правкой
`dashboard-sailing.yaml` — сначала смэтчить с live `.storage` конфигом
(`deploy_dashboard.sh` делает pre-deploy diff автоматически, но при
расследовании — сверять и вручную), пользователь часто правит дашборд
прямо в HA UI.

**Вторая "Configuration error" (2026-08-09, `value.series[3].apex_config
is extraneous`):** реальный баг — у 4-й серии (индекс 3, "Direction (°)")
графика "Wind Direction & Speed — Timeline" было своё поле
`apex_config: {markers: {size: 3}}` прямо внутри серии. В схеме
apexcharts-card `apex_config` существует только на уровне карточки —
per-series такого поля нет вообще, поэтому валидатор отвергал конфиг
целиком при каждой загрузке. Фикс — перенести размер маркера в
карточный `apex_config.markers.size` как массив по одному значению на
серию (`[0, 0, 0, 3, 0]`, маркер только у "Direction (°)").

**График вечно грузится, ничего не рисует (2026-08-09):** реальный runtime-
баг, не связанный с schema-валидацией — подтверждён headless Playwright
сессией (авторизация через `hassTokens` в `localStorage` с long-lived
токеном) против живого дашборда: в консоли браузера `pageerror: Cannot
read properties of undefined (reading 'push')` именно на карточке "Wind
Direction & Speed — Timeline", остальные карточки (включая почти
идентичный график "Wind — History & Forecast") рендерились нормально.
Причина — комбинация dual-Y-axis (`apex_config.yaxis` массив с `id`/
`seriesName` + per-series `yaxis_id`) с миксом recorder-history серий и
`data_generator`-серий на разных осях — внутренняя группировка данных по
осям в apexcharts-card v2.2.3 падает на этой комбинации. Фикс — убран
dual-axis дизайн полностью, график стал direction-only ("Wind Direction —
History & Forecast"), одна ось Y 0-360°, `tickAmount: 6`, по образцу
рабочего графика скорости выше (скорость уже показана там же, ничего не
потеряно). Проверено повторным headless-прогоном после деплоя — `pageerror`
пропал, график реально рисует линию истории направления + пунктир прогноза.

### График-стрелки ветра (вектор) — apexcharts-card не может, план на plotly-graph-card (2026-08-09)
Пользователь хочет график как в `ha/sailing-dash/examples/wind-diraction-n-speed.png`
(Python Plotly): ось X — время, ось Y — скорость, каждая точка — повёрнутая
стрелка (угол = направление, цвет/длина = скорость). Подтверждено —
`apexcharts-card` этого не умеет (только числовые line/column/area точки,
нет per-point rotate/image marker API). Найдена замена:
`custom:plotly-graph` (github.com/dbuezas/lovelace-plotly-graph-card, HACS,
637★, бандлит Plotly.js 2.34) — поддерживает произвольные Plotly Scatter
свойства (`marker.symbol`/`angle`/`angleref`/`colorscale`) и универсальные
JS-функции (`$fn`/`$ex`) на ЛЮБОМ ключе конфига, включая `filters:` с
`store_var`/`vars` для передачи direction между сущностями. Черновик
конфига (НЕ задеплоен, ждёт решения пользователя из-за веса Plotly.js) —
см. README раздел "Wind vector/arrow chart — apexcharts-card limitation
 and the plotly-graph-card plan".

### Локальный офлайн-стенд для превью карточек (2026-08-09)
`ha/sailing-dash/local-preview/` — HTML-страница + headless Playwright
раннер, рендерящие настоящие (скачанные из релизов) бандлы
`apexcharts-card`/`windrose-card`/`compass-card`/`plotly-graph-card` против
фейкового `hass` (без живого HA). Первые 3 конфига в `card-configs.js`
скопированы 1-в-1 из `dashboard-sailing.yaml` — реальная регрессионная
проверка; черновик `plotly-graph-card` помечен `DRAFT`. Ловит именно
schema-ошибки apexcharts-card (`value.series[...] is extraneous` и т.п.) до
деплоя. `fetch-vendor.sh` скачивает бандлы (не коммитятся, `.gitignore`).
Исправлено (2026-08-09): `plotly-graph-card` DRAFT-запись таймаутила
навечно из-за неверного тега custom element в `card-configs.js`
(`plotly-graph-card` вместо реального `plotly-graph` — бандл сам
регистрируется как `var ON=d3?"plotly-graph":"plotly-graph-dev"`, `d3` всегда
`true`). Плюс карточка тянет историю через `hass.callApi('GET',
'history/period/...')`, а не `hass.callWS(...)` как windrose-card — в
`mock-hass.js` добавлена реализация `callApi` для этого REST-формата.
Безвредный `pageerror: setHtmlElements` в консоли может оставаться — не
блокирует рендер (статус карточки — OK).

Также исправлен белый фон карточки в стенде: CSS-переменные темы
(`--card-background-color` и т.п.) в `index.html` были привязаны к
селектору `ha-card`, а `render.js` вставляет карточки прямо в `.card-slot`
без обёртки `<ha-card>` — `plotly-graph-card` читает эти переменные сам
через `getComputedStyle()` и не находил их. Перенесены на `:root` — теперь
темизация наследуется во все карточки независимо от DOM-обёртки.

**Стрелки направления ветра (2026-08-09, 3 итерации по фидбеку пользователя):**
(1) `marker.symbol: triangle-up` + `angle` — треугольник неотличим при
повороте на 0° и 180°; (2) `arrow-bar-up` (голова + перпендикулярная
планка) — пользователь: «это как хорда через треугольник», не похоже на
вектор; (3) **финал** — настоящие Plotly `annotation`-стрелки
(`showarrow: true`, `ax/ay` в пикселях = хвост, `x/y` = точка данных = наконечник)
строятся по одной на точку через `$fn` в `layout.annotations`, используя
`vars.speed`/`vars.forecastSpeed` (сохранены через `store_var` на видимых
точках-маркерах скорости) и `vars.dir`/`vars.forecastDir` — угол считается
как `0°=север=вверх экрана, по часовой`. Там же генерируется линия "Now" +
подсказка "▲ N / ▼ S" (у `plotly-graph-card` нет своего `now:`, как у
apexcharts-card). `resample` увеличен с `5m` до `30m` (иначе ~360 стрелок
на трассу сливались в пятно).

**Цветовая шкала скорости ветра (2026-08-09):** дефолтный `RdYlGn` (без
фиксированного диапазона, "от балды" по фидбеку пользователя) заменён на
явный `WIND_SPEED_COLORSCALE` с фиксированным диапазоном 0–40kt и цветами
по морской конвенции (windy.com, карты NOAA): штиль = светло-голубой →
зелёный → жёлтый → оранжевый → красный → штормовой (35kt+) = фиолетовый,
плюс подписанный colorbar (`title: 'kt'`). Стрелки направления теперь тоже
окрашены по этой же шкале скорости (не фиксированным цветом по трассе
measured/forecast, как раньше) — цвет везде означает одно и то же: силу
ветра. См. `README.md` раздел "Wind vector/arrow chart" и
`local-preview/README.md`.

### Wind — History & Forecast: фикс окна графика при загрузке (2026-08-09)
`graph_span`+`span` у apexcharts-card жёстко задают И запрос данных, И
границы оси X при загрузке — отдельного "начального зума" не существует.
Было `graph_span: 30h`, `span: {start: hour, offset: -6h}` → окно [now до
часа −6h, +24h], из-за чего замеры сжимались в узкую полоску слева
(форекаст занимал большую часть) и выглядело "случайно отцентрованным",
требуя ручного авто-масштаба. Исправлено на `graph_span: 26h`,
`span: {start: minute, offset: -2h}` — без округления по часам, окно
всегда точно "сейчас −2h ... +24h", поэтому последние 2 часа замеров
всегда видны слева при каждой загрузке. `rangeStart` в обоих
`data_generator` (форекаст/гасты) синхронизирован с -6h на -2h. Также в
черновике `plotly-graph-card` (не задеплоен) добавлена горизонтальная
легенда снизу (`layout.legend: {orientation: h, y: -0.2}`) — для единого
стиля с апекс-графиком выше.

### plotly-graph-card DRAFT: позиция "Now", легенда, реальный toggle Measured/Forecast (2026-08-09)
Три жалобы пользователя, всё в неразвёрнутом черновике `plotly-graph-card`
(`local-preview/card-configs.js`, не задеплоен на HA):
1. **"Now" не в 2ч от левой оси** — у этой карточки нет аналога
   `span`/`graph_span` apexcharts-card, поэтому без явного `xaxis.range`
   она авто-масштабируется на весь диапазон истории+форекаста. Добавлен
   `layout.xaxis.range: $fn () => [now-2h, now+24h]` — тот же якорь,
   что и у апекс-графика "Wind — History & Forecast".
2. **Легенда перекрывает подписи оси X** — легенда снизу (`y: -0.3`) не
   имела зарезервированного места под собой; карточка не увеличивает
   нижний margin автоматически под легенду сама. Добавлен
   `layout.margin: {b: 70}`.
3. **Toggle Measured/Forecast не скрывал данные** — стрелки направления
   рисовались отдельным слоем `layout.annotations` (см. предыдущую запись
   про 3 итерации), который считается один раз из `vars` и НЕ реагирует
   на клик по легенде (клик по легенде у Plotly нативно скрывает/показывает
   именно *trace*, а не произвольный слой аннотаций) — поэтому стрелки
   оставались видимыми независимо от переключателя. Исправлено: стрелка
   теперь рисуется как `marker.symbol: 'arrow'` + `marker.angle` прямо на
   самом trace "Measured"/"Forecast" (вместо отдельного annotation-слоя) —
   скрытие trace через легенду теперь нативно скрывает и его стрелки.
   `layout.annotations` упрощён — строит только подпись "Now" + "▲ N / ▼ S".
   Добавлен `legend.groupclick: togglegroup` на случай появления второго
   trace в группе.

### plotly-graph-card ЗАДЕПЛОЕН на bumblebee.local + единый deploy.sh (2026-08-09)
Карточка "Wind Direction & Speed — Vector Chart" (`custom:plotly-graph`,
дизайн — точки-маркеры + отдельный слой `layout.annotations` со
стрелками-векторами) добавлена в `dashboard-sailing.yaml` и реально
задеплоена на живой HA. Подтверждено headless Playwright-заходом на
`http://bumblebee.local:8123/dashboard-sailing/` (логин через
`localStorage.hassTokens` с `HA_TOKEN` из `.env`): в консоли
`PLOTLY-GRAPH 3.3.5 production` без `Configuration error`, на скриншоте
видны реальные точки/стрелки/цветовая шкала/линия "Now" с живыми данными
Raymarine + open-meteo.

Пользователь указал, что ручная установка ресурса через `scp`/`docker cp`
(как раньше `windrose-card`) обходит единый процесс деплоя — создан
`ha/sailing-dash/deploy.sh` (НЕ путать с корневым `./deploy.sh` для
gateway/HA-патчей — разные скрипты в разных директориях), теперь это
**единственный поддерживаемый способ** деплоя чего-либо в этом стеке:
```bash
./deploy.sh --install|--update             # resources + sensors + dashboard
./deploy.sh --resources-only|--dashboard-only|--sensors-only
```
`--resources-only`/`--install` читают `lovelace-resources.yaml`,
подтягивают `/local/*.js`-записи, идемпотентно (по base URL, без query)
мержат их в `.storage/lovelace_resources` и заливают сам `.js`-бандл из
`local-preview/vendor/` в `/config/www/` — заменяет прежние разовые SSH-
команды. `--update`/`--install` вызывают существующие
`deploy_dashboard.sh`/`deploy_sensors.sh` как последний шаг.

### Windy — альтернативный виджет, тап прямо по iframe (2026-08-09)
В секцию "Wind History & Forecast" добавлена `type: iframe` карточка со
встроенным Windy-виджетом (`embed.windy.com/embed2.html?...`, координаты
42.43/18.60) — показывает форекаст и историю ветра из Windy как визуальную
альтернативу графику `apexcharts-card`.

Отдельная кнопка "Open Windy" убрана — вместо неё тап/клик прямо по iframe
открывает windy.com (приложение на мобильном, браузер на десктопе).
Реализовано CSS-трюком через `card-mod`: iframe и полностью прозрачная
`type: button` карточка помещены в один `type: grid` и через `card_mod`
(`display: grid` на секции + `grid-column/grid-row: 1` на обеих дочерних
карточках) наложены друг на друга в одной CSS grid-ячейке — невидимая
`ha-card` кнопки перехватывает тапы поверх iframe и выполняет
`tap_action: {action: url, url_path: https://www.windy.com/...}`.
`windy.com` зарегистрирован официальным приложением Windy как Universal
Link (iOS) / App Link (Android) — если приложение установлено, тап
открывает его напрямую, иначе (или на десктопе) — обычный веб-браузер.
Ни `browser_mod`, ни кастомные условия для этого не нужны.

⚠️ Этот `card_mod`-оверлей зависит от DOM-структуры карточек `grid`/`button`,
которая может отличаться между версиями HA frontend — если тап не работает
после деплоя, нужно проверить в DevTools, что `ha-card` кнопки реально
перекрывает область iframe, и поправить селекторы в `dashboard-sailing.yaml`.

### Компасы Wind/COG — стилизация циферблата (2026-08-09)
Раньше обе `custom:compass-card` (Wind в "Wind & Forecast", COG в "Position")
использовали минимальный конфиг (только `indicator_sensors`) → рендерились
как «просто кружочки» с плавающей стрелкой без делений и сторон света.
Добавлен объект `compass:` (документация:
`https://github.com/tomvanswam/compass-card/wiki/1.-YAML-configuration`):
- `compass.circle.color: '#37474f'` — тёмный фон циферблата.
- `compass.ticks: {show: true, color: '#90a4ae', radius: 52}` — деления по кругу.
- `compass.north/east/south/west: {show: true}` — буквы N/E/S/W (по умолчанию скрыты).
- `header.icon` — иконка в заголовке (`mdi:weather-windy` / `mdi:compass-outline`).
- `indicator.color` — цветная стрелка (`#4fc3f7` ветер, `#ff7043` COG) для контраста с тёмным фоном.
- COG-компас дополнительно получил SOG как `value_sensors` (раньше показывал
  только стрелку без числа); SOG также остаётся отдельным gauge в "Speed & Depth".

---

## ⚠️ Правила

1. **Никогда не трогать тесты** без явного подтверждения бага в тесте (из AGENTS.md)
2. **Перед деплоем** — pre_deploy_diff показывается автоматически
3. **После --patch-ha** — HA перезапускается только если что-то реально изменилось
4. **После --clean-ha** — обязательно запустить live тесты
5. **При появлении дублей** в HA → deploy.sh --clean-ha (patch-v2 предотвращает новые дубли)
6. **test_service_mode.py** падает в sandbox (socket.bind) — это нормально, не баг кода
7. **nmea2000 устанавливается из git форка** (requirements.txt) — не из PyPI upstream
8. **systemd ExecStart** — ОБЯЗАТЕЛЬНО через `python3 -m ydnu02_tcp_gateway.ydnu02_tcp_gateway` (НЕ прямой путь к .py в пакете — Python не распознает package, импорты сломаются)
9. **decode_pgn()** — НИКОГДА не кормить FastPacket PGNs в `_n2k_decoder` (двойной feed = assembly ломается)
10. **deploy.sh** — если менялись файлы из proxy И web групп — нужен полный деплой (без флагов)

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

### N2K Config & Group Function (PGN 126208) Rules

1. **Source Address (`our_src`):** Всегда использовать заявленный адрес виртуального гейтвея (`200` / `0xC8`) или физического YDNU-02 (`64` / `0x40`). Фреймы с невнятным незаявленным адресом (например, `16`) игнорируются устройствами на шине согласно ISO 11783-5.
2. **PGN 127505 Scaling & Encoding:**
   - `Capacity` (поле 4) кодируется в децилитрах (0.1L = 10x) и передаётся строго в **4 байтах uint32 Little-Endian**.
   - `Level` (поле 3) кодируется в единицах 0.004% и передаётся в **2 байтах uint16 Little-Endian**.
3. **Gobius C Write Restrictions:**
   - Gobius C **не поддерживает запись конфигурации через N2K CAN bus (PGN 126208)** и игнорирует такие фреймы (не присылает ACK).
   - Для изменения емкости (`volume_l`) и типа жидкости Gobius C запись должна производиться по **Bluetooth LE через характеристику GATT `0xFFF2` (N2K Config)** или вкладку Gobius C.

---

## 📤 YDNU-02 Serial Write Format (физический USB порт `/dev/ttyACM0`)

> ⚠️ **Критически важно:** YDNU-02 принимает для передачи в CAN-шину ТОЛЬКО строго специфицированный формат. Неверный формат = фрейм молча игнорируется железкой.

### Форматы ASCII-фреймов в системе

| Контекст | Направление | Формат | Regex |
|----------|-------------|--------|-------|
| Из YDNU-02 serial → DataHub (RX) | железо→нам | `HH:MM:SS.mmm R XXXXXXXX XX XX...\n` | `NMEA_LINE_RE` |
| Из YDNU-02 serial → DataHub (TX iron echo) | железо→нам | `HH:MM:SS.mmm T XXXXXXXX XX XX...\n` | `NMEA_LINE_RE` |
| TCP-клиент → DataHub (ISO Requests, command_builder) | клиент→хаб | `XXXXXXXX XX XX...\r\n` | `TX_LINE_RE` |
| **DataHub → YDNU-02 serial (запись в шину)** | **нам→железо** | **`XXXXXXXX XX XX...\r\n`** | ← **единственный верный формат** |
| DataHub → TCP-клиенты (broadcast) | хаб→клиенты | `HH:MM:SS.mmm R XXXXXXXX XX XX...\n` | `NMEA_LINE_RE` |

### Правило формирования raw_tx для `ser.write()`

**Откуда бы ни пришёл фрейм (NMEA_LINE_RE или TX_LINE_RE) — в `ser.write()` всегда передаётся:**

```
XXXXXXXX XX XX XX...\r\n
└──────┘ └──────────┘
CAN ID   DATA BYTES (hex uppercase, разделитель — пробел)
         \r\n обязательны — YDNU-02 требует CRLF
```

**Примеры корректных строк:**

```
19FF04C8 05 00 02 91 7E FF FF 00\r\n   ← PGN 130312 CPU Temp, SA=200
09EE00C8 EE 00 FF\r\n                  ← ISO Request PGN 59904, SA=200
18EAFFC8 00 00 00 00 00 E8 FF 00\r\n   ← ISO Address Claim PGN 60928, SA=200
```

### Преобразование перед `ser.write()`

#### Из NMEA_LINE_RE → raw_tx:
```python
# Вход: b"HH:MM:SS.mmm R 19FF04C8 05 00 02 7E FF FF 00\n"
parts = line.strip().split(b' ')
# parts[0]="HH:MM:SS.mmm"  ← отбрасывается
# parts[1]="R" или "T"     ← отбрасывается
# parts[2]="XXXXXXXX"      ← CAN ID
# parts[3:]=[XX,...]       ← DATA bytes
raw_tx = parts[2] + b' ' + b' '.join(parts[3:]) + b'\r\n'
```

#### Из TX_LINE_RE → raw_tx:
```python
# Вход: b"19FF04C8 05 00 02 7E FF FF 00\r\n"
parts = raw.rstrip(b'\r\n').split(b' ')
# parts[0]="XXXXXXXX"  ← CAN ID
# parts[1:]=[XX,...]   ← DATA bytes
raw_tx = parts[0] + b' ' + b' '.join(parts[1:]) + b'\r\n'
```

### ⚠️ Что YDNU-02 в serial НЕ принимает

| Неверный формат | Проблема |
|-----------------|----------|
| `HH:MM:SS.mmm R XXXXXXXX XX\n` | таймштамп-префикс **запрещён** |
| `XXXXXXXX XX XX\n` | только `\n` без `\r` — YDNU-02 требует **CRLF** |
| `XXXXXXXX XX XX` | без терминатора — пакет **никогда не отправится** |
| `19ff04c8 05 00` | lowercase hex — **не стандартизировано**, только UPPERCASE |

### Защита от петли (loop prevention)

Фреймы, пришедшие из самого `/dev/ttyACM0` через `SerialReader`, поступают в `DataHub.broadcast()` напрямую — они **минуют** `handle_client()` и никогда не попадают обратно в `ser.write()`.

Таким образом, в физический serial пишутся **только фреймы от TCP-клиентов**:
- Фреймы `N2KDevice SA=200` (ISO Claim, Product Info, CPU Temp, Heartbeat)
- ISO Request (PGN 59904) от клиентов
- PGN 126208 Write Config от `n2k_command_builder`

### SA-guard (`data_hub.py::_VIRTUAL_DEVICE_SA = {64, 200}`)

Перед `ser.write()` в обеих ветках `handle_client()` (`NMEA_LINE_RE` и `TX_LINE_RE`) проверяется source address (SA), извлечённый `get_pgn_sa()`:
- `_should_forward_virtual_broadcast_to_serial()` (формат A/broadcast) — пропускает в serial **только** SA∈{64,200} (собственная телеметрия виртуальных устройств), с троттлингом PGN 130312 по `n2k_serial_temp_interval_s`.
- `_should_forward_to_serial()` (формат B/TX от клиента) — **блокирует** SA∈{64,200} (кроме ISO Request PGN 59904), не даёт стороннему клиенту подделать чужой/виртуальный source на физической шине.

Раздельные интервалы телеметрии CPU-temp (PGN 130312): TCP-цикл в `ydnu02_gateway_device.py::_run_device()` шлёт по `settings.n2k_tcp_temp_interval_s` (default 3.0s, читается динамически), форвардинг в serial из `data_hub.py` троттлится независимо по `settings.n2k_serial_temp_interval_s` (default 5.0s).

---

## 🔬 Диагностическое echo-логирование TX-фреймов (ИССЛЕДОВАТЕЛЬСКАЯ ФИЧА)

> ⚠️ Это **не** реализация протокольного ACK — YDNU-02 в RAW-режиме (Appendix E официального мануала) **не имеет** механизма подтверждения доставки на запись в serial. Фича — чисто диагностический/тестовый инструмент, помечена в коде как экспериментальная (`data_hub.py`, `serial_reader.py`).

### Как это работает

1. `DataHub.record_tx_echo_candidate(can_id)` — вызывается сразу после `ser.write()` (в трёх местах: ISO Request, virtual-broadcast forward, client TX forward), запоминает CAN ID и `time.monotonic()` в `self._pending_tx_echo` (окно `_TX_ECHO_WINDOW_S = 3.0s`).
2. `DataHub.check_tx_echo(can_id)` — вызывается `SerialReader` для **каждой** строки, прочитанной с физической шины. Если CAN ID совпадает с недавно записанным — логирует `[data] echo: TX frame ... confirmed on physical bus ... (diagnostic pseudo-ACK, not a protocol ACK)`.
3. Подключено через `SerialReader.__init__(..., check_tx_echo=hub.check_tx_echo)` в `ydnu02_tcp_gateway.py`.

### 🔎 Эмпирическая находка (реальное железо, Pi5 @ `<gateway-host>.local`, 2026-08-01)

Проверено через `journalctl -u ydnu02-tcp-gateway` за 20+ минут работы сервиса (десятки TX-записей: CPU-temp телеметрия каждые 3-5с + ISO Request/Address Claim при старте):

**Ни разу не было залогировано ни одной строки `[data] echo: ...`.**

Проверено и подтверждено, что это не баг проводки:
- `record_tx_echo_candidate()` реально вызывается при каждом TX-write (`data_hub.py`, 3 точки вызова).
- `check_tx_echo()` реально вызывается для каждой прочитанной строки (`serial_reader.py:125`, было `123`).
- Прочее логирование (`[data] client connected/disconnected`) работает нормально (31 совпадение за 200 строк лога) — то есть логи не теряются, просто `echo:` никогда не триггерится.

**Вывод:** физическое устройство YDNU-02 в RAW-режиме **не отражает** (no self-reception) собственные TX-фреймы обратно хосту по USB — вопреки типичному поведению обычных CAN-контроллеров. Либо firmware фильтрует собственные исходящие кадры при формировании `R`-строк для хоста, либо RAW UART-мост вообще не имеет пути self-reception. **Полагаться на echo как индикатор реальной доставки на физическую шину нельзя** — на этом железе он будет молчать всегда. Фича оставлена в коде только как диагностический задел на случай другой прошивки/железа в будущем.

### Побочная находка — единичная ошибка serial при выходе из service-mode

В логах встретилась одноразовая ошибка сразу после переключения из CTRL/service-режима обратно в RAW:
```
[serial] unexpected error: argument must be an int, or have a fileno() method. — retrying in 5s
```
Порт переоткрылся штатно через 5с, без дальнейших проблем. Не расследовано глубже (вероятно гонка между `ctrl_handler` подменой `ser`-хендла и `SerialReader.run()`), помечено в коде (`serial_reader.py`, catch-all `except Exception`) как кандидат для отдельного расследования.
