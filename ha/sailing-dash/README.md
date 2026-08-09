# Sailing Dashboard (Home Assistant)

Source-controlled copy of the "Sailing" Lovelace dashboard
(`http://bumblebee.local:8123/dashboard-sailing/`) running on the boat's
Home Assistant instance.

## Changelog

Dated, chronological list of changes: [`CHANGELOG.md`](./CHANGELOG.md).
Going forward, new changes get a bullet there; this `README.md` stays the
reference doc for the *current* config/rationale (not a running log of
every edit).

## Why this exists

The dashboard was originally created via the HA UI (a *storage-mode*
dashboard), so its config only lived inside HA's internal state file
`.storage/lovelace.dashboard_sailing` on the target host — not as a plain
file anyone could review, diff, or restore from git. This directory makes
`dashboard-sailing.yaml` the source of truth and provides a script to push
edits back into HA's storage.

## Files

- `deploy.sh` — **the single entry point for all deploys** (2026-08-09).
  `--install`/`--update` run all three steps below in order; `--resources-
  only`/`--dashboard-only`/`--sensors-only` run just one. See "Deploying"
  below.
- `dashboard-sailing.yaml` — the dashboard's `views:` config (Lovelace
  "sections" view layout), extracted from the live instance on 2026-08-09.
- `deploy_dashboard.sh` — converts the YAML back into the JSON shape HA
  expects and uploads it into the `homeassistant` docker container's
  `.storage/lovelace.dashboard_sailing`, backing up the previous version
  on the remote host first, then **restarts Home Assistant** (required —
  see "Why the dashboard deploy restarts HA" below).
- `sensors-sailing.yaml` — the `rest:`/`template:` sensor + `device_tracker:`
  config the dashboard depends on but which is NOT published by
  `ydnu02_tcp_gateway`: the open-meteo wind forecast pipeline
  (`sensor.wind_forecast_rest` → `sensor.wind_forecast_flat`) plus
  `sensor.barometer_mmhg`/`sensor.boat_latitude`/`sensor.boat_longitude`
  (derived from N2K sensors). Extracted from `/config/configuration.yaml`
  on 2026-08-09.
- `deploy_sensors.sh` — idempotently merges `sensors-sailing.yaml` into the
  remote `/config/configuration.yaml` (matching existing entries by
  `unique_id` so re-running it updates in place instead of duplicating),
  backs up the previous file, and restarts the `homeassistant` container
  (required — unlike the dashboard, `configuration.yaml` isn't
  hot-reloadable).
- `lovelace-resources.yaml` — list of ALL frontend resources the dashboard's
  custom cards need: HACS-managed ones (`card-mod`, `compass-card`,
  `apexcharts-card` — installed/managed via HACS in the UI, this file just
  documents what must be present) and manually-installed ones
  (`windrose-card`, `plotly-graph-card` — deployed by `deploy.sh`, see
  "Deploying" below).
- `local-preview/` — offline browser test rig (2026-08-09): renders the
  real custom card bundles against a fake `hass`, so config/schema errors
  and new draft configs (e.g. the `plotly-graph-card` sketch below) can be
  checked **without** a live HA instance or a deploy cycle. See
  `local-preview/README.md` for usage.

## Dashboard layout (re-synced 2026-08-09, after the user rearranged tiles in the HA UI)

1. **Wind & Forecast** — `custom:windrose-card` (history + current wind
   direction/speed, see "Wind direction — windrose + timeline chart"
   below), then a single `custom:apexcharts-card` "Wind — History &
   Forecast" (measured wind vs. forecast wind/gusts pulled from
   `sensor.wind_forecast_flat` attributes). **The wind `compass-card` that
   used to sit here is GONE** (2026-08-09, ~13:40) — the user replaced it
   with the windrose card directly in the HA UI and deleted the separate
   "Wind Direction — History (rose)" subsection the rose used to live in;
   do not reintroduce either without checking the live config first (see
   "Editing the dashboard" below). **The combined "Wind Direction & Speed —
   Timeline" `apexcharts-card` is also GONE again** (2026-08-09, ~14:15) —
   the user removed it after it kept failing (see "Wind vector/arrow chart
   — apexcharts-card limitation and the plotly-graph-card plan" below for
   why, and the proposed replacement using a different custom card).
   **Default view fixed (2026-08-09):** `graph_span`/`span` previously
   rounded to the top of the hour and started 6h in the past
   (`start: hour, offset: -6h`, `graph_span: 30h`) - since most of that
   30h window is future forecast, recent measurements were squeezed into a
   thin sliver on the left, which looked "randomly centered" and forced a
   manual zoom/reset-zoom click every load. Changed to
   `span: { start: minute, offset: -2h }` + `graph_span: 26h` - an exact,
   non-rounded "now minus 2 hours" anchor, so the chart always opens
   showing the last 2 hours of measured data in a consistent position. The
   two `data_generator` forecast/gust series' internal `rangeStart` filter
   was updated from `-6h` to `-2h` to match.
