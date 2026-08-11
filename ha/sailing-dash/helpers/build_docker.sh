#!/usr/bin/env bash
# build_docker.sh — thin wrapper kept for muscle memory.
#
# There is exactly ONE Stage entry point: run_stage.sh (which delegates to
# start_stage.py → deploy.sh). This script used to be a second, parallel entry
# point that compiled and deployed everything again on its own.
#
# Usage:
#   ./build_docker.sh             # same as ./run_stage.sh
#   ./build_docker.sh --no-cache  # force a full docker image rebuild first

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_HA_DIR="${SCRIPT_DIR}/local-ha"

ARGS=()
for arg in "$@"; do
    if [[ "${arg}" == "--no-cache" ]]; then
        echo "🔨 Rebuilding the Stage Home Assistant image without cache ..."
        docker compose -f "${LOCAL_HA_DIR}/docker-compose.yml" build --no-cache
    else
        ARGS+=("${arg}")
    fi
done

echo "➡️  Delegating to run_stage.sh (the single Stage entry point) ..."
exec bash "${SCRIPT_DIR}/run_stage.sh" ${ARGS[@]+"${ARGS[@]}"}
