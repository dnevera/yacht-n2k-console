#!/usr/bin/env bash
# deploy_dashboard.sh — push ha/sailing-dash/build/dashboard-sailing.yaml back into
# the live Home Assistant instance's storage-mode Lovelace config (Stage or Prod).
#
# USAGE
#   ./deploy_dashboard.sh --stage               # deploy to local Stage HA container (local-ha)
#   ./deploy_dashboard.sh --prod [user@host]    # deploy to production HA host via SSH (bumblebee)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "== Running build.py before deploy_dashboard =="
python3 "${SCRIPT_DIR}/build.py"

YAML_FILE="${SCRIPT_DIR}/build/dashboard-sailing.yaml"
STORAGE_KEY="lovelace.dashboard_sailing"

# ── Parse flags ──────────────────────────────────────────────────────────────
TARGET_ENV="stage"
HOST_ARG=""

for arg in "$@"; do
    case "${arg}" in
        --stage) TARGET_ENV="stage" ;;
        --prod)  TARGET_ENV="prod" ;;
        *)
            HOST_ARG="${arg}"
            TARGET_ENV="prod"
            ;;
    esac
done

if [[ "${TARGET_ENV}" == "stage" ]]; then
    HA_CONTAINER="${HA_CONTAINER:-local-ha}"
    DEPLOY_HOST="localhost"
    echo "== Sailing dashboard deploy (env: STAGE) → Container: ${HA_CONTAINER} =="

    ha_cat() {
        docker exec "${HA_CONTAINER}" cat "$1" 2>/dev/null
    }

    ha_cp_to_container() {
        local src="$1"
        local dest="$2"
        docker cp "${src}" "${HA_CONTAINER}:${dest}"
    }

    ha_restart() {
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
    echo "== Sailing dashboard deploy (env: PROD) → ${DEPLOY_HOST} (container: ${HA_CONTAINER}) =="

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
        ${SSH} "sudo docker restart ${HA_CONTAINER}"
    }
fi

# ── 1. Detect dashboard storage key and convert source of truth ─────────────
STORAGE_KEYS=("lovelace.dashboard_sailing")
TMP_DASHBOARDS="$(mktemp /tmp/lovelace_dashboards.XXXXXX)"
trap 'rm -f "${TMP_DASHBOARDS}"' EXIT

if ha_cat "/config/.storage/lovelace_dashboards" > "${TMP_DASHBOARDS}" 2>/dev/null; then
    FOUND_KEY=$(python3 - "${TMP_DASHBOARDS}" <<'PYEOF'
import json
import sys

try:
    with open(sys.argv[1]) as f:
        items = json.load(f).get("data", {}).get("items", [])
    for item in items:
        if item.get("url_path") == "dashboard-sailing" and item.get("id"):
            print(f"lovelace.{item['id']}")
            sys.exit(0)
except Exception:
    pass
PYEOF
    )
    if [[ -n "${FOUND_KEY}" ]]; then
        STORAGE_KEYS=("${FOUND_KEY}")
        if [[ "${FOUND_KEY}" != "lovelace.dashboard_sailing" ]]; then
            STORAGE_KEYS+=("lovelace.dashboard_sailing")
        fi
    fi
fi

STORAGE_KEY="${STORAGE_KEYS[0]}"

TMP_JSON="$(mktemp /tmp/lovelace_dashboard_sailing.XXXXXX)"
trap 'rm -f "${TMP_JSON}" "${TMP_DASHBOARDS:-}"' EXIT

python3 - "${YAML_FILE}" "${TMP_JSON}" "${STORAGE_KEY}" <<'PYEOF'
import json
import sys
import yaml

yaml_path, json_path, storage_key = sys.argv[1:4]

with open(yaml_path) as f:
    config = yaml.safe_load(f)

storage_doc = {
    "version": 1,
    "minor_version": 1,
    "key": storage_key,
    "data": {"config": config},
}

with open(json_path, "w") as f:
    json.dump(storage_doc, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"Converted {yaml_path} -> {json_path} ({len(json.dumps(config))} bytes of config)")
PYEOF

# ── 2. Back up & pre-deploy diff ─────────────────────────────────────────────
REMOTE_PATH="/config/.storage/${STORAGE_KEY}"
BACKUP_NAME="${STORAGE_KEY}.$(date +%Y%m%d%H%M%S).bak"

echo "Fetching remote ${REMOTE_PATH} for diff/backup ..."
HAS_LIVE=1
if ! ha_cat "${REMOTE_PATH}" > "/tmp/${BACKUP_NAME}"; then
    echo "(Notice: live dashboard storage file not found inside container — creating fresh document)"
    HAS_LIVE=0
    echo '{"version": 1, "minor_version": 1, "key": "'"${STORAGE_KEY}"'", "data": {"config": {}}}' > "/tmp/${BACKUP_NAME}"
fi

if [[ "${HAS_LIVE}" == "1" && "${TARGET_ENV}" == "prod" ]]; then
    ${SCP} "/tmp/${BACKUP_NAME}" "${DEPLOY_HOST}:~/${BACKUP_NAME}" 2>/dev/null || true
fi

LIVE_AS_YAML="$(mktemp /tmp/lovelace_dashboard_sailing_live.XXXXXX)"
trap 'rm -f "${TMP_JSON}" "${LIVE_AS_YAML}"' EXIT
python3 - "/tmp/${BACKUP_NAME}" "${LIVE_AS_YAML}" <<'PYEOF'
import json
import sys
import yaml

backup_path, out_yaml_path = sys.argv[1:3]

try:
    with open(backup_path) as f:
        live_config = json.load(f)["data"].get("config", {})
except Exception:
    live_config = {}

with open(out_yaml_path, "w") as f:
    yaml.dump(live_config, f, sort_keys=False, allow_unicode=True, width=1000)
PYEOF

echo "--- pre-deploy diff (live HA config vs local build/dashboard-sailing.yaml) ---"
if diff -u "${LIVE_AS_YAML}" <(python3 -c "
import yaml
with open('${YAML_FILE}') as f:
    config = yaml.safe_load(f)
yaml.dump(config, __import__('sys').stdout, sort_keys=False, allow_unicode=True, width=1000)
"); then
    echo "(no differences — local file already matches live dashboard)"
else
    DIFF_STATUS=1
fi
echo "--------------------------------------------------------------------------"
rm -f "/tmp/${BACKUP_NAME}"

if [[ "${DIFF_STATUS:-0}" == "1" && "${REQUIRE_CLEAN_DIFF:-0}" == "1" ]]; then
    echo "ERROR: live HA config differs from ${YAML_FILE} and REQUIRE_CLEAN_DIFF=1 — aborting." >&2
    exit 1
fi

# ── 3. Upload new config ─────────────────────────────────────────────────────
echo "Uploading new dashboard config ..."
ha_cp_to_container "${TMP_JSON}" "${REMOTE_PATH}"

# ── 4. Restart HA ────────────────────────────────────────────────────────────
if [[ "${SKIP_RESTART:-0}" == "1" ]]; then
    echo "SKIP_RESTART=1 — not restarting ${HA_CONTAINER}."
else
    echo "Restarting ${HA_CONTAINER} ..."
    ha_restart
fi

echo "Done. Dashboard updated in container ${HA_CONTAINER}."
