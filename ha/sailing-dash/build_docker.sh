#!/usr/bin/env bash
# build_docker.sh — Build Home Assistant Stage Docker image and start local-ha container
#
# Usage:
#   ./build_docker.sh             # Build artifacts, build Docker image, start local-ha, deploy
#   ./build_docker.sh --no-cache  # Rebuild Docker image without cache

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_HA_DIR="${SCRIPT_DIR}/local-ha"

echo "=================================================="
echo "🐳 Building Home Assistant Stage Docker Environment"
echo "=================================================="

# 1. Check Docker daemon
if ! docker info >/dev/null 2>&1; then
    echo "❌ Error: Docker daemon is not running. Please start Docker." >&2
    exit 1
fi

# 2. Compile dashboard and sensor modules
echo "📦 Step 1: Compiling source modules via build.py ..."
python3 "${SCRIPT_DIR}/build.py"

# 3. Build Docker image
echo "🔨 Step 2: Building Home Assistant Docker image (local-ha) ..."
if [[ "${1:-}" == "--no-cache" ]]; then
    docker compose -f "${LOCAL_HA_DIR}/docker-compose.yml" build --no-cache
else
    docker compose -f "${LOCAL_HA_DIR}/docker-compose.yml" build
fi

# 4. Start local-ha container
echo "🚀 Step 3: Starting local-ha container ..."
docker compose -f "${LOCAL_HA_DIR}/docker-compose.yml" up -d --force-recreate

# 5. Deploy compiled build artifacts to local-ha
echo "📥 Step 4: Deploying build artifacts to local-ha ..."
bash "${SCRIPT_DIR}/deploy.sh" --stage

echo ""
echo "=================================================="
echo "✅ Home Assistant Stage Docker container ready!"
echo "📌 Dashboard URL: http://localhost:8123/dashboard-sailing/"
echo "🐳 Container:     local-ha"
echo "=================================================="
