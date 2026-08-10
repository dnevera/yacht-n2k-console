#!/usr/bin/env bash
# deploy.sh — single entry point to deploy EVERYTHING the Sailing dashboard
# needs onto a Home Assistant instance (Stage or Prod): manually-installed custom
# card JS resources, sensors-sailing.yaml (rest:/template: entries), and
# dashboard-sailing.yaml (the Lovelace dashboard itself).
#
# USAGE
#   ./deploy.sh --stage               # deploy to local Stage HA container (local-ha)
#   ./deploy.sh --prod  [user@host]   # deploy to production HA host via SSH (bumblebee)
#
#   Sub-mode options (work for both --stage and --prod):
#   ./deploy.sh [--stage|--prod] --install
#   ./deploy.sh [--stage|--prod] --update
#   ./deploy.sh [--stage|--prod] --resources-only
#   ./deploy.sh [--stage|--prod] --dashboard-only
#   ./deploy.sh [--stage|--prod] --sensors-only
#
# REQUIRES
#   - python3 + PyYAML on THIS machine
#   - Docker running locally (for --stage) or SSH target (for --prod)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENDOR_DIR="${SCRIPT_DIR}/vendor"
CARDS_DIR="${SCRIPT_DIR}/build/cards"
RESOURCES_FILE="${SCRIPT_DIR}/build/lovelace-resources.yaml"

# ── Parse flags ──────────────────────────────────────────────────────────────
TARGET_ENV="stage"
MODE="update"
HOST_ARG=""

for arg in "$@"; do
    case "${arg}" in
        --stage)          TARGET_ENV="stage" ;;
        --prod)           TARGET_ENV="prod" ;;
        --install|--clean-install) MODE="install" ;;
        --update)         MODE="update" ;;
        --resources-only) MODE="resources-only" ;;
        --dashboard-only) MODE="dashboard-only" ;;
        --sensors-only)   MODE="sensors-only" ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            HOST_ARG="${arg}"
            TARGET_ENV="prod" # Explicit host parameter implies prod
            ;;
    esac
done

# ── Resolve target container / SSH settings ─────────────────────────────────
if [[ "${TARGET_ENV}" == "stage" ]]; then
    HA_CONTAINER="${HA_CONTAINER:-local-ha}"
    DEPLOY_HOST="localhost"
    echo "== Sailing dashboard deploy (mode: ${MODE}, env: STAGE) → Container: ${HA_CONTAINER} =="

    ha_mkdir() {
        docker exec "${HA_CONTAINER}" mkdir -p "$1" 2>/dev/null || true
    }

    ha_cat() {
        docker exec "${HA_CONTAINER}" cat "$1" 2>/dev/null
    }

    ha_cp_to_container() {
        local src="$1"
        local dest="$2"
        docker cp "${src}" "${HA_CONTAINER}:${dest}"
    }

    ha_restart() {
        echo "Restarting local container ${HA_CONTAINER} ..."
        docker restart "${HA_CONTAINER}"
    }
else
    if [[ -n "${HOST_ARG}" ]]; then
        DEPLOY_HOST="${HOST_ARG}"
        HA_CONTAINER="${HA_CONTAINER:-homeassistant}"
    elif [[ -f "${PROJECT_ROOT}/deploy.conf" ]]; then
        # shellcheck source=/dev/null
        source "${PROJECT_ROOT}/deploy.conf"
    else
        echo "ERROR: no host given and ${PROJECT_ROOT}/deploy.conf not found." >&2
        echo "       Usage: $0 --prod [user@host]" >&2
        exit 1
    fi

    SSH="ssh -o ConnectTimeout=8 ${DEPLOY_HOST}"
    SCP="scp -q"
    echo "== Sailing dashboard deploy (mode: ${MODE}, env: PROD) → ${DEPLOY_HOST} (container: ${HA_CONTAINER}) =="

    ha_mkdir() {
        ${SSH} "sudo docker exec ${HA_CONTAINER} mkdir -p $1" 2>/dev/null || true
    }

    ha_cat() {
        ${SSH} "sudo docker exec ${HA_CONTAINER} cat $1" 2>/dev/null
    }

    ha_cp_to_container() {
        local src="$1"
        local dest="$2"
        local filename="$(basename "${src}")"
        ${SCP} "${src}" "${DEPLOY_HOST}:/tmp/${filename}" < /dev/null
        ${SSH} "sudo docker cp /tmp/${filename} ${HA_CONTAINER}:${dest} && rm -f /tmp/${filename}" < /dev/null
    }

    ha_restart() {
        echo "Restarting remote container ${HA_CONTAINER} on ${DEPLOY_HOST} ..."
        ${SSH} "sudo docker restart ${HA_CONTAINER}"
    }
