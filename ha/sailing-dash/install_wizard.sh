#!/usr/bin/env bash
# install_wizard.sh — guided install / re-install of the Sailing dashboard onto
# ANY target profile declared in ha/sailing-dash/.env (see .env.template).
#
# WHY THIS EXISTS
#   A from-scratch install is NOT one script: sensor auto-discovery reads an
#   entity registry that only exists after a few steps a human has to do in the
#   Home Assistant UI (HACS activation via GitHub device-flow, the NMEA 2000
#   config entry pointing at the YDNU-02 tcp-gw, real traffic on the bus).
#
#   So this is a state machine with two BLOCKING gates, not a linear script:
#     GATE A - HACS: the files are delivered automatically, activating it in the
#              UI is manual; verified with `stage_provisioner.py check-hacs`.
#     GATE B - NMEA 2000 integration + config entry on the tcp-gw + raw entities
#              in the registry; verified with `deploy.sh --preflight`.
#   A gate prints its checklist, waits for Enter, then RUNS THE CHECK. On failure
#   it prints why and waits again — it never "warns and carries on", and it is
#   the same for every profile (stage is not treated as advisory).
#
# USAGE
#   ./install_wizard.sh                          # profile "stage", asks before each step
#   ./install_wizard.sh --target prod            # any profile from .env
#   ./install_wizard.sh --target prod --reinstall# wipe/clean-install where allowed
#   ./install_wizard.sh --config                 # interactively configure cards & time windows
#   ./install_wizard.sh --from 5                 # resume from a step (see --list)
#   ./install_wizard.sh --only 6                 # run exactly one step
#   ./install_wizard.sh --list                   # print the step list and exit
#   ./install_wizard.sh --yes                    # never ask, but the gates still block
#   ./install_wizard.sh --dry-run                # print what would run
#
# The wizard only orchestrates the existing entry points — build.py,
# fetch_deps.py, run_stage.sh, deploy.sh, stage_provisioner.py — it contains no
# deploy logic of its own.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# shellcheck source=lib/env_profile.sh
source "${SCRIPT_DIR}/helpers/lib/env_profile.sh"

PROFILE="stage"
REINSTALL=0
DO_CONFIG=0
ASSUME_YES=0
DRY_RUN=0
FROM_STEP=1
ONLY_STEP=""
LIST_ONLY=0
NMEA_MODE=""          # stage only: --demo | --live

while [[ $# -gt 0 ]]; do
    arg="$1"
    case "${arg}" in
        --target)    PROFILE="${2:?--target needs a profile name}"; shift ;;
        --target=*)  PROFILE="${arg#*=}" ;;
        --stage)     PROFILE="stage" ;;
        --prod)      PROFILE="prod" ;;
        --reinstall|--clean-install) REINSTALL=1 ;;
        --config)    DO_CONFIG=1 ;;
        --from)      FROM_STEP="${2:?--from needs a step number}"; shift ;;
        --from=*)    FROM_STEP="${arg#*=}" ;;
        --only)      ONLY_STEP="${2:?--only needs a step number}"; shift ;;
        --only=*)    ONLY_STEP="${arg#*=}" ;;
        --demo)      NMEA_MODE="--demo" ;;
        --live)      NMEA_MODE="--live" ;;
        --yes|-y)    ASSUME_YES=1 ;;
        --dry-run)   DRY_RUN=1 ;;
        --list)      LIST_ONLY=1 ;;
        -h|--help)
            sed -n '2,/^set -uo pipefail/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: ${arg} (try --help)" >&2
            exit 2
            ;;
    esac
    shift
done

# ── Output helpers ───────────────────────────────────────────────────────────
C_HEAD="\033[1;96m"; C_OK="\033[92m"; C_WARN="\033[93m"; C_ERR="\033[91m"
C_MAN="\033[1;95m"; C_DIM="\033[90m"; C_OFF="\033[0m"

