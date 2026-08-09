#!/usr/bin/env bash
# deploy_sensors.sh — merge sensors-sailing.yaml (open-meteo wind forecast +
# derived template sensors/device_tracker) into the live Home Assistant
# instance's /config/configuration.yaml, then restart HA core.
#
# WHY THIS EXISTS
#   dashboard-sailing.yaml alone is not enough to reproduce the dashboard on
#   another HA instance: several of its entities (sensor.wind_forecast_flat,
#   sensor.barometer_mmhg, sensor.boat_latitude/boat_longitude) are NOT
#   published by ydnu02_tcp_gateway — they are defined directly in HA's
#   configuration.yaml (a `rest:` sensor polling api.open-meteo.com, plus a
#   few `template:` sensors/device_tracker deriving from N2K sensors). This
#   script deploys that missing piece.
#
# USAGE
#   ./deploy_sensors.sh                 # uses deploy.conf (../../deploy.conf)
#   ./deploy_sensors.sh user@host       # override target host
#
# REQUIRES
#   - ../../deploy.conf (or an explicit host argument) — see deploy.conf.template
#   - python3 + PyYAML on THIS machine (to merge YAML configs before upload)
#   - passwordless sudo on the remote host for `docker exec`/`docker cp`/
#     `docker restart` (same requirement as deploy.sh:patch_ha())
#
# SAFETY
#   - Idempotent: matches existing `rest:`/`template:` list entries by their
#     `unique_id` (falls back to `sensor[0].unique_id` for the rest: block)
#     and skips/replaces rather than duplicating them on re-run.
#   - Takes a timestamped backup of the remote configuration.yaml before
#     writing, kept in $HOME on the remote host.
#   - UNLIKE deploy_dashboard.sh, this DOES require restarting Home Assistant
#     core for the new sensors/services to load (configuration.yaml is not
#     hot-reloadable the way storage-mode dashboards are). This script
#     restarts the `homeassistant` docker container at the end.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SENSORS_FILE="${SCRIPT_DIR}/sensors-sailing.yaml"

# ── Resolve target host + HA container name ─────────────────────────────────
if [[ -n "${1:-}" ]]; then
    DEPLOY_HOST="$1"
    HA_CONTAINER="${HA_CONTAINER:-homeassistant}"
elif [[ -f "${PROJECT_ROOT}/deploy.conf" ]]; then
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/deploy.conf"
else
    echo "ERROR: no host given and ${PROJECT_ROOT}/deploy.conf not found." >&2
    echo "       Usage: $0 [user@host]" >&2
    exit 1
fi

SSH="ssh -o ConnectTimeout=8 ${DEPLOY_HOST}"
SCP="scp -q"
REMOTE_PATH="/config/configuration.yaml"

echo "== Sailing sensors deploy → ${DEPLOY_HOST} (container: ${HA_CONTAINER}) =="

# ── 1. Fetch current remote configuration.yaml + back it up ─────────────────
TMP_DIR="$(mktemp -d /tmp/sailing_sensors_deploy.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT
REMOTE_CFG="${TMP_DIR}/configuration.yaml"
MERGED_CFG="${TMP_DIR}/configuration.merged.yaml"
BACKUP_NAME="configuration.yaml.$(date +%Y%m%d%H%M%S).bak"

echo "Fetching current ${REMOTE_PATH} ..."
${SSH} "sudo docker exec ${HA_CONTAINER} cat ${REMOTE_PATH}" > "${REMOTE_CFG}" \
    || { echo "ERROR: could not read remote configuration.yaml — aborting." >&2; exit 1; }

echo "Backing up remote ${REMOTE_PATH} -> ~/${BACKUP_NAME} ..."
${SCP} "${REMOTE_CFG}" "${DEPLOY_HOST}:~/${BACKUP_NAME}"

# ── 2. Merge sensors-sailing.yaml into the fetched config (idempotent) ──────
python3 - "${REMOTE_CFG}" "${SENSORS_FILE}" "${MERGED_CFG}" <<'PYEOF'
import sys

import yaml


