# Installation & Deployment Guide for Sailing Dashboard

This guide describes how to install, configure, launch, and re-install the **Sailing Dashboard** for Home Assistant in both **Stage (Local Docker)** and **Prod (Vessel Pi5)** environments.

---

## 📋 System Requirements & Dependencies

### General Prerequisites
- **Python**: Python 3.9 or newer with `pyyaml` installed.
- **Docker**: Docker Engine 20.10+ and Docker Compose v2 (for Stage environment).
- **SSH/SCP**: `ssh` and `scp` CLI tools (for Prod remote deployment).

### Python Environment Setup
```bash
# Install PyYAML dependency required by build engine
pip install pyyaml
```

---

## 🆕 1. First-Time Setup (From Scratch / Clean HA)

Use this workflow when starting with a **clean or uninitialized Home Assistant instance** (e.g. fresh local Docker container or fresh Home Assistant installation on a vessel).

### Stage Environment (Local Docker)

When starting a fresh local Docker container with an empty `/config` directory, Home Assistant normally lands on the initial onboarding wizard (`/onboarding.html`) and lacks registered Lovelace dashboards, resources, and custom card JS files. The Sailing Dashboard includes an automated provisioning engine (`stage_provisioner.py`) that initializes everything automatically.

#### Step-by-Step Initial Launch:

1. **Navigate to the dashboard directory**:
   ```bash
   cd ha/sailing-dash
   ```

2. **Launch Stage environment**:
   ```bash
   ./run_stage.sh
   ```

#### What Happens Automatically during First Launch:
- **HA Container Startup**: Starts `local-ha` Docker container via `docker compose up -d`.
- **Health & Readiness Check**: `stage_provisioner.py` inspects HA state and detects an uninitialized/empty Home Assistant instance.
- **Onboarding Wizard Bypass**: Writes `.storage/onboarding` status to bypass the setup wizard automatically.
- **Dashboard Registry Setup**: Registers `dashboard-sailing` in `.storage/lovelace_dashboards`.
- **Card Bundle Deployment**: Copies all 7 custom card JS bundles (`card-mod`, `compass-card`, `apexcharts-card`, `windrose-card`, `plotly-graph-card`, `config-template-card`, `windy-boat-card`) from local build and `vendor/` directories into `/config/www/` (and `/config/www/community/`).
- **Resource Registry Population**: Populates `.storage/lovelace_resources` with proper card path mappings (`/local/<card>.js`).
- **Sensor & Config Merge**: Merges `sensors-sailing.yaml` into `configuration.yaml` (REST sensors, template entities, automations).
- **Dashboard Deployment**: Compiles `src/` files into `build/dashboard-sailing.yaml` and deploys it to HA storage.
- **HTTP Readiness Verification**: Polls `http://localhost:8123/dashboard-sailing/` until HTTP 200 OK is returned.

3. **Access Dashboard**:
   Open browser and navigate to: `http://localhost:8123/dashboard-sailing/`
   If HA shows a login form, sign in with **`test` / `test`** (provisioned automatically by `stage_provisioner.py provision_auth()` — see Troubleshooting below).

---

## 🔄 2. Complete Re-installation / Reset (`--clean-install`)

Use this workflow if Home Assistant configuration becomes corrupted, custom card resources fail to render, or you want to force a **complete reset and re-installation** of all dashboard components, storage registries, and card bundles.

### Force Re-install on Stage:
```bash
cd ha/sailing-dash
./run_stage.sh --clean-install
# OR
./deploy.sh --stage --clean-install
```

### Force Re-install on Prod:
```bash
cd ha/sailing-dash
./deploy.sh --prod --clean-install
```

#### What Happens during Clean Re-installation:
1. **Registry Reset**: Re-generates `.storage/lovelace_dashboards` and `.storage/lovelace_resources` from scratch, resolving any broken or missing resource mappings.
2. **Card Bundle Overwrite**: Re-copies all 7 vendor and built JS card bundles into `/config/www/` (and `/config/www/community/`).
3. **Configuration Refresh**: Re-applies base sensor YAML configurations (`sensors-sailing.yaml`) into `configuration.yaml`.
4. **Dashboard Overwrite**: Overwrites dashboard storage file (`lovelace.dashboard-sailing`) with freshly built `dashboard-sailing.yaml`.
5. **HA Service Restart**: Triggers a Home Assistant service/container restart and verifies HTTP 200 OK response.

---

## ⚡ 3. Incremental Stage Deployments (Development)

During active dashboard or sensor development, you do not need to re-provision Home Assistant. Use incremental deployment to recompile and apply changes instantly.

### Mode A: Auto-Rebuild with File Watcher (Recommended for Dev)
```bash
cd ha/sailing-dash
./run_stage.sh
```
- Monitors `ha/sailing-dash/src/` for file changes.
- Automatically compiles YAML files and deploys updated dashboard and sensors on save.

### Mode B: Single-Shot Incremental Update
```bash
cd ha/sailing-dash
./deploy.sh --stage
```

### Mode C: Partial Updates
If you only changed a specific component:
```bash
# Update dashboard UI layout only
./deploy.sh --stage --dashboard-only

# Update REST/template sensors only
./deploy.sh --stage --sensors-only

# Update JS card bundles only
./deploy.sh --stage --resources-only
```

---

