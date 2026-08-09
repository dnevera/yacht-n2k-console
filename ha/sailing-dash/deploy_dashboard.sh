#!/usr/bin/env bash
# deploy_dashboard.sh — push ha/sailing-dash/dashboard-sailing.yaml back into
# the live Home Assistant instance's storage-mode Lovelace config.
#
# WHY THIS EXISTS
#   The "Sailing" dashboard on bumblebee's HA (url_path: dashboard-sailing) is
#   a storage-mode dashboard (created via the UI, not YAML mode), so its
#   config lives inside the HA state file .storage/lovelace.dashboard_sailing
#   inside the `homeassistant` docker container — there is no plain on-disk
#   YAML file HA reads directly. This script round-trips our YAML source of
#   truth (dashboard-sailing.yaml) back into that JSON storage file so the
#   dashboard can be reviewed/edited/version-controlled here instead of only
#   via the HA UI.
#
# USAGE
#   ./deploy_dashboard.sh                 # uses deploy.conf (../../deploy.conf)
#   ./deploy_dashboard.sh user@host       # override target host
#
# REQUIRES
#   - ../../deploy.conf (or an explicit host argument) — see deploy.conf.template
#   - python3 + PyYAML on THIS machine (to convert YAML -> JSON before upload)
#   - passwordless sudo on the remote host for `docker exec`/`docker cp`
#     (same requirement as deploy.sh:patch_ha())
#
# SAFETY
#   - Takes a timestamped backup of the existing .storage file on the remote
#     host before overwriting it (kept in $HOME on the remote host).
#   - Restarts nothing — HA picks up storage-mode dashboard changes on next
#     browser reload of the dashboard, no HA restart required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
YAML_FILE="${SCRIPT_DIR}/dashboard-sailing.yaml"
STORAGE_KEY="lovelace.dashboard_sailing"

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

echo "== Sailing dashboard deploy → ${DEPLOY_HOST} (container: ${HA_CONTAINER}) =="

# ── 1. Convert our YAML source of truth into the storage JSON shape ─────────
TMP_JSON="$(mktemp /tmp/lovelace_dashboard_sailing.XXXXXX.json)"
trap 'rm -f "${TMP_JSON}"' EXIT

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

# ── 2. Locate the storage file inside the container + back it up ────────────
REMOTE_PATH="/config/.storage/${STORAGE_KEY}"
BACKUP_NAME="${STORAGE_KEY}.$(date +%Y%m%d%H%M%S).bak"

echo "Backing up remote ${REMOTE_PATH} -> ~/${BACKUP_NAME} ..."
${SSH} "sudo docker exec ${HA_CONTAINER} cat ${REMOTE_PATH}" > "/tmp/${BACKUP_NAME}" \
    || { echo "ERROR: could not read current dashboard config on remote — aborting." >&2; exit 1; }
${SCP} "/tmp/${BACKUP_NAME}" "${DEPLOY_HOST}:~/${BACKUP_NAME}"
rm -f "/tmp/${BACKUP_NAME}"

# ── 3. Upload the new config and copy it into the container ─────────────────
echo "Uploading new dashboard config ..."
${SCP} "${TMP_JSON}" "${DEPLOY_HOST}:/tmp/${STORAGE_KEY}.json"
${SSH} "sudo docker cp /tmp/${STORAGE_KEY}.json ${HA_CONTAINER}:${REMOTE_PATH} \
    && rm -f /tmp/${STORAGE_KEY}.json"

echo "Done. Reload http://<host>:8123/dashboard-sailing/ in the browser to see changes."
echo "Remote backup kept at ~/${BACKUP_NAME} on ${DEPLOY_HOST} (restore via: sudo docker cp ~/${BACKUP_NAME} ${HA_CONTAINER}:${REMOTE_PATH})"
