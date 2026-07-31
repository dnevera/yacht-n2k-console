# Test Plan — Refactoring Verification

## Current Baseline

### Test Files

| File | Tests | Status | Notes |
|------|-------|--------|-------|
| `test_ydnu02_tcp_gateway.py` | 72 | 60 pass, 2 fail, 10 skip | Pre-existing: test_rate_limited, test_sends_to_serial |
| `test_service_mode.py` | ? | TBD | Service mode lifecycle |
| `test_sensors_service.py` | ? | TBD | Sensor state tracking |
| `test_gobius_parsers.py` | ? | TBD | Gobius data parsing |
| `test_gobius_ble_writes.py` | ? | TBD | BLE write operations |
| `test_ble_registry.py` | ? | TBD | BLE device registry |
| `test_ble_api.py` | ? | TBD | BLE REST API |
| `test_api.py` | ? | TBD | General API |
| `test_bus_scanner.py` | ? | TBD | Bus device scanning |
| `test_gobius_ble_nmea.py` | ERR | Collection error | Serial dependency — needs mock |
| `test_gobius_n2k_protocol.py` | ERR | Collection error | Serial dependency — needs mock |
| `test_gobius_profile.py` | ERR | Collection error | Serial dependency — needs mock |

### Pre-existing Failures to Fix

#### 1. `test_rate_limited`
- **Root cause**: `_send_iso_request()` sends 2 serial.write() calls (Address Claim + Product Info)
- **Test expects**: 1 write call
- **Fix**: Update assertion to `assertEqual(mock_serial.write.call_count, 2)`
- **Phase**: P1 (gateway refactoring)

#### 2. `test_sends_to_serial`
- **Root cause**: Same — expects `assert_called_once()` but 2 writes happen
- **Fix**: Change to `assert_called()` and verify both frames
- **Phase**: P1

#### 3. Collection errors (3 test files)
- **Root cause**: Tests import modules that try to open `/dev/ttyACM0` at import time
- **Fix**: Guard serial port access behind lazy init or `if __name__ == '__main__'`
- **Phase**: P2 or separate fix

---

## Phase 0: Verification Plan

Before any refactoring:
```bash
# Record baseline
cd /Users/denn/Develop/yacht/yacht-n2k-console
python3 -m pytest tests/ -q --tb=no \
  --ignore=tests/test_gobius_ble_nmea.py \
  --ignore=tests/test_gobius_n2k_protocol.py \
  --ignore=tests/test_gobius_profile.py \
  2>&1 | tee docs/refactoring/baseline_tests.txt
```

## Phase 1 (P0): DeviceManager Tests

### New Tests Needed

| Test File | What it Tests | Key Assertions |
|-----------|--------------|----------------|
| `test_error_logger.py` | ErrorLogger ring buffer | Max size, FIFO ordering, clear |
| `test_sensor_registry.py` | SensorRegistry PGN dispatch | PGN 127505 → fluid level, PGN 60928 → device info |
| `test_operation_runner.py` | 3 operation patterns | Pause/resume bus worker, lock acquisition order |
| `test_service_manager.py` | Service mode operations | get_info, get_filters via mock ProxyControlClient |
| `test_firmware_manager.py` | Firmware OTA | flash_firmware call sequence, check_latest_firmware HTTP |
| `test_bus_worker.py` | TCP read loop | Connect, read, broadcast, reconnect on error |

### Refactoring Smoke Test

After each module extraction:
```bash
# 1. Verify imports still work
python3 -c "from device_manager import DeviceManager; print('OK')"

# 2. Run existing tests
python3 -m pytest tests/ -q --tb=short \
  --ignore=tests/test_gobius_ble_nmea.py \
  --ignore=tests/test_gobius_n2k_protocol.py \
  --ignore=tests/test_gobius_profile.py

# 3. Verify deploy still works
./deploy.sh user@gateway-host --web
```

## Phase 2 (P1): Gateway Tests

### Fix Existing Failures

```python
# test_rate_limited — new assertion:
def test_rate_limited(self):
    # _send_iso_request sends 2 frames: Address Claim + Product Info
    self.assertEqual(mock_serial.write.call_count, 2)
    # Second call within interval should be rate-limited
    _send_iso_request()
    self.assertEqual(mock_serial.write.call_count, 2)  # no new writes

# test_sends_to_serial — new assertion:
def test_sends_to_serial(self):
    # Verify both ISO Request frames are sent
    calls = mock_serial.write.call_args_list
    self.assertEqual(len(calls), 2)
    self.assertIn(b'18EAFFFE 00 EE 00', calls[0][0][0])  # Address Claim
    self.assertIn(b'18EAFFFE 14 F0 01', calls[1][0][0])  # Product Info
```

### New Tests for Gateway Class

| Test | What | Key Assertion |
|------|------|---------------|
| `test_gateway_init` | Gateway() creates all subsystems | cache, data_hub, ctrl, reader exist |
| `test_device_cache` | DeviceFrameCache isolation | Add/replay/evict without globals |
| `test_data_hub_broadcast` | DataHub.broadcast() | Exclude sender, dead client cleanup |
| `test_ctrl_handler_protocol` | CtrlHandler state machine | SERVICE_START→READY→SERVICE_END→OK |
| `test_serial_reader_reconnect` | SerialReader error recovery | Close + 5s delay + reopen |

## Phase 3 (P2): ydnu02 Tests

### Verify split doesn't break imports
```bash
python3 -c "from ydnu02 import N2KPGNDecoder, YDNU02Controller; print('OK')"
python3 -c "from ydnu02.pgn_decoder import N2KPGNDecoder; print('OK')"
python3 -c "from ydnu02.cli import main; print('OK')"
```

---

## CI Integration

```bash
# Full test suite (to be added to CI pipeline)
pytest tests/ \
  --ignore=tests/test_gobius_ble_nmea.py \
  --ignore=tests/test_gobius_n2k_protocol.py \
  --ignore=tests/test_gobius_profile.py \
  -v --tb=short
```
