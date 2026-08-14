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
#     2. cleans AIS OUT of the live nmea2000 config entry
#        (.storage/core.config_entries): removes the AIS PGNs from pgn_include
#        and sets exclude_AIS=True (helpers/patch_pgn_include.py). AIS is
#        decoded by the ais_targets component straight off the gateway, so the
#        nmea2000 HA integration must NOT decode it (that would flood HA's
#        registry with a throwaway device per passing MMSI);
#     3. fetches + registers the custom:auto-entities + custom:flex-table-card
#        Lovelace cards used by the target table (so ha/ais is self-sufficient
#        and needs no separate sailing-dash deploy for the table to render);
#     4. runs helpers/verify_ais_targets.py against the nmea2000 package
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

# ── patch_pgn_include(): keep AIS OUT of the live nmea2000 config entry ─────
# AIS is decoded by ais_targets off the gateway; the nmea2000 HA integration
# must not also decode it (registry/recorder garbage). This removes AIS PGNs
# from pgn_include and forces exclude_AIS=True.
patch_pgn_include() {
    echo "-- Step: nmea2000 AIS exclusion (keep AIS out of the registry) --"
    local tmp_dir remote_path local_copy
    tmp_dir="$(mktemp -d /tmp/ais_pgn_patch.XXXXXX)"
    trap 'rm -rf "${tmp_dir}"' RETURN
    remote_path="/config/.storage/core.config_entries"
    local_copy="${tmp_dir}/core.config_entries"

    if ! ha_cat "${remote_path}" > "${local_copy}"; then
        echo "WARN: could not fetch ${remote_path} from ${HA_CONTAINER} — is the nmea2000 integration configured yet? Skipping pgn_include patch." >&2
        return 0
    fi

    # Report-only run first: exit 0 = already AIS-free (nothing to do), exit 2
    # = a real change is needed, anything else = an unexpected script failure
    # (a malformed core.config_entries, etc).
    local report_status=0
    python3 "${HELPERS_DIR}/patch_pgn_include.py" "${local_copy}" || report_status=$?

    if [[ "${report_status}" -eq 0 ]]; then
        echo "nmea2000 entry is already AIS-free — nothing to change."
        return 0
    elif [[ "${report_status}" -ne 2 ]]; then
        echo "WARN: patch_pgn_include.py failed unexpectedly (exit ${report_status}) — leaving pgn_include untouched." >&2
        return 0
    fi

    python3 "${HELPERS_DIR}/patch_pgn_include.py" "${local_copy}" --write
    ha_cp_to_container_if_changed "${local_copy}" "${remote_path}" "core.config_entries (AIS exclusion)" \
        && echo "pgn_include" >> "${AIS_CHANGE_FLAG}"
}

# ── provision_helpers(): the AIS table overlay toggle ───────────────────────
# The target list is rendered as an overlay on the map and collapses to a
# name+distance side-bar; the collapsed/expanded state is held by
# input_boolean.ais_table_expanded, which lives in .storage/input_boolean.
provision_helpers() {
    echo "-- Step: AIS dashboard helpers (table overlay toggle, selected target) --"
    local tmp_dir remote_path local_copy
    tmp_dir="$(mktemp -d /tmp/ais_helpers.XXXXXX)"
    trap 'rm -rf "${tmp_dir}"' RETURN
    remote_path="/config/.storage/input_boolean"
    local_copy="${tmp_dir}/input_boolean"
    local reg_remote="/config/.storage/core.entity_registry"
    local reg_copy="${tmp_dir}/core.entity_registry"
    # input_text.ais_selected_mmsi holds the target whose detail card is shown
    # (a geo_location entity's more-info dialog renders NOTHING in HA, so the
    # table selects into this helper instead of opening an empty dialog).
    local text_remote="/config/.storage/input_text"
    local text_copy="${tmp_dir}/input_text"
    # .storage/auth is READ ONLY here: it is the list of users the per-user
    # helpers are provisioned for (HA has no per-user entity state, so each
    # user gets their own selection/expanded helper on the server).
    local auth_remote="/config/.storage/auth"
    local auth_copy="${tmp_dir}/auth"

    # A missing store is normal on an instance without any UI helper yet —
    # provision_helpers.py then creates a fresh, minimally-valid document.
    ha_cat "${remote_path}" > "${local_copy}" 2>/dev/null || rm -f "${local_copy}"
    # The registry is also fed in so a helper registered under a mis-slugged
    # entity_id by an older revision (input_boolean.ais_targets) gets dropped
    # and re-registered as input_boolean.ais_table_expanded.
    ha_cat "${reg_remote}" > "${reg_copy}" 2>/dev/null || rm -f "${reg_copy}"
    ha_cat "${text_remote}" > "${text_copy}" 2>/dev/null || rm -f "${text_copy}"
    ha_cat "${auth_remote}" > "${auth_copy}" 2>/dev/null || rm -f "${auth_copy}"

    local report_status=0
    python3 "${HELPERS_DIR}/provision_helpers.py" "${local_copy}" \
        --input-text "${text_copy}" \
        --auth "${auth_copy}" \
        --entity-registry "${reg_copy}" || report_status=$?

    if [[ "${report_status}" -eq 0 ]]; then
        echo "AIS dashboard helpers already present — nothing to change."
        return 0
    elif [[ "${report_status}" -ne 2 ]]; then
        echo "WARN: provision_helpers.py failed unexpectedly (exit ${report_status}) — leaving helpers untouched." >&2
        return 0
    fi

    python3 "${HELPERS_DIR}/provision_helpers.py" "${local_copy}" \
        --input-text "${text_copy}" \
        --auth "${auth_copy}" \
        --entity-registry "${reg_copy}" --write
    ha_cp_to_container_if_changed "${local_copy}" "${remote_path}" "input_boolean (AIS table toggle)" \
        && echo "input_boolean" >> "${AIS_CHANGE_FLAG}"
    if [[ -f "${text_copy}" ]]; then
        ha_cp_to_container_if_changed "${text_copy}" "${text_remote}" "input_text (AIS selected target)" \
            && echo "input_text" >> "${AIS_CHANGE_FLAG}"
    fi
    if [[ -f "${reg_copy}" ]]; then
        ha_cp_to_container_if_changed "${reg_copy}" "${reg_remote}" "core.entity_registry (AIS toggle slug)" \
            && echo "entity_registry" >> "${AIS_CHANGE_FLAG}"
    fi
}

