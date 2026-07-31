# NMEA 2000 Dynamic Network Manager — Walkthrough

## Summary

Заменил hardcoded N2K Config (только Fluid Level, только для датчиков) на **динамический Network Manager** с автоматическим обнаружением полей из библиотеки nmea2000.

## Что изменилось

### Новые файлы

#### [n2k_meta.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/n2k_meta.py) — PGN Field Metadata Engine
Ядро системы. Извлекает metadata полей для **любого** PGN из библиотеки nmea2000:

| Функция | Что делает |
|---|---|
| `get_pgn_field_metadata(pgn)` | Для любого PGN → список полей с типами, enum-опциями, units, configurable |
| `_extract_lookup_keys(pgn)` | Парсит source decode-функции для извлечения ключей master_dict (TANK_TYPE и т.д.) |
| `build_read_fields_frame()` | PGN 126208 Read Fields Request → CAN RAW frame |
| `build_write_fields_frame()` | PGN 126208 Write Fields → CAN RAW frame |
| `build_command_frame()` | PGN 126208 Command Group Function → CAN RAW frame |
| `build_iso_request_frame()` | PGN 59904 ISO Request → CAN RAW frame |
| `parse_device_info()` | Decode PGN 60928/126996 → structured device info |
| `parse_pgn_list()` | Decode PGN 126464 → list of supported PGNs |
| `decode_raw_line()` | Generic CAN frame decode → dict of fields |
| `get_pgn_name()` | PGN number → human name (e.g. "Fluid Level") |

#### [routes/n2k_config.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/routes/n2k_config.py) — REST API
4 новых endpoints:

| Endpoint | Метод | Описание |
|---|---|---|
| `/api/n2k/devices` | GET | Список всех устройств на шине |
| `/api/n2k/devices/{src}/config/{pgn}` | GET | Read Fields — текущие значения с устройства |
| `/api/n2k/devices/{src}/config/{pgn}` | POST | Write → ACK → Read-back → diff |
| `/api/n2k/pgn/{pgn}/metadata` | GET | Metadata полей PGN (типы, enum опции, units) |

### Изменённые файлы

#### [device_manager.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/device_manager.py)
- **Убрано:** hardcoded `mfg_names` dict, ручной парсинг PGN 60928 (mfg_code bit extraction), ручной PGN 126996 (model_bytes), hardcoded PGN 127508/127506 device class names
- **Добавлено:** вызов `N2KPGNDecoder.parse_device_info()` для library-based resolution
- **Добавлены поля:** `function_name`, `device_class_name` в discovered_devices

#### [static/js/n2k_config.js](file:///Users/denn/Develop/3dprint/ha/nmea2000/static/js/n2k_config.js)
- **Убрано:** hardcoded `N2K_PGNS` registry (только PGN 127505 с захардкоженными полями)
- **Добавлено:** динамическая форма из API `/api/n2k/pgn/{pgn}/metadata`:
  - `LOOKUP` → `<select>` с опциями из enum
  - `NUMBER` → `<input type="number">` с unit label
  - `STRING` → `<input type="text">`
  - Read-only поля (level) → disabled
- **Новые кнопки:** 📖 Read (заполняет текущие значения) и 📡 Write (с diff)

#### [static/js/network.js](file:///Users/denn/Develop/3dprint/ha/nmea2000/static/js/network.js)
- Configure button теперь передаёт `active_pgns` массив для dynamic PGN discovery

#### [app.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/app.py)
- Зарегистрирован новый router `n2k_config`

## Тестирование

### На gateway-host
```
=== PGN 127505 Field Metadata ===
instance: number, configurable ✅
type: lookup (TANK_TYPE enum: Fuel/Water/Gray water/Live well/Oil/Black water) ✅
level: number, unit=%, configurable=false ✅
capacity: number, unit=L ✅

=== CAN Frame Generation ===
ISO Request: 18EAFF10 00 EE 00 ✅
Read Fields: 0CED5C10 03 11 F2 01 FF FF FF 00 FF ✅
Command: 18ED5C10 01 11 F2 01 08 02 01 00 02 01 ✅

=== Import Chain ===
n2k_meta OK, 4 routes OK, DeviceManager OK ✅
```

## Что НЕ сделано (Phase 3)
- Live data broadcast для всех PGN (не только 127505)
- Удаление старого `n2k_command_builder.py` (ещё используется в `routes/n2k.py`)
