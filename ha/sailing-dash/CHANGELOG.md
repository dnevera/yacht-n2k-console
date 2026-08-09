# Changelog — Sailing Dashboard

All notable changes to `ha/sailing-dash/` (dashboard, sensors, deploy
tooling, `local-preview/` test rig). Dates are when the change was made
in this repo, not necessarily when it went live on `bumblebee.local` (some
entries are DRAFTs, not yet deployed — noted explicitly).

Format: reverse-chronological, one bullet per change. Full technical
write-ups/rationale for the entries below still live in `README.md` and
`local-preview/README.md` (this file is an index/summary, not a
replacement for that detail) and in `.agents/skills/nmea2000-setup/SKILL.md`.

## 2026-08-09

- **Fixed reversed wind arrow direction + added compass tooltip.** Both
  `sensor.wind_data_..._wind_angle` (Raymarine) and open-meteo's
  `winddirection_10m` follow the standard meteorological "from" convention
  (0°=N means wind blows *from* the north). The arrow annotation math
  pointed the arrowhead straight at that compass bearing, i.e. towards
  where the wind comes *from* instead of where it's blowing *to* — fixed
  by adding 180° before computing the arrow's pixel offset. Also added a
  hover tooltip on each arrow showing the 16-point compass name + degrees
  (e.g. "NW 275°"), via `captureevents: true` + `hovertext` on the
  annotation.
- **Wind vector chart made full-width in its own section.** The user
  manually split the "Wind Direction & Speed" card (`custom:plotly-graph`)
  into its own wide section (`column_span: 3`) in the HA UI; the card
  itself still defaulted to a narrower auto width inside that section. Pulled
  the live config (repo file had drifted — always match live before editing,
  per the standing rule below) and added `grid_options: {columns: 12}` to the
  card so it spans the section's full internal grid width (12 = full width,
  same convention already used by the Weather & Forecast section's
  Windy/gauge/pressure-trend tiles).
- **`plotly-graph-card` DEPLOYED to `bumblebee.local`.** Added new unified
  `deploy.sh` (`--install`/`--update`/`--resources-only`/`--dashboard-
  only`/`--sensors-only`) — now the ONLY supported way to deploy anything
  in this stack, replacing ad-hoc `scp`/`docker cp` for manually-installed
  card bundles. It installs `plotly-graph-card.js` to `/config/www/`,
  registers it in `.storage/lovelace_resources` (idempotent, matched by
  base URL), then deploys sensors+dashboard. Added the "Wind Direction &
  Speed — Vector Chart" `custom:plotly-graph` card to
  `dashboard-sailing.yaml` (dot markers + a separate `layout.annotations`
  arrow layer — the plain-dots design, not the since-reverted
  `marker.symbol: 'arrow'` one). Confirmed rendering real Raymarine/open-
  meteo data on the live dashboard via a headless Playwright check
  (`PLOTLY-GRAPH 3.3.5` logged, no `Configuration error`, arrows + "Now"
  line + colorbar visible on screenshot).
- **DRAFT `plotly-graph-card` (not deployed):** reverted `marker.symbol:
  'arrow'` direction markers back to real Plotly `annotation` shaft+
  arrowhead vectors — the `arrow` symbol looked like a plain triangle
  (same direction-ambiguity problem as the earlier-rejected `triangle-up`/
  `arrow-bar-up`). Documented as a known, currently-unresolved limitation
  that legend-click (Measured/Forecast) does not hide these annotations,
  since they're not part of either trace and the card's `$fn` has no
  trace-visibility hook.
- **DRAFT `plotly-graph-card`:** fixed "Now" line not sitting ~2h from the
  left edge (added explicit `layout.xaxis.range` `$fn`, matching the
  apexcharts-card chart's `-2h/+24h` window) and legend overlapping the
  x-axis's two-row date/time labels (added `layout.margin.b: 70`, moved
  legend to `y: -0.3`).
- **`apexcharts-card` "Wind — History & Forecast":** fixed default view —
  chart always opens at "now − 2h" instead of a seemingly random window
  that required a manual auto-scale click (`graph_span: 30h→26h`,
  `span: {start: hour, offset: -6h}` → `{start: minute, offset: -2h}`,
  `data_generator` `rangeStart` synced to `-2h`).
