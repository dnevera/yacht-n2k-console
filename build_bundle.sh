#!/usr/bin/env bash
#
# Build ydnu02-bundle.tar.gz — self-contained bundle for Pi deployment
#
set -euo pipefail

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${LOCAL_DIR}/.bundle-build/ydnu02-bundle"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/app/sensors" \
         "${BUILD_DIR}/app/routes" \
         "${BUILD_DIR}/app/static/css" \
         "${BUILD_DIR}/app/static/js" \
         "${BUILD_DIR}/app/tests/specs"

# Setup script
cp "${LOCAL_DIR}/setup_gateway.local.sh" "${BUILD_DIR}/setup.sh"
chmod +x "${BUILD_DIR}/setup.sh"

# Python sources
for f in ydnu02.py app.py device_manager.py models.py \
         gobius_parsers.py mopeka_parsers.py mopeka_scanner.py \
         ble_registry.py gobius_ble_poller.py ydnu02_web.py \
         n2k_command_builder.py; do
    cp "${LOCAL_DIR}/${f}" "${BUILD_DIR}/app/" 2>/dev/null || true
done

# Sensors
cp "${LOCAL_DIR}/sensors/"*.py "${BUILD_DIR}/app/sensors/"

# Routes
cp -r "${LOCAL_DIR}/routes/"* "${BUILD_DIR}/app/routes/"

# Static
cp "${LOCAL_DIR}/static/index.html"    "${BUILD_DIR}/app/static/"
cp "${LOCAL_DIR}/static/css/"*.css     "${BUILD_DIR}/app/static/css/"
cp "${LOCAL_DIR}/static/js/"*.js       "${BUILD_DIR}/app/static/js/"

# Tests
cp "${LOCAL_DIR}/tests/"*.py           "${BUILD_DIR}/app/tests/"       2>/dev/null || true
cp "${LOCAL_DIR}/tests/specs/"*.py     "${BUILD_DIR}/app/tests/specs/" 2>/dev/null || true

# BLE registry
cp "${LOCAL_DIR}/ble_registry.json"    "${BUILD_DIR}/app/" 2>/dev/null || true

# Deploy script
cp "${LOCAL_DIR}/deploy.sh"            "${BUILD_DIR}/app/" 2>/dev/null || true

# Build tarball
TARBALL="${LOCAL_DIR}/ydnu02-bundle.tar.gz"
tar czf "${TARBALL}" -C "${LOCAL_DIR}/.bundle-build" ydnu02-bundle

rm -rf "${LOCAL_DIR}/.bundle-build"

echo "✅ Bundle: ${TARBALL}"
echo ""
echo "Deploy:"
echo "  scp ${TARBALL} user@<gateway-host>:~/"
echo "  ssh user@<gateway-host> 'tar xzf ydnu02-bundle.tar.gz && cd ydnu02-bundle && ./setup.sh'"
