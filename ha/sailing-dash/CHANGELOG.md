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

- **Fixed `forecast_hours: 72` only drawing up to Aug 11, and the bogus
  "kt scale" row in the shared tooltip.** (1) open-meteo counts
  `forecast_days` from **today 00:00 UTC**, not from "now", so the previous
  `ceil(forecast_hours / 24)` was systematically short: at 18:00 UTC with
  `forecast_hours=72` it asked for 3 days = only ~53 h ahead (chart ended
  Aug 11 23:00). Both `resource_template` URLs now use
  `min(ceil((hours_elapsed_today_utc + forecast_hours) / 24), 16)` —
  still derived from the single `sensor.chart_time_window` setting, no new
  hardcoded numbers. Verified live: the flat wind/wave sensors went from 72
  to 96 hourly points, last point Aug 12 23:00, i.e. the full +72 h.
  Documented the harmless startup race (the `rest:` platform builds its URL
  before the template sensor exists, so the first poll after an HA restart
  uses the `| int(48)` fallback; the next poll, or an explicit
  `homeassistant.update_entity`, fixes it). (2) The invisible "kt scale"
  trace (it exists only to render the colorbar legend for the direction
  arrows) showed up as a `kt scale / 4 kts` row in the unified tooltip —
  `hoverinfo: skip` is ignored under `hovermode: x unified`. It is now
  emptied (`extend_to_present: false` + a final `fn: () => ({xs: [], ys: []})`
  filter); Plotly still renders the colorbar from
  `marker.colorscale/cmin/cmax` without any data points. Verified in
  `local-preview/` with real hover: tooltip now reads only
  `Measured / Forecast / Gusts (measured) / Gusts (forecast)`, colorbar
  still present.
- **One place to configure the time window for BOTH plotly cards *and* the
  open-meteo requests: `sensor.chart_time_window`.** The window used to be
  hardcoded four times (`hours_to_show`/`time_offset` in the wind chart and
  in the Waves chart, `forecast_days=2` in both `rest:` URLs). New template
  sensor `sensor.chart_time_window` (`sensors-sailing.yaml`) exposes
  `history_hours` (4) and `forecast_hours` (48 = the agreed 2 days); both
  REST `resource_template` URLs derive `forecast_days` from it as
  `ceil(forecast_hours / 24)`, so the API is asked for exactly the interval
  the charts draw. `forecast_days` is deliberately not a sensor attribute:
  a self-referencing (`this.attributes`) template would not be re-evaluated
  when the literals change. Both `custom:plotly-graph` cards read
  `hours_to_show: $fn ({ hass }) => history_hours + forecast_hours` and
  `time_offset: $fn ({ hass }) => forecast_hours + 'h'`. This works because
  `plotly-graph-card` resolves those two keys through `getFromConfig`, i.e.
  the same `$fn`/`$ex` evaluator used for traces, and `$fn` receives the full
  `hass` object — verified against the installed bundle
  (`local-preview/vendor/plotly-graph-card.js`), not assumed: in the local
  simulator the card's `parsed_config` came out as 28 / "24h" (X axis
  Now-4h..Now+24h), and after changing only the mock sensor attributes to
  8/12 the same card rendered a 20h span with `time_offset: "12h"`.
  `layout.xaxis.range` is still NOT used (it breaks X panning).

