#!/usr/bin/env bash
# deploy_sensors.sh — merge build/sensors-sailing.yaml and build/automations-sailing.yaml
# into the live Home Assistant instance's /config/configuration.yaml (Stage or Prod).
#
# USAGE
#   ./deploy_sensors.sh --stage               # deploy to local Stage HA container (local-ha)
#   ./deploy_sensors.sh --prod [user@host]    # deploy to production HA host via SSH (bumblebee)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "== Running build.py before deploy_sensors =="
python3 "${SCRIPT_DIR}/build.py"

SENSORS_FILE="${SCRIPT_DIR}/build/sensors-sailing.yaml"
AUTOMATIONS_FILE="${SCRIPT_DIR}/build/automations-sailing.yaml"

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
    echo "== Sailing sensors deploy (env: STAGE) → Container: ${HA_CONTAINER} =="

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
    echo "== Sailing sensors deploy (env: PROD) → ${DEPLOY_HOST} (container: ${HA_CONTAINER}) =="

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

REMOTE_PATH="/config/configuration.yaml"

# ── 1. Fetch current remote configuration.yaml + back it up ─────────────────
TMP_DIR="$(mktemp -d /tmp/sailing_sensors_deploy.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT
REMOTE_CFG="${TMP_DIR}/configuration.yaml"
MERGED_CFG="${TMP_DIR}/configuration.merged.yaml"
BACKUP_NAME="configuration.yaml.$(date +%Y%m%d%H%M%S).bak"

echo "Fetching current ${REMOTE_PATH} ..."
if ! ha_cat "${REMOTE_PATH}" > "${REMOTE_CFG}"; then
    echo "(Notice: ${REMOTE_PATH} not found — creating base configuration)"
    if [[ -f "${SCRIPT_DIR}/local-ha/config/configuration.yaml" ]]; then
        cat "${SCRIPT_DIR}/local-ha/config/configuration.yaml" > "${REMOTE_CFG}"
    else
        echo "default_config:" > "${REMOTE_CFG}"
    fi
fi

if [[ "${TARGET_ENV}" == "prod" ]]; then
    ${SCP} "${REMOTE_CFG}" "${DEPLOY_HOST}:~/${BACKUP_NAME}" 2>/dev/null || true
fi

# ── 2. Merge sensors-sailing.yaml into configuration.yaml (idempotent) ──────
python3 - "${REMOTE_CFG}" "${SENSORS_FILE}" "${MERGED_CFG}" <<'PYEOF'
import sys
import yaml

class _HaTag(yaml.YAMLObject):
    def __init__(self, tag, value):
        self.tag = tag
        self.value = value

    @classmethod
    def to_yaml(cls, dumper, data):
        if isinstance(data.value, str):
            return dumper.represent_scalar(data.tag, data.value)
        elif isinstance(data.value, list):
            return dumper.represent_sequence(data.tag, data.value)
        else:
            return dumper.represent_mapping(data.tag, data.value)

def _ha_constructor(loader, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return _HaTag(node.tag, value)

for tag in ("!include", "!secret", "!include_dir_list", "!include_dir_named", "!include_dir_merge_list", "!include_dir_merge_named", "!env_var"):
    yaml.add_constructor(tag, _ha_constructor, Loader=yaml.SafeLoader)
    yaml.add_constructor(tag, _ha_constructor, Loader=yaml.FullLoader)
yaml.add_representer(_HaTag, _HaTag.to_yaml)

remote_cfg_path, sensors_path, merged_cfg_path = sys.argv[1:4]

with open(remote_cfg_path) as f:
    remote_cfg = yaml.safe_load(f) or {}

with open(sensors_path) as f:
    sailing_cfg = yaml.safe_load(f) or {}

def item_uid(entry):
    if not isinstance(entry, dict):
        return None
    if "unique_id" in entry:
        return entry["unique_id"]
    for subkey in ("sensor", "binary_sensor", "switch"):
        subs = entry.get(subkey)
        if isinstance(subs, list) and subs and isinstance(subs[0], dict) and "unique_id" in subs[0]:
            return subs[0]["unique_id"]
    return None

def merge_section(remote_cfg, sailing_cfg, key):
    sailing_list = sailing_cfg.get(key, [])
    if not isinstance(sailing_list, list):
        sailing_list = [sailing_list] if sailing_list else []
    remote_list = remote_cfg.get(key, [])
    if not isinstance(remote_list, list):
        remote_list = [remote_list] if remote_list else []

    existing_by_uid = {item_uid(e): i for i, e in enumerate(remote_list) if item_uid(e) is not None}

    for new_entry in sailing_list:
        uid = item_uid(new_entry)
        if uid and uid in existing_by_uid:
            remote_list[existing_by_uid[uid]] = new_entry
            print(f"[{key}] Replaced existing entry with unique_id '{uid}'")
        else:
            remote_list.append(new_entry)
            print(f"[{key}] Appended new entry (unique_id: {uid})")

    remote_cfg[key] = remote_list

merge_section(remote_cfg, sailing_cfg, "rest")
merge_section(remote_cfg, sailing_cfg, "template")

with open(merged_cfg_path, "w") as f:
    yaml.dump(remote_cfg, f, sort_keys=False, allow_unicode=True, width=1000)

PYEOF

# ── 3. Upload merged configuration ──────────────────────────────────────────
echo "Uploading merged configuration.yaml ..."
ha_cp_to_container "${MERGED_CFG}" "${REMOTE_PATH}"

# ── 4. Restart HA ────────────────────────────────────────────────────────────
if [[ "${SKIP_RESTART:-0}" == "1" ]]; then
    echo "SKIP_RESTART=1 — not restarting ${HA_CONTAINER}."
else
    echo "Restarting ${HA_CONTAINER} ..."
    ha_restart
fi

echo "Done. Sensors updated in container ${HA_CONTAINER}."