fi

echo "== Running build.py before deploy =="
python3 "${SCRIPT_DIR}/build.py"

if [[ "${TARGET_ENV}" == "stage" ]]; then
    echo "== Checking Stage Home Assistant auto-provisioning =="
    PROVISION_FLAGS=("--container" "${HA_CONTAINER}")
    if [[ "${MODE}" == "install" ]]; then
        PROVISION_FLAGS+=("--clean-install")
    fi
    # Do not let a provisioning warning/error abort the whole deploy pipeline (set -e):
    # resource/dashboard/sensors deploy steps below must still run so a partial
    # provisioning failure never leaves the dashboard registered-but-empty.
    python3 "${SCRIPT_DIR}/stage_provisioner.py" provision "${PROVISION_FLAGS[@]}" || \
        echo "WARN: stage_provisioner.py reported issues — continuing with resource/dashboard deploy." >&2
fi

deploy_resources() {
    echo "-- Step: manually-installed card resources --"

    TMP_DIR="$(mktemp -d /tmp/sailing_resources_deploy.XXXXXX)"
    trap 'rm -rf "${TMP_DIR}"' RETURN
    REMOTE_RES="${TMP_DIR}/lovelace_resources.json"
    MERGED_RES="${TMP_DIR}/lovelace_resources.merged.json"
    BACKUP_NAME="lovelace_resources.$(date +%Y%m%d%H%M%S).bak"

    echo "Fetching current .storage/lovelace_resources ..."
    if ! ha_cat "/config/.storage/lovelace_resources" > "${REMOTE_RES}"; then
        echo "(Notice: /config/.storage/lovelace_resources not found — initializing new registry)"
        echo '{"version":1,"minor_version":1,"key":"lovelace_resources","data":{"items":[]}}' > "${REMOTE_RES}"
    fi

    if [[ "${TARGET_ENV}" == "prod" ]]; then
        ${SCP} "${REMOTE_RES}" "${DEPLOY_HOST}:~/${BACKUP_NAME}" 2>/dev/null || true
    fi

    # Merge resource list
    # NOTE: On STAGE (no HACS installed), /hacsfiles/... resources are normalized
    # to /local/<filename> and served from CARDS_DIR/VENDOR_DIR fallback bundles,
    # mirroring stage_provisioner.py's provision_resource_registry() logic. This
    # prevents a missing HACS-only bundle (e.g. card-mod-studio, an optional debug
    # tool with no vendor fallback) from aborting the ENTIRE deploy pipeline before
    # deploy_sensors.sh/deploy_dashboard.sh ever run — which previously left the
    # dashboard registered but empty (no lovelace.<id> content file) on first install.
    python3 - "${RESOURCES_FILE}" "${REMOTE_RES}" "${MERGED_RES}" "${TARGET_ENV}" "${CARDS_DIR}" "${VENDOR_DIR}" <<'PYEOF'
import json
import os
import sys
import uuid
import yaml
from urllib.parse import urlsplit

resources_yaml, remote_json, out_json, target_env, cards_dir, vendor_dir = sys.argv[1:7]

with open(resources_yaml) as f:
    wanted = (yaml.safe_load(f) or {}).get("resources", [])
wanted = [r for r in wanted if urlsplit(r["url"]).path.startswith(("/local/", "/hacsfiles/"))]


def has_local_bundle(filename):
    return os.path.isfile(os.path.join(cards_dir, filename)) or os.path.isfile(os.path.join(vendor_dir, filename))


# On stage (no HACS available), normalize /hacsfiles/ URLs to /local/<filename> so
# they resolve against our own build/vendor card bundles. Drop any resource that
# has neither a local build artifact nor a vendor fallback bundle instead of
# failing the whole deploy.
normalized = []
for entry in wanted:
    url = entry["url"]
    rtype = entry.get("type", "module")
    if target_env == "stage" and url.startswith("/hacsfiles/"):
        filename = url.rsplit("/", 1)[-1]
        if not has_local_bundle(filename):
            print(f"SKIP   {url} (no local/vendor bundle for {filename}, not required on stage)")
            continue
        normalized.append({"url": f"/local/{filename}", "type": rtype})
    else:
        normalized.append(entry)
wanted = normalized

try:
    with open(remote_json) as f:
        registry = json.load(f)
except Exception:
    registry = {"version": 1, "minor_version": 1, "key": "lovelace_resources", "data": {"items": []}}

items = registry.get("data", {}).get("items", [])


def base_path(url):
    return urlsplit(url).path


existing_by_path = {base_path(it["url"]): it for it in items}

to_upload = []
for entry in wanted:
    path = base_path(entry["url"])
    filename = path.rsplit("/", 1)[-1]
    existing = existing_by_path.get(path)
    if existing is None:
        items.append({"id": uuid.uuid4().hex[:24], "url": entry["url"], "type": entry.get("type", "module")})
        print(f"ADD    {entry['url']}")
    elif existing["url"] != entry["url"]:
        existing["url"] = entry["url"]
        print(f"UPDATE {path} -> {entry['url']}")
    else:
        print(f"OK     {entry['url']} (already registered)")
    if path.startswith("/local/"):
        to_upload.append(filename)

registry["data"]["items"] = items

with open(out_json, "w") as f:
    json.dump(registry, f, indent=2)

with open(out_json + ".files", "w") as f:
    f.write("\n".join(to_upload) + "\n")
PYEOF

    FILES_TO_UPLOAD=()
    while IFS= read -r line; do
        [[ -n "${line}" ]] && FILES_TO_UPLOAD+=("${line}")
    done < "${MERGED_RES}.files"

    ha_mkdir "/config/www"
    ha_mkdir "/config/.storage"

    for filename in "${FILES_TO_UPLOAD[@]}"; do
        LOCAL_JS="${CARDS_DIR}/${filename}"
        if [[ ! -f "${LOCAL_JS}" ]]; then
            LOCAL_JS="${VENDOR_DIR}/${filename}"
        fi
        if [[ ! -f "${LOCAL_JS}" ]]; then
            echo "WARN: ${filename} not found in ${CARDS_DIR} nor ${VENDOR_DIR} — skipping upload (resource may fail to load)." >&2
            continue
        fi
        echo "Uploading ${filename} -> ${HA_CONTAINER}:/config/www/${filename} ..."
        ha_cp_to_container "${LOCAL_JS}" "/config/www/${filename}"
    done

    echo "Uploading merged lovelace_resources ..."
    ha_cp_to_container "${MERGED_RES}" "/config/.storage/lovelace_resources"

    echo "Resources deployed."
    if [[ "${SKIP_RESTART:-0}" != "1" ]]; then
        ha_restart
    fi
}

# Subscript execution helpers
SUB_ENV_FLAGS=("--${TARGET_ENV}")
if [[ "${TARGET_ENV}" == "prod" && -n "${DEPLOY_HOST}" ]]; then
    SUB_ENV_FLAGS+=("${DEPLOY_HOST}")
fi

case "${MODE}" in
    install|update)
        deploy_resources
        SKIP_RESTART=1 "${SCRIPT_DIR}/deploy_sensors.sh" "${SUB_ENV_FLAGS[@]}"
        "${SCRIPT_DIR}/deploy_dashboard.sh" "${SUB_ENV_FLAGS[@]}"
        ;;
    resources-only)
        deploy_resources
        ;;
    dashboard-only)
        "${SCRIPT_DIR}/deploy_dashboard.sh" "${SUB_ENV_FLAGS[@]}"
        ;;
    sensors-only)
        "${SCRIPT_DIR}/deploy_sensors.sh" "${SUB_ENV_FLAGS[@]}"
        ;;
esac

echo "== Done (mode: ${MODE}, env: ${TARGET_ENV}) =="
