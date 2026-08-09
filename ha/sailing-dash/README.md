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

## Dashboard layout (as of extraction)

1. **Wind & Heading** — two `compass-card`s (COG, Wind angle/speed).
2. **Wind History & Forecast** — `apexcharts-card` (measured wind vs.
   forecast wind/gusts pulled from `sensor.wind_forecast_flat` attributes)
   + a button linking out to Windy.com.
3. **Position** — map (`device_tracker.nevera`, the boat's own N2K GPS
   position — see "Boat position on the map" below), latitude/longitude
   entities, barometric pressure gauge.
4. **Speed & Depth** — SOG gauge, STW gauge, Depth gauge (each in its own
   grid section; the last section title still says "New section" in HA —
   left as-is to match the live dashboard, rename in the YAML + redeploy
   if desired).

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

1. Edit `dashboard-sailing.yaml` in this repo.
2. Run `./deploy_dashboard.sh` (uses `../../deploy.conf` for the SSH
   target by default, same file `deploy.sh` uses) or
   `./deploy_dashboard.sh user@host` to target explicitly.
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

- Local machine: `python3` + `PyYAML` (`pip install pyyaml`) to run the
  YAML → JSON conversion in `deploy_dashboard.sh` / the YAML merge in
  `deploy_sensors.sh`.
- Remote host: passwordless `sudo` for `docker exec`/`docker cp`/
  `docker restart` against the `homeassistant` container (same requirement
  as `deploy.sh:patch_ha()`).
- HACS custom cards must already be installed on the target HA instance
  (see `lovelace-resources.yaml`, not managed by these scripts):
  `card-mod`, `compass-card`, `apexcharts-card`.
