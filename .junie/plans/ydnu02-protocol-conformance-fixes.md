---
sessionId: session-260801-141742-15p9
---

# Requirements

### Overview & Goals
Протокольный аудит незакоммиченных изменений (`ydnu02_tcp_gateway/*`) в контексте официальной документации YDNU-02 (Appendix E RAW mode) и внутренней документации `frame_utils.py` показал два расхождения между задокументированным/ожидаемым поведением и фактическим кодом. Цель — устранить оба расхождения, не трогая формат ASCII-фреймов (он уже полностью соответствует официальному протоколу — расхождений не найдено).

### Scope
**In Scope:**
1. Реализовать реальное раздельное управление интервалами телеметрии CPU-температуры (PGN 130312): TCP-цикл — `n2k_tcp_temp_interval_s` (default 3.0s), форвардинг в physical serial — независимый таймер `n2k_serial_temp_interval_s` (default 5.0s). Сейчас оба поля настройки существуют (валидация, персистенция, UI), но нигде не читаются — используется единая жёсткая константа `GW_TEMP_INTERVAL_S=3.0`.
2. Добавить SA-guard в `data_hub.py::handle_client()` (ветка `TX_LINE_RE`) — не форвардить в physical serial TX-фреймы, чьи source address (SA) принадлежат виртуальным устройствам гейтвея (SA=64 YDNU-02, SA=200 TCP-GW), независимо от `n2k_serial_tx_enabled`, кроме легитимного ISO Request (PGN 59904, SA=254 unclaimed — не под guard).

**Out of Scope:**
- Изменение формата ASCII-фреймов (`NMEA_LINE_RE`/`TX_LINE_RE`) — соответствие официальному протоколу уже подтверждено.
- Бинарный N2K/DLE-режим (Appendix F) — не используется в проекте, реализация не требуется.
- Полный аудит остального проекта вне `ydnu02_tcp_gateway/*`.

### Functional Requirements
- TCP-клиенты `:4001` должны получать CPU-temp broadcast (PGN 130312) с периодом `n2k_tcp_temp_interval_s` (изменяемым через `/api/gw-settings` без рестарта демона).
- Physical serial bus (`/dev/ttyACM0`) должен получать тот же PGN 130312 с независимым периодом `n2k_serial_temp_interval_s` — не каждую пришедшую TCP-строку, а с собственным троттлингом.
- Изменение любого из двух интервалов через UI должно вступать в силу без перезапуска гейтвея (аналогично существующему паттерну `ha_iso_replay_interval_s`).
- TX-фреймы (формат B) от TCP-клиентов с SA∈{64,200} никогда не должны попадать в `ser.write()`, даже при `n2k_serial_tx_enabled=True`.
- Легитимный ISO Request (PGN 59904) продолжает форвардиться в serial независимо от `n2k_serial_tx_enabled` (текущее поведение сохраняется).

### Non-Functional Requirements
- Не ломать существующие 36 тестов.
- Добавить тесты, покрывающие оба фикса (регрессия сейчас не ловится — 0 совпадений по `n2k_serial_temp_interval_s`/`n2k_tcp_temp_interval_s` в `tests/`).

# Technical Design

### Current Implementation
- `ydnu02_tcp_gateway/ydnu02_gateway_device.py::_run_device()` — единственный `while True: await asyncio.sleep(GW_TEMP_INTERVAL_S)` (константа = 3.0), внутри читает CPU temp и один раз вызывает `await device.send(msg)` — уходит только в TCP-хаб (device подключён к `:4001` как N2KDevice).
- `ydnu02_tcp_gateway/data_hub.py::handle_client()` — при получении строки формата A (`NMEA_LINE_RE`) от TCP-клиента (в т.ч. от N2KDevice SA=200) немедленно форвардит в `ser.write()` при `n2k_serial_tx_enabled=True`, без какого-либо троттлинга по времени. Т.е. серийная шина фактически получает CPU-temp с тем же периодом 3s, что и TCP, а не 5s, как задокументировано.
- `GatewaySettings` (`gateway_settings.py`) уже содержит оба поля с валидацией [1.0, 3600.0] и персистенцией — используется существующий паттерн `ha_iso_replay_interval_s`/`ha_iso_replay_enabled` как образец (динамическое чтение settings внутри цикла `_run_device`, см. строки ~689-694).
- `data_hub.py::handle_client()`, ветка `TX_LINE_RE` (строки ~358-376): при `should_send_serial or pgn == 59904` пишет `raw_tx` в serial без проверки SA — расширение по сравнению с прежним поведением (было только `pgn == 59904`).

