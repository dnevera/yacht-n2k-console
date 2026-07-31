#!/usr/bin/env bash
# apply_ha_patch.sh — Deploy nmea2000/message.py hash-collision fix to HA container.
# =====================================================================================
#
# WHAT IT DOES:
#   1. Copies patch_ha_nmea2000_message.py into the homeassistant Docker container.
#   2. Runs the patch inside the container (idempotent — safe to run multiple times).
#   3. Reports whether the patch was newly applied or already present.
#   4. Does NOT restart Home Assistant automatically — user must do that from HA UI
#      or via: ssh user@<gateway-host> "sudo docker restart homeassistant"
#
# IDEMPOTENCY:
#   The Python patch script checks for marker "yacht-n2k-console-patch-v1" in the
#   target file before applying. Re-running this script is always safe.
#
# USAGE:
#   ./scripts/apply_ha_patch.sh
#   ./scripts/apply_ha_patch.sh user@<gateway-host>   # override SSH host
#
# RELATED FILES:
#   scripts/patch_ha_nmea2000_message.py  — the actual patch logic
#   ydnu02_tcp_gateway/data_hub.py        — Bug 1 fix (two-phase announcement)
#   nmea2000/nmea2000/message.py          — same fix applied in local library fork
#
# BUG BEING FIXED (summary):
#   nmea2000/message.py add_data(): primary_key = f"{self.id}" — no source identity.
#   For PGN 126996 (productInformation) all devices share MD5 818d9516db08fd90ffd1967e3c403bed.
#   Second device in HA gets 0 entities. Fix: include source_iso_name.name in primary_key.

set -euo pipefail

HA_HOST="${1:-user@<gateway-host>}"
CONTAINER="homeassistant"
PATCH_SCRIPT="$(dirname "$0")/patch_ha_nmea2000_message.py"
REMOTE_TMP="/tmp/patch_ha_nmea2000_message.py"

echo "[apply_ha_patch] Host:      ${HA_HOST}"
echo "[apply_ha_patch] Container: ${CONTAINER}"
echo "[apply_ha_patch] Patch:     ${PATCH_SCRIPT}"
echo ""

# 1. Upload patch script to the Pi
echo "[apply_ha_patch] Uploading patch script to ${HA_HOST}:${REMOTE_TMP} ..."
scp -q "${PATCH_SCRIPT}" "${HA_HOST}:${REMOTE_TMP}"
echo "[apply_ha_patch] Upload OK."

# 2. Copy patch script into the container
echo "[apply_ha_patch] Copying into container ${CONTAINER} ..."
ssh "${HA_HOST}" "sudo docker cp ${REMOTE_TMP} ${CONTAINER}:${REMOTE_TMP}"
echo "[apply_ha_patch] Copy OK."

# 3. Run the patch inside the container
echo "[apply_ha_patch] Running patch inside container ..."
ssh "${HA_HOST}" "sudo docker exec ${CONTAINER} python3 ${REMOTE_TMP}"
PATCH_EXIT=$?

echo ""
if [ ${PATCH_EXIT} -eq 0 ]; then
    echo "[apply_ha_patch] Done."
    echo "[apply_ha_patch] NEXT STEP: reload the nmea2000 integration in Home Assistant"
    echo "                 (Settings → Devices & Services → NMEA 2000 → Reload)"
    echo "                 or restart the container:"
    echo "                 ssh ${HA_HOST} 'sudo docker restart ${CONTAINER}'"
else
    echo "[apply_ha_patch] FAILED (exit code ${PATCH_EXIT}). See output above." >&2
    exit ${PATCH_EXIT}
fi