2. **Weather & Forecast** — the Windy `iframe` widget (alternative
   forecast/history view, tap-to-open windy.com, see "Windy alternative
   view" below), a barometric pressure gauge, and a pressure `tile` card
   with a `trend-graph` feature.
3. **Position** — COG `compass-card` (still used here, now also showing SOG
   as its value sensor), map (`device_tracker.nevera`, the boat's own N2K
   GPS position — see "Boat position on the map" below), latitude/longitude
   entities.
4. **Speed & Depth** — SOG gauge, STW gauge, Depth gauge (each in its own
   grid section; the last section title still says "New section" in HA —
   left as-is to match the live dashboard, rename in the YAML + redeploy
   if desired).

This layout has been reshuffled by the user directly in the HA UI more than
once (headings/positions of the compass, pressure gauge, etc. moved between
sections) — `dashboard-sailing.yaml` is re-synced from the live
`.storage/lovelace.dashboard_sailing` config each time this happens, so it
always reflects the exact current on-screen arrangement rather than a fixed
"canonical" layout.

All N2K-derived sensors (COG/SOG, wind, STW, depth) are published by
`ydnu02_tcp_gateway` — see the `nmea2000-setup` skill / `AGENTS.md` for how
those entities get into HA in the first place.

## Deploying (`deploy.sh` — the ONLY supported way)

**As of 2026-08-09, `./deploy.sh` is the single entry point for deploying
ANY part of this stack — do not `scp`/`docker cp` files onto the HA host
by hand.** It wraps three pieces: manually-installed card `.js` bundles
(`windrose-card`, `plotly-graph-card` — anything not in HACS) +
`lovelace_resources` registration, `sensors-sailing.yaml`
(`deploy_sensors.sh`), and `dashboard-sailing.yaml` (`deploy_dashboard.sh`).
The underlying `deploy_dashboard.sh`/`deploy_sensors.sh` scripts still exist
and can be called directly, but `deploy.sh` is what you should reach for by
default since it always keeps all three in sync in one command.

```bash
./deploy.sh --install [user@host]   # fresh HA instance: resources + sensors + dashboard
./deploy.sh --update  [user@host]   # existing install, same 3 steps (default if no flag given)
./deploy.sh --resources-only [user@host]   # just sync manually-installed card JS + resource list
./deploy.sh --dashboard-only [user@host]   # just dashboard-sailing.yaml
./deploy.sh --sensors-only   [user@host]   # just sensors-sailing.yaml
```

HACS-managed cards (`card-mod`, `compass-card`, `apexcharts-card`, listed in
`lovelace-resources.yaml`) are **not** touched by `deploy.sh` — install
those once via HACS → Frontend, same as before. `--install`/
`--resources-only` need `local-preview/vendor/*.js` present locally (run
`local-preview/fetch-vendor.sh` first if missing) — those are the actual
bundles pushed to `/config/www/` on the remote host.

The map card uses `device_tracker.nevera`, deployed by the sensors step
above — no phone/Companion App setup is required for the map to work (see
"Boat position on the map" below).

## Editing the dashboard

**Always compare HA vs. local before deploying.** The user has repeatedly
rearranged tiles directly in the HA UI in the past — if this repo's
`dashboard-sailing.yaml` isn't re-synced first, deploying would silently
overwrite those manual changes. `deploy_dashboard.sh` now does this
automatically (step 2b: pulls the live `.storage` config, converts it to
YAML, and diffs it against the local file, printing the diff every run;
set `REQUIRE_CLEAN_DIFF=1` to make it abort instead of just warning when
they differ). `deploy_sensors.sh` already fetches+diffs the remote
`configuration.yaml` before merging, for the same reason. If either diff
shows unexpected changes, pull the live version into the repo file first
(see "Dashboard layout" above) before pushing your own edits on top of it.

1. Edit `dashboard-sailing.yaml` in this repo.
2. Optional but recommended for new/risky custom-card configs (dual-axis
   charts, unfamiliar card options, etc.): copy the changed card's config
   into `local-preview/card-configs.js` and run the offline browser check
   (`local-preview/README.md`) — it renders the real card bundle and shows
   config/schema errors immediately, without a deploy-and-restart cycle.
3. Run `./deploy.sh --update` (or `./deploy.sh --dashboard-only` if only
   the dashboard changed — see "Deploying" above). Review the printed
   pre-deploy diff before it uploads.
4. Reload the dashboard in the browser (the script already restarted HA,
   wait ~30s for it to come back up).

## Why the dashboard deploy restarts HA

Home Assistant reads the storage-mode Lovelace config **once, at startup**,
and then serves that in-memory copy to the frontend over the websocket
(`lovelace/config`). Writing `.storage/lovelace.dashboard_sailing` behind
its back with `docker cp` therefore has **no effect on what the browser
receives** until HA reloads the file — no amount of browser hard-refresh
helps, and HA may even overwrite the file from memory later. This was the
reason the `rangeStart` fix below appeared not to work: the file on disk was
correct while HA kept serving the old, broken config (verified 2026-08-09 by
querying `lovelace/config` over the websocket API before and after a
`docker restart homeassistant`). `deploy_dashboard.sh` now restarts the
container itself; pass `SKIP_RESTART=1` to opt out (e.g. when the next step
is `deploy_sensors.sh`, which restarts HA anyway).

To verify what HA actually serves (needs `HA_URL`/`HA_TOKEN` from `.env`
and `pip install websockets`):

```python
# ws://<host>:8123/api/websocket -> auth -> {"id":1,"type":"lovelace/config",
#  "url_path":"dashboard-sailing"}  and inspect the returned series/cards
```

## Boat position on the map

The "Position" section's map card uses `device_tracker.nevera` — a
`template: device_tracker` (in `sensors-sailing.yaml`) derived from the N2K
position sensors (PGN 129025/129029, published by the Raymarine display via
`ydnu02_tcp_gateway`), i.e. the **boat's own GPS**, not a crew member's
phone. An earlier revision of the dashboard used
`device_tracker.iphone_17_promax_nevera` (HA Companion App / phone GPS)
instead — that entity is still available (if the Companion App is installed)
but is no longer used here, since it tracks whoever is carrying the phone
rather than the boat, and shows nothing/stale data when the phone is off or
left ashore. If you need the phone tracker back for some reason, swap the
`entity:` under the map card's `entities:` list in `dashboard-sailing.yaml`
and redeploy.

## Compass cards styling (Wind & COG, 2026-08-09)

Both `compass-card`s (Wind angle in "Wind & Forecast", COG in "Position")
used the bare minimum config — just `indicator_sensors`, which renders as a
plain circle with a floating arrow and no dial markings at all. They now use
the card's `compass:` object to render an actual compass face:

- `compass.circle.color` — a dark dial background (`#37474f`) instead of the
  default transparent/white circle.
- `compass.ticks` — tick marks around the rim (`show: true`, light-grey
  `#90a4ae`, `radius: 52`).
- `compass.north/east/south/west` — the four cardinal letters (N/E/S/W) are
  shown around the dial (hidden by default in this card).
- `header.icon` — a matching mdi icon in the card header (`mdi:weather-windy`
  for Wind, `mdi:compass-outline` for COG).
- The indicator arrows themselves are now colored (`#4fc3f7` cyan for Wind,
  `#ff7043` orange for COG) instead of the default color, for contrast
  against the dark dial.
- The COG compass also gained SOG as a `value_sensors` entry (previously it
  only showed the bare COG arrow with no number at all) — SOG is still also
  shown separately as its own gauge in "Speed & Depth", this just adds it
  under the compass too since course+speed over ground are a natural pair.

See the [compass-card wiki](https://github.com/tomvanswam/compass-card/wiki/1.-YAML-configuration)
for the full list of `compass:` sub-options (background images, per-cardinal
offsets, dynamic styling by entity value, etc.) if further styling is wanted.

## Wind History & Forecast troubleshooting

**Root cause found and fixed (2026-08-09):** the "Forecast (kts)"/"Gusts
(kts)" series' `data_generator` JS declared `const start = ...`, but
apexcharts-card invokes `data_generator` as
`new Function('entity','start','end','hass','moment', code)` — i.e. `start`
is already a function **parameter** name. Redeclaring it with `const`
inside the function body is a `SyntaxError`
(`Identifier 'start' has already been declared`), which throws
synchronously on every single evaluation — the forecast/gust series
therefore NEVER rendered, on any browser, cache state, or hard-refresh
(confirmed by replaying the exact minified string through Node's
`new Function(...)`, reproducing the throw outside HA entirely). The
`data_generator` strings now use `rangeStart` instead of `start` for the
local variable to avoid the collision.

Everything else audited on the live instance beforehand checked out fine
and was **not** the cause: `api.open-meteo.com` reachability (HTTP 200 with
populated `hourly.windspeed_10m`/`windgusts_10m`), `sensor.wind_forecast_flat`
attribute population (48 hourly points, recent `last_updated`),
`apexcharts-card.js` install/registration in `.storage/lovelace_resources`,
byte-for-byte match between deployed `.storage/lovelace.dashboard_sailing`
and the YAML, and `fill_raw: 'null'` (apexcharts-card's own default, not a
bug). `cache: false` is also set on the card as a defensive measure against
`apexcharts-card`'s `localStorage`-based history cache (which a plain
hard-refresh does not clear), though the `start`-shadowing bug above was the
actual cause of the blank forecast — not caching.

**Second half of the same bug (2026-08-09):** after fixing the JS, the chart
*still* showed nothing, because `deploy_dashboard.sh` did not restart HA and
HA kept serving the old in-memory config — see "Why the dashboard deploy
restarts HA" above. Both halves are now fixed; the websocket `lovelace/config`
response was confirmed to contain `rangeStart`, and `sensor.wind_forecast_flat`
to carry 48 hourly wind/gust points.

If the forecast/gust lines are still blank after redeploying this fix, open
the browser console (`data_generator` exceptions, if any, print there) and
re-open this investigation with that detail.

### Wind Direction — windrose (history) + forecast arrows (rewritten 2026-08-09, v3)

Two earlier attempts at this were replaced entirely after user feedback
(v1: numeric degrees 0–360 on an ApexCharts Y axis — meaningless for a
compass bearing, wraps at 360°/0°; v2: a strip of 8 large rotated
`mdi:navigation` tile icons — real arrows, but not a "graph", and the
icons were too large). The section is now split into two parts:

1. **History — `custom:windrose-card`.** A genuine polar windrose diagram
   (github.com/aukedejong/lovelace-windrose-card v2.4.2) plotting the real
   distribution of *measured* wind direction (`sensor.wind_direction_history`)
   and speed over the last 24h (`data_period.period_back: -24h`), with a red
   arrow (`current_direction.show_arrow`) for the current reading. This
   card queries actual recorder **history** for its entities, which is
   exactly what it's designed for — unlike ApexCharts/compass-card, it
   natively renders true direction data as a rose, not a misleading line.
   - **Position changed 2026-08-09 (~13:40) — user edit in the HA UI.** The
     rose used to live in its own separate "Wind Direction — History (rose)"
     subsection, below the old wind `compass-card`. The user replaced the
     wind `compass-card` at the very top of "Wind & Forecast" with this
     windrose card directly and deleted the now-redundant subsection — there
     is only **one** windrose card now, at the top of the section, and the
     wind `compass-card` is gone entirely (the COG `compass-card` in
     "Position" is unaffected). This file was re-synced from `.storage`
     to match; see the rule at the top of `dashboard-sailing.yaml`.
   - **Not installed via HACS** (it isn't one of the 3 cards in
     `requirements-ha.txt`): the `.js` was downloaded from the GitHub
     release and manually copied to `/config/www/windrose-card.js` on
     `bumblebee.local`, then registered as a `module` resource
     (`/local/windrose-card.js?v=2.4.2`) directly in
     `.storage/lovelace_resources` (same mechanism the deploy scripts use
     for the dashboard itself). To reproduce on a fresh instance: download
     `windrose-card.js` from
     https://github.com/aukedejong/lovelace-windrose-card/releases/latest,
     copy it to `config/www/`, and add it as a Lovelace resource (URL
     `/local/windrose-card.js`, type `module`) via Settings → Dashboards →
     Resources, or HACS if it's since been added to the default store.
   - **Wind speed is shown three ways (2026-08-09):** the rose already
     encoded speed via `windspeed_entities` (petal color by speed bucket)
     and `windspeed_bar_location: right` (the vertical color-scale legend)
     from the initial v3 rewrite — a `corner_info.top_right` block was
     added on top of that to also print the current numeric wind speed
     (in knots) in the card's corner, for an exact reading alongside the
     visual rose.
2. **Forecast — replaced 2026-08-09 by a real timeline chart.** The
   original compact row of 8 rotated `mdi:navigation` tiles (one per
   forecast hour, `+0h`...`+21h`) was explicitly rejected by the user
   ("не то что нарисовал" / "not what I need") in favor of a proper
   time-series view like
   https://community.plotly.com/t/wind-direction-and-speed-timeline/94120/3
   — a single `custom:apexcharts-card` **"Wind Direction & Speed —
   Timeline"** was added right below the windrose card (the windrose stays
   as-is, this is an addition, not a replacement for it): X axis is time,
   the **left** Y axis (`yaxis_id: speed`) plots wind speed (measured
   history as an area + open-meteo forecast/gust as dashed lines, same
   series as the top "Wind — History & Forecast" chart), and the **right**
   Y axis (`yaxis_id: direction`, `min: 0`/`max: 360`) plots wind direction
   — measured history from `sensor.wind_direction_history` (as markers,
   `stroke_width: 0`) and the open-meteo `forecast_dir` array (as a dashed
   line via the same `data_generator` pattern already used for
   `forecast_wind`/`forecast_gust`). Both axes share the same time-based
   X axis, so direction and speed, history and forecast, are all visible
   together on one chart, matching the linked reference's layout.

