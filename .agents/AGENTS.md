# Project Rules — yacht-n2k-console

## ⚠️ Обязательное правило: перечитывай этот файл
**При каждой новой задаче — перечитай `.agents/AGENTS.md` целиком.**
Не полагайся на память и контекст. Правила могли измениться. Каждый раз читай заново.

## Deploy Rule
**Всегда показывай diff файлов перед деплоем на целевой хост.**

Порядок:
1. Показать пользователю что изменилось (diff)
2. Только после подтверждения — деплоить через `scp` + restart service

**Не деплоить автоматически** без показа diff.

## Запрет копипасты
**Копипаст запрещён.** Повторяющийся код → вынеси в общую функцию/модуль.

## Всегда объясняй свои действия заранее
**Всегда сначала пиши пользователю, что именно ты собираешься сделать, перед тем как запустить любую команду или инструмент.**

## Запрет на коммиты без разрешения
**Категорически запрещено делать git-коммиты без прямого указания пользователя.**

## Запрет анализа логов без спроса
**Не читай и не анализируй логи ошибок без явного разрешения пользователя.**
При ошибке — сообщи факт, не парси вывод самостоятельно.

## Запрет использования браузера
**Запрещено использовать browser tools для просмотра страниц.**

## Target Platform
- **Raspberry Pi 5** (hostname and user defined in `deploy.conf`)
- **Service path:** `/opt/nmea2000/ydnu02-web/`
- **Systemd service:** `ydnu02-web.service`
- **Python 3.13** с библиотекой `nmea2000`
- **Sensitive config:** `deploy.conf` (gitignored, created from `deploy.conf.template`)

## NMEA 2000 — правила разработки
- **NMEA 2000 — ОСНОВНОЙ источник данных. BLE — ТОЛЬКО НАСТРОЙКА.**
- Все PGN metadata извлекаются динамически из библиотеки `nmea2000` через `n2k_meta.py`
- Никаких hardcoded PGN registries — всё из library decode functions
- Lookup enums — из `pgns.master_dict`, ключи извлекаются парсингом source decode-функций

## Скрипты и ключевые модули
- `n2k_meta.py` — ядро: PGN metadata, frame builders, decode
- `device_manager.py` — bus worker, device discovery, sensor state
- `ydnu02.py` — YDNU-02 serial protocol, N2KPGNDecoder
- `routes/n2k_config.py` — REST API для dynamic device config

## Rule: Скиллы и база знаний хранятся в гите проекта
**Все скиллы, справочники и накопленные знания по проекту пишутся ТОЛЬКО в репозиторий проекта.**

- Правильное место: `.agents/skills/<skill-name>/SKILL.md` — в корне того репо к которому относится знание
- Запрещено хранить проектные скиллы в `~/.gemini/` или любом глобальном конфиге агента
- После каждой сессии в которой что-то новое выяснили (баг, архитектурное решение, ловушка) — обновить соответствующий `SKILL.md` и закоммитить

**Почему:** скилл в гите версионируется вместе с кодом, доступен всей команде, не теряется при сбросе агентского контекста.

## Rule: Все комментарии в коде — только на английском
**Категорически запрещено писать комментарии в коде на русском языке.**
Любые `#`, docstring, inline-комментарии — исключительно на английском.
Это относится ко всем файлам проекта: `.py`, `.js`, `.html`, `.css`, конфигам.
Общение с пользователем — на русском. Код — на английском.

## Rule: No sensitive data in code or git
**Категорически запрещено хардкодить чувствительные данные в коде и скриптах.**
Это включает: реальные hostname, username, IP-адреса, пути к конкретным инсталляциям.

Правила:
- В коде и скриптах — только плейсхолдеры (`<gateway-host>`, `user@gateway-host`)
- Фактические значения — в конфиг-файлах (`deploy.conf`, `build.conf`)
- В git попадают ТОЛЬКО шаблоны (`.template`), фактические конфиги — в `.gitignore`
- При сборке/деплое скрипты загружают значения из конфигов через `source`
- В документации/README — generic примеры, не реальные адреса

