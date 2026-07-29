#!/usr/bin/env bash
#
# deploy.sh — YDNU-02 Web Console + TCP Gateway deploy to gateway.local (Pi5)
#
# ── MINI-SKILL (read this if context is lost) ─────────────────────────────────
#
# WHAT THIS DEPLOYS
#   Two independent services, both living in /opt/nmea2000/ydnu02-web/ :
#
#   1. ydnu02-tcp-gateway   (ydnu02_tcp_gateway/ydnu02_tcp_gateway.py)
#      Holds /dev/ttyACM0 exclusively. Exposes:
#        :4001  DATA — NMEA 2000 ASCII broadcast to all TCP clients (HA + web)
#        :4002  CTRL — exclusive passthrough for service terminal / firmware
#      systemd: ydnu02-tcp-gateway.service  (starts BEFORE ydnu02-web)
#
#   2. ydnu02-web           (app.py + routes/ + static/ + …)
#      FastAPI web console on :8080. Reads NMEA from :4001, sends ctrl via :4002.
#      systemd: ydnu02-web.service  (Requires=ydnu02-tcp-gateway.service)
#
# USAGE
#   ./deploy.sh [host]           — deploy both services  (default: user@<gateway-host>)
#   ./deploy.sh [host] --proxy   — gateway only (faster, no web restart)
#   ./deploy.sh [host] --web     — web only (gateway untouched, no HA restart)
#
# FILE OWNERSHIP RULE
#   ydnu02_tcp_gateway.py is uploaded via scp directly to REMOTE_DIR.
#   scp as user denn → file is denn-owned automatically. NO sudo needed.
#   The .service unit goes via /tmp → sudo mv (only systemd dir needs root).
#   NEVER use "sudo cp + sudo chown" for py files — scp ownership is correct.
#
# HA RESTART (MANDATORY after every proxy restart)
#   Bug in nmea2000 lib v2026.5.2 (ioclient.py): when the proxy TCP connection
#   drops on restart, HA enters an infinite spin loop at 100% CPU.
#   Fix: always "sudo docker restart homeassistant" after proxy restart.
#   This script does it automatically in the --proxy / both sections.
#   Only --web skips it (proxy not touched → HA connection unaffected).
#
# SERVICE START ORDER
#   ydnu02-tcp-gateway  →  ydnu02-web  →  homeassistant (docker)
#   ydnu02-web.service has Requires= + After= on ydnu02-tcp-gateway.service.
#
# ONE-TIME MIGRATION (already done, for reference only)
#   Was: nmea-tcp-proxy.service  from /usr/local/bin/nmea_tcp_proxy.py
#   Now: ydnu02-tcp-gateway.service  from /opt/nmea2000/ydnu02-web/ydnu02_tcp_gateway.py
#   Also removed: ydnu02-tcp.service (legacy socat bridge, never used)
#   Migration steps were done manually via SSH (not repeatable via this script).
#
# VERIFY AFTER DEPLOY
#   ssh user@<gateway-host> 'systemctl is-active ydnu02-tcp-gateway ydnu02-web'
#   ssh user@<gateway-host> 'ss -tnp | grep 4001'    # 2 ESTAB: HA + ydnu02-web
#   curl http://gateway.local:8080/api/info           # firmware_version, state: online
# ──────────────────────────────────────────────────────────────────────────────

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

    # Note: py file is uploaded via scp (line above) directly as denn → denn-owned, no sudo needed.
    # Service unit goes via /tmp because /etc/systemd/system/ requires sudo.
    log "Installing ydnu02-tcp-gateway service..."
    ${SSH} ${HOST} "sudo mv /tmp/ydnu02-tcp-gateway.service /etc/systemd/system/${PROXY_SERVICE}.service \
      && sudo systemctl daemon-reload \
      && sudo systemctl enable ${PROXY_SERVICE} \
      && sudo systemctl restart ${PROXY_SERVICE}"

    sleep 3
    ${SSH} ${HOST} "sudo systemctl is-active ${PROXY_SERVICE} \
      && echo 'ydnu02-tcp-gateway: RUNNING ✓' || echo 'ydnu02-tcp-gateway: FAILED ✗'"
    log "ydnu02-tcp-gateway deploy complete ✓"

    # ── HA restart (MANDATORY after every proxy restart) ─────────────────────
    # Bug in nmea2000 lib v2026.5.2 (ioclient.py): when the proxy TCP connection
    # drops (EOF on reconnect), HA enters an infinite spin loop at 100% CPU.
    # The only fix is a full HA container restart. See SKILL.md §HA CPU Spin Loop.
    log "Restarting Home Assistant (CPU spin-loop bug workaround)..."
    ${SSH} ${HOST} "sudo docker restart homeassistant" \
      && log "Home Assistant restarted ✓" \
      || warn "HA restart failed — check: ssh ${HOST} 'sudo docker ps'"
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
