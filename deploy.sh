#!/usr/bin/env bash
#
# deploy.sh — YDNU-02 Web Console + TCP Gateway deploy to target host
#
# ── MINI-SKILL (read this if context is lost) ─────────────────────────────────
#
# WHAT THIS DEPLOYS
#   Two independent services, both living in $REMOTE_DIR (from deploy.conf):
#
#   1. ydnu02-tcp-gateway   (ydnu02_tcp_gateway/ydnu02_tcp_gateway.py)
#      Holds /dev/ttyACM0 exclusively. Exposes:
#        :$DATA_PORT  DATA — NMEA 2000 ASCII broadcast to all TCP clients
#        :$CTRL_PORT  CTRL — exclusive passthrough for service/firmware
#      systemd: ydnu02-tcp-gateway.service  (starts BEFORE ydnu02-web)
#
#   2. ydnu02-web           (app.py + routes/ + static/ + …)
#      FastAPI web console on :$WEB_PORT. Reads NMEA via DATA, sends ctrl via CTRL.
#      systemd: ydnu02-web.service  (Requires=ydnu02-tcp-gateway.service)
#
# USAGE
#   ./deploy.sh                     — deploy both services + patch HA (host from deploy.conf)
#   ./deploy.sh --proxy             — gateway only + patch HA (no web restart)
#   ./deploy.sh --web               — web only (gateway/HA untouched)
#   ./deploy.sh --patch-ha          — re-apply HA patches only (after HA update)
#   ./deploy.sh --clean-ha          — delete garbage NMEA devices + restart HA
#   ./deploy.sh user@host --proxy   — override host from CLI
#   ./deploy.sh --proxy --no-test   — deploy without running post-deploy tests
#
# POST-DEPLOY TESTS
#   After deploying, tests run automatically on the remote Pi via SSH + pytest.
#   Each deploy mode runs only the relevant test subset:
#     --proxy   → gateway + service mode tests (2 suites)
#     --web     → API + sensors + BLE tests (7 suites)
#     (full)    → all tests (9 suites)
#     --patch-ha / --clean-ha → manual verification only
#   Pass --no-test to skip.
#
# CONFIGURATION
#   All sensitive settings (hostname, user, paths) are in deploy.conf.
#   deploy.conf is in .gitignore — NEVER commit it.
#   deploy.conf.template is the reference — copy and fill in your values.
#
# SECURITY RULES
#   - NO hardcoded hostnames, usernames, or IPs in this script
#   - All connection details come from deploy.conf (gitignored)
#   - Only deploy.conf.template (with placeholders) is committed to git
#   - CLI arg can override DEPLOY_HOST but the default is from deploy.conf
#
# FILE OWNERSHIP RULE
#   Files uploaded via scp are owned by the SSH user automatically.
#   The .service unit goes via /tmp → sudo mv (only systemd dir needs root).
#   NEVER use "sudo cp + sudo chown" for py files — scp ownership is correct.
#
# HA PATCHES (idempotent — safe to run multiple times)
#
#   Patch 1: patches/nmea2000_ioclient.py → nmea2000/ioclient.py
#     Fix: TextNmea2000Gateway readline() EOF → ConnectionError (not silent return → 100% CPU)
#     Upstream PR merged: github.com/tomer-w/nmea2000 (PR #61)
#     Idempotency: MD5 checksum comparison before copy — skipped if identical.
#
#   Patch 2: scripts/patch_ha_nmea2000_message.py → nmea2000/message.py
#     Fix: primary_key = f"{self.id}" caused hash collision for PGN 126996 — all devices
#     shared MD5 818d9516db08fd90ffd1967e3c403bed → second device got 0 HA entities.
#     Fix: include source_iso_name.name (64-bit ISO NAME) in primary_key.
#     Upstream PR pending: github.com/dnevera/nmea2000/tree/fix/pgn-126996-hash-collision-per-source
#     Idempotency: marker "yacht-n2k-console-patch-v1" checked before applying.
#
#   HA is restarted ONLY if at least one patch was actually applied (changed).
#   If HA image is updated: run ./deploy.sh --patch-ha to re-apply both patches.
#   Remove each patch when: nmea2000 package has the fix included upstream.
#
# SERVICE START ORDER
#   ydnu02-tcp-gateway  →  ydnu02-web  →  homeassistant (docker)
#   ydnu02-web.service has Requires= + After= on ydnu02-tcp-gateway.service.
#
# SKILL: Adding a new patch to HA
#   1. Put the patched file in patches/
#   2. Add a new block in patch_ha() following the ioclient pattern:
#      - Discover path dynamically via `python3 -c 'import ... print(__file__)'`
#      - scp to /tmp → docker cp into container
#   3. Test: ./deploy.sh --patch-ha
#
# SKILL: Adding a new Python module to web deploy
#   1. Add filename to the `for f in ...` loop in the DEPLOY_WEB section
#   2. If it's a new sub-package, add a cp -r line like routes/sensors
#   3. Test: ./deploy.sh --web
#
# SKILL: Debugging a failed deploy
#   ssh <host> 'systemctl status ydnu02-tcp-gateway --no-pager -l'
#   ssh <host> 'sudo journalctl -u ydnu02-web -n 50 --no-pager'
#   ssh <host> 'ss -tnp | grep 4001'  # should show 2+ ESTAB connections
#   curl http://<host>:8080/api/info   # should return JSON with state: online
#
# PYTHON DEPENDENCIES (sync_python_deps — runs before proxy/web restart)
#   requirements.txt is uploaded and `pip3 install --user --break-system-packages`
#   is run on the remote host on every deploy (idempotent — pip skips packages
#   that already satisfy the version spec). This exists because deploy.sh used to
#   ONLY copy .py files and never touched the remote Python environment: the
#   installed `nmea2000` package silently drifted from our git-fork requirement
#   (a plain PyPI release ended up installed instead), which caused a 100% CPU
#   spin-loop bug that was only found/fixed manually via SSH.
#   A drift guard additionally checks that the installed nmea2000/message.py
#   actually contains our fork's PGN-126996 fix (pip alone can't detect that a
#   git+ branch moved forward, or that a PyPI release replaced the fork) and
#   force-reinstalls the exact fork branch from requirements.txt if missing.
#
# TODO: Add --dry-run mode that shows what would be deployed without executing
# TODO: Add rollback support (backup previous version before overwrite)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Load configuration ────────────────────────────────────────────────────────
# SKILL: Config loading pattern
#   deploy.conf is sourced as bash — all variables become available.
#   CLI host arg overrides DEPLOY_HOST from config.
#   Required variables: DEPLOY_HOST, REMOTE_DIR, WEB_SERVICE, PROXY_SERVICE,
#                       HA_CONTAINER, DATA_PORT, CTRL_PORT, WEB_PORT
#
# SECURITY: deploy.conf is in .gitignore. Only deploy.conf.template (with
#   placeholders like user@gateway-host) is committed to git.
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/deploy.conf"

