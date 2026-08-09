#!/usr/bin/env bash
# Downloads the real, unmodified custom card JS bundles this preview
# harness renders against a fake `hass`. Not committed to git (see
# .gitignore in this folder) - ~7.5MB of 3rd-party release artifacts,
# re-download instead of tracking them. Versions match requirements-ha.txt.
set -euo pipefail
cd "$(dirname "$0")/vendor"

echo "Fetching apexcharts-card.js (v2.2.3)..."
curl -sL -o apexcharts-card.js \
  "https://github.com/RomRider/apexcharts-card/releases/download/v2.2.3/apexcharts-card.js"

echo "Fetching compass-card.js (v3.5.0)..."
curl -sL -o compass-card.js \
  "https://github.com/tomvanswam/compass-card/releases/download/v3.5.0/compass-card.js"

echo "Fetching windrose-card.js (v2.4.2)..."
curl -sL -o windrose-card.js \
  "https://github.com/aukedejong/lovelace-windrose-card/releases/download/v2.4.2/windrose-card.js"

echo "Fetching plotly-graph-card.js (v3.3.5, draft card, not deployed yet)..."
curl -sL -o plotly-graph-card.js \
  "https://github.com/dbuezas/lovelace-plotly-graph-card/releases/download/v3.3.5/plotly-graph-card.js"

echo "Fetching config-template-card.js (v1.3.6, iantrich/config-template-card)..."
curl -sL -o config-template-card.js \
  "https://github.com/iantrich/config-template-card/releases/download/1.3.6/config-template-card.js"

for f in apexcharts-card.js compass-card.js windrose-card.js plotly-graph-card.js config-template-card.js; do
  size=$(wc -c < "$f")
  echo "  $f: $size bytes"
  if [ "$size" -lt 1000 ]; then
    echo "  WARNING: $f looks too small - check the release tag/URL above." >&2
  fi
done
echo "Done."