Neither `apexcharts-card` nor `compass-card` support per-data-point marker
rotation (confirmed by reading the installed `apexcharts-card.js` source
directly — only a per-series `data_generator`/`transform` JS hook exists,
no generic per-point image/rotation API), so unlike the linked plotly
example this chart shows direction as a plain numeric Y value (0-360°)
rather than rotated wind-barb icons; the windrose card above remains the
place where direction is shown visually as an actual compass rose.

**"Configuration error" screenshot (2026-08-09, `value.series[0].type is
none of "line","column","area"`):** re-extracted `apexcharts-card.js`
(v2.2.3) directly from the live container and checked its embedded config
schema (`ChartCardAllSeriesExternalConfig`) — `type` really only allows
`line`/`column`/`area` (no `scatter`, confirming the earlier `scatter` bug
elsewhere in this doc) and `yaxis_id` **is** a valid, schema-supported
per-series field. Every series in both the "Wind — History & Forecast" and
"Wind Direction & Speed — Timeline" charts as currently deployed uses only
`area`/`line`, so this specific config passes schema validation — the
error in that screenshot does not reproduce against the config now live on
`bumblebee.local`. If it still shows up after a hard-refresh, capture the
browser DevTools console output (the exact failing card's config is logged
there) so the real offending card/series can be pinpointed.

