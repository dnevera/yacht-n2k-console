# Installation & Deployment Guide for Sailing Dashboard

This guide describes how to install, configure, launch, and re-install the **Sailing Dashboard** for Home Assistant in both **Stage (Local Docker)** and **Prod (Vessel Pi5)** environments.

> For the manual UI steps to set up **HACS itself** (device-flow activation) and add our custom
> **NMEA 2000** integration (`github.com/dnevera/ha-nmea2000`) as a HACS custom repository, see the
> dedicated [`HACS_SETUP.md`](./HACS_SETUP.md) guide.

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
- **Real HACS Install**: Downloads the official HACS release (cached under `.cache/hacs/`, ~50MB, requires internet on first run) and installs it into `/config/custom_components/hacs/` — the same real HACS used on Prod, not an emulation. If offline, this step is skipped with a warning and the rest of provisioning (including the NMEA 2000 integration below) still completes.
- **NMEA 2000 Integration & Data Source**: Installs the vendored `NMEA 2000` custom integration (our own `dnevera/ha-nmea2000` fork) into `/config/custom_components/nmea2000/` and registers a config entry pointed at `127.0.0.1:4001` (`mock_nmea_emulator.py`) — this is what actually creates the N2K sensor entities the dashboard displays.
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
| Dashboard/cards render fine but every N2K sensor (COG/SOG, wind, STW, depth, GPS) shows "unavailable" on a fresh Stage install | Stage never had HACS **or** the "NMEA 2000" custom integration (domain `nmea2000`, normally installed via HACS on Prod from **our own fork** `github.com/dnevera/ha-nmea2000` (branch `bumblebee-custom`), added as a HACS custom repository — not the upstream `tomer-w/ha-nmea2000`) — `stage_provisioner.py` used to provision only the dashboard/cards/auth, never the actual data source that connects to `:4001` and creates the N2K entities in the first place | Fixed: `deploy_nmea2000_integration()` installs a vendored copy of the integration (`vendor/custom_components/nmea2000/`, mirrored from our own `dnevera/ha-nmea2000` fork — HACS is just a downloader, copying the same files works identically) into `/config/custom_components/nmea2000/`, and `provision_nmea2000_config_entry()` auto-registers a `gateway_type: text` config entry pointed at `127.0.0.1:4001` (`local-ha` uses `network_mode: host`, reaching `mock_nmea_emulator.py` directly) — both run automatically as part of `stage_provisioner.py provision` / `./deploy.sh --stage --clean-install`. HA installs the integration's pip dependency (our patched `dnevera/nmea2000` git fork, see `requirements-ha.txt` section 0) on next start; give it ~10-20s after `--clean-install` before checking entities. |
| `local-ha` had the NMEA 2000/frontend cards working but **no HACS integration at all** (no `Settings -> HACS` panel), even though Prod installs everything through HACS | Stage provisioning only ever copied vendored files directly into `/config/custom_components/` and `/config/www/`, bypassing HACS entirely — functionally equivalent for the dashboard, but not a faithful Stage/Prod parity, and made it impossible to add *other* HACS repos through the UI for further testing | `stage_provisioner.py`'s `deploy_hacs_integration()` now downloads the real official HACS release from `github.com/hacs/integration` (cached in `ha/sailing-dash/.cache/hacs/`, gitignored, **not** committed to git — it's a ~50MB prebuilt frontend bundle) and installs it into `/config/custom_components/hacs/`, exactly like HACS's own install script does on a real HA instance. This is not a copy-of-files emulation of "what HACS would install" — it's the actual HACS integration. Runs automatically as part of `stage_provisioner.py provision`; requires internet access on the machine running `docker` the first time (subsequent runs reuse the cache). If offline, HACS install is skipped with a `WARN` and the rest of provisioning (dashboard, cards, NMEA 2000 data source) still completes normally, since none of it ever depended on HACS being present. To finish activating HACS itself (GitHub device-flow login, adding `dnevera/ha-nmea2000` as a custom repository through the real UI instead of the vendored copy), open `Settings -> Devices & Services -> HACS` once in the browser, same one-time step as a fresh Prod install. |
| HA container **crash-loops** right after `--clean-install` (`docker logs local-ha` shows `KeyError: 'subentries'` in `config_entries.async_initialize()`, dashboard never comes up) | The `nmea2000` config entry written by `provision_nmea2000_config_entry()` was missing the `subentries` key that this HA core version requires on every config entry at boot (a schema field added after this script was first written) | Fixed: the entry dict now always includes `"subentries": {}`, and re-running `provision` on an entry created by an older version of this script backfills it via `existing.setdefault("subentries", {})` — no manual `.storage/` editing needed, just re-run `./deploy.sh --stage --clean-install` (or `python3 stage_provisioner.py provision --clean-install`). |
