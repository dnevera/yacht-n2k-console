#!/usr/bin/env bash
# deploy.sh — single entry point to deploy EVERYTHING the Sailing dashboard
# needs onto a Home Assistant instance (Stage or Prod): manually-installed custom
# card JS resources, sensors-sailing.yaml (rest:/template: entries), and
# dashboard-sailing.yaml (the Lovelace dashboard itself).
#
# USAGE
#   ./deploy.sh --stage                 # deploy to the "stage" target profile
#   ./deploy.sh --prod  [user@host]     # deploy to the "prod" target profile
#   ./deploy.sh --target <profile>      # deploy to ANY profile from .env (e.g. stage-pi5)
#
#   Target profiles are declared in ha/sailing-dash/.env (see .env.template):
#   transport, ssh host, container, config dir, HA url/token and the YDNU-02
#   tcp-gw of that instance. --stage/--prod are aliases of --target stage/prod.
#
#   Sub-mode options (work for both --stage and --prod):
#   ./deploy.sh [--stage|--prod] --install
#   ./deploy.sh [--stage|--prod] --update
#   ./deploy.sh [--stage|--prod] --resources-only
#   ./deploy.sh [--stage|--prod] --dashboard-only
#   ./deploy.sh [--stage|--prod] --sensors-only
#
#   Delivery is idempotent: a file is uploaded only when its sha256 differs from
#   what the container already has, a whole directory only when its tree manifest
#   changed, and Home Assistant is restarted only when something was actually
#   delivered. Use --force to re-upload everything regardless.
#
#   Prod bring-up:
#   ./deploy.sh --prod --bootstrap    # push cards + integration from build/deps/
#   ./deploy.sh --prod --preflight    # only check whether the target is ready
#   ./deploy.sh --prod --rollback     # restore the last configuration/lovelace backup
#
# PREFLIGHT GATE
#   Sensor auto-discovery reads an entity registry that only exists AFTER the
#   manual steps (HACS device-flow, NMEA2000 config entry on the tcp-gw, real
#   traffic on the bus). --install/--update therefore refuse to run against an
#   unprepared instance and print exactly what is still missing instead of
#   silently deploying a dashboard bound to nothing. Use --skip-preflight to
#   override at your own risk.
#
# REQUIRES
#   - python3 + PyYAML on THIS machine
#   - ha/sailing-dash/.env for anything beyond the two default profiles
#   - Docker running locally (local-docker profiles) or SSH access (ssh-docker)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Everything that is not an entry point lives in helpers/ (build.py, fetch_deps.py,
# stage_provisioner.py, deploy_sensors.sh, deploy_dashboard.sh, lib/, ...).
HELPERS_DIR="${SCRIPT_DIR}/helpers"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CARDS_DIR="${SCRIPT_DIR}/build/cards"
DEPS_CARDS_DIR="${SCRIPT_DIR}/build/deps/cards"
RESOURCES_FILE="${SCRIPT_DIR}/build/lovelace-resources.yaml"

# shellcheck source=lib/ha_target.sh
source "${HELPERS_DIR}/lib/ha_target.sh"

# ── Parse flags ──────────────────────────────────────────────────────────────
TARGET_ENV="stage"
MODE="update"
HOST_ARG=""
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-0}"
# Re-upload everything even when the target already holds identical content.
HA_FORCE_DELIVERY="${HA_FORCE_DELIVERY:-0}"
BACKUP_KEEP=5

while [[ $# -gt 0 ]]; do
    arg="$1"
    case "${arg}" in
        --stage)          TARGET_ENV="stage" ;;
        --prod)           TARGET_ENV="prod" ;;
        --target)         TARGET_ENV="${2:?--target needs a profile name}"; shift ;;
        --target=*)       TARGET_ENV="${arg#*=}" ;;
        --install|--clean-install) MODE="install" ;;
        --update)         MODE="update" ;;
        --resources-only) MODE="resources-only" ;;
        --dashboard-only) MODE="dashboard-only" ;;
        --sensors-only)   MODE="sensors-only" ;;
        --bootstrap)      MODE="bootstrap" ;;
        --preflight)      MODE="preflight" ;;
        --rollback)       MODE="rollback" ;;
        --skip-preflight) SKIP_PREFLIGHT=1 ;;
        --force|--force-delivery) HA_FORCE_DELIVERY=1 ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            # A bare argument is an ssh destination; it only overrides the host of
            # the selected profile, never the profile itself (unless still default).
            HOST_ARG="${arg}"
            if [[ "${TARGET_ENV}" == "stage" ]]; then
                TARGET_ENV="prod"
            fi
            ;;
    esac
    shift
