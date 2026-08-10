# Installation & Environment Setup Guide

This guide describes how to install, configure, and launch the **Sailing Dashboard** for Home Assistant in both **Stage (Local Docker)** and **Prod (Vessel Pi5)** environments.

---

## 📋 System Requirements

### General Prerequisites
- **Python**: Python 3.9 or newer with `pyyaml` installed.
- **Docker**: Docker Engine 20.10+ and Docker Compose v2 for Stage environment.
- **SSH**: `ssh` and `scp` CLI tools for Prod deployment.

### Python Environment Setup
```bash
# Install PyYAML for the build engine
pip install pyyaml
```

---

## 🛠️ Stage Environment Setup (Local Docker)

The Stage environment runs a full Home Assistant instance in a local Docker container (`local-ha`) alongside a local NMEA 2000 PGN frame emulator (`mock_nmea_emulator.py`).

### 1. Directory Structure
All Stage configuration files live under `ha/sailing-dash/local-ha/`:
- `docker-compose.yml`: Launches Home Assistant using `ghcr.io/home-assistant/home-assistant:stable`.
- `config/configuration.yaml`: Pre-configured HA configuration including dashboard routing.
- `config/.storage/`: Pre-initialized Home Assistant storage containing Lovelace dashboard registrations.
- `mock_nmea_emulator.py`: NMEA 2000 PGN frame simulator broadcasting telemetry on TCP port 4001.

### 2. Launching Stage Environment
Run the launcher or build script from `ha/sailing-dash/`:

```bash
cd ha/sailing-dash

# Option 1: Build Docker container & deploy artifacts directly
./build_docker.sh

# Option 2: Launch full Stage orchestrator with live file watcher (Demo mode)
./run_stage.sh

# Option 3: Launch in Live mode (connected to remote NMEA TCP gateway on Pi5)
./run_stage.sh --live --gw-host bumblebee.local
```

### 3. What Happens on Launch
1. `build.py` compiles all `src/` modules into `build/` artifacts.
2. `docker compose up -d` starts the `local-ha` container.
3. `mock_nmea_emulator.py` starts broadcasting live NMEA 2000 PGN frames on TCP port 4001 (in `--demo` mode).
4. `./deploy.sh --stage` uploads dashboard, sensor, automation, and resource configurations directly into `local-ha`.
5. Active file watcher monitors `src/` directory and automatically re-compiles and re-deploys on any source file changes.

### 4. Accessing Stage HA
Open your browser and navigate to:
```
http://localhost:8123/dashboard-sailing/
```

---

## 🚀 Prod Environment Setup (Vessel Pi5)

The Prod environment deploys compiled build artifacts directly to the production Home Assistant instance running on the vessel (e.g. `bumblebee.local`).

### 1. Configure Target Host
Copy `deploy.conf.template` to `deploy.conf` in the project root if not already present:

```bash
# In project root (/Users/denn/Develop/yacht/yacht-n2k-console)
cp deploy.conf.template deploy.conf
```

Edit `deploy.conf` with production credentials:
```bash
DEPLOY_HOST="user@bumblebee.local"
HA_CONTAINER="homeassistant"
```

### 2. Initial Full Deployment
Execute full initial deployment to Prod:

```bash
cd ha/sailing-dash
./deploy.sh --prod --install
```

### 3. Incremental Updates
For subsequent updates during operation:

```bash
# Deploy all components (resources + sensors + dashboard)
./deploy.sh --prod

# Deploy dashboard only
./deploy.sh --prod --dashboard-only

# Deploy sensors only
./deploy.sh --prod --sensors-only
```

---

## 🔍 Troubleshooting Installation

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| `Docker daemon is not running` | Docker process stopped | Start Docker Desktop / `systemctl start docker` |
| `ModuleNotFoundError: No module named 'yaml'` | Missing Python PyYAML library | Run `pip install pyyaml` |
| `Permission denied` on deploy | Missing SSH key or sudo privileges | Ensure SSH key is added and sudo has passwordless docker access |
| HA Dashboard displays blank cards | Custom JS resources missing in HA | Run `./deploy.sh --resources-only` |