# ── deploy_card_deps(): the target table's Lovelace card dependencies ───────
# Fetches the community cards the AIS target table needs (pinned in
# ../sailing-dash/deps.yaml) and registers them as Lovelace resources on THIS
# target, so ha/ais never depends on a separate sailing-dash deploy. Without
# this the table renders as a "Custom element doesn't exist" error box.
#   - flex-table-card: renders the geo_location.ais_* set as a multi-column,
#     clickable table (it resolves the wildcard entity include itself — the
#     auto-entities wrapper was dropped because it re-called setConfig() on
#     every target change and reset the user's chosen sort column).
#   - auto-entities: no longer used by this dashboard, but kept registered so
#     an older cached dashboard revision doesn't break.
deploy_card_deps() {
    echo "-- Step: Lovelace card dependencies (target table) --"
    local sailing_dir="${PROJECT_ROOT}/ha/sailing-dash"
    local deps_helper="${sailing_dir}/helpers/fetch_deps.py"
    local merge_helper="${sailing_dir}/helpers/merge_lovelace_resources.py"
    local cards_dir="${sailing_dir}/build/deps/cards"
    # asset:version pairs — keep the version in sync with ../sailing-dash/
    # deps.yaml's `ref` for each card and the ?v= query below.
    local assets=("auto-entities.js:1.16.1" "flex-table-card.js:1.4")

    if [[ ! -f "${deps_helper}" || ! -f "${merge_helper}" ]]; then
        echo "WARN: sailing-dash dependency helpers not found — skipping card deploy." >&2
        echo "      The target table will show a 'Custom element doesn't exist' error." >&2
        return 0
    fi

    if ! python3 "${deps_helper}" --only cards; then
        echo "WARN: could not fetch Lovelace cards (no network access?) — skipping." >&2
        return 0
    fi

    local tmp_dir resources_yaml current_json merged_json
    tmp_dir="$(mktemp -d /tmp/ais_card_deps.XXXXXX)"
    trap 'rm -rf "${tmp_dir}"' RETURN
    resources_yaml="${tmp_dir}/resources.yaml"
    current_json="${tmp_dir}/lovelace_resources.json"
    merged_json="${tmp_dir}/lovelace_resources.merged.json"

    echo "resources:" > "${resources_yaml}"
    local pair asset version
    for pair in "${assets[@]}"; do
        asset="${pair%%:*}"
        version="${pair#*:}"
        if [[ ! -f "${cards_dir}/${asset}" ]]; then
            echo "WARN: ${asset} missing from ${cards_dir} after fetch — skipping this card." >&2
            continue
        fi
        ha_cp_to_container_if_changed "${cards_dir}/${asset}" "/config/www/${asset}" "${asset}" \
            && echo "${asset}" >> "${AIS_CHANGE_FLAG}"
        cat >> "${resources_yaml}" <<EOF
  - url: /local/${asset}?v=${version}
    type: module
EOF
    done

    # Our own module: turns a click on an AIS marker on the map into a target
    # selection for the detail card (geo_location has no more-info control in
    # HA, so the stock dialog is empty). Versioned by content hash so the
    # browser picks up an edit without a manual cache purge.
    local bridge_src="${SCRIPT_DIR}/src/js/ais-select-bridge.js"
    if [[ -f "${bridge_src}" ]]; then
        local bridge_ver
        bridge_ver="$(shasum -a 256 "${bridge_src}" | cut -c1-8)"
        ha_cp_to_container_if_changed "${bridge_src}" "/config/www/ais-select-bridge.js" "ais-select-bridge.js" \
            && echo "ais-select-bridge.js" >> "${AIS_CHANGE_FLAG}"
        cat >> "${resources_yaml}" <<EOF
  - url: /local/ais-select-bridge.js?v=${bridge_ver}
    type: module
EOF
    fi

    ha_cat "/config/.storage/lovelace_resources" > "${current_json}" 2>/dev/null || echo "{}" > "${current_json}"

    python3 "${merge_helper}" "${resources_yaml}" "${current_json}" "${merged_json}" \
        "${TARGET_ENV}" "${cards_dir}" "${cards_dir}"

    ha_cp_to_container_if_changed "${merged_json}" "/config/.storage/lovelace_resources" "lovelace_resources (ais cards)" \
        && echo "lovelace_resources" >> "${AIS_CHANGE_FLAG}"
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
        SKIP_RESTART=1 provision_helpers
        SKIP_RESTART=1 deploy_card_deps
        verify_ais_in_container || true
        "${HELPERS_DIR}/deploy_dashboard.sh" --target "${TARGET_ENV}" ${HOST_ARG:+"${HOST_ARG}"}
        ;;
    update)
        "${HELPERS_DIR}/deploy_dashboard.sh" --target "${TARGET_ENV}" ${HOST_ARG:+"${HOST_ARG}"}
        ;;
esac

echo "== Done (mode: ${MODE}, profile: ${TARGET_ENV}) =="
