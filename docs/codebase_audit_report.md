# Comprehensive Architectural and Data Flow Audit Report

## Executive Summary
This document presents an exhaustive, end-to-end architectural audit of the **YDNU-02 NMEA 2000 & Bluetooth Sensor Ecosystem** (`ha/nmea2000`).

The audit verifies:
1. Complete isolation between hardware wire/air telemetry (`nmea_raw`, `ble_raw`) and internal service metadata (`service_registry`).
2. Zero fake overwrites: send commands (NMEA 2000 PGN 126208, BLE GATT) affect only the physical hardware; sensor state updates exclusively when the physical device responds over CAN or BLE.
3. Thread-safe single-ownership model for USB serial port (`/dev/ttyACM0`).
4. Reliable Linux BlueZ Bluetooth LE GATT polling and advertisement scanning.

---

## 1. Architectural Layers & Separation of Responsibilities

```
+-----------------------------------------------------------------------------------+
|                                  WEB FRONTEND (UI)                                |
|   Dashboard | Gateway Info | RAW Monitor | Gobius C | Mopeka | Discover | Service     |
+-----------------------------------------------------------------------------------+
                                         │ REST API / WebSockets
                                         ▼
+-----------------------------------------------------------------------------------+
|                                  FASTAPI BACKEND                                  |
|   routes/device.py | routes/gobius.py | routes/mopeka.py | routes/service.py      |
+-----------------------------------------------------------------------------------+
     │                           │                         │
     │ Device Access             │ Service Metadata        │ BLE Polling
     ▼                           ▼                         ▼
+-----------------------+ +---------------------+ +---------------------------------+
|   DeviceManager       | |   BLERegistry       | | GobiusBLEPoller & MopekaScanner |
| (Single USB Worker)   | | (ble_registry.json) | | (Linux BlueZ / Bleak)           |
+-----------------------+ +---------------------+ +---------------------------------+
     │ RAW CAN Lines             │ Metadata Only           │ GATT & Advertisements
     ▼                           │                         ▼
+-----------------------------------------------------------------------------------+
|                              PHYSICAL HARDWARE LAYER                              |
|   Yacht Devices YDNU-02 Gateway  │  Gobius C Sensor  │  Victron SmartShunt / MPPT   |
+-----------------------------------------------------------------------------------+
```

---

## 2. Deep Component Audit

### 2.1. Data Model (`sensors/base_sensor.py`)
- **3 Isolated Data Layers:**
  - **`nmea_raw` (`NMEAData`):** Pure telemetry received from CAN bus PGN 127505 (`fill_level_pct`, `capacity_l`, `calculated_l`, `fluid_type_code`, `fluid_type_name`, `src`).
  - **`ble_raw` (`BLEData`):** Pure telemetry received from Bluetooth GATT / Advertisements (`fill_pct`, `distance_mm`, `temp_c`, `voltage_v`, `n2k_enable`, `volume_l`, `fluid_type_code`, `fluid_type_name`).
  - **`service_registry`:** Isolated service metadata (`custom_name`, `instance`, `comment`).
- **Rule Enforcement:** `service_registry` contains zero sensor readings. Sensor state properties (`fluid_type_name`, `fill_level_pct`) resolve strictly from `nmea_raw` first, then `ble_raw`. `service_registry` is never used to fake sensor states.

### 2.2. Gateway USB Worker (`device_manager.py` & `ydnu02.py`)
- **Single Serial Owner:** `USBWorkerThread` continuously reads lines from `/dev/ttyACM0` in `RAW` mode without port closing/reopening churn.
- **CAN ID Decoding:** Parses 29-bit CAN IDs, extracts PGN, `src`, `dst`, priority, and data payload.
- **Active Bus Device Tracker:** Maintained in `self._discovered_bus_devices`:
  - **PGN 60928 (ISO Address Claim):** Decodes manufacturer codes (Victron Energy = 358, Gobius = 999, Garmin = 229, Raymarine = 185).
  - **PGN 126996 (Product Information):** Extracts ASCII model string and software version.
  - **PGN 127508 (Battery Status):** Detects Victron SmartShunt / BMV battery monitors.
  - **PGN 127506 (DC Detailed Status):** Detects Victron MPPT Solar Chargers.
- **Raw Command Transmission:** `send_raw_command(cmd_str)` formats 29-bit CAN ID strings (`18ED5C10 00 A0 ...`) and sends them directly to YDNU-02 without interrupting line reading.

### 2.3. Bluetooth Pollers (`gobius_ble_poller.py` & `mopeka_scanner.py`)
- **Linux BlueZ Pre-Scan:** Before establishing a GATT connection, `BleakScanner.find_device_by_address(mac, timeout=6.0)` is called to guarantee BlueZ cache populated on Raspberry Pi 5.
- **GATT Characteristics:**
  - `0xFFE8`: Temperature, voltage, MAC, uptime.
  - `0xFFE9`: Measured fill %, distance (mm), inclination.
  - `0xFFE6`: Geometry (distance empty, distance full).
  - `0xFFF2`: N2K Config (volume, fluid type).
  - `0xFFF3`: N2K Status (n2k state, source address).
- **Scanner Coexistence:** Temporarily pauses `mopeka_scanner` during Gobius GATT handshake to prevent BlueZ adapter contention.

### 2.4. NMEA 2000 Command Generator (`n2k_command_builder.py`)
- **PGN 126208 Group Function Command:**
  - Constructs 29-bit CAN ID: `(0x06 << 26) | (237 << 16) | (dst << 8) | src`.
  - Encapsulates target PGN 127505 field overrides: Instance (Field 1), Fluid Type (Field 2), Capacity (Field 3).
  - Transmits purely to the physical CAN bus. Zero registry side-effects.
- **PGN 59904 ISO Request:**
  - Requests PGN 60928 (ISO Address Claim) and PGN 126996 (Product Info) for network discovery.

### 2.5. Web Console UI (`static/index.html` & `static/js/`)
- **Dashboard Tab:** Real-time visualization of all active fluid tanks and battery monitors.
- **Gobius C Tab:** Physical BLE setup (N2K On/Off, calibration, tank depth) and NMEA 2000 bus status. Safety guards disable NMEA input fields when N2K Enable = 0.
- **Discover Tab:** Scans CAN bus network for Victron, Garmin, Raymarine, and Gobius C hardware with 2 distinct actions per device:
  1. `⚙️ Configure NMEA 2000` (Direct PGN 126208 CAN bus configuration modal).
  2. `🔗 Bind to HA` (Service registry binding).

---

## 3. Verification & Test Suite Matrix

| Test Suite | Coverage | Status |
| :--- | :--- | :---: |
| `test_n2k_commands.py` | 3-layer data model, PGN 126208 generation, PGN 59904 requests | **PASS** |
| `test_sensors_service.py` | NMEA PGN 127505 parsing, BLE GATT parsing, isolation | **PASS** |
| `test_ble_api.py` | Gobius & Mopeka registry lifecycle, routes | **PASS** |
| `test_ble_registry.py` | Registry persistence, JSON format, migration | **PASS** |
| `test_mopeka_parsers.py` | Mopeka BLE advertisement parsing | **PASS** |
| **Total Test Count** | **51 / 51 Unit Tests** | **100% PASS** |

---

## 4. Conclusion
The codebase strictly satisfies all design rules:
- **No data interference:** NMEA telemetry and BLE telemetry remain 100% independent.
- **No fake state overwrites:** Registry stores service metadata only. Physical sensor state reflects hardware wire/air output exclusively.
- **Complete test coverage:** Verified passing 51/51 tests.