hdr()   { printf "\n${C_HEAD}== %s ==${C_OFF}\n" "$*"; }
ok()    { printf "${C_OK}[OK]${C_OFF} %s\n" "$*"; }
info()  { printf "${C_DIM}%s${C_OFF}\n" "$*"; }
warn()  { printf "${C_WARN}[WARN]${C_OFF} %s\n" "$*" >&2; }
fail()  { printf "${C_ERR}[ERROR]${C_OFF} %s\n" "$*" >&2; }

die() {
    fail "$1"
    printf "${C_DIM}Fix it and resume with:  ./install_wizard.sh --target %s --from %s${C_OFF}\n" \
        "${PROFILE}" "${CURRENT_STEP}" >&2
    exit 1
}

run() {
    printf "${C_DIM}\$ %s${C_OFF}\n" "$*"
    if [[ "${DRY_RUN}" == "1" ]]; then
        return 0
    fi
    "$@"
}

confirm() {
    # confirm <question>  → 0 = yes
    if [[ "${ASSUME_YES}" == "1" || "${DRY_RUN}" == "1" ]]; then
        return 0
    fi
    local reply=""
    printf "%s [Y/n/q] " "$1"
    read -r reply </dev/tty || reply="q"
    case "${reply}" in
        ""|y|Y|yes|д|да) return 0 ;;
        q|Q|quit)
            info "Stopped. Resume later with: ./install_wizard.sh --target ${PROFILE} --from ${CURRENT_STEP}"
            exit 0
            ;;
        *) return 1 ;;
    esac
}

wait_enter() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        return 0
    fi
    printf "\n${C_MAN}Press Enter when the checklist above is done (Ctrl+C to abort) ...${C_OFF}"
    read -r _ </dev/tty || true
    printf "\n"
}

# ── Steps ────────────────────────────────────────────────────────────────────
STEP_NAMES=(
    "Environment profile (.env) and prerequisites"
    "Wipe the target (re-install only)"
    "Build artifacts and fetch pinned dependencies"
    "Bring up Home Assistant and deliver HACS + the pinned integration"
    "GATE A - MANUAL: activate HACS in the UI (verified, blocking)"
    "GATE B - MANUAL: NMEA 2000 integration, tcp-gw config entry, bus traffic (verified, blocking)"
    "Deploy sensors, cards and dashboard"
    "Verify"
)
TOTAL_STEPS="${#STEP_NAMES[@]}"
CURRENT_STEP=1

print_steps() {
    local i=1
    for name in "${STEP_NAMES[@]}"; do
        printf "  %s. %s\n" "${i}" "${name}"
        i=$((i + 1))
    done
}

if [[ "${LIST_ONLY}" == "1" ]]; then
    hdr "Wizard steps"
    print_steps
    exit 0
fi

should_run() {
    local n="$1"
    if [[ -n "${ONLY_STEP}" ]]; then
        [[ "${n}" == "${ONLY_STEP}" ]] && return 0 || return 1
    fi
    [[ "${n}" -ge "${FROM_STEP}" ]]
}

begin_step() {
    CURRENT_STEP="$1"
    hdr "Step $1/${TOTAL_STEPS}: ${STEP_NAMES[$(($1 - 1))]}"
}

# ── Resolve the profile up front: everything else depends on it ──────────────
if ! env_profile_load "${PROFILE}"; then
    exit 1
fi

IS_STAGE=0
case "${PROFILE}" in stage|stage-*|stage_*) IS_STAGE=1 ;; esac
IS_LOCAL=0
[[ "${HA_TRANSPORT}" == "local-docker" ]] && IS_LOCAL=1

DASH_URL="${HA_URL:-http://localhost:8123}"
DASH_URL="${DASH_URL%/}/dashboard-sailing/"

