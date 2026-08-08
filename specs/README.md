# Spec-Driven Development (SDD)

Спецификации — основной источник истины для новой работы в `yacht-n2k-console`.
Любая нетривиальная задача (фича, багфикс, новое устройство) начинается со спеки в `specs/active/`.

## Структура

```
specs/
  README.md            — этот файл
  templates/           — шаблоны (feature, bugfix, n2k-device)
  active/              — спеки в работе и ретро-спеки подсистем (as-is)
  completed/           — завершённые и верифицированные спеки
```

## Жизненный цикл (4 фазы)

1. **Requirements** — секции `Context` и `Requirements`. Что и зачем, без решений.
2. **Architecture & Technical Design** — секции `Architecture & Technical Design` и `Interfaces / Contracts`. Модули, потоки данных, контракты.
3. **Implementation Plan** — пошаговый план с файлами и ожидаемым результатом.
4. **Verification** — тесты и критерии приёмки; после зелёного прогона спека архивируется.

## Именование

`NNN-slug.md`, где `NNN` — трёхзначный сквозной номер (`001`, `002`, ...), `slug` — kebab-case заголовка.
Номер выделяется автоматически командой `create` и никогда не переиспользуется.

## Статусы

| status | значение |
|---|---|
| `draft` | спека пишется, реализация не начата |
| `approved` | согласована, можно реализовывать |
| `in-progress` | идёт реализация |
| `as-is` | ретро-спека существующей подсистемы (описывает текущее состояние) |
| `completed` | верифицирована, файл перемещён в `specs/completed/` |

## Обязательные секции

Валидатор требует наличия H2-заголовков:

- `Metadata`
- `Context`
- `Requirements`
- `Architecture & Technical Design`
- `Interfaces / Contracts`
- `Implementation Plan`
- `Verification`

Секция `Known Issues` рекомендуется, но не обязательна.

## CLI

```bash
python ~/.junie/scripts/spec.py create --type feature --title "Signal K data hub"
python ~/.junie/scripts/spec.py list
python ~/.junie/scripts/spec.py list --status completed
python ~/.junie/scripts/spec.py validate                 # все спеки в active/ и completed/
python ~/.junie/scripts/spec.py validate specs/active/001-tcp-gateway.md
python ~/.junie/scripts/spec.py archive specs/active/001-tcp-gateway.md
```

`validate` возвращает exit code `1`, если в спеке нет обязательной секции.

## Реестр спек

### Сквозные спеки уровня проекта

| Спека | Содержание |
|---|---|
| `000-project-overview.md` | Общие требования к проекту, реестр спек, глоссарий — точка входа |
| `006-integrations.md` | Технический дизайн интеграций: YDNU-02/NMEA 2000, Home Assistant, Signal K, BLE, REST/WebSocket |
| `007-testing-strategy.md` | Стратегия тестирования: уровни, карта `tests/` → подсистемы, Definition of Done |

### Ретро-спеки подсистем

| Спека | Подсистема |
|---|---|
| `001-tcp-gateway.md` | `ydnu02_tcp_gateway/` — serial ↔ hub ↔ TCP |
| `002-device-manager.md` | `device_manager/` — discovery, состояние, сервисы |
| `003-web-api-ui.md` | `app.py`, `routes/`, `static/` |
| `004-ble-sensors.md` | `sensors/`, `gobius_*`, `mopeka_*`, `ble_registry.py` |
| `005-deploy-ha-integration.md` | `deploy.sh`, `scripts/`, `patches/`, `homeassistant/` |

### Порядок чтения

`000-project-overview.md` → профильная подсистемная спека (`001`–`005`) → `006-integrations.md` (внешние стыки) → `007-testing-strategy.md` (проверки).