- **Documented the Open-Meteo API limits in `sensors-sailing.yaml`
  (comments only, no behaviour change).** Numbers were verified live by
  probing both endpoints and counting `hourly.time` points, not copied from
  the docs: `forecast_days` is `0..16` on BOTH `api.open-meteo.com/v1/forecast`
  and `marine-api.open-meteo.com/v1/marine` (16 -> 384 hourly points,
  17 -> HTTP 400 "Allowed range 0 to 16" — so the marine API is *not* capped
  at 8 days as often assumed, though wave skill degrades after ~7-8 days and
  the high-resolution weather models end at ~5 days). History via `past_days`
  is `0..93` on both (93 -> 2400 hourly points, 94 -> HTTP 400), and those
  past values are model reanalysis, not measurements; anything older needs
  the separate Archive API (`archive-api.open-meteo.com/v1/archive`, ERA5
  from 1940, ~5 days behind real time). Also noted why we keep
  `forecast_days=2` (charts only draw Now+24h) and never pass `past_days`
  (history comes from HA's recorder = real Raymarine measurements), plus the
  free-tier quota (~10 000 calls/day) vs our 96 calls/day per endpoint at
  `scan_interval: 900`.
- **Documented the "time from Now" window setting on both plotly cards
  (comments only, no behaviour change).** The setting was never removed —
  `plotly-graph-card` simply has no dedicated "hours before now" key: the
  visible window is `hours_to_show` (total span) minus `time_offset` (how
  far the right edge reaches into the future), i.e. `28 - 24h` =
  Now-4h ... Now+24h. Both `custom:plotly-graph` cards (wind vector chart,
  Waves) now carry an explicit comment block stating that formula, telling
  which key to edit to change the history offset (`hours_to_show` only,
  keep `time_offset: 24h` = the open-meteo forecast horizon) and warning not
  to re-add `layout.xaxis.range` (the card merges its own `{xaxis:{range}}`
  around the user layout, which snapped the X pan back on every redraw —
  the original reason the explicit range was dropped).
- **New "Waves" section: Open-Meteo Marine wave forecast, mirroring the
  wind vector chart.** Implementation was delegated to the Gemini writer
  subagent (`~/.junie/scripts/ask_gemini.py --tag gemini:writer`, context =
  the real `sensors-sailing.yaml` + the live wind `custom:plotly-graph`
  card), then reviewed/cleaned by hand. Sensors (`sensors-sailing.yaml`): a
  second `rest:` entry against `marine-api.open-meteo.com/v1/marine`
  (`hourly=wave_height,wave_direction,wave_period`, `forecast_days=2`,
  `timezone=UTC`, `scan_interval: 900`) using the *same* `resource_template`
  live-GPS trick as the wind forecast, plus `sensor.wave_forecast_flat`
  (array attributes `forecast_time`/`wave_height`/`wave_direction`/
  `wave_period`) and `sensor.wave_height_next_hour` (m) /
  `sensor.wave_period_next_hour` (s) — first `forecast_time >= now` point,
  same jinja pattern as `wind_forecast_next_hour`. Dashboard: a new grid
  section (`column_span: 3`) with a `type: glance` header row styled by the
  identical `card_mod` as the wind header (value on top 26px, caption 12px,
  all `!important`; height `#4fc3f7`, period `#b0bec5`) and a
  `custom:plotly-graph` chart — forecast height markers, a direction arrow
  layer (`layout.annotations`, same `+180°` "where the waves are going"
  convention, arrow colour graded by wave height), the rounded "Now" badge,
  `hovermode: x unified` + X spike cursor, `yaxis.fixedrange`,
  `config: {scrollZoom: false, displayModeBar: false, doubleClick: false}`
  and the same `on_dblclick` reset. Deviations from the model draft: its
  trailing `- entity_id: ...` block was invalid HA config and was dropped,
  `swell_wave_height` was removed from the request (it was never rendered),
  and the period trace is `visible: legendonly` (plotly-graph-card has no
  per-trace secondary axis) with the period also folded into the height
  trace's tooltip (`0.18 m · WSW 262° · 3.7 s`). No measured wave series —
  Raymarine publishes no wave sensor. Marine API verified live for the
  anchorage coordinates: 48 hourly points with real height/direction/period.
- **Pulled another manual re-layout from the live HA (no deploy).** In the
  "Weather & Forecast" section the user moved the Windy grid card (iframe +
  transparent overlay button) below the windrose card, changed the iframe
  `aspect_ratio` 65% -> 50% and added `hide_background: true`, and the section
  now carries an explicit `column_span: 1`. `dashboard-sailing.yaml` re-synced
  1:1 from `.storage/lovelace.dashboard_sailing` (`yaml.safe_load(...) == live`
  -> True).
- **Pulled the user's manual re-layout from the live HA (no deploy).** The
  user rearranged the dashboard in the HA UI again, so
  `dashboard-sailing.yaml` was re-synced 1:1 from
  `.storage/lovelace.dashboard_sailing` (header comments preserved,
  `yaml.safe_load(...) == live config` -> True). Changes taken over: wind
  cards (windrose + apexcharts history/forecast) moved between sections,
  Speed/Depth gauges regrouped, the "Speed & Depth" / "New section"
  headings removed, `card_mod` added to the Latitude/Longitude entities and
  the "(kts)" suffixes dropped from the header-row entity names.
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
