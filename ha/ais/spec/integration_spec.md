# Спецификация кастомной интеграции `ais_targets`

## Назначение

`ais_targets` — кастомная интеграция Home Assistant (`domain: ais_targets`), которая читает AIS-данные напрямую с TCP-шлюза YDNU-02 (минуя интеграцию `nmea2000`) и предоставляет их в виде временных сущностей `geo_location.ais_<mmsi>` для отображения на карте и в таблице целей.

## Структура пакета

```
ha/ais/custom_components/ais_targets/
├── manifest.json      — метаданные интеграции (domain, config_flow: true, iot_class: local_polling)
├── const.py            — константы: ключи конфигурации, значения по умолчанию, PGN, маппинг полей
├── __init__.py         — установка/выгрузка config entry, запуск AisBusClient, форвард на платформу geo_location
├── ais_bus.py          — TCP-клиент шлюза + декодер AIS + таблица целей в памяти (см. architecture.md)
├── geo_location.py     — платформа geo_location: AisTarget (сущность) и AisTargetsManager (жизненный цикл)
├── config_flow.py       — UI-конфигурация (User Flow + Options Flow)
└── README.md            — документация по пакету и допущениям
```

## Параметры конфигурации (Config Flow)

Единый источник правды для всей конфигурации интеграции — `config_flow.py`. Настраивается один раз через **Settings → Devices & Services → Add Integration → "AIS Targets"**, впоследствии может быть изменена через Options Flow (шестерёнка у интеграции) без переустановки.

| Параметр | Ключ | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| Хост шлюза | `gw_host` | `str` (обязателен) | `127.0.0.1` | Адрес, по которому HA видит TCP-порт шлюза YDNU-02 |
| Порт шлюза | `gw_port` | `int` (обязателен), 1–65535 | `4001` | TCP-порт сырого потока N2K на шлюзе |
| MMSI своей лодки | `own_mmsi` | `str` (опционален) | пусто | MMSI собственного AIS-передатчика («Bumblebee»); при заполнении цель с этим MMSI помечается как собственное судно |
| Интервал обновления | `update_interval` | `int`, 1–3600 сек | `5` | Как часто сущности `geo_location` обновляются из таблицы целей в памяти |
| Таймаут устаревания | `stale_timeout` | `int`, 1–1440 мин | `10` | Через сколько минут без обновления позиции цель удаляется |

**Важно**: интеграция допускает только один экземпляр (single instance) — `self._async_current_entries()` в `async_step_user` возвращает `abort(reason="single_instance_allowed")`, если экземпляр уже настроен.

## Обработка собственного судна (`own_mmsi`)

Поле `own_mmsi` в конфигурации интеграции — **единственный источник правды** для определения, какая из целей на шине является собственной лодкой.

Логика в `geo_location.py`:

```python
is_own = own_mmsi is not None and mmsi == own_mmsi
```

Если целевой MMSI совпадает с `own_mmsi`:
- Сущность получает имя `"⛵ Bumblebee (Own Boat)"` (константа `_OWN_BOAT_NAME`), если статическое имя судна ещё не пришло с шины (`reading.vessel_name or _OWN_BOAT_NAME`).
- В атрибутах цели выставляется `is_own_ship: true`.
- Сущность репортит отдельный `source` — `GEO_LOCATION_SOURCE_OWN = "ais_targets_own"` вместо обычного `GEO_LOCATION_SOURCE = "ais_targets"`.

### Зачем отдельный `source`

Карточка `type: map` использует `geo_location_sources: ['ais_targets']` для отображения **чужих** целей. Так как собственная лодка репортит другой `source` (`ais_targets_own`), она **не будет задвоена** на карте картой целей — но при этом сама сущность `geo_location.ais_<mmsi>` продолжает существовать и присутствует в детальной таблице (которая фильтрует по маске `entity_id: "geo_location.ais_*"`, а не по `source`), со всеми обычными атрибутами AIS-цели.

## Жизненный цикл сущностей `geo_location`

### Создание и обновление (`AisTargetsManager._async_refresh_now`)

На каждом тике `update_interval`:
1. Считывается снимок таблицы целей (`AisBusClient.snapshot()`).
2. Для каждой цели без позиции (`not reading.has_position`) — пропуск (цель ещё не готова к отображению).
3. Для целей, чей `last_seen` старше `stale_before` (текущее время минус `stale_timeout`) — пропуск (устарела).
4. Для оставшихся («живых») целей:
   - Если сущность ещё не создана — создаётся новый `AisTarget` и регистрируется через `async_add_entities`.
   - Если уже существует — обновляется на месте (`update_from_reading`), без пересоздания объекта.

### Удаление устаревших целей

```python
for mmsi in list(self._entities):
    entity = self._entities[mmsi]
    if mmsi not in seen or entity.last_seen < stale_before:
        del self._entities[mmsi]
        self._client.drop(mmsi)
        self.hass.async_create_task(entity.async_remove(force_remove=True))
```

Цель удаляется как из набора активных HA-сущностей (`async_remove(force_remove=True)`), так и из таблицы клиента (`AisBusClient.drop`) — таким образом, память освобождается с обеих сторон (кастомная таблица + HA state machine).

### Нулевое загрязнение реестра

Класс `AisTarget(GeolocationEvent)` **намеренно не задаёт `unique_id`**:

```python
# Deliberately NO unique_id: these are purely transient in-memory
# entities (like HA core's adsb/opensky geo_location events), so they
# never create entity_registry rows
```

Это гарантирует, что HA не создаёт постоянных записей `entity_registry`/`device_registry` для проходящих судов — единственное состояние живёт в оперативной памяти процесса HA, полностью соответствуя цели архитектуры (см. `architecture.md`).

## Атрибуты сущности `AisTarget`

Реализовано через `extra_state_attributes`:

```python
{
  "mmsi": 232053931,
  "latitude": 42.4312,
  "longitude": 18.6021,
  "sog": 6.4,
  "cog": 187.0,
  "heading": 185.0,
  "nav_status": "Under way using engine",
  "rate_of_turn": 0.0,
  "vessel_name": "SEA BREEZE",
  "callsign": "ZA1234",
  "ship_type": "Sailing",
  "length": 12.5,
  "beam": 3.8,
  "destination": "BUDVA",
  "eta": "2026-08-20 14:00",
  "is_own_ship": false,
  "last_seen": "2026-08-15T10:22:31+00:00"
}
```

`entity_id` детерминированно формируется как `geo_location.ais_<mmsi>` (без хэшей/суффиксов), что позволяет строить прямые ссылки на конкретное судно из таблицы деталей (см. `dashboard_spec.md`).

## Связь с `AisBusClient`

`__init__.py` при установке config entry создаёт `AisBusClient(gw_host, gw_port)`, запускает его (`client.start()`) и сохраняет в `hass.data[DOMAIN][entry.entry_id]["client"]`. Платформа `geo_location.async_setup_entry` берёт этот клиент, создаёт `AisTargetsManager` и запускает периодический цикл (`manager.async_start()`). При выгрузке записи конфигурации (`entry.async_on_unload`) вызывается `manager.async_stop()`, который отменяет таймер обновления.

## Связанные документы

- [`architecture.md`](./architecture.md) — общая архитектура, поток данных, обоснование прямого чтения шлюза.
- [`pgn_specifications.md`](./pgn_specifications.md) — маппинг и конвертация полей PGN.
- [`dashboard_spec.md`](./dashboard_spec.md) — как атрибуты используются на карте и в таблице.
