#!/usr/bin/env bash
# deploy.sh — single entry point to deploy the AIS dashboard package onto a
# Home Assistant instance (Stage or Prod): the `ais_targets` custom
# integration, the `pgn_include` allow-list patch on the `nmea2000` config
# entry, and the dashboard-ais.yaml Lovelace view itself.
#
# USAGE
#   ./deploy.sh --stage                 # deploy to the "stage" target profile
#   ./deploy.sh --prod  [user@host]     # deploy to the "prod" target profile
#   ./deploy.sh --target <profile>      # deploy to ANY profile from .env (e.g. stage-pi5)
#
#   Target profiles are declared in ha/ais/.env (see .env.template): transport,
#   ssh host, container, config dir. --stage/--prod are aliases of
#   --target stage/prod. This package does NOT read ha/sailing-dash/.env —
#   copy the values across by hand if the two point at the same box.
#
#   Sub-mode options (work for both --stage and --prod):
#   ./deploy.sh [--stage|--prod] --install          # full install (see below)
#   ./deploy.sh [--stage|--prod] --update           # dashboard-only refresh (default)
#   ./deploy.sh [--stage|--prod] --dashboard-only    # same as --update
#   ./deploy.sh [--stage|--prod] --clean-ha          # remove garbage devices & orphaned entities
#   ./deploy.sh [--stage|--prod] --clean-sensors     # alias for --clean-ha
#   ./deploy.sh [--stage|--prod] --clean-ais         # remove raw sensor.ais_* entities
#   ./deploy.sh [--stage|--prod] --clean-all         # remove ALL nmea2000 devices
#   ./deploy.sh [--stage|--prod] --dry-sensors       # dry-run mode for cleanup
#
#   --install, in addition to the dashboard, ALSO:
#     1. copies custom_components/ais_targets into the target's HA config dir
#        (idempotent sha256-diff directory copy, like sailing-dash's
#        ha_cp_dir_to_container_if_changed());
#     2. patches the live nmea2000 config entry's pgn_include allow-list
#        (.storage/core.config_entries) to include the AIS PGN set, if
#        missing, via helpers/patch_pgn_include.py;
#     3. runs helpers/verify_ais_targets.py against the nmea2000 package
#        found inside the container, as a drift-guard (best effort — logs a
#        warning, never aborts the deploy on its own).
#
#   Delivery is idempotent: a file/directory is uploaded only when its
#   content differs from what the container already has, and Home Assistant
#   is restarted only when something was actually delivered. Use --force to
#   re-upload everything regardless.
#
# REQUIRES
#   - python3 + PyYAML on THIS machine
#   - ha/ais/.env for anything beyond the two default profiles
#   - Docker running locally (local-docker profiles) or SSH access (ssh-docker)
#   - The `nmea2000` HA integration already installed/configured (see
#     ha/sailing-dash's bootstrap) — this package does not install it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPERS_DIR="${SCRIPT_DIR}/helpers"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CUSTOM_COMPONENT_DIR="${SCRIPT_DIR}/custom_components/ais_targets"

# shellcheck source=helpers/lib/ha_target.sh
source "${HELPERS_DIR}/lib/ha_target.sh"

# ── Parse flags ──────────────────────────────────────────────────────────────
TARGET_ENV="stage"
MODE="update"
HOST_ARG=""
# Re-upload everything even when the target already holds identical content.
HA_FORCE_DELIVERY="${HA_FORCE_DELIVERY:-0}"
CLEAN_AIS_FLAG=0
CLEAN_ALL_FLAG=0
DRY_RUN_FLAG=0
MODE_EXPLICIT=0