class _HaTag(yaml.YAMLObject):
    """Opaque passthrough for HA's custom YAML tags (!include, !secret,
    !include_dir_merge_named, etc.) — configuration.yaml relies on these at
    the top level (e.g. `themes: !include_dir_merge_named themes`,
    `automation: !include automations.yaml`), which plain yaml.safe_load()
    cannot parse (ConstructorError: could not determine a constructor for
    the tag). We round-trip them unchanged instead of trying to resolve
    their contents, since this script never touches those keys."""

    def __init__(self, tag, value):
        self.tag = tag
        self.value = value

    @classmethod
    def from_yaml(cls, loader, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node)
        else:
            value = loader.construct_mapping(node)
        return cls(node.tag, value)

    @classmethod
    def to_yaml(cls, dumper, data):
        if isinstance(data.value, str):
            return dumper.represent_scalar(data.tag, data.value)
        if isinstance(data.value, list):
            return dumper.represent_sequence(data.tag, data.value)
        return dumper.represent_mapping(data.tag, data.value)


class HaSafeLoader(yaml.SafeLoader):
    pass


class HaDumper(yaml.SafeDumper):
    pass


for _tag in (
    "!include", "!secret", "!include_dir_list", "!include_dir_named",
    "!include_dir_merge_list", "!include_dir_merge_named",
):
    HaSafeLoader.add_constructor(_tag, _HaTag.from_yaml)
HaDumper.add_representer(_HaTag, _HaTag.to_yaml)

remote_path, sensors_path, out_path = sys.argv[1:4]

with open(remote_path) as f:
    remote = yaml.load(f, Loader=HaSafeLoader) or {}
with open(sensors_path) as f:
    incoming = yaml.load(f, Loader=HaSafeLoader) or {}


def merge_platform_list(existing, new_items):
    """Merge a list of `rest:`/`template:` platform dicts, matching entries by
    unique_id (searched inside sensor:/device_tracker:/etc sub-lists) so
    re-running this script updates in place instead of duplicating."""
    def unique_ids(entry):
        ids = set()
        for _platform_key, items in entry.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "unique_id" in item:
                        ids.add(item["unique_id"])
        return ids

    existing = list(existing or [])
    for new_entry in new_items:
        new_ids = unique_ids(new_entry)
        replaced = False
        for i, old_entry in enumerate(existing):
            if new_ids and unique_ids(old_entry) & new_ids:
                existing[i] = new_entry
                replaced = True
                break
        if not replaced:
            existing.append(new_entry)
    return existing


for key, value in incoming.items():
    if isinstance(value, list) and isinstance(remote.get(key), list):
        remote[key] = merge_platform_list(remote[key], value)
    else:
        remote[key] = value

with open(out_path, "w") as f:
    yaml.dump(remote, f, Dumper=HaDumper, sort_keys=False, allow_unicode=True,
              width=1000, default_flow_style=False)

print(f"Merged {sensors_path} into {remote_path} -> {out_path}")
PYEOF

echo "--- diff (remote current vs merged) ---"
diff -u "${REMOTE_CFG}" "${MERGED_CFG}" || true
echo "----------------------------------------"

# ── 3. Upload merged config and restart HA ───────────────────────────────────
echo "Uploading merged configuration.yaml ..."
${SCP} "${MERGED_CFG}" "${DEPLOY_HOST}:/tmp/configuration.merged.yaml"
${SSH} "sudo docker cp /tmp/configuration.merged.yaml ${HA_CONTAINER}:${REMOTE_PATH} \
    && rm -f /tmp/configuration.merged.yaml"

echo "Restarting ${HA_CONTAINER} to load new sensors/services ..."
${SSH} "sudo docker restart ${HA_CONTAINER}"

echo "Done. sensor.wind_forecast_flat and friends should appear within ~30s of HA restart."
echo "Remote backup kept at ~/${BACKUP_NAME} on ${DEPLOY_HOST}"
echo "(restore via: sudo docker cp ~/${BACKUP_NAME} ${HA_CONTAINER}:${REMOTE_PATH} && sudo docker restart ${HA_CONTAINER})"