if [[ ! -f "$CONF_FILE" ]]; then
    echo "ERROR: ${CONF_FILE} not found."
    echo "Copy deploy.conf.template to deploy.conf and fill in your values."
    exit 1
fi

# shellcheck source=deploy.conf.template
source "$CONF_FILE"

# CLI overrides: ./deploy.sh [host] [mode]
# If first arg is not a flag (--), treat it as host override
if [[ "${1:-}" != --* ]] && [[ -n "${1:-}" ]]; then
    DEPLOY_HOST="$1"
    shift
fi
HOST="$DEPLOY_HOST"
MODE="${1:-}"   # --proxy | --web | --patch-ha | (empty = both)

# REMOTE_DIR, WEB_SERVICE, PROXY_SERVICE, HA_CONTAINER are from deploy.conf
LOCAL_DIR="$SCRIPT_DIR"
PATCH_DIR="${LOCAL_DIR}/patches"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
section() { echo -e "${BLUE}[deploy]${NC} ── $* ──"; }

SSH="ssh -o ConnectTimeout=15 -o ServerAliveInterval=5"
SCP="scp -o ConnectTimeout=10"
RSYNC="rsync -az --checksum --exclude=__pycache__ --exclude=*.pyc"

DEPLOY_PROXY=true
DEPLOY_WEB=true
CLEAN_HA=false
RESTART_HA=false