while [[ $# -gt 0 ]]; do
    arg="$1"
    case "${arg}" in
        --stage)           TARGET_ENV="stage" ;;
        --prod)            TARGET_ENV="prod" ;;
        --target)          TARGET_ENV="${2:?--target needs a profile name}"; shift ;;
        --target=*)        TARGET_ENV="${arg#*=}" ;;
        --install)         MODE="install"; MODE_EXPLICIT=1 ;;
        --update)          MODE="update"; MODE_EXPLICIT=1 ;;
        --dashboard-only)  MODE="update"; MODE_EXPLICIT=1 ;;
        --clean-ha|--clean-sensors) MODE="clean-ha"; MODE_EXPLICIT=1 ;;
        --clean-ais)      MODE="clean-ha"; CLEAN_AIS_FLAG=1; MODE_EXPLICIT=1 ;;
        --clean-all)      MODE="clean-ha"; CLEAN_ALL_FLAG=1; MODE_EXPLICIT=1 ;;
        --dry-run|--dry-sensors) DRY_RUN_FLAG=1 ;;
        --force|--force-delivery) HA_FORCE_DELIVERY=1 ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            # A bare argument is an ssh destination; it only overrides the host
            # of the selected profile, never the profile itself (unless still
            # default).
            HOST_ARG="${arg}"
            if [[ "${TARGET_ENV}" == "stage" ]]; then
                TARGET_ENV="prod"
            fi
            ;;
    esac
    shift
done

if [[ "${DRY_RUN_FLAG}" == "1" && "${MODE_EXPLICIT}" == "0" ]]; then
    MODE="clean-ha"
fi

ha_target_init "${TARGET_ENV}" "${HOST_ARG}"
export HA_FORCE_DELIVERY

echo "== AIS dashboard deploy (mode: ${MODE}, profile: ${TARGET_ENV}) → ${HA_HOST} (container: ${HA_CONTAINER}, transport: ${HA_TRANSPORT}) =="

echo "== Running build.py before deploy =="
python3 "${HELPERS_DIR}/build.py"
# build.py has run once for this pipeline; deploy_dashboard.sh must not repeat it.
export AIS_BUILD_DONE=1

# Shared "something really changed" marker, same convention as sailing-dash's
# SAILING_CHANGE_FLAG: the install steps below run with SKIP_RESTART=1, so
# deploy_dashboard.sh (the last step) is the one that decides whether the
# whole pipeline needs a restart at all.
AIS_CHANGE_FLAG="$(mktemp -t ais_deploy_changed)"
export AIS_CHANGE_FLAG
trap 'rm -f "${AIS_CHANGE_FLAG}"' EXIT

# ── install_component(): copy custom_components/ais_targets into /config ────
install_component() {
    echo "-- Step: ais_targets custom component --"
    if ! ha_container_running; then
        echo "ERROR: container '${HA_CONTAINER}' is not running on ${HA_HOST}." >&2
        exit 1
    fi
    ha_state_load
    ha_cp_dir_to_container_if_changed \
        "${CUSTOM_COMPONENT_DIR}" \
        "/config/custom_components/ais_targets" \
        "ais_targets custom component" \
        && echo "ais_targets" >> "${AIS_CHANGE_FLAG}"
    ha_state_flush
}

# ── patch_pgn_include(): verify/patch the live nmea2000 config entry ────────
patch_pgn_include() {
    echo "-- Step: nmea2000 pgn_include allow-list --"
    local tmp_dir remote_path local_copy
    tmp_dir="$(mktemp -d /tmp/ais_pgn_patch.XXXXXX)"
    trap 'rm -rf "${tmp_dir}"' RETURN
    remote_path="/config/.storage/core.config_entries"
    local_copy="${tmp_dir}/core.config_entries"

    if ! ha_cat "${remote_path}" > "${local_copy}"; then
        echo "WARN: could not fetch ${remote_path} from ${HA_CONTAINER} — is the nmea2000 integration configured yet? Skipping pgn_include patch." >&2
        return 0
    fi

    # Report-only run first: exit 0 = pgn_include already covers every AIS
    # PGN (nothing to do), exit 2 = a real change is needed, anything else =
    # an unexpected script failure (a malformed core.config_entries, etc).
    local report_status=0
    python3 "${HELPERS_DIR}/patch_pgn_include.py" "${local_copy}" || report_status=$?

    if [[ "${report_status}" -eq 0 ]]; then
        echo "pgn_include already covers all AIS PGNs — nothing to patch."
        return 0
    elif [[ "${report_status}" -ne 2 ]]; then
        echo "WARN: patch_pgn_include.py failed unexpectedly (exit ${report_status}) — leaving pgn_include untouched." >&2
        return 0
    fi

    python3 "${HELPERS_DIR}/patch_pgn_include.py" "${local_copy}" --write
    ha_cp_to_container_if_changed "${local_copy}" "${remote_path}" "core.config_entries (pgn_include)" \
        && echo "pgn_include" >> "${AIS_CHANGE_FLAG}"
}