**Second "Configuration error" (2026-08-09, `value.series[3] is not a
ChartCardSeriesExternalConfig`, `value.series[3].apex_config is
extraneous`):** the actual bug this time — the 4th series (index 3,
"Direction (°)") in the "Wind Direction & Speed — Timeline" chart had its
own `apex_config: {markers: {size: 3}}` block nested directly inside the
series item. `apex_config` is **card-level only** in the apexcharts-card
schema (confirmed via `ChartCardAllSeriesExternalConfig` — no `apex_config`
property exists per-series), so the strict schema validator rejected it as
an unknown ("extraneous") key on every load. Fixed by moving the marker
size up to the card's top-level `apex_config.markers.size`, as an array
with one entry per series in order (`[0, 0, 0, 3, 0]` — only the
"Direction (°)" series at index 3 gets a visible marker, all others stay
at `0` to match their existing `stroke_width`-based styling).

**Chart stuck forever "loading" / never drew anything (2026-08-09):**
this was a real *runtime* bug, distinct from both schema errors above and
not visible as a config-error card — confirmed with a headless
Playwright session (auth via `hassTokens` in `localStorage` seeded with a
long-lived token) against the live dashboard: the browser console showed
`Cannot read properties of undefined (reading 'push')` (a `pageerror`,
i.e. an uncaught exception inside `apexcharts-card`) exactly when the
"Wind Direction & Speed — Timeline" card rendered, while every other card
on the page (including the near-identical "Wind — History & Forecast"
chart) worked fine. The one structural difference was the dual-Y-axis
setup (`apex_config.yaxis` as an array of `{id, seriesName, ...}` objects
+ per-series `yaxis_id: speed`/`direction`) mixing real recorder-history
series with `data_generator` series on two different axes — apexcharts-
card v2.2.3's internal per-axis data grouping throws on that combination.
**Fix:** dropped the dual-axis/combined-speed-and-direction design
entirely; the chart is now direction-only ("Wind Direction — History &
Forecast", renamed from "…& Speed — Timeline"), single implicit Y axis
(0-360°, `tickAmount: 6`), same shape as the working "Wind — History &
Forecast" chart above it. Speed is already covered by that other chart, so
nothing is lost, only the risky multi-axis combination is gone. Verified
by re-running the headless check after redeploy: no more `pageerror`s, and
the chart now visibly draws the direction history line + forecast dashed
line in the screenshot.

### Wind vector/arrow chart — apexcharts-card limitation and the plotly-graph-card plan (2026-08-09)

The user pointed to a reference chart
(`ha/sailing-dash/examples/wind-diraction-n-speed.png`, made with Python's
Plotly): X axis = time, Y axis = wind speed, and each data point is drawn
as a **rotated arrow** (rotation = wind direction, color/length = speed) —
i.e. a proper wind-vector/quiver plot, not a line. This is fundamentally
different from every chart built so far on this dashboard (all plain
line/area charts with a numeric Y value), and it's **not achievable with
apexcharts-card**: its `data_generator`/series API only supports numeric
`[x, y]` points rendered as line/column/area — there is no per-point
marker rotation or custom shape/image API in the library (confirmed
earlier by reading `apexcharts-card.js`'s bundled ApexCharts core; this is
also why the `custom:windrose-card` above, not apexcharts-card, is what
renders the actual compass-rose visualization on this dashboard).

**Researched replacement: `custom:plotly-graph` (`lovelace-plotly-graph-card`,
github.com/dbuezas/lovelace-plotly-graph-card, HACS default store, 637★).**
It bundles Plotly.js `^2.34.0` directly (the same charting library used to
render the reference screenshot) and lets YAML config set **arbitrary**
Plotly `Scatter` trace properties (see
https://plotly.com/javascript/reference/scatter/#scatter) — including
`marker.symbol`, `marker.angle`/`angleref` (rotated markers, added in
Plotly.js 2.10, well below the bundled 2.34) and `marker.color`/
`colorscale` — so it can reproduce the reference chart exactly, unlike
apexcharts-card. It also supports:
- `filters:` — a per-entity pipeline of transforms (`map_y`, `resample`,
  `store_var`, arbitrary `fn:`) to reshape/align history data, including
  **custom JS filters that receive `hass`** (so pulling
  `sensor.wind_forecast_flat` attributes for the forecast half of the
  chart works the same way the `data_generator` strings did for
  apexcharts-card).
- **Universal functions** (`$fn`/`$ex`) usable at **any** YAML key,
  including nested ones like `marker.angle` — so direction data collected
  from one entity via `store_var` can be read back into another entity's
  `marker.angle` via `vars.<name>.ys`.

**DEPLOYED (2026-08-09) on `bumblebee.local`** as the "Wind Direction &
Speed — Vector Chart" card in `dashboard-sailing.yaml`, installed via
`./deploy.sh --update` (see "Deploying" below — this is now the ONLY
supported way to push the manually-installed `.js` bundle + resource
registration + the dashboard config together). Confirmed rendering real
data end-to-end via a headless Playwright check against the live instance
(no `Configuration error`, `PLOTLY-GRAPH 3.3.5 production` logged, arrows +
"Now" line + colorbar visible on a screenshot).

Two visible dot traces mark the actual (time, speed) data points (measured
history + open-meteo forecast, colored by a fixed 0-40kt scale), and
direction is drawn separately as real Plotly **annotation arrows** (a
proper shaft ending in an arrowhead, `->`, not a rotated marker shape — see
"Why annotation arrows, not a rotated marker" below for why an
`marker.symbol: 'arrow'` variant was tried and then reverted: it still
looked like a plain triangle, same ambiguity problem as the earlier
rejected shapes). **Known limitation:** the legend
click ("Measured"/"Forecast") only toggles trace visibility, not this
separate annotation layer, so the arrows always show regardless of the
toggle (see the long comment in the config below).

```yaml
type: custom:plotly-graph
hours_to_show: 30
entities:
  # 1) direction history, hidden — just feeds vars.dir for the arrows below
  - entity: sensor.wind_direction_history
    internal: true
    filters:
      - resample: 30m
      - map_y: parseFloat(y)
      - store_var: dir
  # 2) measured wind speed - visible dots + vars.speed for the arrows below
  - entity: sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed
    name: Measured
    mode: markers
    filters:
      - resample: 30m
      - map_y: parseFloat(y)
      - store_var: speed
    marker: { size: 5, color: $ex ys, colorscale: WIND_SPEED_COLORSCALE, cmin: 0, cmax: 40, showscale: true, colorbar: { title: { text: kt, side: top }, ticksuffix: " kt", len: 0.9 } }
  # 3) open-meteo forecast speed + direction - same pattern as (2)/(1)
  - entity: sensor.wind_forecast_flat
    name: Forecast
    mode: markers
    extend_to_present: false
    filters:
      - fn: |-
          ({ meta }) => ({
            xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),
            ys: (meta.forecast_wind || []),
          })
      - fn: |-
          ({ meta, vars }) => { vars.forecastDir = meta.forecast_dir || []; return {}; }
      - store_var: forecastSpeed
    marker: { size: 5, color: $ex ys, colorscale: WIND_SPEED_COLORSCALE, cmin: 0, cmax: 40 }
layout:
  yaxis:
    title: Wind speed (kts)
    rangemode: tozero
  # Fix (2026-08-09): without this, "Now" doesn't sit ~2h from the left
  # edge like on the apexcharts-card chart above (that one is pinned by
  # `span: {start: minute, offset: -2h}` + `graph_span: 26h`; this card has
  # no such option and otherwise autoranges over the entire fetched
  # history+forecast extent). Re-evaluated via $fn on every render so the
  # window always tracks the real current time, same -2h/+24h anchor.
  xaxis:
    range: $fn () => [new Date(Date.now() - 2 * 3600000), new Date(Date.now() + 24 * 3600000)]
  # Bottom horizontal legend, matching the apexcharts-card chart above it
  # (Plotly's default is a vertical legend on the right, inconsistent with
  # every other chart's bottom legend on this dashboard).
  legend: { orientation: h, x: 0.5, xanchor: center, y: -0.3 }
  # Fix (2026-08-09): the legend (y: -0.3) was overlapping the x-axis's
  # two-row date+time tick labels underneath the default margin - the card
  # doesn't auto-grow its bottom margin for the legend, so it has to be
  # reserved explicitly.
  margin: { b: 70 }
  # One real Plotly *annotation arrow* per data point (shaft via ax/ay in
  # pixel space + an arrowhead at x/y, angle = compass bearing, 0deg=up=
  # North, clockwise), plus the "Now" vertical line/label + a fixed N/S
  # legend (plotly-graph-card has no built-in `now:` feature like
  # apexcharts-card, so this is built by hand via $fn, re-evaluated on
  # every render so "Now" always tracks the actual current time).
  # KNOWN LIMITATION (unresolved): because this is a separate
  # `layout.annotations` array (not part of either data trace), it does
  # NOT react to clicking "Measured"/"Forecast" in the legend - a legend
  # click only ever native-toggles a *trace*'s visibility (Plotly
  # restyle), and this $fn has no way to read that state back (confirmed
  # by reading the bundled source - its call signature has no
  # trace-visibility argument). A real fix would need e.g. an
  # `input_boolean` HA helper read via `hass` inside this `$fn` plus a
  # toggle switch added to the dashboard (not done).
  annotations: >-
    $fn ({ vars }) => { const windSpeedColor = (v) => {
    const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];
    for (const [max, color] of stops) if (v < max) return color;
    return '#8e1b8e'; }; const toArrows = (xs, ys, dirs) =>
    (xs || []).map((x, i) => { const rad = ((dirs[i] || 0) * Math.PI) / 180;
    const len = 10 + (ys[i] || 0); return { x, y: ys[i], xref: "x", yref: "y",
    ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: "pixel",
    ayref: "pixel", showarrow: true, arrowhead: 2, arrowsize: 1,
    arrowwidth: 1.5, arrowcolor: windSpeedColor(ys[i] || 0) }; }); const arrows = [
    ...toArrows(vars.speed.xs, vars.speed.ys, vars.dir.ys),
    ...toArrows(vars.forecastSpeed.xs, vars.forecastSpeed.ys, vars.forecastDir),
    ]; return [ ...arrows,
    { xref: "x", yref: "paper", x: new Date(), y: 1, yanchor: "bottom",
    text: "Now", showarrow: false, font: { color: "#ffffff", size: 10 } },
    { xref: "paper", yref: "paper", x: 0, y: 1, xanchor: "left",
    yanchor: "bottom", text: "▲ N &nbsp;&nbsp; ▼ S", showarrow: false,
    font: { color: "#90a4ae", size: 10 } } ]; }
  shapes: >-
    $fn () => [{ type: "line", xref: "x", yref: "paper", x0: new Date(),
    x1: new Date(), y0: 0, y1: 1,
    line: { color: "#ffffff", width: 1, dash: "dot" } }]
```

**Why annotation arrows, not a rotated marker (2026-08-09, 2 revisions):**

1. First attempt used `marker.symbol: triangle-up` + `marker.angle`. The
   user correctly pointed out this is ambiguous — a triangle rotated 0°
   vs. 180° looks nearly identical at a glance, so "where's north" wasn't
   actually readable.
2. Second attempt switched to `arrow-bar-up` (arrowhead + a straight bar
   across the tail) to make rotation legible. The user rejected this too
   — visually it reads as "a chord cut across the top of a triangle", not
   a directional vector; there was no visible **shaft** pointing away from
   where the wind blows from, which is what makes an arrow unambiguously
   readable as `->` rather than just "a rotated shape".
3. Third attempt: real Plotly `annotation` arrows (`ax`/`ay` in pixel
   space for the tail, `x`/`y` for the arrowhead), computed once per
   render from `vars.speed`/`vars.dir` into a separate `layout.annotations`
   array. Visually correct (clear shaft+arrowhead), **but** this
   introduced a *new*, unrelated bug the user caught: clicking
   "Measured"/"Forecast" in the legend natively hides/shows a Plotly
   *trace*, but the annotations layer isn't a trace — it's computed once
   from `vars` regardless of which traces are currently toggled — so the
   arrows kept showing even after hiding a trace ("toggle doesn't hide
   data, always shown").
4. **Final (2026-08-09): `marker.symbol: 'arrow'` directly on the data
   trace**, rotated per-point via `marker.angle` (array, degrees,
   0°=North=up the screen, clockwise — reusing the exact `vars.dir`/
   `vars.forecastDir` arrays that used to feed the annotation `$fn`).
   `arrow` is a different symbol from the same Plotly family as the
   rejected `arrow-bar-up` — a real shaft+arrowhead, so it keeps the
   "attempt 3" fix for direction-ambiguity — but because it's now the
   trace's own marker instead of a side-channel annotation, hiding a trace
   via the legend natively hides its arrows too. This also made the
   `layout.annotations` block much simpler: it now only builds the "Now"
   label + N/S legend (the arrows moved onto the entities above), and
   `legend.groupclick: togglegroup` was added in case a group ever gains a
   second trace. Verified visually in `local-preview/`: arrows render with
   a clear shaft+arrowhead rotating with direction, on the same trace as
   the data point.

**Wind-speed color scale (2026-08-09):** the dot markers' colorbar (a
gradient legend, sometimes described as "the thermometer on the right")
originally used Plotly's default `colorscale: RdYlGn, reversescale: true`
— an arbitrary green-to-red gradient with no fixed reference range (it
auto-scales to whatever's visible), and no relation to any real
meteorological convention. Replaced with an explicit `WIND_SPEED_COLORSCALE`
(defined once in `local-preview/card-configs.js`, applied to both the
"Measured" and "Forecast" dot markers): a fixed **0–40kt** range
(`cmin`/`cmax`) with color stops matching the convention used by marine
wind-speed charts (windy.com's wind layer, NOAA wind maps) — calm = light
blue, rising through green/yellow/orange/red, gale-force (35kt+) = purple —
plus a labeled colorbar (`title: 'kt'`, `ticksuffix: ' kt'`) so the legend
has a stable, learnable meaning across reloads instead of shifting its
range to match whatever data happens to be on screen. The direction arrows
(previously colored per-trace, blue for measured/orange for forecast, with
no relation to speed at all) now use the **same** color-to-speed mapping
(a duplicated discrete `windSpeedColor()` helper inside the `$fn` string,
since it can't reference the outer JS constant directly) — so a point's
color means "wind speed" consistently everywhere on the chart, and
measured-vs-forecast is distinguished only by position/shape, not color.

**Not installed/deployed on `bumblebee` yet** — pending the user's
decision on whether to proceed (adds a full Plotly.js bundle to the
dashboard's JS payload, on top of apexcharts-card/compass-card/
windrose-card already loaded). If approved, install the same way as
`windrose-card` (manual `.js` copy to `/config/www/` + `lovelace_resources`
entry, since it isn't in the current `requirements-ha.txt` HACS list
either) and redeploy `local-preview/card-configs.js`'s already-tuned
config above rather than starting from scratch.

### Windy alternative view (2026-08-09)

As an alternative to the `apexcharts-card` chart (which only plots
"Measured" wind + the open-meteo REST forecast/gust), the "Wind History &
Forecast" section now also embeds a Windy `type: iframe` widget
(`embed.windy.com/embed2.html?...`) right below the chart — Windy's own map
shows both wind history playback (via the calendar/timeline control inside
the widget) and its multi-model forecast for the same spot, as a visual
cross-check against the open-meteo series.

**No separate "Open Windy" button anymore (2026-08-09):** it was removed,
and the whole widget area is now tappable/clickable directly — clicking or
tapping anywhere on the embedded Windy map opens `windy.com` (browser on
desktop, the Windy app on mobile if installed, same as the old button's
behavior — see below for why). This is implemented with a `card-mod` CSS
trick, since HA's built-in `type: iframe` card has no `tap_action` of its
own: the iframe and an invisible `type: button` card are placed in the same
`type: grid` section and forced (via `card_mod` on the grid and on the
button) into the same CSS grid cell, so the fully transparent button's
`ha-card` sits on top of, and the same size as, the iframe, intercepting
taps/clicks and forwarding them to its `tap_action`.

The button's `tap_action: {action: url, url_path: https://www.windy.com/...}`
gets the "browser on desktop, app on mobile" behavior for free: on
iOS/Android, `windy.com` links are registered by the official Windy app as
Universal Links / App Links, so if the app is installed the OS opens it
there directly on tap; if it isn't installed (or on a desktop browser), the
same link simply opens in the regular web browser. No extra HA-side logic
(browser_mod, custom conditions, etc.) is needed for that part — it's
handled entirely by the mobile OS's link-routing.

The iframe widget and the transparent overlay button currently use the
boat's last known anchorage coordinates (42.43/18.60), matching the
map/forecast defaults elsewhere on this dashboard — update both entries in
`dashboard-sailing.yaml` (and redeploy) if the boat moves to a new home
location for an extended period; making them follow the live GPS position
like the open-meteo `rest:` sensor does would require a
`card-mod`/`card_templater`-based templated `url`, which is a possible
follow-up if wanted.

**Note:** the card-mod overlay/grid-stacking CSS trick above depends on the
exact DOM structure `type: grid` and `type: button` cards render, which can
vary slightly across HA frontend versions — if tapping the widget doesn't
open Windy after redeploying, open the browser DevTools inspector on the
widget to confirm the button's `ha-card` actually covers the iframe area,
and adjust the `card_mod` selectors/styles in `dashboard-sailing.yaml`
accordingly.

### Forecast location follows the boat's GPS

As of 2026-08-09, the `rest:` sensor in `sensors-sailing.yaml` uses
`resource_template` (Jinja) instead of a static `resource` URL, so the
open-meteo query's `latitude`/`longitude` are read live from the same N2K
position sensor the map/`boat_latitude`/`boat_longitude` entities use
(falling back to the boat's last known anchorage, 42.43/18.60, if that
sensor is ever unavailable) — the wind forecast now follows the boat instead
of a fixed point. A native "Open-Meteo" HA integration also exists but was
evaluated and rejected for this: its hourly forecast only exposes
condition/precipitation/temperature, not wind speed/gust (those are only
available as a daily max or in "current"), so it cannot drive this chart.

## Editing the sensors/services (e.g. changing the open-meteo forecast location)

1. Edit `sensors-sailing.yaml` in this repo (e.g. the `latitude`/`longitude`
   query params in the `rest:` resource URL).
2. Run `./deploy_sensors.sh` (or `./deploy_sensors.sh user@host`). It merges
   the file into the remote `configuration.yaml` by matching `unique_id`
   (safe to re-run — updates existing entries in place) and restarts HA,
   since `configuration.yaml` changes are not hot-reloadable.

## Requirements

- Local machine: `python3` + `PyYAML` (`pip install pyyaml`, now tracked in
  the repo root `requirements.txt`) to run the YAML → JSON conversion in
  `deploy_dashboard.sh` / the YAML merge in `deploy_sensors.sh`. Optionally
  `websockets` (also in `requirements.txt`) to verify the live
  `lovelace/config` (see "Why the dashboard deploy restarts HA" above).
- Remote host: passwordless `sudo` for `docker exec`/`docker cp`/
  `docker restart` against the `homeassistant` container (same requirement
  as `deploy.sh:patch_ha()`).
- HACS custom cards must already be installed on the target HA instance
  (see `lovelace-resources.yaml`, not managed by these scripts):
  `card-mod`, `compass-card`, `apexcharts-card`.
- **Setting up a brand-new HA instance from scratch?** See
  `requirements-ha.txt` for the full checklist (HACS itself, the custom
  cards above, and which parts are already built into HA core) — the
  "Setting this up from scratch" section above covers the deploy order.

## Chart time window — one place to configure it

`sensor.chart_time_window` (`sensors-sailing.yaml`) is the single source of
truth for how much time the charts show *and* how much data is requested
from Open-Meteo. Only two numbers are ever edited:

```yaml
attributes:
  history_hours: "{{ 4 }}"    # measured history left of "Now"
  forecast_hours: "{{ 48 }}"  # forecast right of "Now"  (2 days)
```

Consumers:

* both `rest:` `resource_template` URLs — `forecast_days` is computed as
  `ceil(forecast_hours / 24)`, so the API returns exactly the drawn interval;
* both `custom:plotly-graph` cards —
  `hours_to_show: $fn ({ hass }) => history_hours + forecast_hours` and
  `time_offset: $fn ({ hass }) => forecast_hours + 'h'`. This works because
  `plotly-graph-card` resolves those keys through `getFromConfig`, i.e. the
  same `$fn`/`$ex` evaluator used for traces, and `$fn` receives `hass`.

Never re-add `layout.xaxis.range` — the card merges its own `{xaxis:{range}}`
around the user layout, which snaps X panning back on every redraw.
Verified live: both cards render `Now-4h … Now+48h`, both forecast sensors
return 48 hourly points.