### Key Decisions
1. **Раздельные интервалы через собственный таймер форвардинга в data_hub, а не второй sleep-цикл в device.py** — TCP-цикл (`_run_device`) продолжает слать с периодом `n2k_tcp_temp_interval_s` (переименовать/подключить вместо жёсткой `GW_TEMP_INTERVAL_S` для temp-сообщений), а серийный форвардинг конкретно для PGN 130312 в `data_hub.py` получает собственный `last_serial_temp_sent` timestamp и троттлится по `n2k_serial_temp_interval_s`, независимо от частоты прихода TCP-строк. Это не требует второго сетевого соединения/устройства — используется уже существующий путь `TCP→serial forward`.
2. **SA-guard как whitelist-фильтр по source address** — `get_pgn_sa()` уже возвращает `(pgn, sa)`; добавляем константу `_VIRTUAL_DEVICE_SA = {64, 200}` и проверяем `sa not in _VIRTUAL_DEVICE_SA` перед `ser.write()` в обеих ветках (`NMEA_LINE_RE` и `TX_LINE_RE`), кроме случая `pgn == 59904` (ISO Request, SA=254 unclaimed — не подпадает под guard).

### Proposed Changes
- `ydnu02_gateway_device.py`: заменить `GW_TEMP_INTERVAL_S` в temp-ветке цикла на `settings.n2k_tcp_temp_interval_s` (читается динамически каждую итерацию, как уже делается для `ha_iso_replay_interval_s`); sleep цикла оставить на минимальном общем шаге (например `min(1.0, ...)` или оставить текущий фиксированный тик для ISO replay, но слать temp по отдельному накопленному таймеру `now - last_tcp_temp_sent >= settings.n2k_tcp_temp_interval_s`, аналогично `_last_replay_t`).
- `data_hub.py::handle_client()`:
  - В обеих ветках (`NMEA_LINE_RE` и `TX_LINE_RE`) — распарсить `sa` из `get_pgn_sa()` (уже вычисляется), добавить SA-guard: `if sa in _VIRTUAL_DEVICE_SA and pgn != 59904: skip serial write`.
  - Для PGN 130312 (temp) конкретно — добавить троттлинг по `n2k_serial_temp_interval_s`: хранить `self._last_serial_temp_t` (per-DataHub instance, под существующим `_serial_lock` или отдельным малым lock), писать в serial только если `now - self._last_serial_temp_t >= settings.n2k_serial_temp_interval_s`.
  - Убрать неиспользуемую переменную `pgn` warning (строка 349) — теперь `pgn`/`sa` реально используются для guard, проблема решается попутно.
- `gateway_settings.py` — без изменений схемы (поля уже корректны), возможно уточнить docstring, что интервалы теперь реально используются.

### Data Models / Contracts
```python
# data_hub.py — новая логика (псевдокод)
_VIRTUAL_DEVICE_SA = {64, 200}  # YDNU-02, TCP-GW — не физически на CAN-шине

def _should_forward_to_serial(pgn: int, sa: int, settings) -> bool:
    if pgn == 59904:
        return True  # ISO Request — легитимен независимо от enabled/SA
    if sa in _VIRTUAL_DEVICE_SA:
        return False  # SA-guard: не пускаем виртуальные source на физическую шину
    if not settings.n2k_serial_tx_enabled:
        return False
    if pgn == 130312:
        now = time.monotonic()
        if now - self._last_serial_temp_t < settings.n2k_serial_temp_interval_s:
            return False
        self._last_serial_temp_t = now
    return True
```

### File Structure
- Изменяются: `ydnu02_tcp_gateway/ydnu02_gateway_device.py`, `ydnu02_tcp_gateway/data_hub.py`.
- Без изменений: `ydnu02_tcp_gateway/gateway_settings.py`, `ydnu02_tcp_gateway/frame_utils.py` (документация уже соответствует целевому поведению — фикс приводит код в соответствие с ней), UI (`static/tabs/service.html`, `static/js/service.js` уже готовы).

