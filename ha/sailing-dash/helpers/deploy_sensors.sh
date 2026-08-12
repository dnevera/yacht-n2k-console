#!/usr/bin/env bash
# deploy_sensors.sh — merge build/sensors-sailing.yaml and build/automations-sailing.yaml
# into the live Home Assistant instance's /config/configuration.yaml (Stage or Prod).
#
# USAGE
#   ./deploy_sensors.sh --stage                  # the "stage" target profile
#   ./deploy_sensors.sh --prod [user@host]       # the "prod" target profile
#   ./deploy_sensors.sh --target <profile>       # any profile from .env (e.g. stage-pi5)
set -euo pipefail

# This script lives in ha/sailing-dash/helpers/; build/ and local-ha/ belong to
# the subproject root one level up.
HELPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "${HELPERS_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck source=lib/ha_target.sh
source "${HELPERS_DIR}/lib/ha_target.sh"

SENSORS_FILE="${SCRIPT_DIR}/build/sensors-sailing.yaml"
AUTOMATIONS_FILE="${SCRIPT_DIR}/build/automations-sailing.yaml"
HELPERS_FILE="${SCRIPT_DIR}/build/helpers-sailing.yaml"

# ── Parse flags ──────────────────────────────────────────────────────────────
TARGET_ENV="stage"
HOST_ARG=""

while [[ $# -gt 0 ]]; do
    arg="$1"
    case "${arg}" in
        --stage)     TARGET_ENV="stage" ;;
        --prod)      TARGET_ENV="prod" ;;
        --target)    TARGET_ENV="${2:?--target needs a profile name}"; shift ;;
        --target=*)  TARGET_ENV="${arg#*=}" ;;
        *)
            HOST_ARG="${arg}"
            if [[ "${TARGET_ENV}" == "stage" ]]; then
                TARGET_ENV="prod"
            fi
            ;;
    esac
    shift
done

ha_target_init "${TARGET_ENV}" "${HOST_ARG}"
echo "== Sailing sensors deploy (profile: ${TARGET_ENV}) → ${HA_HOST} (container: ${HA_CONTAINER}, transport: ${HA_TRANSPORT}) =="

REMOTE_PATH="/config/configuration.yaml"

# ── 1. Auto-discover NMEA 2000 sensors & compile build artifacts ───────────
TMP_DIR="$(mktemp -d /tmp/sailing_sensors_deploy.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT
REMOTE_CFG="${TMP_DIR}/configuration.yaml"
MERGED_CFG="${TMP_DIR}/configuration.merged.yaml"
ENTITY_REG_TMP="${TMP_DIR}/core.entity_registry"
BACKUP_NAME="configuration.yaml.$(date +%Y%m%d%H%M%S).bak"

echo "== Step 0: Auto-discovering NMEA 2000 sensors =="
# NO `|| true` HERE. It used to swallow every discovery failure, so a deploy
# happily shipped the PREVIOUS binding (or the generic fallbacks) and looked
# successful while every sensor.boat_* stayed unavailable on the target.
# The url/token of THIS profile are passed along on purpose: the registry cannot
# tell a live entity from a leftover duplicate of the same PGN — only the live
# states can, and that is what --strict validates.
# macOS ships bash 3.2, where an empty array expansion trips `set -u`; keep the
# array non-empty from the start.
MAP_ARGS=("--strict")
HAVE_API=0
if [[ -n "${HA_URL}" && -n "${HA_TOKEN}" ]]; then
    MAP_ARGS+=("--api-url" "${HA_URL}" "--api-token" "${HA_TOKEN}")
    HAVE_API=1
fi
if ha_cat "/config/.storage/core.entity_registry" > "${ENTITY_REG_TMP}" 2>/dev/null && [[ -s "${ENTITY_REG_TMP}" ]]; then
    MAP_ARGS+=("--entity-registry" "${ENTITY_REG_TMP}")
elif [[ "${HAVE_API}" == "0" ]]; then
    MAP_ARGS+=("--config-dir" "${SCRIPT_DIR}/local-ha/config")
fi
if ! python3 "${HELPERS_DIR}/map_nmea_sensors.py" "${MAP_ARGS[@]}"; then
    echo "ERROR: NMEA 2000 auto-discovery found no live entities for the required bus values." >&2
    echo "       Check the gateway/integration on ${HA_HOST} (sensor.nmea_2000_gateway_state," >&2
    echo "       messages per minute) and that stale duplicate entities are not the only match." >&2
    exit 1
