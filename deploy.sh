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
#   ./deploy.sh                     — deploy both services (host from deploy.conf)
#   ./deploy.sh --proxy             — gateway only (no web restart)
#   ./deploy.sh --web               — web only (gateway/HA untouched)
#   ./deploy.sh --check-ha          — verify the nmea2000 fork inside the HA container
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
#     --check-ha / --clean-ha → manual verification only
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
# NMEA2000 LIBRARY — OUR FORK, NO PATCHES
#
#   There are no patch files any more. Both fixes we need live in the tag
#   dnevera/nmea2000@cpu-overload-fix (commit 6c9df918d19a) and the library is
#   installed from that tag everywhere:
#     - this host / our venv+Docker → requirements.txt (`nmea2000 @ git+...@tag`)
#     - inside the HA container      → manifest.json of our ha-nmea2000 fork
#   The tag is declared once, in ha/sailing-dash/deps.yaml.
#
#   Fix 1 (ioclient.py): TextNmea2000Gateway readline() EOF → ConnectionError
#     instead of a silent return that span the event loop at 100% CPU.
#   Fix 2 (message.py): primary_key includes the source unique_number, so PGN
#     126996 no longer collides and every device gets its own HA entities.
#
#   verify_nmea2000_fork() is a DRIFT GUARD, not a patcher: it checks that the
#   installed library actually contains both markers (i.e. it is the fork, not a
#   PyPI release) and reports loudly if it is not. Run ./deploy.sh --check-ha
#   after a Home Assistant image update.
#
# SERVICE START ORDER
#   ydnu02-tcp-gateway  →  ydnu02-web  →  homeassistant (docker)
#   ydnu02-web.service has Requires= + After= on ydnu02-tcp-gateway.service.
#
# SKILL: Changing the nmea2000 library version
#   1. Move the tag in the fork github.com/dnevera/nmea2000
#   2. Update `ref:` in ha/sailing-dash/deps.yaml and the pin in requirements.txt
#   3. Test: ./deploy.sh --check-ha
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
#   A drift guard additionally checks that the installed nmea2000 really is our
#   fork (pip alone can't detect that a PyPI release replaced it) by looking for
#   both fix markers in ioclient.py/message.py — see verify_nmea2000_fork().
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
MODE="${1:-}"   # --proxy | --web | --check-ha | --clean-ha | (empty = both)

# REMOTE_DIR, WEB_SERVICE, PROXY_SERVICE, HA_CONTAINER are from deploy.conf
LOCAL_DIR="$SCRIPT_DIR"

# The nmea2000 fork tag is declared once, in ha/sailing-dash/deps.yaml; these are
# the content markers proving the installed library really is that fork.
NMEA2000_FORK_SPEC="git+https://github.com/dnevera/nmea2000.git@cpu-overload-fix"
NMEA2000_MARKER_IOCLIENT="Connection closed by remote host"
NMEA2000_MARKER_MESSAGE="primary_key = f\"{self.id}_{source_id}\""

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
[[ "$MODE" == "--check-ha" ]] && DEPLOY_PROXY=false && DEPLOY_WEB=false
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
# NOTE: this installs exactly what requirements.txt says — including the
# nmea2000 library pinned to our fork's TAG (never a branch: a branch is a moving
# pointer and two installs on different days would give different code). Nothing
# is patched afterwards; verify_nmea2000_fork() only verifies the result.
sync_python_deps() {
    section "Python dependencies (requirements.txt)"

    log "Uploading requirements.txt..."
    ${SCP} "${LOCAL_DIR}/requirements.txt" "${HOST}:/tmp/requirements.txt"

    log "Ensuring dependencies from requirements.txt are installed (pip3 --user)..."
    ${SSH} ${HOST} "pip3 install --user --break-system-packages --quiet -r /tmp/requirements.txt" \
        && log "Dependencies: up to date ✓" \
        || warn "pip3 install -r requirements.txt reported errors — review output above"
}