done

# ── Resolve the target profile (transport / host / container) ───────────────
ha_target_init "${TARGET_ENV}" "${HOST_ARG}"
# NOTE: no ${VAR^^} anywhere in these scripts — macOS ships bash 3.2, where case
# conversion expansion does not exist ("bad substitution").
# A profile whose name starts with "stage" is a verification environment: only
# there are provisioning shortcuts (onboarding bypass, test/test, mock emulator)
# allowed, and only there is the preflight gate relaxed. Everything else is
# treated as a real target.
IS_STAGE=0
case "${TARGET_ENV}" in stage|stage-*|stage_*) IS_STAGE=1 ;; esac

echo "== Sailing dashboard deploy (mode: ${MODE}, profile: ${TARGET_ENV}) → ${HA_HOST} (container: ${HA_CONTAINER}, transport: ${HA_TRANSPORT}) =="

echo "== Running build.py before deploy =="
python3 "${HELPERS_DIR}/build.py"

echo "== Fetching external dependencies declared in deps.yaml =="
python3 "${HELPERS_DIR}/fetch_deps.py"

# build.py has run once for this pipeline; the sub-scripts must not repeat it.
export SAILING_BUILD_DONE=1
# --force must reach deploy_sensors.sh / deploy_dashboard.sh too.
export HA_FORCE_DELIVERY

if [[ "${IS_STAGE}" == "1" && "${MODE}" != "preflight" && "${MODE}" != "rollback" ]]; then
    echo "== Checking Stage Home Assistant auto-provisioning =="
    PROVISION_FLAGS=("--container" "${HA_CONTAINER}")
    if [[ "${MODE}" == "install" ]]; then
        PROVISION_FLAGS+=("--clean-install")
    fi
    # Do not let a provisioning warning/error abort the whole deploy pipeline (set -e):
    # resource/dashboard/sensors deploy steps below must still run so a partial
    # provisioning failure never leaves the dashboard registered-but-empty.
    PROVISION_FLAGS+=("--target" "${TARGET_ENV}")
    python3 "${HELPERS_DIR}/stage_provisioner.py" provision "${PROVISION_FLAGS[@]}" || \
        echo "WARN: stage_provisioner.py reported issues — continuing with resource/dashboard deploy." >&2
fi

