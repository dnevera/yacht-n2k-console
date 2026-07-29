#!/usr/bin/env bash
#
# YDNU-02 Web Console + N2K Proxy — deploy to gateway.local (Pi5)
#
# Usage:
#   ./deploy.sh [host]           — deploy both web service and proxy
#   ./deploy.sh [host] --proxy   — deploy proxy only (ydnu02_tcp_gateway.py + service)
#   ./deploy.sh [host] --web     — deploy web service only
#
# Default host: user@<gateway-host>
#
# Both services are collocated in /opt/nmea2000/ydnu02-web/:
#   ydnu02_tcp_gateway.py     → ydnu02-tcp-gateway.service (starts first)
#   app.py + ...     → ydnu02-web.service (requires ydnu02-tcp-gateway)
#

set -euo pipefail

HOST="${1:-user@<gateway-host>}"
MODE="${2:-}"   # --proxy | --web | (empty = both)

REMOTE_DIR="/opt/nmea2000/ydnu02-web"
WEB_SERVICE="ydnu02-web"
PROXY_SERVICE="ydnu02-tcp-gateway"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
section() { echo -e "${BLUE}[deploy]${NC} ── $* ──"; }

SSH="ssh -o ConnectTimeout=15 -o ServerAliveInterval=5"
SCP="scp -o ConnectTimeout=10"

DEPLOY_PROXY=true
DEPLOY_WEB=true
[[ "$MODE" == "--proxy" ]] && DEPLOY_WEB=false
[[ "$MODE" == "--web"   ]] && DEPLOY_PROXY=false

${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}"

# ── N2K Proxy ────────────────────────────────────────────────────────────────

if $DEPLOY_PROXY; then
    section "ydnu02-tcp-gateway"
    log "Uploading ydnu02_tcp_gateway.py to ${HOST}:${REMOTE_DIR}"
    ${SCP} "${LOCAL_DIR}/ydnu02_tcp_gateway/ydnu02_tcp_gateway.py" "${HOST}:${REMOTE_DIR}/ydnu02_tcp_gateway.py"
    ${SCP} "${LOCAL_DIR}/ydnu02_tcp_gateway/ydnu02-tcp-gateway.service" "${HOST}:/tmp/ydnu02-tcp-gateway.service"

    log "Installing ydnu02-tcp-gateway service..."
    ${SSH} ${HOST} "sudo mv /tmp/ydnu02-tcp-gateway.service /etc/systemd/system/${PROXY_SERVICE}.service \
      && sudo systemctl daemon-reload \
      && sudo systemctl enable ${PROXY_SERVICE} \
      && sudo systemctl restart ${PROXY_SERVICE}"

    sleep 3
    ${SSH} ${HOST} "sudo systemctl is-active ${PROXY_SERVICE} \
      && echo 'ydnu02-tcp-gateway: RUNNING ✓' || echo 'ydnu02-tcp-gateway: FAILED ✗'"
    log "ydnu02-tcp-gateway deploy complete ✓"
fi

# ── Web Service ───────────────────────────────────────────────────────────────

if $DEPLOY_WEB; then
    section "ydnu02-web"
    log "Uploading files to ${HOST}:${REMOTE_DIR}"
    ${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/static/css ${REMOTE_DIR}/static/js \
      ${REMOTE_DIR}/static/tabs ${REMOTE_DIR}/tests ${REMOTE_DIR}/tests/specs \
      ${REMOTE_DIR}/sensors ${REMOTE_DIR}/routes"

    # Core Python modules
    for f in ydnu02.py app.py device_manager.py models.py \
              gobius_parsers.py mopeka_parsers.py mopeka_scanner.py \
              ble_registry.py gobius_ble_poller.py \
              n2k_command_builder.py n2k_meta.py; do
        ${SCP} "${LOCAL_DIR}/${f}" "${HOST}:${REMOTE_DIR}/${f}"
    done

    # Sub-packages
    ${SCP} -r "${LOCAL_DIR}/sensors/"*.py   "${HOST}:${REMOTE_DIR}/sensors/"
    ${SCP} -r "${LOCAL_DIR}/routes/"*       "${HOST}:${REMOTE_DIR}/routes/"

    # Static assets
    ${SCP} "${LOCAL_DIR}/static/index.html"       "${HOST}:${REMOTE_DIR}/static/index.html"
    ${SCP} "${LOCAL_DIR}/static/css/style.css"    "${HOST}:${REMOTE_DIR}/static/css/style.css"
    ${SCP} "${LOCAL_DIR}/static/js/"*.js          "${HOST}:${REMOTE_DIR}/static/js/"
    ${SCP} "${LOCAL_DIR}/static/tabs/"*.html      "${HOST}:${REMOTE_DIR}/static/tabs/"

    # Tests
    ${SCP} "${LOCAL_DIR}/tests/"*.py              "${HOST}:${REMOTE_DIR}/tests/"
    ${SCP} "${LOCAL_DIR}/tests/specs/"*.py        "${HOST}:${REMOTE_DIR}/tests/specs/"

    # Service unit
    ${SCP} "${LOCAL_DIR}/ydnu02-web.service"      "${HOST}:${REMOTE_DIR}/ydnu02-web.service"

    log "Files uploaded ✓"

    log "Installing ydnu02-web service..."
    ${SSH} ${HOST} "sudo cp ${REMOTE_DIR}/ydnu02-web.service \
        /etc/systemd/system/${WEB_SERVICE}.service \
      && sudo systemctl daemon-reload \
      && sudo systemctl enable ${WEB_SERVICE} \
      && sudo systemctl restart ${WEB_SERVICE}"

    sleep 3
    ${SSH} ${HOST} "sudo systemctl is-active ${WEB_SERVICE} \
      && echo 'ydnu02-web: RUNNING ✓' || echo 'ydnu02-web: FAILED ✗'"

    log "Checking API..."
    if curl -sf --connect-timeout 5 --max-time 10 \
        "http://${HOST#*@}:8080/static/js/core.js" > /dev/null 2>&1; then
        log "API: reachable ✓"
    else
        warn "API: not reachable yet (may need a few more seconds)"
    fi

    log "ydnu02-web deploy complete ✓"
fi

log "Deploy done → http://${HOST#*@}:8080"