# DRIFT GUARD (replaces the old patch_ha()/patch_gateway_nmea2000() pair).
#
# The library is installed from our fork's tag — on this host through
# requirements.txt, inside the HA container through the integration's
# manifest.json requirement. Neither pip nor HACS can tell us afterwards that a
# plain PyPI release replaced the fork, so we check the installed files for both
# fix markers instead.
#
#   verify_nmea2000_fork gateway   — the gateway host's own --user install
#   verify_nmea2000_fork ha        — the copy inside the HA docker container
verify_nmea2000_fork() {
    local scope="$1"
    local runner=""
    [[ "$scope" == "ha" ]] && runner="sudo docker exec ${HA_CONTAINER} "
    section "nmea2000 fork check (${scope})"

    local pkg_dir
    pkg_dir=$(${SSH} ${HOST} \
        "${runner}python3 -c 'import os, nmea2000; print(os.path.dirname(nmea2000.__file__))'" \
        2>/dev/null) || pkg_dir=""

    if [[ -z "$pkg_dir" ]]; then
        warn "nmea2000 is not importable (${scope}) — cannot verify the fork"
        return 0
    fi

    local missing=()
    ${SSH} ${HOST} "${runner}grep -qF '${NMEA2000_MARKER_IOCLIENT}' '${pkg_dir}/ioclient.py'" \
        >/dev/null 2>&1 || missing+=("ioclient.py (EOF spin-loop fix)")
    ${SSH} ${HOST} "${runner}grep -qF '${NMEA2000_MARKER_MESSAGE}' '${pkg_dir}/message.py'" \
        >/dev/null 2>&1 || missing+=("message.py (PGN 126996 per-source primary_key)")

    if [[ ${#missing[@]} -eq 0 ]]; then
        log "nmea2000 (${scope}): our fork is installed ✓  (${pkg_dir})"
        return 0
    fi

    warn "nmea2000 (${scope}) at ${pkg_dir} is NOT our fork — missing:"
    local item
    for item in "${missing[@]}"; do
        warn "  - ${item}"
    done
    if [[ "$scope" == "ha" ]]; then
        warn "Fix: reinstall the NMEA 2000 integration from our fork's tag through HACS"
        warn "     (dnevera/ha-nmea2000@ydnu-02-usb-tcp-gw), then restart Home Assistant."
        warn "     Its manifest.json must require ${NMEA2000_FORK_SPEC}"
    else
        warn "Fix: pip3 install --user --break-system-packages --force-reinstall \\"
        warn "       '${NMEA2000_FORK_SPEC}'"
    fi
    return 1
}


${SSH} ${HOST} "mkdir -p ${REMOTE_DIR}"

if $DEPLOY_PROXY || $DEPLOY_WEB; then
    sync_python_deps
    verify_nmea2000_fork gateway || true
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
        verify_nmea2000_fork ha || true
        log "Restarting Home Assistant to reconnect to :${DATA_PORT} ..."
        ${SSH} ${HOST} "sudo docker restart ${HA_CONTAINER}"
        log "HA restarted ✓  (auto-reconnects within ~10s)"
    else
        log "Skipping Home Assistant restart (pass --restart-ha to restart HA container)"
    fi
fi

# ── Standalone --check-ha: drift guard only, changes nothing ────────────────
if [[ "$MODE" == "--check-ha" ]]; then
    verify_nmea2000_fork ha
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
#   --check-ha  → (no tests, manual verification only)
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
    elif [[ "$MODE" == "--check-ha" ]] || [[ "$MODE" == "--clean-ha" ]]; then
        log "No automated tests for ${MODE} — verify manually"
    else
        # Full deploy — run all tests
        run_post_deploy_tests "${TESTS_ALL[@]}"
    fi
else
    warn "Tests skipped (--no-test)"
fi

log "Deploy done → http://${HOST#*@}:${WEB_PORT}"