deploy_resources() {
    echo "-- Step: manually-installed card resources --"

    TMP_DIR="$(mktemp -d /tmp/sailing_resources_deploy.XXXXXX)"
    trap 'rm -rf "${TMP_DIR}"' RETURN
    REMOTE_RES="${TMP_DIR}/lovelace_resources.json"
    MERGED_RES="${TMP_DIR}/lovelace_resources.merged.json"
    BACKUP_NAME="lovelace_resources.$(date +%Y%m%d%H%M%S).bak"

    local delivered_before="${HA_DELIVERED}"

    echo "Fetching current .storage/lovelace_resources ..."
    if ! ha_cat "/config/.storage/lovelace_resources" > "${REMOTE_RES}"; then
        echo "(Notice: /config/.storage/lovelace_resources not found — initializing new registry)"
        echo '{"version":1,"minor_version":1,"key":"lovelace_resources","data":{"items":[]}}' > "${REMOTE_RES}"
    fi

    if [[ "${HA_TRANSPORT}" == "ssh-docker" ]]; then
        ${SCP} "${REMOTE_RES}" "${DEPLOY_HOST}:~/${BACKUP_NAME}" 2>/dev/null || true
    fi

    # merge_lovelace_resources.py only distinguishes stage from prod (it rewrites
    # /hacsfiles/ to /local/ on stage), so pass the class, not the profile name.
    local MERGE_ENV="prod"
    if [[ "${IS_STAGE}" == "1" ]]; then
        MERGE_ENV="stage"
    fi

    # Merge the resource list. merge_lovelace_resources.py is the SINGLE
    # implementation of this merge — stage_provisioner.py imports the very same
    # module, so Stage and Prod can never drift apart.
    python3 "${HELPERS_DIR}/merge_lovelace_resources.py" \
        "${RESOURCES_FILE}" "${REMOTE_RES}" "${MERGED_RES}" \
        "${MERGE_ENV}" "${CARDS_DIR}" "${DEPS_CARDS_DIR}"

    FILES_TO_UPLOAD=()
    while IFS= read -r line; do
        [[ -n "${line}" ]] && FILES_TO_UPLOAD+=("${line}")
    done < "${MERGED_RES}.files"

    ha_mkdir "/config/www"
    ha_mkdir "/config/.storage"

    for filename in "${FILES_TO_UPLOAD[@]}"; do
        LOCAL_JS="${CARDS_DIR}/${filename}"
        if [[ ! -f "${LOCAL_JS}" ]]; then
            LOCAL_JS="${DEPS_CARDS_DIR}/${filename}"
        fi
        if [[ ! -f "${LOCAL_JS}" ]]; then
            echo "WARN: ${filename} not found in ${CARDS_DIR} nor ${DEPS_CARDS_DIR} — run 'python3 helpers/fetch_deps.py'; skipping upload (resource may fail to load)." >&2
            continue
        fi
        # Card bundles are the bulk of a deploy and change only when deps.yaml
        # moves, so they are content-compared instead of re-uploaded blindly.
        ha_cp_to_container_if_changed "${LOCAL_JS}" "/config/www/${filename}" "${filename}" || true
    done

    ha_cp_to_container_if_changed "${MERGED_RES}" "/config/.storage/lovelace_resources" "lovelace_resources" || true

    if [[ "${HA_DELIVERED}" == "${delivered_before}" ]]; then
        echo "Resources already up to date (${HA_SKIPPED} file(s) unchanged) — nothing uploaded, no restart."
        return 0
    fi

    echo "Resources deployed."
    if [[ -n "${SAILING_CHANGE_FLAG:-}" ]]; then
        echo "resources" >> "${SAILING_CHANGE_FLAG}"
    fi
    if [[ "${SKIP_RESTART:-0}" != "1" ]]; then
        ha_restart
    fi
}