for arg in "$@"; do
    [[ "$arg" == "--restart-ha" ]] && RESTART_HA=true
done

[[ "$MODE" == "--proxy"    ]] && DEPLOY_WEB=false
[[ "$MODE" == "--web"      ]] && DEPLOY_PROXY=false
[[ "$MODE" == "--patch-ha" ]] && DEPLOY_PROXY=false && DEPLOY_WEB=false && RESTART_HA=true
[[ "$MODE" == "--clean-ha" ]] && DEPLOY_PROXY=false && DEPLOY_WEB=false && CLEAN_HA=true && RESTART_HA=true

# ── pre_deploy_diff() ────────────────────────────────────────────────────────
# Shows current remote state and what will change BEFORE uploading anything.
# SKILL: Pre-deploy diff pattern
#   1. Check remote service status (systemctl is-active)
#   2. For each file to deploy: compare remote vs local via diff
#   3. Show summary table: file → +lines / -lines
#   4. For initial install (file missing on remote): show "[NEW]"
#
# This runs automatically. Pass --no-diff to skip.
# ──────────────────────────────────────────────────────────────────────────────

# Files per deploy mode (local path → remote path)
PROXY_FILES=(
    "VERSION:VERSION"
    "ydnu02_tcp_gateway/__init__.py:ydnu02_tcp_gateway/__init__.py"
    "ydnu02_tcp_gateway/ydnu02_tcp_gateway.py:ydnu02_tcp_gateway/ydnu02_tcp_gateway.py"
    "ydnu02_tcp_gateway/ydnu02_gateway_device.py:ydnu02_tcp_gateway/ydnu02_gateway_device.py"
    "ydnu02_tcp_gateway/data_hub.py:ydnu02_tcp_gateway/data_hub.py"
    "ydnu02_tcp_gateway/serial_reader.py:ydnu02_tcp_gateway/serial_reader.py"
    "ydnu02_tcp_gateway/gateway.py:ydnu02_tcp_gateway/gateway.py"
    "ydnu02_tcp_gateway/gateway_settings.py:ydnu02_tcp_gateway/gateway_settings.py"
)

WEB_FILES=(
    "VERSION:VERSION"
    "device_manager.py:device_manager.py"
    "ydnu02.py:ydnu02.py"
    "app.py:app.py"
    "models.py:models.py"
    "gobius_parsers.py:gobius_parsers.py"
    "mopeka_parsers.py:mopeka_parsers.py"
    "mopeka_scanner.py:mopeka_scanner.py"
    "ble_registry.py:ble_registry.py"
    "gobius_ble_poller.py:gobius_ble_poller.py"
    "n2k_command_builder.py:n2k_command_builder.py"
)

