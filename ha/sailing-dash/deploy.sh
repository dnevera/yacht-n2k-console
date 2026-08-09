#!/usr/bin/env bash
# deploy.sh — single entry point to deploy EVERYTHING the Sailing dashboard
# needs onto a Home Assistant instance: manually-installed custom card JS
# resources, sensors-sailing.yaml (rest:/template: entries), and
# dashboard-sailing.yaml (the Lovelace dashboard itself).
#
# WHY THIS EXISTS
#   Before this script, each piece was deployed with a separate ad-hoc
#   mechanism: dashboard/sensors had their own scripts (deploy_dashboard.sh,
#   deploy_sensors.sh), but manually-installed card bundles (windrose-card,
#   plotly-graph-card — anything NOT available via HACS) were pushed by
#   one-off `scp`/`docker cp` commands typed directly into the terminal.
#   That's error-prone and undocumented/unrepeatable. This script is now the
#   ONLY supported way to deploy any part of the Sailing dashboard stack —
#   do not `scp`/`docker cp` files onto the HA host by hand.
#
# USAGE
#   ./deploy.sh --install [user@host]   # fresh HA instance: resources + sensors + dashboard
#   ./deploy.sh --update  [user@host]   # default: same three steps, for an existing install
#   ./deploy.sh --resources-only [user@host]   # just sync manually-installed card JS + resource list
#   ./deploy.sh --dashboard-only [user@host]   # just dashboard-sailing.yaml (calls deploy_dashboard.sh)
#   ./deploy.sh --sensors-only   [user@host]   # just sensors-sailing.yaml (calls deploy_sensors.sh)
#   (no flag = --update)
#
# REQUIRES
#   - ../../deploy.conf (or an explicit host argument) — see deploy.conf.template
#   - python3 + PyYAML on THIS machine
#   - passwordless sudo on the remote host for `docker exec`/`docker cp`/`docker restart`
#   - ha/sailing-dash/local-preview/vendor/*.js present locally for
#     --install/--resources-only (run local-preview/fetch-vendor.sh first if missing)
#
# SAFETY
#   --install/--resources-only/--resources-only are idempotent: the manually-
#   installed resource list is matched by `url` (ignoring any `?v=`/`?hacstag=`
#   query string) so re-running never duplicates entries, only updates the
#   `.js` file + bumps the registered url if the version changed. HACS-managed
#   resources (card-mod, compass-card, apexcharts-card) are NEVER touched by
#   this script — those stay HACS's responsibility (see lovelace-resources.yaml).
#   --dashboard-only/--sensors-only delegate to the existing
#   deploy_dashboard.sh/deploy_sensors.sh, which already do their own
#   backup + pre-deploy-diff + restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENDOR_DIR="${SCRIPT_DIR}/local-preview/vendor"
RESOURCES_FILE="${SCRIPT_DIR}/lovelace-resources.yaml"

# ── Parse flags ──────────────────────────────────────────────────────────────
MODE="update"
HOST_ARG=""
for arg in "$@"; do
    case "${arg}" in
        --install)        MODE="install" ;;
        --update)         MODE="update" ;;
        --resources-only) MODE="resources-only" ;;
        --dashboard-only) MODE="dashboard-only" ;;
        --sensors-only)   MODE="sensors-only" ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) HOST_ARG="${arg}" ;;
    esac
done

# ── Resolve target host + HA container name ─────────────────────────────────
if [[ -n "${HOST_ARG}" ]]; then
    DEPLOY_HOST="${HOST_ARG}"
    HA_CONTAINER="${HA_CONTAINER:-homeassistant}"
elif [[ -f "${PROJECT_ROOT}/deploy.conf" ]]; then
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/deploy.conf"
else
    echo "ERROR: no host given and ${PROJECT_ROOT}/deploy.conf not found." >&2
    echo "       Usage: $0 [--install|--update|--resources-only|--dashboard-only|--sensors-only] [user@host]" >&2
    exit 1
fi

SSH="ssh -o ConnectTimeout=8 ${DEPLOY_HOST}"
SCP="scp -q"

echo "== Sailing dashboard deploy (mode: ${MODE}) → ${DEPLOY_HOST} (container: ${HA_CONTAINER}) =="

