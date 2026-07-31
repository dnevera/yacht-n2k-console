#!/usr/bin/env bash
#
# build_bundle.sh — Build self-contained deployment bundle for Raspberry Pi
#
# ── MINI-SKILL (read this if context is lost) ─────────────────────────────────
#
# PURPOSE
#   Packages the entire YDNU-02 Web Console into a single tar.gz bundle
#   that can be copied to a Pi and installed via setup_gateway.sh.
#   The bundle is fully self-contained — no git clone needed on the target.
#
# WHAT GOES INTO THE BUNDLE
#   ydnu02-bundle/
#   ├── setup.sh                 ← copied from setup_gateway.sh
#   └── app/
#       ├── *.py                 ← all core Python modules
#       ├── sensors/*.py         ← N2K sensor parsers
#       ├── routes/*             ← FastAPI route handlers
#       ├── static/              ← SPA frontend (html, css, js)
#       ├── tests/               ← test files
#       ├── ble_registry.json    ← BLE sensor config
#       └── deploy.sh            ← for subsequent incremental deploys
#
# OUTPUT
#   build/ydnu02-bundle.tar.gz   (build/ is in .gitignore)
#
# SECURITY RULES
#   - NO sensitive data (hostnames, usernames, IPs) in this script
#   - NO deploy.conf included in the bundle (user creates it on target)
#   - All deploy examples use placeholders: user@gateway-host
#   - deploy.conf.template IS included for reference
#
# USAGE
#   ./build_bundle.sh
#
# DEPLOY (after build)
#   scp build/ydnu02-bundle.tar.gz user@gateway-host:~/
#   ssh user@gateway-host 'tar xzf ydnu02-bundle.tar.gz && cd ydnu02-bundle && ./setup.sh'
#
# SKILL: Adding a new file to the bundle
#   1. Add the cp line in the appropriate section below
#   2. If it's a new directory, add it to the mkdir -p block
#   3. Run: ./build_bundle.sh && tar tzf build/ydnu02-bundle.tar.gz | grep <filename>
#
# SKILL: Verifying bundle has no sensitive data
#   tar xzf build/ydnu02-bundle.tar.gz -C /tmp
#   grep -rn 'gateway.local\|user@\|192\.168' /tmp/ydnu02-bundle/ && echo "LEAK!" || echo "clean"
#
# TODO: Add deploy.conf.template to the bundle so user has the reference
# TODO: Add version stamp (git describe --tags) into the bundle
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${LOCAL_DIR}/.bundle-build/ydnu02-bundle"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/app/sensors" \
         "${BUILD_DIR}/app/routes" \
         "${BUILD_DIR}/app/static/css" \
         "${BUILD_DIR}/app/static/js" \
         "${BUILD_DIR}/app/tests/specs"

# Setup script (generic — no hardcoded hostnames)
cp "${LOCAL_DIR}/setup_gateway.sh" "${BUILD_DIR}/setup.sh"
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

# Deploy script + config template
cp "${LOCAL_DIR}/deploy.sh"            "${BUILD_DIR}/app/" 2>/dev/null || true
cp "${LOCAL_DIR}/deploy.conf.template" "${BUILD_DIR}/app/" 2>/dev/null || true

# Build tarball
BUILD_OUT="${LOCAL_DIR}/build"
mkdir -p "${BUILD_OUT}"
TARBALL="${BUILD_OUT}/ydnu02-bundle.tar.gz"
tar czf "${TARBALL}" -C "${LOCAL_DIR}/.bundle-build" ydnu02-bundle

rm -rf "${LOCAL_DIR}/.bundle-build"

echo "✅ Bundle: ${TARBALL}"
echo ""
echo "Deploy:"
echo "  scp ${TARBALL} user@gateway-host:~/"
echo "  ssh user@gateway-host 'tar xzf ydnu02-bundle.tar.gz && cd ydnu02-bundle && ./setup.sh'"
