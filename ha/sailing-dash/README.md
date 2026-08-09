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
  on the remote host first.
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
3. **Position** — map (`device_tracker.iphone_17_promax_nevera`),
   latitude/longitude entities, barometric pressure gauge.
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
   storage-mode dashboard from `dashboard-sailing.yaml`.
4. Install the HA Companion App on the phone that should show up as
   `device_tracker.iphone_17_promax_nevera` on the "Position" section (or
   edit that entity id in `dashboard-sailing.yaml` first).

## Editing the dashboard

1. Edit `dashboard-sailing.yaml` in this repo.
2. Run `./deploy_dashboard.sh` (uses `../../deploy.conf` for the SSH
   target by default, same file `deploy.sh` uses) or
   `./deploy_dashboard.sh user@host` to target explicitly.
3. Reload the dashboard in the browser — no HA restart is required for
   storage-mode dashboard changes to take effect.

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