# ── preflight(): is this instance actually ready for a dashboard deploy? ─────
# Returns 0 when everything is in place, 1 otherwise (printing the remaining
# manual steps). Read-only: touches nothing in /config.
preflight() {
    echo "-- Step: preflight (${TARGET_ENV}) --"
    local missing=()

    if ! ha_container_running; then
        echo "PREFLIGHT: container '${HA_CONTAINER}' is not running on ${HA_HOST}" >&2
        echo "  Start it first:  docker start ${HA_CONTAINER}" >&2
        return 1
    fi
    echo "OK     container ${HA_CONTAINER} is running"

    # HACS has TWO independent states and conflating them is what used to let a
    # not-really-working instance pass this gate: the files are delivered by us
    # (automated), while activation happens only in the UI via the GitHub
    # device-flow (cannot be automated). check-hacs reports them separately.
    if python3 "${HELPERS_DIR}/stage_provisioner.py" check-hacs --target "${TARGET_ENV}" \
            --container "${HA_CONTAINER}"; then
        echo "OK     HACS is delivered and activated"
    else
        missing+=("Fix HACS — see the check-hacs output above (it says whether the files are missing, which we can deliver automatically, or whether activation in the UI is missing, which you must do by hand at github.com/login/device).")
    fi

    if ha_cat "/config/custom_components/nmea2000/manifest.json" | grep -q '"domain"'; then
        echo "OK     NMEA 2000 integration is installed"
    else
        missing+=("Install the NMEA 2000 integration from our fork: HACS -> Integrations -> Custom repositories -> dnevera/ha-nmea2000 (tag ydnu-02-usb-tcp-gw), then restart Home Assistant.")
    fi

    # TRAP: HA writes .storage compactly ('"domain":"nmea2000"') while a
    # hand-formatted/provisioned file may have a space after the colon. Always
    # match with an optional-whitespace regex, never a fixed string.
    if ha_cat "/config/.storage/core.config_entries" | grep -Eq '"domain": *"nmea2000"'; then
        echo "OK     an nmea2000 config entry exists"
    else
        missing+=("Create the NMEA 2000 config entry in the UI: Host = the gateway's IP, Port = 4001, Gateway type = text.")
    fi

    local raw_count
    raw_count=$(ha_cat "/config/.storage/core.entity_registry" | grep -Eoc '"platform": *"nmea2000"' || true)
    if [[ "${raw_count:-0}" -gt 0 ]]; then
        echo "OK     ${raw_count} raw nmea2000 entities in the registry"
    else
        missing+=("Wait for raw nmea2000 entities to appear: the bus must carry traffic so the devices announce themselves (two-phase announce, SA 64/200). Auto-discovery has nothing to map until then.")
    fi

    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "PREFLIGHT: target is ready ✓"
        return 0
    fi

    echo "" >&2
    echo "PREFLIGHT FAILED — do these MANUAL steps first (nothing was changed):" >&2
    local i=1
    for item in "${missing[@]}"; do
        echo "  ${i}. ${item}" >&2
        i=$((i + 1))
    done
    echo "" >&2
    echo "Then re-run: $0 --target ${TARGET_ENV} --install" >&2
    echo "(Override with --skip-preflight only if you know what you are doing.)" >&2
    return 1
}