hdr "Sailing dashboard install wizard"
cat <<EOF
  profile     : ${PROFILE}$( [[ "${IS_STAGE}" == "1" ]] && echo "  (stage: provisioning shortcuts allowed)" || echo "  (real target: no shortcuts)" )
  transport   : ${HA_TRANSPORT}${HA_SSH_HOST:+  via ${HA_SSH_HOST}}
  container   : ${HA_CONTAINER}
  config dir  : ${HA_CONFIG_DIR}
  HA url      : ${HA_URL:-<unset>}
  YDNU-02 gw  : ${HA_GW_HOST}:${HA_GW_DATA_PORT}  (gateway type: text)
  mode        : $( [[ "${REINSTALL}" == "1" ]] && echo "RE-INSTALL (wipes state)" || echo "install / update (keeps state)" )
EOF
info "Steps:"
print_steps
confirm "Proceed with this profile?" || { info "Nothing done. Edit .env or pass --target <profile>."; exit 0; }

# ── Step 1: profile + prerequisites ──────────────────────────────────────────
if should_run 1; then
    begin_step 1
    if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
        warn ".env not found — built-in defaults are used for 'stage'/'prod' only."
        if confirm "Create .env from .env.template now?"; then
            run cp "${SCRIPT_DIR}/.env.template" "${SCRIPT_DIR}/.env" || die "could not create .env"
            ok ".env created — review it and re-run the wizard."
            exit 0
        fi
    fi

    command -v python3 >/dev/null 2>&1 || die "python3 not found on this machine"
    python3 -c "import yaml" 2>/dev/null || die "PyYAML missing: pip install -r requirements-ha.txt"
    run python3 "${SCRIPT_DIR}/helpers/env_profile.py" "${PROFILE}" || die "profile '${PROFILE}' cannot be resolved"

    if [[ "${IS_LOCAL}" == "1" ]]; then
        command -v docker >/dev/null 2>&1 || die "docker not found (needed for a local-docker profile)"
        docker info >/dev/null 2>&1 || die "Docker daemon is not running — start Docker Desktop"
    else
        [[ -n "${HA_SSH_HOST}" ]] || die "profile '${PROFILE}' has no SSH host — set ${PROFILE}_SSH_HOST in .env"
        if [[ "${DRY_RUN}" != "1" ]]; then
            ssh -o ConnectTimeout=8 -o BatchMode=yes "${HA_SSH_HOST}" "docker --version" >/dev/null 2>&1 \
                || die "cannot reach ${HA_SSH_HOST} over SSH (key auth) or docker is missing there"
        fi
    fi
    ok "Prerequisites satisfied."
fi

# ── Step 2: wipe (re-install only) ───────────────────────────────────────────
if should_run 2; then
    begin_step 2
    if [[ "${REINSTALL}" != "1" ]]; then
        info "Skipped (pass --reinstall to wipe the target)."
    elif [[ "${IS_STAGE}" == "1" && "${IS_LOCAL}" == "1" ]]; then
        warn "This deletes local-ha/config (whole HA state incl. .storage) and build/."
        if confirm "Wipe the local stage instance?"; then
            run docker compose -f "${SCRIPT_DIR}/local-ha/docker-compose.yml" down -v
            run rm -rf "${SCRIPT_DIR}/local-ha/config" "${SCRIPT_DIR}/build"
            ok "Stage wiped."
        else
            info "Wipe skipped."
        fi
    else
        warn "Refusing to wipe a non-local / non-stage target ('${PROFILE}')."
        info  "On a real vessel server the HA state is deleted by hand, deliberately."
        info  "The wizard will instead re-run provisioning/deploy on top of the existing state."
    fi
fi

# ── Step 3: build + deps ─────────────────────────────────────────────────────
if should_run 3; then
    begin_step 3
    if [[ "${DO_CONFIG}" == "1" ]]; then
        info "Running dashboard configuration wizard..."
        CONF_ARGS=()
        [[ "${ASSUME_YES}" == "1" ]] && CONF_ARGS+=("--non-interactive")
        run python3 "${SCRIPT_DIR}/helpers/configure.py" "${CONF_ARGS[@]}" || die "configure.py failed"
    fi
    run python3 "${SCRIPT_DIR}/helpers/build.py" || die "build.py failed"
    run python3 "${SCRIPT_DIR}/helpers/fetch_deps.py" || \
        die "fetch_deps.py failed — every artifact is pinned by tag in deps.yaml and needs GitHub reachable"
    ok "build/ and build/deps/ are ready."