# ── verify_ais(): best-effort drift-guard inside the container ──────────────
verify_ais_in_container() {
    echo "-- Step: verify_ais_targets drift-guard (container) --"
    local tmp_dir local_pgns
    tmp_dir="$(mktemp -d /tmp/ais_verify.XXXXXX)"
    trap 'rm -rf "${tmp_dir}"' RETURN
    local_pgns="${tmp_dir}/pgns.py"

    local pkg_dir
    pkg_dir=$(ha_exec python3 -c 'import os, nmea2000; print(os.path.dirname(nmea2000.__file__))' 2>/dev/null) || pkg_dir=""
    if [[ -z "${pkg_dir}" ]]; then
        echo "WARN: nmea2000 is not importable inside ${HA_CONTAINER} — cannot run the drift-guard." >&2
        return 0
    fi

    if ! ha_cat "${pkg_dir}/pgns.py" > "${local_pgns}"; then
        echo "WARN: could not fetch ${pkg_dir}/pgns.py from ${HA_CONTAINER} — skipping drift-guard." >&2
        return 0
    fi

    python3 "${HELPERS_DIR}/verify_ais_targets.py" --pgns-file "${local_pgns}" \
        || echo "WARN: verify_ais_targets.py reported drift inside ${HA_CONTAINER} — see output above." >&2
}

# ── clean_ha_target(): clean NMEA 2000 devices/entities on target HA ─────────
clean_ha_target() {
    echo "-- Step: Home Assistant NMEA cleanup (${TARGET_ENV}) --"
    if ! ha_container_running; then
        echo "ERROR: container '${HA_CONTAINER}' is not running on ${HA_HOST}." >&2
        exit 1
    fi

    local script_path="${PROJECT_ROOT}/homeassistant/cleanup_nmea_devices.py"
    if [[ ! -f "${script_path}" ]]; then
        echo "ERROR: cleanup script not found at ${script_path}" >&2
        exit 1
    fi

    local cleanup_flags=()
    [[ "${CLEAN_ALL_FLAG:-0}" == "1" ]] && cleanup_flags+=("--all")
    [[ "${CLEAN_AIS_FLAG:-0}" == "1" ]] && cleanup_flags+=("--clean-ais")
    [[ "${DRY_RUN_FLAG:-0}" == "1" ]] && cleanup_flags+=("--dry-run")

    echo "Copying cleanup script to ${HA_CONTAINER}..."
    ha_cp_to_container "${script_path}" "/tmp/cleanup_nmea_devices.py"

    echo "Running cleanup_nmea_devices.py ${cleanup_flags[*]:-(default cleanup)} inside ${HA_CONTAINER}..."
    ha_exec python3 /tmp/cleanup_nmea_devices.py "${cleanup_flags[@]}"

    if [[ "${DRY_RUN_FLAG:-0}" == "1" ]]; then
        echo "Dry run complete — no changes written, no restart."
    else
        echo "Restarting ${HA_CONTAINER}..."
        ha_restart
        echo "HA restarted ✓"
    fi
}

case "${MODE}" in
    clean-ha)
        clean_ha_target
        ;;
    install)
        # Best effort locally too, before touching the container: fails loud
        # in CI/dev, but never blocks the actual deploy (mirrors how
        # deploy.sh --check-ha is advisory in the root gateway deploy.sh).
        python3 "${HELPERS_DIR}/verify_ais_targets.py" || \
            echo "WARN: local nmea2000 drift-guard reported an issue — continuing with deploy." >&2

        SKIP_RESTART=1 install_component
        SKIP_RESTART=1 patch_pgn_include
        verify_ais_in_container || true
        "${HELPERS_DIR}/deploy_dashboard.sh" --target "${TARGET_ENV}" ${HOST_ARG:+"${HOST_ARG}"}
        ;;
    update)
        "${HELPERS_DIR}/deploy_dashboard.sh" --target "${TARGET_ENV}" ${HOST_ARG:+"${HOST_ARG}"}
        ;;
esac

echo "== Done (mode: ${MODE}, profile: ${TARGET_ENV}) =="