## 🚀 4. Production Deployment (Vessel Pi5)

Deploying to the live vessel Home Assistant instance running on Pi5 (`bumblebee.local`).

### 1. Configure Target Credentials
Copy `deploy.conf.template` to `deploy.conf` in the project root if not already present:
```bash
cp deploy.conf.template deploy.conf
```
Configure `deploy.conf` with production details:
```bash
DEPLOY_HOST="user@bumblebee.local"
HA_CONTAINER="homeassistant"
```

### 2. First-Time Prod Install:
```bash
cd ha/sailing-dash
./deploy.sh --prod --install
```

### 3. Production Updates (Incremental):
```bash
# Standard full update (Sensors + Dashboard + Resources)
./deploy.sh --prod

# Deploy dashboard only
./deploy.sh --prod --dashboard-only
```

---

## ⚙️ Summary of Deployment Commands & Flags

| Command | Environment | Mode / Purpose | What it Does |
| :--- | :--- | :--- | :--- |
| `./run_stage.sh` | Stage | **Auto-Detect / Watcher** | Checks HA state -> auto-provisions if clean -> starts NMEA emulator -> deploys -> launches watcher |
| `./run_stage.sh --clean-install` | Stage | **Force Reinstall** | Overwrites storage registries & JS card bundles -> redeploys dashboard -> starts watcher |
| `./deploy.sh --stage` | Stage | **Incremental Update** | Compiles `src/` and updates HA sensors and dashboard once |
| `./deploy.sh --stage --clean-install` | Stage | **Force Reinstall** | Forces re-provisioning of storage, card bundles, sensors, and dashboard |
| `./deploy.sh --prod --install` | Prod | **First-time Install** | Installs card resources, registers dashboard, and deploys configuration to Prod HA |
| `./deploy.sh --prod` | Prod | **Incremental Update** | Compiles `src/`, shows diff against live Prod dashboard, and deploys updates |
| `./deploy.sh --prod --dashboard-only` | Prod | **Dashboard Only** | Deploys dashboard YAML only |
| `./deploy.sh --prod --sensors-only` | Prod | **Sensors Only** | Merges sensors into production `configuration.yaml` |

---

## 🔍 Troubleshooting Installation

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| `Docker daemon is not running` | Docker process stopped | Start Docker Desktop / `systemctl start docker` |
| `ModuleNotFoundError: No module named 'yaml'` | Missing Python PyYAML library | Run `pip install pyyaml` |
| `Permission denied` on deploy | Missing SSH key or sudo privileges | Ensure SSH key is added and sudo has passwordless docker access |
| HA Dashboard displays "Custom element doesn't exist" | Custom JS resources missing or unregistered | Run `./run_stage.sh --clean-install` or `./deploy.sh --resources-only` |
| HA stuck on onboarding wizard | Onboarding registry not set | Run `python3 stage_provisioner.py provision` |
| Dashboard registered but shows completely empty (no cards) on first install | On Stage, `/hacsfiles/...` resources (e.g. `card-mod-studio`) had no vendor fallback, so `deploy.sh` used to abort the whole pipeline before `deploy_dashboard.sh` ever ran, leaving `dashboard-sailing` registered without a content file | Fixed: `deploy.sh` now normalizes `/hacsfiles/` → `/local/` on Stage and skips resources without a vendor bundle instead of aborting. Re-run `./deploy.sh --stage --clean-install` if you still see this on an older checkout. |
| Stage HA redirects to a login page and there is no known user/password | Stage config had no auth provider/owner user configured, so HA fell back to the login form with no credentials ever created | `stage_provisioner.py`'s `provision_auth()` creates a real owner user via the standard `homeassistant` auth provider with a fixed **login `test` / password `test`**, writing a valid bcrypt hash straight into `.storage/auth` + `.storage/auth_provider.homeassistant`. Just run `./deploy.sh --stage --clean-install` (or `python3 stage_provisioner.py provision --clean-install`) and log in with `test` / `test` at `http://localhost:8123/` — no manual container restart needed, see next row. (An earlier attempt used the `trusted_networks` provider to skip login entirely, but that provider crashed inside HA's auth flow with `TypeError: 'NoneType' object is not subscriptable` — replaced with this real test/test user instead.) |
| After provisioning, `test`/`test` login (or any `.storage/*` edit) silently reverts / stops working once the container is next stopped or restarted | HA keeps `.storage/auth`, `.storage/onboarding`, etc. loaded **in memory** and flushes that in-memory copy back to disk on container shutdown. If `stage_provisioner.py` edits those files while an *already-running* container still holds an older/stale copy in memory, the next `docker restart` silently overwrites the fresh edit with the stale one (this is how the `test`/`test` credential's `data` field was observed reverting to `null`, crashing login with `TypeError: 'NoneType' object is not subscriptable`) | Fixed at the source: `stage_provisioner.py provision` now automatically **stops** the `local-ha` container before writing to `.storage/`, and **starts** it again afterwards, so HA only ever boots by reading exactly what was just written (no stale in-memory state to flush). `deploy.sh` already calls `stage_provisioner.py provision` first, so this happens transparently. If you ever edit `.storage/` files by hand, always `docker stop local-ha` first, edit, then `docker start local-ha` — never `docker restart`/`docker compose restart` on a container that was running while you edited its storage. |