- Dashboard layout re-synced from live HA after the user rearranged tiles
  in the UI (roses/compass swap, "Wind Direction — History (rose)"
  subsection removed, "Wind Direction & Speed — Timeline" chart removed).
- **DRAFT `plotly-graph-card`:** replaced arbitrary `RdYlGn` colorscale
  with an explicit 0–40kt `WIND_SPEED_COLORSCALE` (marine convention:
  calm=light blue → gale=purple), applied to both dot markers and arrows.
- **DRAFT `plotly-graph-card`:** direction arrows redesigned 3 times after
  user feedback (`triangle-up` → `arrow-bar-up` → real `annotation`
  shaft+arrowhead vectors); added "Now" vertical line + N/S legend; fixed
  white background in `local-preview/` (theme CSS vars were scoped to
  `ha-card`, this card has no such wrapper).
- Created `local-preview/` offline test rig: renders the real card JS
  bundles (`apexcharts-card`, `windrose-card`, `compass-card`,
  `plotly-graph-card`) against a fake `hass`, in-browser or headless
  (Playwright), to catch config/schema errors before a live deploy.
- Added `custom:windrose-card` "Wind Speed" corner readout (current wind
  speed shown 3 ways: petal color, right-side legend bar, corner number).
- Replaced the direction-forecast "tile row of small rotated arrows" with
  a combined `apexcharts-card` "Wind Direction & Speed — Timeline"
  (later removed by the user — see above), after the tile approach was
  rejected as not matching the requested plotly-style reference chart.
- Added `custom:windrose-card` for wind direction (24h history + current
  direction/speed), replacing the wind `compass-card`.
- Fixed `Configuration error: value.series[3].apex_config is extraneous`
  on the (now-removed) direction chart — per-series `apex_config` isn't
  valid in apexcharts-card v2.2.3's schema; moved to card-level
  `apex_config.markers.size` array.
- Fixed `value.series[0].type is none of "line","column","area"` — the
  installed apexcharts-card (2.2.3) doesn't support `type: scatter`;
  switched to `type: line` + `stroke_width: 0` + `markers.size`.
- Added pre-deploy diff safeguard to `deploy_dashboard.sh` (`REQUIRE_CLEAN_DIFF=1`)
  so manual UI edits made directly on `bumblebee.local` are never silently
  overwritten by a stale local YAML.
- Restyled Wind/COG `compass-card`s (dial ticks, N/E/S/W labels, colored
  indicator arrows, dark circle background) instead of the bare default
  "circle with a dot".
- Fixed COG compass showing SOG (knots) instead of COG (degrees) under
  the arrow — wrong `value_sensors` entity.
- **Root cause found for "Wind History & Forecast never showed forecast":**
  two bugs, both now fixed — (1) `deploy_dashboard.sh` didn't restart HA
  after pushing the storage-mode Lovelace config, so the browser kept
  serving the stale cached config no matter how hard-refreshed (now
  restarts by default, `SKIP_RESTART=1` to opt out); (2) both
  `data_generator` strings declared `const start` while `apexcharts-card`
  already passes `start` as a function parameter — a `SyntaxError` thrown
  on every evaluation, silently dropping the forecast/gust lines (renamed
  to `rangeStart`).
- Investigated (and rejected) HA's native "Open-Meteo" integration for the
  forecast pipeline — its hourly forecast has no `wind_speed`/`wind_gust`.
- `sensors-sailing.yaml`: open-meteo REST query switched from static
  anchor coordinates to a `resource_template` reading live lat/lon from
  the boat's N2K GPS sensor.
- Map card: switched from `device_tracker.iphone_17_promax_nevera`
  (phone GPS) to `device_tracker.nevera` (derived from the boat's own N2K
  GPS) — the map was showing the phone's position, not the boat's.
- Added Windy embed widget + tap-to-open windy.com (later: removed the
  separate button, made the widget itself tappable via a `card-mod`
  overlay trick).
- Added `ha/sailing-dash/requirements-ha.txt` (fresh-install checklist:
  HACS + the 3 custom cards) and root `requirements.txt` entries
  (`pyyaml`, `websockets`) needed by the deploy scripts.

## Earlier

- Initial extraction of the storage-mode Lovelace dashboard, sensors, and
  deploy scripts into this directory (`dashboard-sailing.yaml`,
  `sensors-sailing.yaml`, `deploy_dashboard.sh`, `deploy_sensors.sh`,
  `lovelace-resources.yaml`).