## Rule: Комментарии — уровня мини-скилла
**All new code must be commented at "mini-skill" level — explain WHY, not WHAT.**

Must document:
- **WHY** this approach was chosen (not just what the line does)
- **Traps and gotchas** — all non-obvious behaviour that already burned us
- **Architectural context** — how this piece fits into the system
- **What breaks** if the fix is removed or behaviour is changed

Good comment example (from service mode):
```python
# TRAP — default-arg capture:
# port=_PROXY_CTRL_PORT is evaluated ONCE at class definition time (module load).
# Patching dm._PROXY_CTRL_PORT after import does NOT affect the already-captured default.
# Always pass port= explicitly or patch the class itself (dm.ProxyControlClient = _TestPCC).
```

## Rule: nmea2000 устанавливается из нашего git форка, не из PyPI
**`requirements.txt` подключает `git+https://github.com/dnevera/nmea2000.git@fix/pgn-126996-hash-collision-per-source`.**
PyPI upstream `nmea2000==2026.5.2` содержит два критических бага:
1. EOF spin-loop в `ioclient.py` (100% CPU после рестарта gateway)
2. Hash collision в `message.py` (все устройства → один device в HA)

**Запрещено менять `requirements.txt` на PyPI-версию** без предварительного подтверждения что оба бага merged в upstream.

## Rule: unique_number, НЕ iso_name.name для идентификации NMEA устройств

**КРИТИЧЕСКИ ВАЖНО:** Идентификаторы NMEA 2000 устройств в HA строятся на MD5 от `primary_key`.
Для устойчивости `primary_key` ОБЯЗАН использовать `source_iso_name.unique_number`, а НЕ `source_iso_name.name`.

**Почему:**
- `iso_name.name` = 64-бит integer, включает `device_instance` — **меняется** при переинициализации шины
- `unique_number` = 21-бит, manufacturer-assigned (NMEA 2000 §3.1.1) — **никогда не меняется**
- Использование `.name` → разный MD5 при каждом рестарте YDNU-02 → новый device в HA registry → дубли

**Стабильные хэши (patch-v2):**
- SA=64 (YDNU-02, unique_number=402047): `ef195c7c99c762fdfda4e198aae87930`
- SA=200 (TCP-GW, unique_number=902047): `c11f5c824c71fe7e186cba56bf0f8672`

## Rule: Двухфазный анонс — обязателен

`data_hub.py::announce_all_devices()` ДОЛЖЕН отправлять PGN 60928 (ISO Claim) ДО PGN 126996 (Product Info) с задержкой `ANNOUNCE_PRODUCT_INFO_DELAY = 0.6s`.

**Почему:** HA decoder делает `return None` (silent drop) для PGN 126996 если `source_to_iso_name[SA]` ещё не заполнен PGN 60928. При одновременной отправке decoder получает 126996 до того как обработал 60928 → устройство создаётся с 0 entities.

**В тестах** вызывать `announce_all_devices()` БЕЗ аргументов (delay=0, синхронный).
**В production** `send_iso_request()` передаёт `product_info_delay=ANNOUNCE_PRODUCT_INFO_DELAY`.

## Rule: HA Patch — идемпотентный, версионированный

Патч `scripts/patch_ha_nmea2000_message.py` применяется в HA Docker контейнере через `deploy.sh --patch-ha`.
Три состояния:
- **v2 marker** → пропускает (уже актуальный)
- **v1 marker** → автоматически апгрейдит `.name` → `.unique_number`
- **без маркера** → fresh install

**При появлении дублей устройств в HA:**
```bash
./deploy.sh --clean-ha   # удалить все старые nmea2000 devices
# затем проверить: python -m pytest tests/test_live_ha_integration.py
```
