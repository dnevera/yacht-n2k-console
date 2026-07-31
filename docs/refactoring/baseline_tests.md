# Test Baseline — 2026-07-31

## Command
```
pytest tests/ -q --tb=no \
  --ignore=tests/test_gobius_ble_nmea.py \
  --ignore=tests/test_gobius_n2k_protocol.py \
  --ignore=tests/test_gobius_profile.py
```

## Result
- **171 passed**
- **21 failed**
- **10 skipped**
- **3 collection errors** (serial-dependent, excluded)

## Failed Tests (pre-existing)

### Gateway (2 failures)
- `test_ydnu02_tcp_gateway::TestISORequestBroadcast::test_rate_limited`
- `test_ydnu02_tcp_gateway::TestISORequestBroadcast::test_sends_to_serial`

### Service mode (5 failures)
- `test_service_mode::TestProxyControlClient::test_passthrough_write_delivers_to_serial`
- `test_service_mode::TestDeviceManagerService::test_concurrent_enter_serialized_by_lock`
- `test_service_mode::TestDeviceManagerService::test_enter_service_returns_service_state`
- `test_service_mode::TestDeviceManagerService::test_exit_service_returns_idle_state`
- `test_service_mode::TestDeviceManagerService::test_get_state_reflects_enter_exit`

### Other (14 failures)
TBD — need to catalog remaining failures.

## Collection Errors (excluded from baseline)
- `test_gobius_ble_nmea.py` — serial.SerialException at import
- `test_gobius_n2k_protocol.py` — serial.SerialException at import
- `test_gobius_profile.py` — serial.SerialException at import
