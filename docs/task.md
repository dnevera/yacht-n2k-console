# NMEA 2000 Dynamic Network Manager — Tasks

## Phase 1: Backend — Dynamic Device Properties

- [x] Создать `n2k_meta.py` — PGN field metadata + Read/Write Fields + ISO Request
- [x] Создать `routes/n2k_config.py` — REST API для device config (4 endpoints)
- [x] Обновить `device_manager.py` — library-based device info (убран hardcoded mfg_names)
- [ ] Удалить `n2k_command_builder.py` — заменён `n2k_meta.py` (нужно проверить что routes/n2k.py перенесён)

## Phase 2: Frontend — Dynamic Config UI

- [x] Переписать `n2k_config.js` — динамическая форма из API metadata (Read + Write + diff)
- [x] Обновить `network.js` — передача active_pgns в openN2KConfigModal

## Phase 3: Live Data

- [ ] Обновить `device_manager.py` — broadcast decoded data для всех PGN

## Deployed to gateway-host: ✅
Files: n2k_meta.py, routes/n2k_config.py, routes/__init__.py, app.py, device_manager.py, static/js/n2k_config.js, static/js/network.js
