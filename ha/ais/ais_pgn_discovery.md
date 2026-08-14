### Отчёт об исследовании NMEA 2000 (AIS PGN Discovery)

### 1. Обзор источника данных
* **Источник шины N2K:** YDNU-02 TCP Gateway (`bumblebee.local:4001`)
* **Адрес источника AIS (Source Address):** `SRC 31` (`0x1F`) — трансивер/приёмник **Alltek AIS 208253**
* **Статус декодера:** Библиотека-форк `nmea2000` (`dnevera/nmea2000@cpu-overload-fix`) полностью поддерживает декодирование всех FastPacket и SingleFrame сообщений AIS.

---

### 2. Обнаруженные и поддерживаемые PGN пакета AIS

| PGN | Название (N2K / NMEA) | Тип фрейма | Основные поля |
| :--- | :--- | :--- | :--- |
| **129038** | Class A Position Report | FastPacket | `userId` (MMSI), `latitude`, `longitude`, `sog`, `cog`, `heading`, `navStatus`, `rateOfTurn`, `positionAccuracy`, `timestamp` |
| **129039** | Class B Position Report | FastPacket | `userId` (MMSI), `latitude`, `longitude`, `sog`, `cog`, `heading`, `positionAccuracy`, `timestamp` |
| **129040** | Class B Extended Position | FastPacket | `userId` (MMSI), `sog`, `cog`, `latitude`, `longitude`, `typeOfShip`, `length`, `beam` |
| **129041** | Aid to Navigation (AtoN) | FastPacket | `userId` (MMSI), `atonType`, `name`, `latitude`, `longitude`, `length`, `beam` |
| **129793** | AIS UTC and Date Report | SingleFrame | `userId` (MMSI), `longitude`, `latitude`, `utcYear`, `utcMonth`, `utcDay`, `utcHour`, `utcMinute`, `utcSecond` |
| **129794** | AIS Class A Static & Voyage Data | FastPacket | `userId` (MMSI), `imoNumber`, `callsign`, `name`, `typeOfShip`, `length`, `beam`, `draft`, `destination`, `etaDate`, `etaTime` |
| **129809** | AIS Class B Static Data (Part A) | FastPacket | `userId` (MMSI), `name` |
| **129810** | AIS Class B Static Data (Part B) | FastPacket | `userId` (MMSI), `typeOfShip`, `vendorId`, `callsign`, `length`, `beam` |

---

### 3. Декодированные живые цели с шины (Live Targets)

В ходе диагностики на шине зафиксированы следующие реальные судна и береговые станции:

#### Цель 1: Класс А (Судно в пути)
* **MMSI:** `238052410`
* **Широта / Долгота:** `42.4312° N`, `18.6028° E`
* **Скорость (SOG):** `0.0 - 0.1 kn`
* **Курс (COG / Heading):** COG `270.0°`, Heading `180.0°`
* **Навигационный статус:** `Under way using engine` (На ходу под мотором)
* **Принятые PGN:** 129038

#### Цель 2: Класс B (Маломерное судно / яхта)
* **MMSI:** `232053931`
* **Широта / Долгота:** `42.4287° N`, `18.6091° E`
* **Скорость (SOG):** `0.0 kn`
* **Курс (COG):** `115.0°`
* **Принятые PGN:** 129039, 129809

#### Цель 3: Береговая станция (Base Station)
* **MMSI:** `2620002`
* **Широта / Долгота:** `42.4091° N`, `18.6105° E`
* **Время UTC Sync:** Передаёт точное время UTC (PGN 129793)
* **Принятые PGN:** 129793

#### Дополнительные обнаруженные MMSI:
* `249759000`
* `518159228`
* `325137100`

---

### 4. Архитектура интеграции в Home Assistant

```
[N2K Bus / Alltek AIS]
       │ (CAN Frames)
       ▼
[ydnu02 / TCP Gateway]
       │ (Raw N2K stream via TCP :4001)
       ▼
[Custom Component: ais_targets] ─── (Прямой TCP-клиент ais_bus.py, декодирование PGN в RAM)
       │
       ▼ (Группировка по MMSI, конвертация м/с→kn, рад→deg, таймаут 10 мин)
[Entities: geo_location.ais_<mmsi>] ─── (Временные объекты в RAM, 0 записей в реестре HA)
       │
       ├──► [HA Dashboard: Map] ─── (geo_location_sources: ['ais_targets'])
       └──► [HA Dashboard: Table] ─── (custom:auto-entities + custom:flex-table-card)
```

1. **Прямое чтение TCP-потока:** Компонент `ais_targets` открывает асинхронный TCP-сокет к порту 4001 шлюза YDNU-02 и декодирует PGN с помощью библиотеки `nmea2000` непосредственно в памяти процесса Home Assistant.
2. **Исключение из интеграции `nmea2000`:** AIS PGN намеренно исключены из `pgn_include` штатной интеграции `nmea2000`, благодаря чему `core.device_registry` и `core.entity_registry` остаются чистыми от транзитных судов.
3. **Уникальность по MMSI:** Поле `userId` (MMSI) служит ключом группировки всех входящих динамических (129038/129039) и статических (129794/129809/129810) сообщений.
4. **Дашборд `ha/ais/`:** Карта `type: map` отрисовывает цели через источник `geo_location_sources: ['ais_targets']`, а таблица `custom:auto-entities` + `custom:flex-table-card` показывает полные характеристики целей с кликабельным переходом к mini-map через `more-info`.

---

### 5. Выводы
* Приём и декодирование NMEA 2000 пакетов AIS полностью исправны и протестированы на реальном трафике шины.
* Данные о местоположении, курсе, скорости, типе судна, позывном и названии успешно извлекаются.
* Пакет `ha/ais/` со всеми вспомогательными скриптами сборки и деплоя готов к использованию.