fi

# ── Step 4: bring the instance up ────────────────────────────────────────────
if should_run 4; then
    begin_step 4
    if [[ "${IS_STAGE}" == "1" && "${IS_LOCAL}" == "1" ]]; then
        STAGE_ARGS=("--no-watch" "--target" "${PROFILE}")
        [[ -n "${NMEA_MODE}" ]] && STAGE_ARGS+=("${NMEA_MODE}")
        [[ "${REINSTALL}" == "1" ]] && STAGE_ARGS+=("--clean-install")
        STAGE_ARGS+=("--provision-only")
        info "run_stage.sh starts the container and provisions HA (onboarding bypass, test/test,"
        info "HACS FILES, the nmea2000 integration from its tag, the config entry on ${HA_GW_HOST}:${HA_GW_DATA_PORT})."
        info "It deliberately stops BEFORE deploying: the gates below must pass first."
        info "Default NMEA source is the mock emulator (--demo); pass --live for a real gw."
        run "${SCRIPT_DIR}/run_stage.sh" "${STAGE_ARGS[@]}" || die "run_stage.sh failed"
    else
        info "Bootstrap pushes HACS, the card bundles and the pinned integration from build/deps/"
        info "to the target — the same automated delivery stage uses, so the two never diverge."
        run "${SCRIPT_DIR}/deploy.sh" --target "${PROFILE}" --bootstrap || die "deploy.sh --bootstrap failed"
    fi
    ok "Instance is up; HACS files and the integration are in place."
fi

