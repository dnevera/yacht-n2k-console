# Task: Refactoring v1 — yacht-n2k-console

## Overview
Refactor `yacht-n2k-console` from monolithic files (`device_manager.py`, `ydnu02_tcp_gateway.py`, `ydnu02.py`) into a clean, modular, testable architecture with facade delegation.

---

## Phase 1 (P0): DeviceManager Decomposition
Break `device_manager.py` (1464 lines) into facade + 7 specialized modules in `device_manager/` package.

- [x] **1.1 Package Setup & `tcp_connection.py`**
  - Create `device_manager/` package and `__init__.py` (re-exports `DeviceManager`).
  - Move `TCPProxyConnection` and `ProxyControlClient` into `device_manager/tcp_connection.py`.
  - Verify imports & run pytest baseline.

- [x] **1.2 `error_logger.py`**
  - Move `ErrorLogger` into `device_manager/error_logger.py`.
  - Verify imports & run pytest.

- [x] **1.3 `sensor_registry.py`**
  - Move `SensorRegistry` into `device_manager/sensor_registry.py`.
  - Verify imports & run pytest.

- [x] **1.4 `operation_runner.py`**
  - Move `OperationRunner` into `device_manager/operation_runner.py`.
  - Verify imports & run pytest.

- [x] **1.5 `service_manager.py`**
  - Move `ServiceManager` into `device_manager/service_manager.py`.
  - Verify imports & run pytest.

- [x] **1.6 `firmware_manager.py`**
  - Move `FirmwareManager` into `device_manager/firmware_manager.py`.
  - Verify imports & run pytest.

- [x] **1.7 `ws_stream_hub.py`**
  - Move `WSStreamHub` into `device_manager/ws_stream_hub.py`.
  - Verify imports & run pytest.

- [x] **1.8 `bus_worker.py`**
  - Move `BusWorker` into `device_manager/bus_worker.py`.
  - Verify imports & run pytest.

- [x] **1.9 `manager.py` (Facade)**
  - Move facade class into `device_manager/manager.py`.
  - Keep backward-compatibility wrapper `device_manager.py`.
  - Ensure all 55 methods delegate cleanly to sub-managers.
  - Run full test suite & verify local build.

---

## Phase 2 (P1): Gateway Modularization
Refactor `ydnu02_tcp_gateway/` to use a `Gateway` class instead of module-level globals.

- [x] **2.1 `frame_utils.py`** — Extract CAN ID parsing and NMEA line formatting.
- [x] **2.2 `device_cache.py`** — Extract `DeviceFrameCache` (fast-packet reassembly).
- [x] **2.3 `data_hub.py`** — Extract `DataHub` (:4001 broadcast hub).
- [x] **2.4 `ctrl_handler.py`** — Extract `CtrlHandler` (:4002 service/firmware mode).
- [x] **2.5 `serial_reader.py`** — Extract `SerialReader` daemon thread.
- [x] **2.6 `gateway.py`** — Implement `Gateway` class holding state.
- [x] **2.7 Fix gateway test failures** (`test_rate_limited`, `test_sends_to_serial`).

---

## Phase 3 (P2): YDNU02 Module Split
Split `ydnu02.py` into 3 clean files.

- [x] **3.1 `ydnu02/pgn_decoder.py`** — Extract `N2KPGNDecoder`.
- [x] **3.2 `ydnu02/controller.py`** — Extract `YDNU02Controller`.
- [x] **3.3 `ydnu02/cli.py`** — Extract CLI parser & main entry point.
- [x] **3.4 `ydnu02/__init__.py`** — Re-export for backward compatibility.

---

## Verification & Deployment
- [x] Run full test suite locally after each phase (Phase 1: 171 passed, zero regressions).
- [ ] Run `build_bundle.sh` to verify tarball packaging.
- [ ] Deploy with `deploy.sh` (pre-deploy diff + post-deploy tests).