# ── bootstrap(): push cards + integration from build/deps/ into the target ───
# Idempotent. Use it when the target has no HACS yet, or to guarantee the exact
# pinned tag is what actually sits in /config.
bootstrap_target() {
    echo "-- Step: bootstrap (${TARGET_ENV}) --"

    if ! ha_container_running; then
        echo "ERROR: container '${HA_CONTAINER}' is not running on ${HA_HOST}." >&2
        echo "       Install Docker and start Home Assistant there first." >&2
        exit 1
    fi

    # HACS files are delivered for EVERY profile, not just stage: keeping Prod on a
    # "install it by hand with wget" path is exactly how the two environments drift.
    local delivered_before="${HA_DELIVERED}"
    ha_state_load

    local hacs_dir="${SCRIPT_DIR}/build/deps/hacs/custom_components/hacs"
    if [[ -d "${hacs_dir}" ]]; then
        ha_cp_dir_to_container_if_changed "${hacs_dir}" "/config/custom_components/hacs" \
            "HACS (pinned release)" || true
    else
        echo "ERROR: ${hacs_dir} is missing — run 'python3 helpers/fetch_deps.py' first." >&2
        exit 1
    fi

    local integration_dir="${SCRIPT_DIR}/build/deps/nmea2000/custom_components/nmea2000"
    if [[ -d "${integration_dir}" ]]; then
        ha_cp_dir_to_container_if_changed "${integration_dir}" "/config/custom_components/nmea2000" \
            "NMEA 2000 integration (pinned tag)" || true
    else
        echo "ERROR: ${integration_dir} is missing — run 'python3 helpers/fetch_deps.py' first." >&2
        exit 1
    fi

    ha_mkdir "/config/www"
    local card
    for card in "${DEPS_CARDS_DIR}"/*.js "${CARDS_DIR}"/*.js; do
        [[ -f "${card}" ]] || continue
        ha_cp_to_container_if_changed "${card}" "/config/www/$(basename "${card}")" || true
    done

    ha_state_flush

    if [[ "${HA_DELIVERED}" == "${delivered_before}" ]]; then
        echo "Bootstrap: everything already in place (${HA_SKIPPED} artifact(s) unchanged) — no restart."
    else
        ha_restart
    fi
    echo "Bootstrap done. HACS files, the pinned integration and the card bundles are in"
    echo "place. What is left CANNOT be automated — do it in the Home Assistant UI:"
    echo "  1. Settings -> Devices & services -> Add integration -> HACS,"
    echo "     authorize at https://github.com/login/device"
    echo "  2. Add the NMEA 2000 config entry (Host = the gateway, Port = 4001, type 'text')"
    echo "Verify with:  python3 helpers/stage_provisioner.py check-hacs --target ${TARGET_ENV}"
    echo "Then run:     $0 --target ${TARGET_ENV} --install"
}

# ── rollback(): restore the newest backup taken by a previous deploy ─────────
rollback_target() {
    echo "-- Step: rollback (${TARGET_ENV}) --"
    if [[ "${HA_TRANSPORT}" != "ssh-docker" ]]; then
        echo "ERROR: rollback restores backups stored on the target's home directory," >&2
        echo "       which only exists for ssh-docker targets." >&2
        exit 1
    fi

    local restored=0 newest
    local pair
    for pair in "configuration.yaml:/config/configuration.yaml" \
                "lovelace_resources:/config/.storage/lovelace_resources"; do
        local prefix="${pair%%:*}" dest="${pair##*:}"
        newest=$(${SSH} "ls -1t ~/${prefix}.*.bak 2>/dev/null | head -1" < /dev/null || true)
        if [[ -z "${newest}" ]]; then
            echo "(no backup found for ${prefix} — skipping)"
            continue
        fi
        echo "Restoring ${newest} -> ${dest}"
        ${SSH} "sudo docker cp ${newest} ${HA_CONTAINER}:${dest}" < /dev/null
        restored=$((restored + 1))
        # Keep only the newest BACKUP_KEEP backups of this kind.
        ${SSH} "ls -1t ~/${prefix}.*.bak 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f" < /dev/null || true
    done

    if [[ ${restored} -eq 0 ]]; then
        echo "Nothing to roll back." >&2
        exit 1
    fi
    ha_restart
    echo "Rollback done (${restored} file(s) restored)."
}

# Subscript execution helpers
SUB_ENV_FLAGS=("--target" "${TARGET_ENV}")
if [[ "${HA_TRANSPORT}" == "ssh-docker" && -n "${HA_HOST}" ]]; then
    SUB_ENV_FLAGS+=("${HA_HOST}")
fi

case "${MODE}" in
    bootstrap)
        bootstrap_target
        ;;
    preflight)
        preflight
        ;;
    rollback)
        rollback_target
        ;;
    install|update)
        # The gate is enforced on real targets only: Stage is provisioned by
        # stage_provisioner.py right above and may legitimately have no raw
        # entities yet (the mock emulator has just started).
        if [[ "${IS_STAGE}" != "1" && "${SKIP_PREFLIGHT}" != "1" ]]; then
            preflight || exit 1
        fi
        # Shared "something really changed" marker: the sensors step runs with
        # SKIP_RESTART=1, so the dashboard step is the one that decides whether
        # the pipeline needs a restart at all.
        SAILING_CHANGE_FLAG="$(mktemp -t sailing_deploy_changed)"
        export SAILING_CHANGE_FLAG
        trap 'rm -f "${SAILING_CHANGE_FLAG}"' EXIT
        SKIP_RESTART=1 deploy_resources
        SKIP_RESTART=1 "${HELPERS_DIR}/deploy_sensors.sh" "${SUB_ENV_FLAGS[@]}"
        "${HELPERS_DIR}/deploy_dashboard.sh" "${SUB_ENV_FLAGS[@]}"
        ;;
    resources-only)
        deploy_resources
        ;;
    dashboard-only)
        "${HELPERS_DIR}/deploy_dashboard.sh" "${SUB_ENV_FLAGS[@]}"
        ;;
    sensors-only)
        "${HELPERS_DIR}/deploy_sensors.sh" "${SUB_ENV_FLAGS[@]}"
        ;;
esac

echo "== Done (mode: ${MODE}, profile: ${TARGET_ENV}) =="
