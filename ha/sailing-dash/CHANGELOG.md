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

- **RULE (restated, mandatory): pull + sync the live HA dashboard BEFORE
  every edit, not just before deploy.** Violated again — while an edit was
  in flight the user had renamed the section heading ("Wind & Forecast" ->
  "Wind"), renamed the apexcharts card title ("Wind — History & Forecast" ->
  "Wind History & Forecast") and deleted the "Wind Direction & Speed —
  Vector Chart" subtitle in the HA UI; deploying would have reverted all
  three. Mandatory order is now written into the header of
  `dashboard-sailing.yaml`: (1) pull live `.storage` config and sync this
  file to it (the user's UI edits always win), (2) apply the new change on
  top, (3) confirm the live-vs-local diff contains ONLY that change,
  (4) deploy via `./deploy.sh`.
- **Header values row restyled to the apexcharts `show_states` look, and
  the forecast horizon is now explicit.** The stock `type: glance` rendered
  a small name-above-tiny-value list; `card_mod` now flips each entity
  (`flex-direction: column-reverse`) so the value is the big top line
  (26px, coloured per series: `#4fc3f7` measured / `#ff7043` forecast /
  `#b0bec5` gusts) with a small 12px caption underneath. All declarations
  need `!important`: HA's own card CSS ships via `adoptedStyleSheets`,
  which the cascade applies *after* card-mod's injected `<style>`, so
  same-specificity rules (e.g. `.entity { flex-direction: column }`) would
  otherwise win. Second gotcha: card-mod gives **each glance entity its own
  shadow root** (`card-mod-type="glance"`, containing `div.name` + an
  unclassed `div` holding the state), so card-level CSS can't reach the
  value/caption at all — the card-level `card_mod` only does the layout flip
  and padding, while font sizes and per-series colours live in a `card_mod`
  block **on each entity** (`div.name` / `div:not(.name)`). Confirmed on the
  live dashboard by screenshot. Labels now state the period the value covers:
  "Measured now (kts)", "Forecast next 1h (kts)", "Gusts next 1h (kts)" —
  both forecast sensors take the first `forecast_time >= now` point from
  `sensor.wind_forecast_flat`, i.e. the next full hour, which is why a value
  of `0` there simply means calm in that hour.
- **Wind vector chart: Measured vs Forecast markers no longer blend
  (DEPLOYED).** Both marker traces were coloured by the same 0–40 kt speed
  colorscale (`marker.color: $ex ys`), so at similar wind speeds the two
  series were indistinguishable. Measured is now a fixed light-blue circle
  (`#4fc3f7`), Forecast a fixed orange diamond (`#ff7043`). The speed
  colorscale is still meaningful for the direction arrows, so the `kt`
  colorbar was preserved by moving it onto an extra invisible marker trace
  ("kt scale", `opacity: 0`, `showlegend: false`, `hoverinfo: skip`) — it
  only renders the colorbar and stays out of the legend/unified tooltip.
  Arrow colours untouched (per request). Verified in `local-preview/`
  (trace dump + screenshot: blue circles left of Now, orange diamonds on
  the forecast side, colorbar intact).

- **Header values row above the wind vector chart** (mirrors the
  apexcharts card's `show_states` header the user pointed at): current
  measured wind speed (Raymarine), plus the *next full forecast hour* wind
  and gust from open-meteo. The latter two needed new template sensors
  (`sensor.wind_forecast_next_hour`, `sensor.wind_gust_next_hour` in
  `sensors-sailing.yaml`) that pick the first `forecast_time` entry >= now
  from `sensor.wind_forecast_flat` - the flat sensor only exposes arrays.
  First tried a `type: markdown` card with inline HTML/CSS to match the
  big-coloured-number look: HA sanitizes `<div style=...>` away, so the
  values rendered as a plain stacked list - replaced with a stock
  `type: glance` (`show_icon: false`, `columns: 3`,
  `grid_options.columns: 36` for full section width).
- **"Now" badge padding/rounding, final form.** The hand-drawn `path`
  rounded rect had no way to know the pixel size of the text, so its
  padding was uneven; reverted to Plotly's own `bgcolor` + `borderpad: 4`
  (which centres the label exactly) and got the rounded corners instead by
  injecting `.annotation rect.bg { rx: 4px; ry: 4px }` (SVG geometry
  properties settable via CSS) into the card's shadow root from the same
  `layout.annotations` `$fn`, once per card. Measured in `local-preview/`:
  rect 21x31 around a 12x21.7 text = symmetric padding, `rx` computes to 4px.
- **"Now" badge: correct text orientation, left-anchored, rounded corners.**
  `textangle: 90` rendered the label upside down -> `-90`; added
  `xanchor: 'right'` (+ `xshift`) so the badge hangs on the *left* side of
  the "Now" line instead of straddling it. Plotly annotations have no
  `borderradius` (confirmed absent from the bundled Plotly build), so the
  white background was moved out of the annotation into a `shapes` entry of
  `type: path` with `Q` arcs on all four corners; the annotation now only
  draws the black text on top. Gotcha: a `path` on a date axis silently
  renders nothing when its coordinates are ISO strings - it must use epoch
  milliseconds (this is why the badge was invisible on the first attempt).
- **"Now" marker on the wind vector chart made visible (like apexcharts').**
  The annotation existed but was drawn *above* the plot area (`yref: paper,
  y: 1, yanchor: bottom`) with no top margin, so it was clipped off the
  canvas. Moved inside the plot as a white badge with vertical text
  (`y: 0.97`, `yanchor: top`, `textangle: 90`, `bgcolor: '#ffffff'`,
  `borderpad: 3`, black font) and switched the `shapes` line from
  `dash: dot` to `dash: dash` so it matches the "Wind — History & Forecast"
  chart. The "▲ N / ▼ S" hint was clipped the same way and was moved
  inside too (`x: 0.01`, `y: 0.97`, `yanchor: top`).
- **Wind vector chart: single unified tooltip + vertical dash cursor +
  gusts.** Replaced the two competing hover popups (the per-trace box plus
  the per-arrow `annotation` tooltip) with one unified tooltip:
  `layout.hovermode: x unified`, `hoverdistance: -1`, the direction arrows'
  `captureevents` turned off (they no longer capture hover), and per-trace
  `hovertemplate`s that put the series name in `<extra>` so the unified box
  reads `Measured: 9.5 kt · NW 320°`. Added the vertical dashed
  time cursor via `xaxis.showspikes/spikemode: across/spikedash: dash/
  spikesnap: cursor` (`yaxis.showspikes: false`), matching the other charts.
  Gusts are now shown as two thin dotted lines with their own tooltip rows:
  *Gusts (forecast)* from open-meteo's `forecast_gust`, and *Gusts
  (measured)* computed as a 10-minute rolling max over the Raymarine wind
  speed history (the instrument reports instantaneous speed only, so the
  gust is derived in a `filters.fn`). Verified in `local-preview/` by
  driving a real hover and reading the DOM: one hover box with a time
  header + 4 rows, spike line present, no second popup.
- **Wind vector chart: 4h default window, X-only panning, reset only on
  double click (DEPLOYED).** Root cause of "panning always snaps back":
  the card merges `layout` twice around its own `{xaxis: {range:
  visible_range}}` (`merge({}, layout, {xaxis...}, yaxes, layout)`), so a
  hand-written `layout.xaxis.range` always wins and re-applies on every
  re-render, defeating the card's built-in browsing mode. Fixed by dropping
  that manual range and using the card's own window instead:
  `hours_to_show: 28` + `time_offset: 24h` = exactly Now−4h … Now+24h.
  Panning now persists (card enters browsing mode); `layout.yaxis.
  fixedrange: true` + `autorange: true` keeps the Y axis auto-scaled and
  un-pannable/un-zoomable; `config.doubleClick: false` disables Plotly's
  own `reset+autosize` (which caused the "autoscale instead of reset"
  behaviour), and a new `on_dblclick` handler clicks the card's built-in
  reset button (`exitBrowsingMode`) so a double click/tap always returns to
  the Now−4h window, repeatably. Verified in `local-preview/` by driving a
  real drag + two double clicks and reading `gd.layout` (X stays shifted
  after pan, Y never moves, both double clicks land back on −4h/+24h).
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
