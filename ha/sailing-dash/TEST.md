# Testing & Verification Guide

This document details the test procedures and verification steps for building, debugging, and deploying the **Sailing Dashboard** across **Stage** and **Prod** environments.

---

## 🧪 1. Build Pipeline Verification

Verify that all source modules in `src/` compile cleanly into target deployment artifacts.

### Execution
```bash
cd ha/sailing-dash
python3 build.py
```

### Expected Results
The build script should create/update the following files in `ha/sailing-dash/build/`:
- `build/dashboard-sailing.yaml`: Lovelace dashboard with resolved JS includes.
- `build/sensors-sailing.yaml`: REST and N2K derived template sensors.
- `build/automations-sailing.yaml`: Forecast refresh automations.
- `build/lovelace-resources.yaml`: Registered Lovelace resource cards.
- `build/cards/windy-boat-card.js`: Custom Windy Web Component element.

### Validation Commands
```bash
# Verify all build artifacts exist
ls -la build/

# Check YAML syntax of generated dashboard
python3 -c "import yaml; yaml.safe_load(open('build/dashboard-sailing.yaml'))"
```

---

## 📡 2. NMEA 2000 Simulator Verification

Verify that the Stage NMEA 2000 PGN emulator broadcasts valid marine sensor frames.

### Execution
Start the emulator in a terminal window:
```bash
python3 ha/sailing-dash/local-ha/mock_nmea_emulator.py --port 4001
```

### Verification via Python NMEA Decoder
Run a quick test script to verify TCP reception and PGN decoding:
```bash
python3 -c "
import socket, time
from nmea2000.decoder import NMEA2000Decoder

client = socket.socket()
client.connect(('127.0.0.1', 4001))
decoder = NMEA2000Decoder()

for _ in range(10):
    line = client.recv(1024).decode('ascii', errors='ignore')
    for l in line.splitlines():
        msg = decoder.decode(l.strip())
        if msg:
            print(f'Decoded PGN {msg.PGN}: {msg.description}')
client.close()
"
```

### Expected Output
Valid decodes for PGNs: `128259` (STW), `128267` (Depth), `130306` (Wind), `129025` (Position), `129026` (COG/SOG), `127250` (Heading).

---

## 🐳 3. Stage Deployment Verification

Verify that deployment to the local Docker Home Assistant container (`local-ha`) works smoothly without SSH.

### Execution
```bash
cd ha/sailing-dash
./deploy.sh --stage
```

### Expected Results
1. `build.py` executes automatically before deploy.
2. `lovelace_resources` are uploaded into `local-ha` container storage.
3. `sensors-sailing.yaml` is merged into `/config/configuration.yaml` inside `local-ha`.
4. `dashboard-sailing.yaml` is written into `/config/.storage/lovelace.dashboard_sailing` inside `local-ha`.
5. `local-ha` Docker container restarts cleanly.

---

## 🔄 4. Stage Orchestrator & File Watcher Verification

Test end-to-end Stage environment launching and live file re-building.

### Execution
```bash
cd ha/sailing-dash
./run_stage.sh
```

### Verification Steps
1. Confirm that `local-ha` container is running:
   ```bash
   docker ps --filter "name=local-ha"
   ```
2. Open browser at `http://localhost:8123/dashboard-sailing/`.
3. Modify a comment or title in `src/yaml/dashboard/sections/01_sensors.yaml`.
4. Check console output: confirm that `[INFO] Source file change detected` appears, triggering re-build and re-deploy.
5. Refresh browser to confirm the UI reflects the change.

---

## 🚢 5. Prod Deploy & Safety Diff Verification

Verify that production deploy correctly compares live HA configuration against local source files before overwriting.

### Execution
```bash
cd ha/sailing-dash
./deploy.sh --prod
```

### Safety Features to Verify
1. **Timestamped Remote Backup**: Check that a backup file (`lovelace.dashboard_sailing.YYYYMMDDHHMMSS.bak`) is created on the target host before updating.
2. **Pre-Deploy Diff**: Review the printed unified diff comparing live HA UI config against `build/dashboard-sailing.yaml`.
3. **Clean Diff Guard**: Test `REQUIRE_CLEAN_DIFF=1 ./deploy.sh --prod` to ensure deployment aborts if unexpected manual UI changes exist on the remote instance.
