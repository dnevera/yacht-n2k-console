#!/usr/bin/env bash
# run_stage.sh — Launch the Stage Home Assistant Docker environment & auto-deploy
# Usage:
#   ./run_stage.sh               # Launch in Demo mode with local NMEA emulator (default)
#   ./run_stage.sh --live        # Launch in Live mode connected to remote NMEA TCP gateway
#   ./run_stage.sh --clean-install # Launch with forced clean re-provisioning of Stage HA

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure build script runs first
echo "== Executing pre-launch build == "
python3 "${SCRIPT_DIR}/build.py"

# Delegate to Stage orchestrator
exec python3 "${SCRIPT_DIR}/start_stage.py" "$@"