deploy_resources() {
    echo "-- Step: manually-installed card resources --"

    # 1. Fetch current resource registry from the remote host.
    TMP_DIR="$(mktemp -d /tmp/sailing_resources_deploy.XXXXXX)"
    trap 'rm -rf "${TMP_DIR}"' RETURN
    REMOTE_RES="${TMP_DIR}/lovelace_resources.json"
    MERGED_RES="${TMP_DIR}/lovelace_resources.merged.json"
    BACKUP_NAME="lovelace_resources.$(date +%Y%m%d%H%M%S).bak"

    echo "Fetching current .storage/lovelace_resources ..."
    ${SSH} "sudo docker exec ${HA_CONTAINER} cat /config/.storage/lovelace_resources" > "${REMOTE_RES}" \
        || { echo "ERROR: could not read remote lovelace_resources — aborting." >&2; exit 1; }
    ${SCP} "${REMOTE_RES}" "${DEPLOY_HOST}:~/${BACKUP_NAME}"

    # 2. For every "manually installed" (/local/*.js) entry in
    #    lovelace-resources.yaml, upload the matching vendor bundle to
    #    /config/www/ and merge the resource entry (matched by base URL,
    #    ignoring the ?v=/?hacstag= query string) into the fetched registry.
    python3 - "${RESOURCES_FILE}" "${REMOTE_RES}" "${MERGED_RES}" <<'PYEOF'
import json
import sys
import uuid
from urllib.parse import urlsplit

resources_yaml, remote_json, out_json = sys.argv[1:4]

import yaml

with open(resources_yaml) as f:
    wanted = (yaml.safe_load(f) or {}).get("resources", [])
wanted = [r for r in wanted if urlsplit(r["url"]).path.startswith("/local/")]

with open(remote_json) as f:
    registry = json.load(f)

items = registry["data"]["items"]


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
    to_upload.append(filename)

with open(out_json, "w") as f:
    json.dump(registry, f)

# Tell the shell (via stdout marker) which vendor files to upload.
with open(out_json + ".files", "w") as f:
    f.write("\n".join(to_upload))
PYEOF

    # 3. Upload each referenced vendor .js file to /config/www/.
    while IFS= read -r filename; do
        [[ -z "${filename}" ]] && continue
        LOCAL_JS="${VENDOR_DIR}/${filename}"
        if [[ ! -f "${LOCAL_JS}" ]]; then
            echo "ERROR: ${LOCAL_JS} not found — run local-preview/fetch-vendor.sh first." >&2
            exit 1
        fi
        echo "Uploading ${filename} -> ${HA_CONTAINER}:/config/www/${filename} ..."
        ${SCP} "${LOCAL_JS}" "${DEPLOY_HOST}:/tmp/${filename}"
        ${SSH} "sudo docker cp /tmp/${filename} ${HA_CONTAINER}:/config/www/${filename} && rm -f /tmp/${filename}"
    done < "${MERGED_RES}.files"

    # 4. Upload the merged resource registry.
    echo "Uploading merged lovelace_resources ..."
    ${SCP} "${MERGED_RES}" "${DEPLOY_HOST}:/tmp/lovelace_resources.json"
    ${SSH} "sudo docker cp /tmp/lovelace_resources.json ${HA_CONTAINER}:/config/.storage/lovelace_resources \
        && rm -f /tmp/lovelace_resources.json"

    echo "Resources deployed. Remote backup kept at ~/${BACKUP_NAME} on ${DEPLOY_HOST}."
    echo "(a HA restart is required for new resources to be served — done automatically unless SKIP_RESTART=1)"
    if [[ "${SKIP_RESTART:-0}" != "1" ]]; then
        ${SSH} "sudo docker restart ${HA_CONTAINER}"
    fi
}

case "${MODE}" in
    install)
        deploy_resources
        SKIP_RESTART=1 "${SCRIPT_DIR}/deploy_sensors.sh" "${DEPLOY_HOST}"
        "${SCRIPT_DIR}/deploy_dashboard.sh" "${DEPLOY_HOST}"
        ;;
    update)
        deploy_resources
        SKIP_RESTART=1 "${SCRIPT_DIR}/deploy_sensors.sh" "${DEPLOY_HOST}"
        "${SCRIPT_DIR}/deploy_dashboard.sh" "${DEPLOY_HOST}"
        ;;
    resources-only)
        deploy_resources
        ;;
    dashboard-only)
        "${SCRIPT_DIR}/deploy_dashboard.sh" "${DEPLOY_HOST}"
        ;;
    sensors-only)
        "${SCRIPT_DIR}/deploy_sensors.sh" "${DEPLOY_HOST}"
        ;;
esac

echo "== Done (mode: ${MODE}) =="