pre_deploy_diff() {
    section "Pre-deploy status & diff"

    # 1. Remote service status
    log "Remote service status:"
    ${SSH} ${HOST} "
        printf '  %-30s %s\n' '${PROXY_SERVICE}' \"\$(systemctl is-active ${PROXY_SERVICE} 2>/dev/null || echo 'not installed')\"
        printf '  %-30s %s\n' '${WEB_SERVICE}' \"\$(systemctl is-active ${WEB_SERVICE} 2>/dev/null || echo 'not installed')\"
        printf '  %-30s %s\n' 'TCP :${DATA_PORT} (DATA)' \"\$(ss -tnp 2>/dev/null | grep -c ':${DATA_PORT}') connections\"
        printf '  %-30s %s\n' 'TCP :${CTRL_PORT} (CTRL)' \"\$(ss -tnp 2>/dev/null | grep -c ':${CTRL_PORT}') connections\"
    " 2>/dev/null || warn "Cannot reach ${HOST}"

    # 2. Per-file diff
    local -a file_list=()
    $DEPLOY_PROXY && file_list+=("${PROXY_FILES[@]}")
    $DEPLOY_WEB   && file_list+=("${WEB_FILES[@]}")

    if [[ ${#file_list[@]} -eq 0 ]]; then
        log "No file diffs for mode ${MODE}"
        return 0
    fi

    echo ""
    log "File changes (remote → local):"
    printf "  %-45s %s\n" "File" "Changes"
    printf "  %-45s %s\n" "----" "-------"

    for entry in "${file_list[@]}"; do
        local_path="${entry%%:*}"
        remote_name="${entry##*:}"

        # Get remote file content
        remote_content=$(${SSH} ${HOST} "cat ${REMOTE_DIR}/${remote_name} 2>/dev/null") 2>/dev/null
        if [[ -z "$remote_content" ]]; then
            printf "  %-45s %s\n" "${remote_name}" "[NEW] $(wc -l < "${LOCAL_DIR}/${local_path}" | tr -d ' ') lines"
        else
            # Count diff lines
            diff_output=$(echo "$remote_content" | diff - "${LOCAL_DIR}/${local_path}" 2>/dev/null) || true
            if [[ -z "$diff_output" ]]; then
                printf "  %-45s %s\n" "${remote_name}" "identical"
            else
                added=$(echo "$diff_output" | grep -c '^>' || true)
                removed=$(echo "$diff_output" | grep -c '^<' || true)
                printf "  %-45s %s\n" "${remote_name}" "+${added} / -${removed}"
            fi
        fi
    done
    echo ""
}

# Check for --no-diff flag or NO_DIFF env var
SKIP_DIFF=false
if [[ "${NO_DIFF:-0}" == "1" ]] || [[ "${SKIP_DIFF:-false}" == "true" ]]; then
    SKIP_DIFF=true
fi
for arg in "$@"; do
    [[ "$arg" == "--no-diff" ]] && SKIP_DIFF=true
done

if ! $SKIP_DIFF && [[ "$MODE" != "--clean-ha" ]]; then
    pre_deploy_diff
fi

# ── patch_ha() ───────────────────────────────────────────────────────────────
# Applies local patches/ fixes to third-party libs inside the HA docker container.
# Called automatically on every proxy deploy (gateway restart triggers EOF → HA
# needs patched nmea2000 to reconnect cleanly instead of spinning at 100% CPU).
# Also callable standalone: ./deploy.sh [host] --patch-ha
#
# Patches applied:
#   patches/nmea2000_ioclient.py → nmea2000/ioclient.py
#     Fix: TextNmea2000Gateway readline() EOF → ConnectionError (not silent return)
#     Upstream PR: github.com/dnevera/nmea2000/tree/fix/text-gateway-eof-spin-loop
#
# ── sync_python_deps() ───────────────────────────────────────────────────────
# Ensures the remote host's Python environment matches requirements.txt BEFORE
# proxy/web services are (re)started. Runs on every full/--proxy/--web deploy.
#
# SKILL: Adding/upgrading a Python dependency
#   1. Edit requirements.txt (pin exact, known-good release — see the big
#      warning comment above the `nmea2000` line before ever pointing it back
#      at a git+ branch)
#   2. Test: ./deploy.sh --proxy --no-test   (watch the "Python dependencies" section)
#
# Uses --user --break-system-packages because the target Pi runs Debian's
# externally-managed-environment Python (PEP 668) — plain `pip install` is
# refused otherwise. Discovered the hard way while debugging the 100% CPU bug.
#
# NOTE: this only installs whatever requirements.txt says (currently a pinned
# PyPI nmea2000 release). It intentionally does NOT try to auto-detect/auto-fix
# "drift" by force-reinstalling arbitrary specs — an earlier version of this
# function did that (force-reinstalling the git+ fork branch whenever a fix
# marker was missing) and that fork branch turned out to be incompatible with
# the nmea2000 release actually running here (`NameError: NMEA2000Field`),
# which took production down. requirements.txt now pins the exact working
# release instead; see patch_gateway_nmea2000() for the one local patch that
# IS still needed on top of it.
sync_python_deps() {
    section "Python dependencies (requirements.txt)"

    log "Uploading requirements.txt..."
    ${SCP} "${LOCAL_DIR}/requirements.txt" "${HOST}:/tmp/requirements.txt"

    log "Ensuring dependencies from requirements.txt are installed (pip3 --user)..."
    ${SSH} ${HOST} "pip3 install --user --break-system-packages --quiet -r /tmp/requirements.txt" \
        && log "Dependencies: up to date ✓" \
        || warn "pip3 install -r requirements.txt reported errors — review output above"
}

# ── patch_gateway_nmea2000() ─────────────────────────────────────────────────
# Applies patches/nmea2000_ioclient.py (EOF spin-loop fix) directly to the
# gateway's OWN nmea2000 install (its --user site-packages), separate from
# patch_ha() which patches the copy running inside the HA docker container.
# Without this, every `pip install`/reinstall of the pinned nmea2000 release
# in sync_python_deps() would silently drop the fix and reintroduce the
# 100% CPU EOF spin-loop bug on the gateway side.
# Idempotent — compares MD5 checksums before overwriting, same pattern as Patch 1
# in patch_ha().
patch_gateway_nmea2000() {
    section "Gateway nmea2000 patch (ioclient EOF fix)"

    local ioclient_path
    ioclient_path=$(${SSH} ${HOST} \
        "python3 -c 'import nmea2000.ioclient as m; print(m.__file__)'" 2>/dev/null) \
        || { warn "Cannot find nmea2000.ioclient on gateway host — skipping"; return 0; }

    if [[ -z "$ioclient_path" ]]; then
        warn "nmea2000.ioclient path empty — skipping"
        return 0
    fi

    local local_md5 remote_md5
    local_md5=$(md5 -q "${PATCH_DIR}/nmea2000_ioclient.py" 2>/dev/null \
                || md5sum "${PATCH_DIR}/nmea2000_ioclient.py" | cut -d' ' -f1)
    remote_md5=$(${SSH} ${HOST} "md5sum '${ioclient_path}' 2>/dev/null | cut -d' ' -f1") || remote_md5=""

    if [[ "$local_md5" == "$remote_md5" ]]; then
        log "Gateway ioclient EOF fix: already up to date — skipping"
    else
        log "Gateway ioclient EOF fix: applying → ${ioclient_path}"
        ${SCP} "${PATCH_DIR}/nmea2000_ioclient.py" "${HOST}:/tmp/nmea2000_ioclient_gw.py"
        ${SSH} ${HOST} "cp /tmp/nmea2000_ioclient_gw.py '${ioclient_path}' \
            && rm -rf \"\$(dirname '${ioclient_path}')/__pycache__\""
        log "Gateway ioclient EOF fix: applied ✓"
    fi
}


patch_ha() {
    section "HA patches"
    local ha_changed=false  # track if any patch was actually applied → need HA restart

    # ── Patch 1: nmea2000 ioclient EOF spin-loop fix ──────────────────────────
    # Discover exact path inside container (survives Python version bumps).
    # Same dynamic discovery pattern used for Patch 2 below.
    local ioclient_path
    ioclient_path=$(${SSH} ${HOST} \
        "sudo docker exec ${HA_CONTAINER} python3 -c \
        'import nmea2000.ioclient as m; print(m.__file__)'" 2>/dev/null) \
        || { warn "Cannot find nmea2000.ioclient in HA container — skipping Patch 1"; ioclient_path=""; }

    if [[ -n "$ioclient_path" ]]; then
        # IDEMPOTENCY: compare MD5 checksums before overwriting
        local local_md5 remote_md5
        local_md5=$(md5 -q "${PATCH_DIR}/nmea2000_ioclient.py" 2>/dev/null \
                    || md5sum "${PATCH_DIR}/nmea2000_ioclient.py" | cut -d' ' -f1)
        remote_md5=$(${SSH} ${HOST} \
            "sudo docker exec ${HA_CONTAINER} md5sum '${ioclient_path}' 2>/dev/null \
             | cut -d' ' -f1") || remote_md5=""

        if [[ "$local_md5" == "$remote_md5" ]]; then
            log "Patch 1 (ioclient EOF fix): already up to date — skipping"
        else
            log "Patch 1 (ioclient EOF fix): applying → ${ioclient_path}"
            ${SCP} "${PATCH_DIR}/nmea2000_ioclient.py" "${HOST}:/tmp/nmea2000_ioclient.py"
            ${SSH} ${HOST} "sudo docker cp /tmp/nmea2000_ioclient.py \
                ${HA_CONTAINER}:${ioclient_path}"
            log "Patch 1 (ioclient EOF fix): applied ✓"
            ha_changed=true
        fi
    fi

    # ── Patch 2: nmea2000 message.py PGN 126996 hash collision fix ────────────
    # Script is idempotent — checks for marker "yacht-n2k-console-patch-v1" before applying.
    # Reports "Already applied" if marker found, "SUCCESS" if newly applied.
    # Dynamic path discovery is done inside the script itself (survives Python version bumps).
    log "Patch 2 (message.py hash collision fix): checking..."
    ${SCP} "${SCRIPT_DIR}/scripts/patch_ha_nmea2000_message.py" \
        "${HOST}:/tmp/patch_ha_nmea2000_message.py"
    ${SSH} ${HOST} "sudo docker cp /tmp/patch_ha_nmea2000_message.py \
        ${HA_CONTAINER}:/tmp/patch_ha_nmea2000_message.py"

    local patch2_out
    patch2_out=$(${SSH} ${HOST} \
        "sudo docker exec ${HA_CONTAINER} python3 /tmp/patch_ha_nmea2000_message.py")
    log "Patch 2: ${patch2_out}"
    if echo "$patch2_out" | grep -q "SUCCESS"; then
        ha_changed=true
    fi

    # ── Restart HA only if something actually changed ─────────────────────────
    if $ha_changed; then
        log "Patches changed — restarting HA to reload modules..."
        ${SSH} ${HOST} "sudo docker restart ${HA_CONTAINER}"
        log "HA restarted ✓  (auto-reconnects to :4001 within ~10s)"
    else
        log "All patches already up to date — HA restart skipped ✓"
    fi
}


${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}"

if $DEPLOY_PROXY || $DEPLOY_WEB; then
    sync_python_deps
    patch_gateway_nmea2000
fi

# ── N2K Proxy ────────────────────────────────────────────────────────────────

if $DEPLOY_PROXY; then
    section "ydnu02-tcp-gateway"
    log "Uploading ydnu02_tcp_gateway.py + ydnu02_gateway_device.py to ${HOST}:${REMOTE_DIR}"
    # Clean up stale legacy root file and test file that conflicts with new split test suite
    ${SSH} ${HOST} "rm -f ${REMOTE_DIR}/ydnu02_tcp_gateway.py ${REMOTE_DIR}/tests/test_ydnu02_tcp_gateway.py"

    # Ensure remote package directories exist
    ${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/ydnu02_tcp_gateway ${REMOTE_DIR}/ydnu02 ${REMOTE_DIR}/device_manager"

    # Upload gateway package files (rsync: only changed files transferred)
    ${SCP} "${LOCAL_DIR}/VERSION"                  "${HOST}:${REMOTE_DIR}/VERSION"
    ${RSYNC} "${LOCAL_DIR}/ydnu02_tcp_gateway/"    "${HOST}:${REMOTE_DIR}/ydnu02_tcp_gateway/"
    ${RSYNC} "${LOCAL_DIR}/ydnu02/"               "${HOST}:${REMOTE_DIR}/ydnu02/"
    ${RSYNC} "${LOCAL_DIR}/device_manager/"       "${HOST}:${REMOTE_DIR}/device_manager/"
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

    if $RESTART_HA; then
        patch_ha
    else
        log "Skipping Home Assistant restart (pass --restart-ha to restart HA container)"
    fi
fi

# ── Standalone --patch-ha ────────────────────────────────────────────────────
if [[ "$MODE" == "--patch-ha" ]]; then
    patch_ha
fi

# ── --clean-ha: remove garbage NMEA 2000 devices from HA registry ────────────

clean_ha() {
    section "HA NMEA 2000 device cleanup"
    local script="${LOCAL_DIR}/homeassistant/cleanup_nmea_devices.py"
    log "Copying cleanup script into HA container..."
    ${SCP} "${script}" "${HOST}:/tmp/cleanup_nmea_devices.py"
    ${SSH} ${HOST} "sudo docker cp /tmp/cleanup_nmea_devices.py ${HA_CONTAINER}:/tmp/cleanup_nmea_devices.py"
    log "Running cleanup (--all: remove ALL nmea2000 devices)..."
    ${SSH} ${HOST} "sudo docker exec ${HA_CONTAINER} python3 /tmp/cleanup_nmea_devices.py --all"
    log "Restarting HA..."
    ${SSH} ${HOST} "sudo docker restart ${HA_CONTAINER}"
    log "HA restarted ✓  devices will rebuild from live N2K data"
}

if $CLEAN_HA; then
    clean_ha
fi

# ── Web Service ───────────────────────────────────────────────────────────────

if $DEPLOY_WEB; then
    section "ydnu02-web"
    log "Uploading files to ${HOST}:${REMOTE_DIR}"
    ${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}/static/css ${REMOTE_DIR}/static/js \
      ${REMOTE_DIR}/static/tabs ${REMOTE_DIR}/tests ${REMOTE_DIR}/tests/specs \
      ${REMOTE_DIR}/sensors ${REMOTE_DIR}/routes"

    # Core Python modules (rsync batch: one SSH connection for all root files)
    ${RSYNC} \
        "${LOCAL_DIR}/VERSION" \
        "${LOCAL_DIR}/ydnu02.py" \
        "${LOCAL_DIR}/app.py" \
        "${LOCAL_DIR}/device_manager.py" \
        "${LOCAL_DIR}/models.py" \
        "${LOCAL_DIR}/gobius_parsers.py" \
        "${LOCAL_DIR}/mopeka_parsers.py" \
        "${LOCAL_DIR}/mopeka_scanner.py" \
        "${LOCAL_DIR}/ble_registry.py" \
        "${LOCAL_DIR}/gobius_ble_poller.py" \
        "${LOCAL_DIR}/n2k_command_builder.py" \
        "${LOCAL_DIR}/n2k_meta.py" \
        "${HOST}:${REMOTE_DIR}/"

    # Sub-packages and assets (rsync: only changed files per directory)
    ${RSYNC} "${LOCAL_DIR}/sensors/"  "${HOST}:${REMOTE_DIR}/sensors/"
    ${RSYNC} "${LOCAL_DIR}/routes/"   "${HOST}:${REMOTE_DIR}/routes/"
    ${RSYNC} "${LOCAL_DIR}/static/"   "${HOST}:${REMOTE_DIR}/static/"
    ${RSYNC} "${LOCAL_DIR}/tests/"    "${HOST}:${REMOTE_DIR}/tests/"

    # Service unit (goes via /tmp → sudo cp — keep scp for this one)
    ${SCP} "${LOCAL_DIR}/ydnu02-web.service" "${HOST}:${REMOTE_DIR}/ydnu02-web.service"

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

# ── Post-deploy tests ────────────────────────────────────────────────────────
# SKILL: Test suite mapping by deploy mode
#   Each deploy mode has a corresponding test set. Tests run on the REMOTE Pi
#   via SSH + pytest. Tests are already copied to REMOTE_DIR/tests/ by deploy.
#
#   Mode        → Test files
#   --proxy     → tests/test_frame_utils.py, test_data_hub.py, test_bidirectional_hub.py,
#                 test_gateway_device.py, test_integration.py, test_device_contract.py, test_service_mode.py
#   --web       → test_api.py, test_sensors_service.py, test_ble_api.py,
#                  test_ble_registry.py, test_gobius_parsers.py,
#                  test_mopeka_parsers.py, test_n2k_commands.py,
#                  test_gobius_ble_writes.py
#   --patch-ha  → (no tests, manual verification only)
#   --clean-ha  → (no tests, manual verification only)
#   (full)      → ALL test files
#
# SKILL: Adding tests for a new component
#   1. Create test file in tests/
#   2. Add the filename to the appropriate TESTS_* array below
#   3. If it's a new deploy mode, add a new TESTS_ array and elif block
#
# SKIP: pass --no-test as last arg to skip post-deploy tests
# ──────────────────────────────────────────────────────────────────────────────

# Test sets keyed by deploy mode
TESTS_PROXY=(
    "tests/test_frame_utils.py"
    "tests/test_data_hub.py"
    "tests/test_bidirectional_hub.py"
    "tests/test_gateway_device.py"
    "tests/test_integration.py"
    "tests/test_device_contract.py"
    "tests/test_service_mode.py"
    "tests/test_live_ha_integration.py"
)

TESTS_WEB=(
    "tests/test_sensors_service.py"
    "tests/test_ble_api.py"
    "tests/test_ble_registry.py"
    "tests/test_gobius_parsers.py"
    "tests/test_mopeka_parsers.py"
    "tests/test_n2k_commands.py"
    "tests/test_gobius_ble_writes.py"
)

TESTS_ALL=( "${TESTS_PROXY[@]}" "${TESTS_WEB[@]}" )

run_post_deploy_tests() {
    local -a test_files=("$@")
    if [[ ${#test_files[@]} -eq 0 ]]; then
        log "No tests for this deploy mode — skipping"
        return 0
    fi

    section "Post-deploy tests (${#test_files[@]} suites)"

    # Use unittest (built-in, no pip install needed).
    # Convert file paths to module names: tests/test_foo.py → tests.test_foo
    local test_modules=""
    for t in "${test_files[@]}"; do
        # tests/test_foo.py → tests.test_foo
        mod="${t%.py}"         # strip .py
        mod="${mod//\//.}"     # / → .
        test_modules+=" ${mod}"
    done

    log "Running: python3 -m unittest -v${test_modules}"
    if ${SSH} ${HOST} "cd ${REMOTE_DIR} && python3 -m unittest -v ${test_modules} 2>&1"; then
        log "Tests: ALL PASSED ✓"
    else
        warn "Tests: SOME FAILED ✗ (see output above)"
        warn "Review failures before considering this deploy stable"
    fi
}

# Check for --no-test flag (can be passed as additional arg)
SKIP_TESTS=false
for arg in "$@"; do
    [[ "$arg" == "--no-test" ]] && SKIP_TESTS=true
done

if ! $SKIP_TESTS; then
    if [[ "$MODE" == "--proxy" ]]; then
        run_post_deploy_tests "${TESTS_PROXY[@]}"
    elif [[ "$MODE" == "--web" ]]; then
        run_post_deploy_tests "${TESTS_WEB[@]}"
    elif [[ "$MODE" == "--patch-ha" ]] || [[ "$MODE" == "--clean-ha" ]]; then
        log "No automated tests for ${MODE} — verify manually"
    else
        # Full deploy — run all tests
        run_post_deploy_tests "${TESTS_ALL[@]}"
    fi
else
    warn "Tests skipped (--no-test)"
fi

log "Deploy done → http://${HOST#*@}:${WEB_PORT}"