### Risks
- SA-guard не должен блокировать легитимные широковещательные фреймы от других физических устройств (SA≠64/200) — guard узкий (whitelist из 2 значений), риск минимален.
- Троттлинг forward temp-фреймов в serial по отдельному таймеру должен корректно работать даже если `n2k_serial_tx_enabled` переключается на лету — состояние `_last_serial_temp_t` не должно "залипать" при повторном включении (сбрасывать при переходе enabled=False→True не обязательно, т.к. таймер основан на monotonic времени, а не на состоянии enabled).

# Testing

### Validation Approach
Запустить существующий набор `pytest` (36 тестов) — убедиться, что регресса нет; добавить целевые unit-тесты на оба фикса в `tests/` (директория для гейтвея, где уже есть тесты `data_hub`/`gateway_settings`).

### Key Scenarios
1. `n2k_tcp_temp_interval_s=1.0`, `n2k_serial_temp_interval_s=10.0` — за 3 секунды симуляции TCP-клиент получает ~3 temp-broadcast'а, а serial mock получает не более 1.
2. `n2k_serial_tx_enabled=True`, входящая строка с SA=200 (или SA=64) в формате A/B — `ser.write()` НЕ вызывается (кроме случая PGN 59904).
3. `n2k_serial_tx_enabled=True`, входящая строка с обычным физическим SA (например 92) — `ser.write()` вызывается как раньше.
4. ISO Request (PGN 59904) форвардится в serial независимо от `n2k_serial_tx_enabled` и от SA — поведение не регрессирует.

### Edge Cases
- Изменение интервалов через `/api/gw-settings` на лету — новое значение должно применяться без перезапуска демона (проверить, что `GatewaySettings.instance()` вызывается динамически, не кэшируется на старте).
- `n2k_serial_tx_enabled=False` — temp-throttle-таймер не должен ломать другие ветки форвардинга (ISO Request всё ещё проходит).

# Delivery Steps

### ✓ Step 1: Реализовать раздельные интервалы TCP/serial для температурной телеметрии
PGN 130312 (CPU temperature) реально рассылается в TCP с периодом n2k_tcp_temp_interval_s и форвардится в physical serial с независимым периодом n2k_serial_temp_interval_s, оба читаются из GatewaySettings динамически.

- В `ydnu02_gateway_device.py::_run_device()` заменить использование жёсткой `GW_TEMP_INTERVAL_S` для temp-broadcast на динамическое чтение `settings.n2k_tcp_temp_interval_s` (с накопительным таймером по аналогии с существующим `_last_replay_t`/`ha_iso_replay_interval_s`).
- В `data_hub.py::handle_client()` добавить троттлинг форвардинга PGN 130312 в serial: собственный `_last_serial_temp_t` (monotonic), сравнение с `settings.n2k_serial_temp_interval_s` перед `ser.write()`, независимо от частоты прихода TCP-строк.
- Обновить docstring/комментарии в `gateway_settings.py` (module-level и в `GW_TEMP_INTERVAL_S`-related местах `ydnu02_gateway_device.py`), чтобы отражать реальное разделение интервалов, устранив расхождение с `frame_utils.py` §2.3.

### ✓ Step 2: Добавить SA-guard против форвардинга виртуальных source в physical serial
TX-фреймы с SA, принадлежащим виртуальным устройствам гейтвея (SA=64, SA=200), никогда не попадают в physical serial bus, кроме легитимного ISO Request (PGN 59904).

- В `data_hub.py` добавить константу `_VIRTUAL_DEVICE_SA = {64, 200}`.
- В обеих ветках `handle_client()` (`NMEA_LINE_RE` и `TX_LINE_RE`) использовать уже вычисляемый `sa` из `get_pgn_sa()` для проверки: если `sa in _VIRTUAL_DEVICE_SA and pgn != 59904` — пропустить `ser.write()`.
- Устранить ранее найденный unused-variable warning на `pgn` (data_hub.py:349) — переменная теперь используется по назначению в guard-условии.

### ✓ Step 3: Покрыть оба фикса тестами и проверить регресс
Новые unit-тесты подтверждают раздельные интервалы и SA-guard; полный existing test suite проходит без регрессий.

- Добавить тесты в `tests/` (рядом с существующими тестами `data_hub`/`gateway_settings`): проверка разных периодов TCP/serial для temp-фреймов через mock serial + mock TCP клиент/время.
- Добавить тесты на SA-guard: TX-фрейм с SA=64/200 не доходит до `ser.write()` (кроме PGN 59904), TX-фрейм с обычным физическим SA доходит.
- Прогнать полный `pytest` набор (36 существующих тестов) — убедиться в отсутствии регрессий.