# ── Step 5: GATE A — HACS activation (blocking) ───────────────────────────────
# The files are already on the target (step 4). What is left cannot be automated:
# adding the HACS integration in the UI and the GitHub device-flow login. So the
# wizard STOPS here and refuses to move on until check-hacs actually passes —
# identically for every profile, stage included (no "advisory" mode).
if should_run 5; then
    begin_step 5
    while :; do
        printf "${C_MAN}MANUAL — HACS setup in the Home Assistant UI (cannot be automated):${C_OFF}\n\n"
        if [[ "${IS_STAGE}" == "1" ]]; then
            cat <<EOF
  0. Open ${HA_URL:-http://localhost:8123} and log in with  test / test
     → confirms onboarding really was bypassed.
  1. The HACS FILES are already installed by the script
     (/config/custom_components/hacs/ — delivered from the pin in deps.yaml).
  2. ACTIVATION is manual, exactly like on a real server:
       restart HA → Settings → Devices & services → Add integration → HACS
       → authorize at https://github.com/login/device
EOF
        else
            cat <<EOF
  0. Open ${HA_URL:-http://<host>:8123} and finish the REAL onboarding: owner
     account, home name, coordinates, units, time zone. (No test/test here.)
  1. The HACS FILES are already installed by the script
     (/config/custom_components/hacs/ — delivered from the pin in deps.yaml).
     ('wget -O - https://get.hacs.xyz | bash -' is only an alternative way to
      deliver the same files; it is not required.)
  2. ACTIVATION is manual and CANNOT be automated:
       restart HA → Settings → Devices & services → Add integration → HACS
       → authorize at https://github.com/login/device
EOF
        fi
        wait_enter
        if run python3 "${SCRIPT_DIR}/helpers/stage_provisioner.py" check-hacs \
                --target "${PROFILE}" --container "${HA_CONTAINER}"; then
            ok "GATE A passed: HACS is delivered AND activated."
            break
        fi
        fail "GATE A not passed — see the reason above (not delivered vs not activated)."
        if ! confirm "Retry the HACS gate?"; then
            die "aborted at GATE A (HACS)"
        fi
    done
fi

# ── Step 6: GATE B — integration, config entry, bus traffic (blocking) ────────
# Auto-discovery reads an entity registry that only exists once the integration
# is loaded, the config entry points at the tcp-gw and the bus actually carries
# traffic. deploy.sh --preflight verifies all of it; the gate is the same for
# every profile, so nothing gets deployed onto an empty registry.
if should_run 6; then
    begin_step 6
    while :; do
        printf "${C_MAN}MANUAL — NMEA 2000 setup (entity IDs only appear after real traffic):${C_OFF}\n\n"
        if [[ "${IS_STAGE}" == "1" ]]; then
            cat <<EOF
  1. Settings → Devices & services → NMEA 2000: the entry must point at
     ${HA_GW_HOST}:${HA_GW_DATA_PORT}, gateway type "text".
     (The script provisions it; if it is missing, re-run step 4.)
  2. Make sure telemetry flows — the mock emulator (--demo) or a real YDNU-02
     (--live) — so raw nmea2000.* entities get created.
EOF
        else
            cat <<EOF
  1. HACS → Integrations → ⋮ Custom repositories → add
       dnevera/ha-nmea2000   (category: Integration)
     and install the NMEA 2000 integration at tag  ydnu-02-usb-tcp-gw .
     (The files are already delivered from the pin; use HACS if you prefer it
      to own the updates.)
  2. HACS → Frontend: install card-mod, compass-card, apexcharts-card,
     windrose-card, plotly-graph-card, config-template-card.
  3. Restart Home Assistant (custom components are not picked up live).
  4. Settings → Devices & services → Add integration → NMEA 2000:
       Host = ${HA_GW_HOST}
       Port = ${HA_GW_DATA_PORT}
       Gateway type = text
  5. Restart HA once more and wait until raw nmea2000.* entities show up —
     this REQUIRES traffic on the bus (two-phase announce, SA 64/200).
EOF
        fi
        wait_enter
        if run "${SCRIPT_DIR}/deploy.sh" --target "${PROFILE}" --preflight; then
            ok "GATE B passed: the target is ready for a dashboard deploy."
            break
        fi
        fail "GATE B not passed — the checklist above lists exactly what is missing."
        if ! confirm "Retry the preflight gate?"; then
            die "aborted at GATE B (preflight)"
        fi
    done
fi

# ── Step 7: deploy ───────────────────────────────────────────────────────────
if should_run 7; then
    begin_step 7
    DEPLOY_MODE="--update"
    [[ "${REINSTALL}" == "1" ]] && DEPLOY_MODE="--install"
    info "This runs auto-discovery (map_nmea_sensors.py), merges the sensors into"
    info "configuration.yaml, merges lovelace_resources, writes the dashboard and restarts HA."
    run "${SCRIPT_DIR}/deploy.sh" --target "${PROFILE}" "${DEPLOY_MODE}" || die "deploy.sh ${DEPLOY_MODE} failed"
    ok "Deployed."
fi

# ── Step 8: verify ───────────────────────────────────────────────────────────
if should_run 8; then
    begin_step 8
    run python3 "${SCRIPT_DIR}/helpers/stage_provisioner.py" inspect --target "${PROFILE}" --container "${HA_CONTAINER}" || \
        warn "inspect reported issues — see the list above"
    run python3 "${SCRIPT_DIR}/helpers/stage_provisioner.py" verify --target "${PROFILE}" --timeout 60 || \
        die "the dashboard did not answer HTTP 200 — check: docker logs --tail 50 ${HA_CONTAINER}"
    ok "Dashboard is live: ${DASH_URL}"
fi

hdr "Wizard finished (profile: ${PROFILE})"
cat <<EOF
  dashboard : ${DASH_URL}
  re-deploy : ./deploy.sh --target ${PROFILE} --update
  rollback  : ./deploy.sh --target ${PROFILE} --rollback
  details   : INSTALLATION.md, HACS_SETUP.md
EOF
