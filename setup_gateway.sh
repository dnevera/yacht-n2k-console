#!/usr/bin/env bash
#
# setup_gateway.sh — First-time setup on Raspberry Pi (runs locally on Pi)
#
# ── MINI-SKILL (read this if context is lost) ─────────────────────────────────
#
# PURPOSE
#   One-time initial setup of the YDNU-02 Web Console on a fresh Raspberry Pi.
#   Installs system packages, Python deps, application files, and systemd service.
#   After this script, use deploy.sh for incremental updates.
#
# WHEN TO USE
#   - First deployment to a new Pi
#   - After OS reinstall
#   - NOT for routine updates (use deploy.sh instead)
#
# HOW IT WORKS
#   1. Installs system packages: python3, pip, bluetooth, bluez
#   2. Installs Python packages: fastapi, uvicorn, bleak, websockets
#   3. Adds current user to dialout + bluetooth groups
#   4. Copies app files from bundle to INSTALL_DIR
#   5. Creates and enables systemd service
#   6. Starts service and verifies
#
# SECURITY RULES
#   - NO hardcoded usernames — uses $(whoami) for the current SSH user
#   - NO hardcoded hostnames — uses $(hostname) for local hostname
#   - Installation directory INSTALL_DIR is the only hardcoded path
#
# USAGE
#   1. Copy bundle to Pi:  scp build/ydnu02-bundle.tar.gz user@gateway-host:~/
#   2. SSH into Pi:        ssh user@gateway-host
#   3. Extract:            tar xzf ydnu02-bundle.tar.gz
#   4. Run:                cd ydnu02-bundle && ./setup.sh
#
# SKILL: Adding a new system dependency
#   Add to the apt-get install line in Step 1
#
# SKILL: Adding a new Python dependency
#   Add to the pip3 install block in Step 2
#   Pin exact version for reproducibility
#
# SKILL: Changing the install directory
#   Modify INSTALL_DIR variable AND update the .service unit paths
#
# TODO: Add requirements.txt instead of inline pip install
# TODO: Add --uninstall flag to cleanly remove everything
# TODO: Add version check to skip re-install if already up-to-date
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[setup]${NC} $*"; }
fail() { echo -e "${RED}[setup] ERROR:${NC} $*"; exit 1; }

INSTALL_DIR="/opt/nmea2000"
SERVICE="ydnu02-web"
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
CURRENT_USER="$(whoami)"

echo ""
echo "══════════════════════════════════════════════════"
echo "  YDNU-02 Console — Setup"
echo "  Install dir: ${INSTALL_DIR}"
echo "  User:        ${CURRENT_USER}"
echo "══════════════════════════════════════════════════"
echo ""

# ── 1. System packages ──
log "Step 1/6: Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip bluetooth bluez
log "System packages ✓"

# ── 2. Python dependencies ──
log "Step 2/6: Installing Python packages..."
pip3 install --break-system-packages --quiet \
  fastapi==0.140.0 \
  uvicorn==0.51.0 \
  bleak==3.0.2 \
  websockets==16.1.1 \
  python-multipart
log "Python packages ✓"

# ── 3. User permissions ──
log "Step 3/6: Setting permissions..."
sudo usermod -aG dialout,bluetooth "${CURRENT_USER}" 2>/dev/null || true
log "Permissions ✓"

# ── 4. Copy application files ──
log "Step 4/6: Installing application to ${INSTALL_DIR} (SSD)..."
sudo mkdir -p "${INSTALL_DIR}/static/css" \
              "${INSTALL_DIR}/static/js" \
              "${INSTALL_DIR}/sensors" \
              "${INSTALL_DIR}/routes" \
              "${INSTALL_DIR}/tests/specs"
sudo chown -R "${CURRENT_USER}:${CURRENT_USER}" "${INSTALL_DIR}"

# Copy all app files from bundle
cp -v "${BUNDLE_DIR}/app/"*.py               "${INSTALL_DIR}/"
cp -v "${BUNDLE_DIR}/app/sensors/"*.py       "${INSTALL_DIR}/sensors/"
cp -v "${BUNDLE_DIR}/app/routes/"*           "${INSTALL_DIR}/routes/"
cp -v "${BUNDLE_DIR}/app/static/index.html"  "${INSTALL_DIR}/static/"
cp -v "${BUNDLE_DIR}/app/static/css/"*.css   "${INSTALL_DIR}/static/css/"
cp -v "${BUNDLE_DIR}/app/static/js/"*.js     "${INSTALL_DIR}/static/js/"

# Tests (optional)
cp "${BUNDLE_DIR}/app/tests/"*.py            "${INSTALL_DIR}/tests/"       2>/dev/null || true
cp "${BUNDLE_DIR}/app/tests/specs/"*.py      "${INSTALL_DIR}/tests/specs/" 2>/dev/null || true

# BLE registry (sensor config)
if [ -f "${BUNDLE_DIR}/app/ble_registry.json" ]; then
    cp "${BUNDLE_DIR}/app/ble_registry.json" "${INSTALL_DIR}/"
    log "BLE registry (sensor config) copied"
fi

# Deploy script + config template for future incremental deploys
cp "${BUNDLE_DIR}/app/deploy.sh"             "${INSTALL_DIR}/" 2>/dev/null || true
cp "${BUNDLE_DIR}/app/deploy.conf.template"  "${INSTALL_DIR}/" 2>/dev/null || true

log "Application installed ✓"

# ── 5. Install systemd service ──
# NOTE: User= is set dynamically from CURRENT_USER, not hardcoded
log "Step 5/6: Installing systemd service..."
sudo tee /etc/systemd/system/${SERVICE}.service > /dev/null <<EOF
[Unit]
Description=YDNU-02 NMEA 2000 Web Console
After=network.target bluetooth.target
Wants=network.target bluetooth.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/app.py --port 8080
Restart=on-failure
RestartSec=5
TimeoutStopSec=3s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ydnu02-web

# Serial port + BLE access
SupplementaryGroups=dialout bluetooth

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${INSTALL_DIR}
ProtectHome=false

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE}
log "Service installed ✓"

# ── 6. Start and verify ──
log "Step 6/6: Starting service..."
sudo systemctl restart ${SERVICE}
sleep 3

STATUS=$(sudo systemctl is-active ${SERVICE} 2>/dev/null || echo "failed")
if [ "$STATUS" = "active" ]; then
    log "Service: RUNNING ✓"
else
    warn "Service: ${STATUS}"
    warn "Check logs: journalctl -u ${SERVICE} -n 30"
fi

HOSTNAME=$(hostname)
echo ""
log "══════════════════════════════════════════════════"
log " Setup complete → http://${HOSTNAME}.local:8080"
log "══════════════════════════════════════════════════"
echo ""
log "Next steps:"
log "  1. Plug YDNU-02 USB gateway into this Pi"
log "  2. Open http://${HOSTNAME}.local:8080"
log "  3. Verify NMEA + BLE data"
log "  4. Copy deploy.conf.template to deploy.conf and fill in your settings"