fi

# This build.py call is NOT the duplicate one: map_nmea_sensors.py has just
# rewritten src/yaml/sensors/derived_n2k.yaml with the entity ids discovered on
# THIS vessel, so the sensor artifacts must be recompiled from it.
echo "== Rebuilding artifacts from the freshly discovered sensor mapping =="
python3 "${HELPERS_DIR}/build.py"

echo "Fetching current ${REMOTE_PATH} ..."
if ! ha_cat "${REMOTE_PATH}" > "${REMOTE_CFG}"; then
    echo "(Notice: ${REMOTE_PATH} not found — creating base configuration)"
    if [[ -f "${SCRIPT_DIR}/local-ha/config/configuration.yaml" ]]; then
        cat "${SCRIPT_DIR}/local-ha/config/configuration.yaml" > "${REMOTE_CFG}"
    else
        echo "default_config:" > "${REMOTE_CFG}"
    fi
fi


# ── 2. Merge sensors-sailing.yaml into configuration.yaml (idempotent) ──────
python3 - "${REMOTE_CFG}" "${SENSORS_FILE}" "${MERGED_CFG}" "${HELPERS_FILE}" <<'PYEOF'
import copy
import os
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
helpers_path = sys.argv[4] if len(sys.argv) > 4 else None

with open(remote_cfg_path) as f:
    remote_cfg = yaml.safe_load(f) or {}

original_cfg = copy.deepcopy(remote_cfg)

with open(sensors_path) as f:
    sailing_cfg = yaml.safe_load(f) or {}

def item_uid(entry):
    if not isinstance(entry, dict):
        return None
    if "unique_id" in entry:
        return entry["unique_id"]
    for subkey in ("sensor", "binary_sensor", "switch", "device_tracker"):
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

def merge_helper_mapping(remote_cfg, helper_cfg, key):
    """Merge an input helper domain (`input_select:` & co), keyed by object id.

    Unlike `rest:`/`template:` these domains are mappings, not lists. A target
    that keeps its helpers in a separate file (`input_select: !include ...`) is
    left completely alone - overwriting the tag would drop the crew's own
    helpers.
    """
    incoming = helper_cfg.get(key)
    if not isinstance(incoming, dict):
        return
    existing = remote_cfg.get(key)
    if existing is not None and not isinstance(existing, dict):
        print(f"[{key}] Skipped: target keeps this domain in a separate file")
        return
    target = existing if isinstance(existing, dict) else {}
    for obj_id, entry in incoming.items():
        action = "Replaced" if obj_id in target else "Added"
        target[obj_id] = entry
        print(f"[{key}] {action} helper '{obj_id}'")
    remote_cfg[key] = target

if helpers_path and os.path.exists(helpers_path):
    with open(helpers_path) as f:
        helper_cfg = yaml.safe_load(f) or {}
    for helper_key in sorted(helper_cfg):
        merge_helper_mapping(remote_cfg, helper_cfg, helper_key)

merged_text = yaml.dump(remote_cfg, sort_keys=False, allow_unicode=True, width=1000)
with open(merged_cfg_path, "w") as f:
    f.write(merged_text)

# The merge is idempotent, so re-running a deploy usually produces exactly the
# configuration that is already live. Tell the shell so it can skip both the
# upload and the Home Assistant restart instead of bouncing HA for nothing.
# The comparison is on the SERIALIZED form, not on the object graphs: HA tags
# (!include, !secret, ...) are loaded as _HaTag instances, which have no
# __eq__, so every object-level comparison would report a difference.
original_text = yaml.dump(original_cfg, sort_keys=False, allow_unicode=True, width=1000)
if merged_text == original_text:
    open(merged_cfg_path + ".unchanged", "w").close()
    print("configuration.yaml already contains this exact sensor set")

PYEOF

