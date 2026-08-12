#!/usr/bin/env bash
# run_stage.sh — Launch the Stage Home Assistant Docker environment & auto-deploy
# Usage:
#   ./run_stage.sh               # Launch in Demo mode with local NMEA emulator (default)
#   ./run_stage.sh --live        # Launch in Live mode connected to remote NMEA TCP gateway
#   ./run_stage.sh --clean-install # Launch with forced clean re-provisioning of Stage HA

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# This is the single Stage entry point. build.py is NOT called here: deploy.sh
# runs it exactly once per pipeline, together with fetch_deps.py.
exec python3 "${SCRIPT_DIR}/helpers/start_stage.py" "$@"
