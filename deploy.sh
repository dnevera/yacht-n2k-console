#!/usr/bin/env bash
#
# YDNU-02 Web Console — deploy to gateway.local (Pi5)
#
# Usage:
#   ./deploy.sh [host]
#
# Default host: user@<gateway-host>
#

set -euo pipefail

HOST="${1:-user@<gateway-host>}"
REMOTE_DIR="/opt/nmea2000/ydnu02-web"
SERVICE="ydnu02-web"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }

SSH="ssh -o ConnectTimeout=15 -o ServerAliveInterval=5"
SCP="scp -o ConnectTimeout=10"

# --- 1. Upload files ---
log "Uploading files to ${HOST}:${REMOTE_DIR}"
${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/static/css ${REMOTE_DIR}/static/js ${REMOTE_DIR}/static/tabs ${REMOTE_DIR}/tests"

${SCP} "${LOCAL_DIR}/ydnu02.py"               "${HOST}:${REMOTE_DIR}/ydnu02.py"
${SCP} "${LOCAL_DIR}/app.py"                  "${HOST}:${REMOTE_DIR}/app.py"
${SCP} "${LOCAL_DIR}/device_manager.py"       "${HOST}:${REMOTE_DIR}/device_manager.py"
${SCP} "${LOCAL_DIR}/models.py"               "${HOST}:${REMOTE_DIR}/models.py"
${SCP} "${LOCAL_DIR}/gobius_parsers.py"       "${HOST}:${REMOTE_DIR}/gobius_parsers.py"
${SCP} "${LOCAL_DIR}/mopeka_parsers.py"      "${HOST}:${REMOTE_DIR}/mopeka_parsers.py"
${SCP} "${LOCAL_DIR}/mopeka_scanner.py"      "${HOST}:${REMOTE_DIR}/mopeka_scanner.py"
${SCP} "${LOCAL_DIR}/ble_registry.py"        "${HOST}:${REMOTE_DIR}/ble_registry.py"
${SCP} "${LOCAL_DIR}/gobius_ble_poller.py"  "${HOST}:${REMOTE_DIR}/gobius_ble_poller.py"
${SCP} "${LOCAL_DIR}/n2k_command_builder.py"    "${HOST}:${REMOTE_DIR}/n2k_command_builder.py"
${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/sensors"
${SCP} -r "${LOCAL_DIR}/sensors/"*.py        "${HOST}:${REMOTE_DIR}/sensors/"
${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/routes"
${SCP} -r "${LOCAL_DIR}/routes/"*             "${HOST}:${REMOTE_DIR}/routes/"
${SCP} "${LOCAL_DIR}/static/index.html"        "${HOST}:${REMOTE_DIR}/static/index.html"
${SCP} "${LOCAL_DIR}/static/css/style.css"     "${HOST}:${REMOTE_DIR}/static/css/style.css"
${SCP} ${LOCAL_DIR}/static/js/*.js            "${HOST}:${REMOTE_DIR}/static/js/"
${SCP} ${LOCAL_DIR}/static/tabs/*.html        "${HOST}:${REMOTE_DIR}/static/tabs/"
${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/tests/specs"
${SCP} -r "${LOCAL_DIR}/tests/"*.py           "${HOST}:${REMOTE_DIR}/tests/"
${SCP} -r "${LOCAL_DIR}/tests/specs/"*.py     "${HOST}:${REMOTE_DIR}/tests/specs/"
${SCP} "${LOCAL_DIR}/ydnu02-web.service"       "${HOST}:${REMOTE_DIR}/ydnu02-web.service"

log "Files uploaded ✓"

# --- 2. Install & restart service ---
log "Installing systemd service..."
${SSH} ${HOST} "sudo cp ${REMOTE_DIR}/ydnu02-web.service /etc/systemd/system/${SERVICE}.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable ${SERVICE} \
  && sudo systemctl restart ${SERVICE}"

log "Service restarted ✓"

# --- 3. Wait and verify ---
sleep 3
log "Checking service status..."
${SSH} ${HOST} "sudo systemctl is-active ${SERVICE} && echo 'Service: RUNNING' || echo 'Service: FAILED'"

log "Checking API..."
if curl -sf --connect-timeout 5 --max-time 10 "http://${HOST#*@}:8080/static/js/core.js" > /dev/null 2>&1; then
    log "API: reachable ✓"
else
    warn "API: not reachable yet (may need a few more seconds)"
fi

log "Deploy complete → http://${HOST#*@}:8080"
