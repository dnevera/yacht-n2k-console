# Sailing Dashboard (Home Assistant)

Source-controlled copy of the "Sailing" Lovelace dashboard
(`http://bumblebee.local:8123/dashboard-sailing/`) running on the boat's
Home Assistant instance.

## Why this exists

The dashboard was originally created via the HA UI (a *storage-mode*
dashboard), so its config only lived inside HA's internal state file
`.storage/lovelace.dashboard_sailing` on the target host — not as a plain
file anyone could review, diff, or restore from git. This directory makes
`dashboard-sailing.yaml` the source of truth and provides a script to push
edits back into HA's storage.

## Files

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
- `lovelace-resources.yaml` — reference list of the HACS frontend resources
  (`card-mod`, `compass-card`, `apexcharts-card`) the dashboard's custom
  cards need; these are installed/managed via HACS in the UI, not deployed
  by a script — this file just documents what must be present.

## Dashboard layout (re-synced 2026-08-09, after the user rearranged tiles in the HA UI)

1. **Wind & Forecast** — `compass-card` (Wind angle/speed) + `apexcharts-card`
   (measured wind vs. forecast wind/gusts pulled from
   `sensor.wind_forecast_flat` attributes).
2. **Weather & Forecast** — the Windy `iframe` widget (alternative
   forecast/history view, tap-to-open windy.com, see "Windy alternative
   view" below), a barometric pressure gauge, and a pressure `tile` card
   with a `trend-graph` feature.
3. **Position** — COG `compass-card` (now also showing SOG as its value
   sensor), map (`device_tracker.nevera`, the boat's own N2K GPS position —
   see "Boat position on the map" below), latitude/longitude entities.
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

## Setting this up from scratch on a new HA instance

1. Install the HACS custom cards listed in `lovelace-resources.yaml`
   (`card-mod`, `compass-card`, `apexcharts-card`) via HACS → Frontend.
2. Run `./deploy_sensors.sh` to add the open-meteo forecast pipeline and
   derived sensors/`device_tracker` from `sensors-sailing.yaml` — this
   restarts Home Assistant, so do it first and wait for it to come back up.
3. Run `./deploy_dashboard.sh` to create/overwrite the `dashboard-sailing`
   storage-mode dashboard from `dashboard-sailing.yaml`. The map card uses
   `device_tracker.nevera`, deployed by `./deploy_sensors.sh` above — no
   phone/Companion App setup is required for the map to work (see "Boat
   position on the map" below).

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
2. Run `./deploy_dashboard.sh` (uses `../../deploy.conf` for the SSH
   target by default, same file `deploy.sh` uses) or
   `./deploy_dashboard.sh user@host` to target explicitly. Review the
   printed pre-deploy diff before it uploads.
3. Reload the dashboard in the browser (the script already restarted HA,
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
2. **Forecast — small rotated arrow tiles.** open-meteo's
   `forecast_dir`/`forecast_wind` are a fixed-length **array** on one
   sensor's attributes, not per-hour recorder history — `windrose-card`
   (like `apexcharts-card`/`compass-card`) has no way to plot that. Kept as
   a compact row of 8 `type: tile` cards (`type: grid`, `columns: 8`), one
   per forecast hour from now (`+0h`, `+3h`, ... `+21h`), each showing a
   *small* (`--tile-icon-size: 22px`, down from the tile card's oversized
   default) `mdi:navigation` icon rotated via `card_mod` to that hour's
   forecast direction, degrees shown as the label. A `card_mod` Jinja
   template on each tile locates **today's current hour** inside
   `forecast_time` (matching `utcnow()`), then reads
   `forecast_dir[found_index + N]` for offset `N` — needed because
   `forecast_time`/`forecast_dir` always start at *midnight UTC* of the
   query day, not "now", so a naive fixed index would point at the wrong
   hours for most of the day.

Neither `apexcharts-card` nor `compass-card` support per-data-point marker
rotation on a time-series chart (confirmed by reading the installed
`apexcharts-card.js` source directly — only a per-series `data_generator`/
`transform` JS hook exists, no generic per-point image/rotation API) —
that's the reason a dedicated windrose card was added instead of trying to
force this into a line chart.

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
