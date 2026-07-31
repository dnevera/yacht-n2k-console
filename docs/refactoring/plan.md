# Refactoring Plan — yacht-n2k-console

## Overview

This document tracks the refactoring of yacht-n2k-console from monolithic files
into a modular architecture. The refactoring is divided into 3 phases.

## Phase 0: Documentation & Preparation (current)

**Goal**: Full documentation of all key files before any structural changes.

- [ ] Add mini-skill docstrings to `device_manager.py` (all 3 classes, ~55 methods)
- [ ] Add mini-skill docstrings to `ydnu02.py` (2 classes + CLI)
- [ ] ✅ Add mini-skill docstrings to `ydnu02_tcp_gateway.py` (done)
- [ ] ✅ Add mini-skill docstrings to `ydnu02_gateway_device.py` (done)
- [ ] Create test plan (`docs/refactoring/test_plan.md`)
- [ ] Create refactoring rules (`docs/refactoring/rules.md`)
- [ ] Document current test coverage baseline
- [ ] Fix 2 pre-existing test failures (test_rate_limited, test_sends_to_serial)

## Phase 1 (P0): DeviceManager → Facade + 7 Modules

**Goal**: Break `DeviceManager` (1464 lines, 55 methods) into facade + specialized modules.

### New directory: `device_manager/`

| Module | Responsibility | Est. Lines |
|--------|---------------|------------|
| `manager.py` | Facade — shared state, delegation | ~200 |
| `bus_worker.py` | TCP connect/read loop, pause/resume | ~200 |
| `sensor_registry.py` | PGN → sensor state, device tracking | ~120 |
| `error_logger.py` | CAN error ring buffer | ~60 |
| `operation_runner.py` | 3 operation patterns (stop/resume + proxy) | ~150 |
| `service_manager.py` | YDNU-02 service mode ops | ~220 |
| `firmware_manager.py` | OTA flash + version check | ~90 |
| `ws_stream_hub.py` | WebSocket monitor + scan | ~220 |

### Migration strategy

1. Create `device_manager/` package with `__init__.py` re-exporting `DeviceManager`
2. Move helpers first (`ErrorLogger`, `OperationRunner`) — least coupled
3. Then sensors, firmware, service — medium coupling
4. Then bus worker, ws hub — most coupled
5. DeviceManager facade keeps ALL public methods, delegates internally
6. Routes don't change — `get_device_mgr()` returns same interface

### Backward compatibility

```python
# routes/__init__.py — NO CHANGE needed
from device_manager import DeviceManager  # works with package too

# All route code unchanged:
dm = get_device_mgr()
dm.get_info()           # delegates to ServiceManager.get_info()
dm.flash_firmware(...)  # delegates to FirmwareManager.flash_firmware()
```

## Phase 2 (P1): ydnu02_tcp_gateway → Gateway class + modules

**Goal**: Replace module-level globals with `Gateway` class for testability.

### New structure

| Module | Responsibility | Est. Lines |
|--------|---------------|------------|
| `gateway.py` | Gateway class (main, shared state) | ~250 |
| `frame_utils.py` | Regex, _fmt_frame, _get_pgn_sa | ~100 |
| `device_cache.py` | DeviceFrameCache (fast-packet + replay) | ~200 |
| `data_hub.py` | DataHub (:4001 broadcast + hub) | ~200 |
| `ctrl_handler.py` | CtrlHandler (:4002 service/firmware) | ~300 |
| `serial_reader.py` | SerialReader (owns serial port) | ~200 |

### Test fixes

- `test_rate_limited`: expect 2 writes (Address Claim + Product Info)
- `test_sends_to_serial`: expect 2 serial.write() calls

## Phase 3 (P2): ydnu02.py → 3 files

**Goal**: Clean separation of PGN decoder, controller, and CLI.

| File | Class | Lines |
|------|-------|-------|
| `ydnu02/pgn_decoder.py` | `N2KPGNDecoder` | ~220 |
| `ydnu02/controller.py` | `YDNU02Controller` | ~575 |
| `ydnu02/cli.py` | `build_parser()`, `main()` | ~200 |

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-31 | Facade pattern for DeviceManager | Minimal diff — routes don't change |
| 2026-07-31 | Documentation first, refactoring second | Ensure full understanding before structural changes |
| 2026-07-31 | Fix gateway test failures during P1 | Natural fit — tests need update for 2-write ISO request |