# ── 2b. Merge automations-sailing.yaml into the target's automations.yaml ────
# Home Assistant's default configuration.yaml keeps automations in their own
# `automation: !include automations.yaml` list, so they can NOT be merged into
# configuration.yaml above: doing so would make HA refuse to start with a
# duplicate `automation` key. Without this step the compiled automations were
# built but never delivered, which is why switching the forecast model did not
# re-poll the REST sensors (the forecast then only refreshed on scan_interval).
AUTOMATIONS_REMOTE="/config/automations.yaml"
if [[ -f "${AUTOMATIONS_FILE}" ]]; then
    REMOTE_AUTO="${TMP_DIR}/automations.yaml"
    MERGED_AUTO="${TMP_DIR}/automations.merged.yaml"
    ha_cat "${AUTOMATIONS_REMOTE}" > "${REMOTE_AUTO}" 2>/dev/null || echo "[]" > "${REMOTE_AUTO}"

    python3 - "${REMOTE_AUTO}" "${AUTOMATIONS_FILE}" "${MERGED_AUTO}" <<'PYEOF'
import sys
import yaml

remote_path, ours_path, merged_path = sys.argv[1:4]

with open(remote_path) as f:
    remote = yaml.safe_load(f) or []
if not isinstance(remote, list):
    # A target that keeps automations in some other shape is left alone.
    print("[automations] Skipped: target automations.yaml is not a list")
    sys.exit(0)

with open(ours_path) as f:
    ours = yaml.safe_load(f) or []

original = yaml.dump(remote, sort_keys=False, allow_unicode=True, width=1000)

# Keyed by `id`, so a crew-authored automation is never touched and re-running
# the deploy replaces our own entry instead of appending a duplicate.
by_id = {a.get("id"): i for i, a in enumerate(remote) if isinstance(a, dict) and a.get("id")}
for entry in ours:
    auto_id = entry.get("id")
    if auto_id in by_id:
        remote[by_id[auto_id]] = entry
        print(f"[automations] Replaced '{auto_id}'")
    else:
        by_id[auto_id] = len(remote)
        remote.append(entry)
        print(f"[automations] Added '{auto_id}'")

merged = yaml.dump(remote, sort_keys=False, allow_unicode=True, width=1000)
with open(merged_path, "w") as f:
    f.write(merged)
if merged == original:
    open(merged_path + ".unchanged", "w").close()
    print("[automations] already up to date")
PYEOF

    if [[ -f "${MERGED_AUTO}" && ! -f "${MERGED_AUTO}.unchanged" ]]; then
        echo "Uploading merged automations.yaml ..."
        ha_cp_to_container "${MERGED_AUTO}" "${AUTOMATIONS_REMOTE}"
        if [[ -n "${SAILING_CHANGE_FLAG:-}" ]]; then
            echo "automations" >> "${SAILING_CHANGE_FLAG}"
        fi
        AUTOMATIONS_CHANGED=1
    fi
fi

# ── 3. Upload merged configuration (only when the merge changed something) ──
if [[ -f "${MERGED_CFG}.unchanged" && "${HA_FORCE_DELIVERY:-0}" != "1" ]]; then
    echo "= configuration.yaml unchanged — nothing uploaded."
    # Freshly delivered automations still need HA to reload them, so only a
    # deploy that changed nothing at all may skip the restart.
    if [[ "${AUTOMATIONS_CHANGED:-0}" == "1" && "${SKIP_RESTART:-0}" != "1" ]]; then
        echo "Restarting ${HA_CONTAINER} (automations changed) ..."
        ha_restart
    else
        echo "${HA_CONTAINER} not restarted."
    fi
    echo "Done. Sensors already up to date in container ${HA_CONTAINER}."
    exit 0
fi

# Only a deploy that really rewrites configuration.yaml needs a rollback point.
if [[ "${HA_TRANSPORT}" == "ssh-docker" ]]; then
    ${SCP} "${REMOTE_CFG}" "${DEPLOY_HOST}:~/${BACKUP_NAME}" 2>/dev/null || true
fi

echo "Uploading merged configuration.yaml ..."
ha_cp_to_container "${MERGED_CFG}" "${REMOTE_PATH}"

# deploy.sh restarts Home Assistant once at the end of the pipeline; this tells
# it that a restart is actually warranted (see SAILING_CHANGE_FLAG).
if [[ -n "${SAILING_CHANGE_FLAG:-}" ]]; then
    echo "sensors" >> "${SAILING_CHANGE_FLAG}"
fi

# ── 4. Restart HA ────────────────────────────────────────────────────────────
if [[ "${SKIP_RESTART:-0}" == "1" ]]; then
    echo "SKIP_RESTART=1 — not restarting ${HA_CONTAINER}."
else
    echo "Restarting ${HA_CONTAINER} ..."
    ha_restart
fi

echo "Done. Sensors updated in container ${HA_CONTAINER}."
