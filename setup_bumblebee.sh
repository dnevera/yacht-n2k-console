#!/usr/bin/env bash
#
# YDNU-02 Console — setup on Raspberry Pi 5 (runs locally on Pi)
#
# Usage:
#   1. Copy this bundle to Pi:  scp ydnu02-bundle.tar.gz user@<gateway-host>:~/
#   2. SSH into Pi:             ssh user@<gateway-host>
#   3. Extract:                 tar xzf ydnu02-bundle.tar.gz
#   4. Run:                     cd ydnu02-bundle && ./setup.sh
#

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

echo ""
echo "══════════════════════════════════════════════════"
echo "  YDNU-02 Console — Setup"
echo "  Install dir: ${INSTALL_DIR}"
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
sudo usermod -aG dialout,bluetooth denn 2>/dev/null || true
log "Permissions ✓"

# ── 4. Copy application files ──
log "Step 4/6: Installing application to ${INSTALL_DIR} (SSD)..."
sudo mkdir -p "${INSTALL_DIR}/static/css" \
              "${INSTALL_DIR}/static/js" \
              "${INSTALL_DIR}/sensors" \
              "${INSTALL_DIR}/routes" \
              "${INSTALL_DIR}/tests/specs"
sudo chown -R denn:denn "${INSTALL_DIR}"

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

# Deploy script
cp "${BUNDLE_DIR}/app/deploy.sh"             "${INSTALL_DIR}/" 2>/dev/null || true

log "Application installed ✓"

# ── 5. Install systemd service ──
log "Step 5/6: Installing systemd service..."
sudo tee /etc/systemd/system/${SERVICE}.service > /dev/null <<'EOF'
[Unit]
Description=YDNU-02 NMEA 2000 Web Console
After=network.target bluetooth.target
Wants=network.target bluetooth.target

[Service]
Type=simple
User=denn
WorkingDirectory=/opt/nmea2000
ExecStart=/usr/bin/python3 /opt/nmea2000/app.py --port 8080
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
ReadWritePaths=/opt/nmea2000
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
