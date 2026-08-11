# Changelog — Sailing Dashboard

All notable changes to `ha/sailing-dash/` (dashboard, sensors, deploy
tooling, `local-preview/` test rig). Dates are when the change was made
in this repo, not necessarily when it went live on `bumblebee.local` (some
entries are DRAFTs, not yet deployed — noted explicitly).

Format: reverse-chronological, one bullet per change. Full technical
write-ups/rationale for the entries below still live in `README.md` and
`local-preview/README.md` (this file is an index/summary, not a
replacement for that detail) and in `.agents/skills/nmea2000-setup/SKILL.md`.

## 2026-08-11

- **Fixed: ApexCharts wind chart — arrows were still a separate plaque under the chart, tooltip/colours didn't match Plotly, `k` instead of `kts`, no colour legend by the Y axis:**
  - **Arrows genuinely ON the chart now.** Replaced the two sibling cards (`custom:apexcharts-card` + `custom:wind-arrows-row-card` stacked via a `margin-top: -232px` hack) with a single new wrapper card, `src/js/cards/wind-chart-with-arrows-card.js` (`custom:wind-chart-with-arrows-card`). It creates the nested `custom:apexcharts-card` via HA's own `loadCardHelpers().createCardElement()` and paints the arrow row + colour legend as an absolutely-positioned SVG layer in the exact same shadow DOM — pixel-locked to the chart regardless of its height, instead of a fragile cross-card negative margin.
  - **Colour legend moved next to the Y axis (left edge)**, as a vertical 0–40+ kt gradient strip, mirroring where Plotly's own colourbar sits relative to the chart.
  - **Unified tooltip in the Plotly style.** Added `src/js/common/apex_wind_tooltip.js`, a custom ApexCharts `tooltip.custom` formatter (date/time header + one coloured row per series, all values in `kts`) wired in via `$include:apex_wind_tooltip`, replacing the generic `tooltip.shared` box that never matched the rest of the dashboard. `build.py` gained `"custom"` to `RAW_INCLUDE_KEYS` so this raw-JS key is inlined without the `$fn ` wrapper `apexcharts-card` doesn't expect.
  - **`3k` → `3.0 kts`, arrow colour by speed.** The stray `${spd}k` label under each arrow (from an earlier, not-yet-deployed revision) is gone — the new card never renders per-arrow text, only a `title` tooltip in `kts`; arrow stroke/fill colour is `windSpeedColor(spd)` (same 8-stop scale as Plotly's "kt scale" colourbar).
  - Updated `04_wind_apexcharts.yaml` to the single-card layout (`chart_config` holds the former `apexcharts-card` config), `build.py::build_dashboard()` to inject `history_hours`/`forecast_hours`/`arrow_spacing_hours` on the wrapper and `graph_span`/`span.offset` into its nested `chart_config`, and `lovelace-resources.yaml` to load `wind-chart-with-arrows-card.js` instead of the removed `wind-arrows-card.js`.
  - Tests: `tests/js/wind_chart_snippets.test.js` and `tests/test_sailing_dash.py` updated for the new card/file names; all previously-passing assertions (colour-scale parity, injected time window, spacing, no `history.map` bug) kept and re-verified. Deployed to stage (`./deploy.sh --stage --update`).

- **Fixed: ApexCharts wind chart (`chart_engine: apexcharts`) — missing NMEA history, non-reusable style, boxed arrow row:**
  - **No NMEA history:** the `Measured` series used `data_generator` reading a `history` variable that `custom:apexcharts-card` never provides to that callback (it only receives `entity`/`hass`) — the series silently produced zero points. Removed `data_generator` for that series entirely; the card now fetches and plots `sensor.boat_wind_speed`'s own recorder history over `graph_span` the standard way, exactly like every other measured trace on the dashboard.
  - **Non-reusable tooltip/style:** extracted the wind speed colour scale (`windSpeedColor`, previously only living inline in `plotly_wind_annotations.js`) into a new shared module `src/js/common/wind_chart_style.js`, and aligned `apex-wind.js`'s series colours (Measured/Forecast/Gusts) to the same palette used everywhere else on the dashboard, instead of a third, unrelated set of colours.
  - **Boxed arrow row:** `wind-arrows-card.js` no longer renders as a separate `ha-card` plaque below the chart. It is now a transparent, `pointer-events: none` overlay stacked directly on top of the chart card (Open-Meteo preview style — arrows sit above the plot, not next to it) via a `card_mod` negative top margin. Arrows are now: (a) coloured by wind speed using the same `windSpeedColor` scale as Plotly's "kt scale" colourbar, (b) spaced by a new configurable `sections.wind.arrow_spacing_hours` (default 3h) instead of a fixed point count, so the row reads as a handful of vectors rather than a dense wall of icons, and (c) labelled in knots (title text), not `k`. A vertical CSS colour-legend strip (0–40+ kt) is drawn next to the arrows as an ApexCharts equivalent of Plotly's colourbar.
  - `helpers/build.py::build_dashboard()` now injects `history_hours`/`forecast_hours`/`arrow_spacing_hours` into `custom:wind-arrows-row-card`, keeping the overlay's time axis in lock-step with the chart it floats above; `config.yaml.template` and `helpers/configure.py`'s wizard gained `sections.wind.arrow_spacing_hours`.
  - Tests: `tests/js/wind_chart_snippets.test.js` gained a regression check that `wind_chart_style.js` and the copy of `windSpeedColor` inlined in `wind-arrows-card.js` agree on every sample; `tests/test_sailing_dash.py` gained assertions for the injected time-window/spacing values and for the removed `history.map` bug. 36 Python tests, all JS tests passed.

- **Added: ApexCharts wind chart engine (`chart_engine: apexcharts`), Open-Meteo preview style:**
  Third `sections.wind.chart_engine` option alongside `plotly`/`open_meteo_sdk`, built as a
  standard Lovelace custom card (`custom:apexcharts-card`, already in `deps.yaml`) rather than
  a hand-rolled SVG renderer. New isolated section file
  `src/yaml/dashboard/sections/04_wind_apexcharts.yaml`: measured NMEA history
  (`sensor.boat_wind_speed`, stops exactly at "Now") on the same time axis as the ECMWF forecast
  speed (filled area) and gusts (dashed line) from `sensor.wind_forecast_flat`, with a shared/unified
  tooltip and a "Now" marker line — matching `examples/open-meteo.png`. The top row of wind-direction
  vector arrows (length scales with speed) is factored into a new reusable card,
  `src/js/cards/wind-arrows-card.js` (`custom:wind-arrows-row-card`), so it can sit above ANY chart
  engine without that engine needing to know about arrows. `helpers/build.py::build_dashboard()`
  now picks exactly one of the three `04_wind*.yaml` variants based on `chart_engine` and, for
  `custom:apexcharts-card`, injects `graph_span`/`span.offset` from `config.yaml`'s `time_window`
  (same mechanism as `hours_to_show`/`time_offset` for Plotly). `helpers/configure.py`'s wizard now
  asks for the wind chart engine (`plotly`/`apexcharts`/`open_meteo_sdk`). None of the existing
  `04_wind.yaml` (Plotly), `04_wind_openmeteo.yaml` (SVG) or their JS snippets were modified.
  Fixed a pre-existing bug along the way: the section-config lookup for `04_wind_openmeteo.yaml`/
  `04_wind_apexcharts.yaml` used to strip only the leading `NN_` prefix, landing on the wrong config
  key (`wind_openmeteo`/`wind_apexcharts` instead of `wind`) — all three wind variants now share the
  `sections.wind` config key. Also reverted an accidental default flip in `config.yaml.template`
  (`chart_engine: open_meteo_sdk` left over from local experimentation) back to `plotly`.
- **Added: Universal history series filter module (`src/js/common/plotly_history_series.js`):**
  - Created a clean, reusable JS snippet with detailed Russian code comments explaining the filtering logic line-by-line.
  - Filters out non-finite values (`NaN`, `unknown`, `unavailable`) and timestamps past `Date.now()`.
  - Preserves all measured historical data before/at `Date.now()` for display alongside forecast data, while strictly dropping future points so measurements never bleed into the forecast region in tooltips and charts.
  - Connected across `04_wind.yaml` and `05_waves.yaml` dashboard section cards.
  - Added unit tests in `tests/js/wind_chart_snippets.test.js` validating filtering before and after "Now". All JS snippet tests and 32 Python tests passed.

- **Fixed: excluded measured data from unified tooltips after current time ("Now"):**
  - Created `src/js/common/plotly_drop_future.js` which drops resampled points with timestamps in the future (`> Date.now()`) after `resample: 30m`.
  - Added `plotly_drop_future` filter step to `sensor.wind_direction_history` and `sensor.boat_wind_speed` in `04_wind.yaml`.
  - Removed `hoverdistance: -1` from `04_wind.yaml`, `05_waves.yaml`, `plotly-wind.js`, and `plotly-wave.js`. With `hoverdistance: -1`, Plotly searched infinitely along the X-axis for closest points, forcing measured data from "Now" to appear in tooltips when hovering over forecast times.
  - Added unit test in `tests/js/wind_chart_snippets.test.js` and regression assertion in `tests/test_sailing_dash.py` ensuring `layout.hoverdistance != -1` and `plotly_drop_future` filters out future timestamps. All 32 Python tests and 10 Node JS snippet tests passed.

- **Refactored: extracted all inline JS strings in dashboard YAML sections into standalone JS modules (`src/js/common/`):**
  - Replaced inline multi-line JavaScript strings in `04_wind.yaml` and `05_waves.yaml` with clean `$include:` references.
  - Created 11 new shared JS snippet files in `src/js/common/`: `plotly_wind_gust_bucket.js`, `plotly_forecast_wind_series.js`, `plotly_forecast_wind_store.js`, `plotly_forecast_wind_customdata.js`, `plotly_forecast_gust_series.js`, `plotly_forecast_wave_series.js`, `plotly_forecast_wave_store.js`, `plotly_forecast_wave_customdata.js`, `plotly_forecast_wave_period_series.js`, `plotly_wave_annotations.js`, and `plotly_empty_series.js`.
  - All JS logic across dashboard sections is now modular, cleanly formatted, and maintainable. All 32 Python tests and 10 Node JS snippet tests passed.

- **Fixed: wind chart tooltip showed static measured data when hovering after "Now":**
  - Added `extend_to_present: false` to all history-backed entity traces on the wind chart (`sensor.wind_direction_history`, `sensor.boat_wind_speed` for `Measured` and `Gusts (measured)`).
  - Updated `src/js/common/plotly_drop_non_finite.js` to drop points with timestamps in the future (`> Date.now()`), preventing historical samples from spilling past "Now" into the forecast region.
  - Updated `Gusts (measured)` filter in `04_wind.yaml` to cap bucket end times at `Date.now()`.
  - Added unit test in `tests/js/wind_chart_snippets.test.js` and regression assertions in `tests/test_sailing_dash.py` ensuring history traces do not extend past "Now" and unified hover tooltips in the forecast region show only forecast data. 32 passed.

- **Added: Central build configuration file (`config.yaml`), card tagging, and interactive setup wizard:**
  - Added `config.yaml` / `config.yaml.template` defining customizable chart time windows (`time_window.history_hours: 4`, `forecast_days: 3`) and section/card enablement toggles.
  - Tagged section cards in `src/yaml/dashboard/sections/*.yaml` with unique `id` keys (`stw_gauge`, `depth_gauge`, `sog_gauge`, `hdg_compass`, `cog_compass`, `map`, `latitude`, `longitude`, `windrose`, `barometer_gauge`, `barometer_trend`, `glance`, `chart`, `windy_map`).
  - Implemented `helpers/configure.py` (CLI wizard prompting for time windows and section/card enablement) and integrated `--config` flag into `./install_wizard.sh`.
  - Updated `helpers/build.py`: parses `config.yaml`, filters disabled sections and cards (stripping temporary `id` keys from built dashboard YAML), and injects dynamic `history_hours` and `forecast_hours = forecast_days * 24` attributes into `sensor.chart_time_window`.
  - Added regression test suite in `tests/test_sailing_dash.py` validating config loading, card/section filtering, parameter injection, and `configure.py` execution. 32 passed.
  - Updated documentation in `README.md`, `INSTALLATION.md`, and `.agents/skills/nmea2000-setup/SKILL.md`.

- **Audited: full decoupling of UI cards from physical NMEA sensors & mapping layer verification:**
  - Audited all 6 dashboard section YAMLs (`01` through `06`), automations, and JS cards: 0 direct usages of physical NMEA entities (100% canonical `sensor.boat_*` virtual sensors).
  - Verified installation mapping engine (`helpers/map_nmea_sensors.py` and `deploy_sensors.sh`): binds target vessel's PGN entities to `sensor.boat_*` template sensors in `derived_n2k.yaml` dynamically during deploy.
  - Added automated regression test `test_all_dashboard_and_automation_yaml_files_use_only_virtual_sensors` to `tests/test_sailing_dash.py`. All 30 Python tests and Node JS tests passed.

- **Restored: generic default fallbacks in NMEA mapping engine (`helpers/map_nmea_sensors.py`):**
  - Reverted `DEFAULT_FALLBACKS` in `map_nmea_sensors.py` to generic NMEA entity IDs (`sensor.speed_water_referenced`, `sensor.water_depth`, `sensor.wind_speed`, `sensor.wind_angle`, `sensor.cog`, `sensor.sog`, `sensor.latitude`, `sensor.longitude`, `sensor.pressure`, `sensor.vessel_heading`, `sensor.magnetic_variation`).
  - regenerated `src/yaml/sensors/derived_n2k.yaml` with generic default fallbacks, keeping the UI dashboard completely decoupled from specific hardware PGN primary key hashes.
  - Documented the Virtual Sensors Architecture rule in `.agents/skills/nmea2000-setup/SKILL.md`.
  - Updated regression tests in `tests/test_sailing_dash.py`. 29 passed.

- **Updated: HDG compass card displays True Heading with cardinal direction + bottom-right magnetic details:**
  - `02_position.yaml`: HDG `custom:compass-card` center value displays True Heading (`sensor.boat_heading`) with cardinal abbreviation (`state_abbreviation: { show: true }`, e.g. WNW, NE).
  - Raw magnetic heading (`Mag: 283°`) and variation (`Var: +2.5°`) are displayed as secondary info in the bottom-right corner via `card_mod` styling.
  - `derived_n2k.yaml` & `map_nmea_sensors.py`: `boat_magnetic_variation` availability is linked to heading sensor availability, defaulting variation to 0.0° when PGN variation field is missing/unavailable on the bus (so heading calculation doesn't drop when compass doesn't send variation).
  - Updated regression test `test_position_section_has_hdg_compass_card_before_cog` in `tests/test_sailing_dash.py`. 29 passed.

- **Fixed: magnetic compass entities (`boat_heading_magnetic`, `boat_magnetic_variation`, `boat_heading`) showed `unavailable`:**
  - Root cause: `DEFAULT_FALLBACKS` in `map_nmea_sensors.py` used the wrong primary key hash (`a008...` from `wind_data` instead of `b70b...` from `vessel_heading` PGN 127250). `match_entities()` matched `direction_data` instead of `vessel_heading`, and `variation` matched `_source` instead of `_variation`.
  - Updated `map_nmea_sensors.py`: corrected fallback PKs to `b70bbc9b5eef0afbfed7ae988ce2ddb4`, prioritized `vessel_heading` for heading, and required exact `_variation` entity suffix.
  - Updated `local-ha/mock_nmea_emulator.py`: packed magnetic variation (+2.5°) in PGN 127250 frame instead of `0x7FFF` (unavailable).
  - Regenerated `src/yaml/sensors/derived_n2k.yaml`, rebuilt artifacts, redeployed to `local-ha` stage, and added regression assertions in `tests/test_sailing_dash.py`. 29 passed.

- **Added: NMEA magnetic compass heading (HDG) card in Position section (`02_position.yaml`):**
  - Added HDG `custom:compass-card` right before the COG compass card in `src/yaml/dashboard/sections/02_position.yaml`.
  - Added NMEA 2000 discovery & fallback support for heading (PGN 127250) and magnetic variation (PGN 127250 / 127258) in `helpers/map_nmea_sensors.py`.
  - Generated template sensors in `src/yaml/sensors/derived_n2k.yaml`: `boat_heading_magnetic` (raw magnetic heading), `boat_magnetic_variation` (magnetic variation), and `boat_heading` (true heading with variation correction: `(hdg + var) % 360`).
  - Added regression test `test_position_section_has_hdg_compass_card_before_cog` to `tests/test_sailing_dash.py`. 29 passed.

- **Fixed: wind chart allowed vertical dragging of forecast data / created dual Y-axes on local-ha:**
  - Root cause: `boat_wind_speed` was defined with `unit_of_measurement: 'kn'`, while forecast sensors used `unit_of_measurement: 'kts'`. `plotly-graph-card` automatically creates a second Y-axis (`yaxis2`) when non-internal entities have different units of measurement, leaving `yaxis2` unlocked for panning (since layout only locks `yaxis`).
  - Standardized `unit_of_measurement: 'kts'` across all speed entities in `src/yaml/sensors/derived_n2k.yaml` (`boat_wind_speed`, `boat_stw`, `boat_sog`) and generator `helpers/map_nmea_sensors.py`. All traces now map to `yaxis` and stay synchronized on a single locked scale.
  - Added regression test `test_wind_speed_sensors_share_exact_unit_of_measurement` to `tests/test_sailing_dash.py`. 28 passed.

- **Fixed for real: the wind chart still showed nonsense — the bug was in how the card *processes* the data, not in the sensors:**
  - Verified first what is NOT the cause: the built dashboard (including every `$fn` JS scalar) is byte-identical to the live, working Prod config once the seven `sensor.boat_*` aliases are substituted for their raw entities, `chart_time_window` is 4h/72h on both, and the live Stage data is sound (position 42.4345/18.6032, 96 forecast points, ~1200 history points per series). The derived-alias layer stays — it is what makes the dashboard reproducible on any instance.
  - **Defect 1 — NaN poisoned the traces.** Recorder history is not purely numeric: every HA restart writes `unknown`, and an alias with an `availability` template reports `unavailable` while the N2K bus is quiet (3 such points per series on Stage right now). `map_y: parseFloat(y)` turns them into `NaN`, and a single `NaN` inside a `resample: 30m` bucket propagates into the bucket value — and from there into the trace, the `$ex ys` colour scale and the autoranged Y axis. New shared snippet `src/js/common/plotly_drop_non_finite.js` runs between `map_y` and `resample` on every recorder-backed series (`Measured`, `Gusts (measured)`, `kt scale`, and the internal direction trace), so a missing sample stays a gap.
  - **Defect 2 — arrows and hover labels were bound by array index, not by time.** Wind speed and wind direction are two separate entities, each resampled from its own history (on Stage: 1152 vs 1199 points), so `vars.dir.ys[i]` labelled/rotated each point with *another moment's* direction, and `dirs[i] || 0` drew a due-North arrow wherever direction was missing. `src/js/common/plotly_wind_customdata.js` and `plotly_wind_annotations.js` now match the direction series **by timestamp** (±15 min tolerance) and skip the point entirely when there is no direction or the speed is not finite; the forecast arrows keep index alignment (same open-meteo `hourly` payload) but are guarded against ragged/non-numeric arrays.
  - `helpers/build.py`: `$include:` inside a plotly `filters: - fn:` step is now inlined verbatim (new `RAW_INCLUDE_KEYS`) — that key already holds a raw JS function body and must not receive the `$fn ` marker, which only tags config values the card has to evaluate.
  - Tests: `tests/js/wind_chart_snippets.test.js` (run by `tests/test_sailing_dash.py::test_wind_chart_js_snippets`) exercises the snippets on fixtures with `unknown` samples, series of different length and a time shift; three new pytest checks assert the non-finite filter really sits before `resample` in the built dashboard, that no index-based direction lookup (`vars.dir.ys[i]` / `dirs[i] || 0`) survives, and that `filters: - fn:` includes stay `$fn`-free. 27 passed.
  - Verified end-to-end by replaying the *deployed* JS over the live Stage history/forecast: 3 non-finite samples dropped per series, 0 points left without a direction label, 1248 arrows all with finite coordinates, "Now" marker present.

- **Fixed: the wind chart showed nonsense — the derived N2K aliases reported `0` instead of "no data", and the forecast was fetched for the wrong place:**
  - Root cause (diffed against the working `main`): in `main` the chart read the raw `nmea2000` entities and both Open-Meteo `resource_template` URLs fell back to Kotor (42.43/18.60) via `| float(42.43)`. In this branch the chart and the URLs go through the aliases `sensor.boat_wind_speed` / `boat_latitude_raw` / `boat_longitude_raw`, whose `{{ states(src) | float(0) }}` body turns a quiet bus into a literal `0.0`. `| float(42.43)` never fires for a value that *is* a number, so the forecast was requested for 0°N/0°E (Gulf of Guinea) and the measured series was drawn as a flat zero line — a plausible-looking but completely wrong chart.
  - `src/yaml/sensors/derived_n2k.yaml` (and its generator `helpers/map_nmea_sensors.py`): every alias now declares `availability: "{{ states(<raw entity>) | is_number }}"`, so a missing source becomes `unavailable` (a gap in the chart) instead of `0` — STW, Depth, Wind Speed, Wind Angle, COG, SOG, Latitude/Longitude Raw, Pressure Raw, Wind Direction History.
  - `wind_direction_history` reads the raw wind-angle entity directly instead of hopping through `sensor.boat_wind_angle`, so it cannot inherit that alias' `0`-default either.
  - `src/yaml/sensors/open_meteo.yaml`: both URLs now check the position explicitly (`if lat|abs < 0.01 and lon|abs < 0.01` → 42.43/18.60) instead of relying on a `float()` default that a zero position never triggers.
  - Bonus bug from the same loose matching: `map_nmea_sensors.match_entities()` matched `_cog`/`_sog` as substrings, so PGN 129026's `..._cog_reference` enum (True/Magnetic) was used for **both** Boat COG and Boat SOG. Both now require an exact entity suffix, and the generated file points at `..._cog` / `..._sog`.
  - Regression tests (`tests/test_sailing_dash.py`, 23 passed): every alias defaulting to `0` must declare availability on the same source; COG/SOG must not read `_cog_reference`; the generator must match the exact suffixes; both Open-Meteo templates must contain the explicit zero-position fallback.

- **Deploy is incremental now: nothing already in place is copied again, and Home Assistant is restarted only when something really changed:**
  - `helpers/lib/ha_target.sh` gained a content-addressed delivery layer — `ha_local_sha256`, `ha_remote_sha256` (hashes the file's content *as the container sees it*, so a hand-edited target is still fixed), `ha_tree_manifest`, `ha_cp_to_container_if_changed` and `ha_cp_dir_to_container_if_changed`, plus the counters `HA_DELIVERED`/`HA_SKIPPED`. Directory deliveries (HACS, the `nmea2000` integration) are compared through a tree-manifest hash kept in `/config/.storage/sailing_deploy_state` inside the container, because hashing a whole tree remotely on every run would cost more than the copy it saves.
  - `deploy.sh`: the resources step and `--bootstrap` deliver the 7 card bundles, `lovelace_resources`, HACS and the integration only on mismatch; `deploy_sensors.sh` skips the upload *and* the backup when the (idempotent) merge produces exactly the live `configuration.yaml`; `deploy_dashboard.sh` reuses its own pre-deploy diff as the upload condition. A repeat `./deploy.sh --stage --update` is now a ~1 s no-op instead of ~10 file copies plus a restart.
  - Restart bookkeeping: the steps share the `SAILING_CHANGE_FLAG` marker, so HA is bounced exactly once and only if resources/sensors/dashboard actually changed — the sensors step still runs with `SKIP_RESTART=1` and the dashboard step decides. New `--force` flag (`HA_FORCE_DELIVERY=1`) re-uploads everything unconditionally.
  - Comparing the merged sensor config is done on the *serialized* YAML, not on the object graphs: HA tags (`!include`, `!secret`, ...) are loaded as `_HaTag` instances without `__eq__`, so an object-level comparison always reported a difference and defeated the whole check.
  - `helpers/stage_provisioner.py::copy_card_to_ha()` (also the per-file engine behind `copy_dir_to_ha()`) compares sha256 with the target first, so a clean-install/bootstrap no longer re-pushes every card and every file of both custom integrations; `copy_dir_to_ha()` prints a `delivered / unchanged` summary.

- **Fixed: the forecast sensors the dashboard references did not exist in the modular sensor sources:**
  - Restored the 7 template sensors lost when the monolithic `sensors-sailing.yaml` was split into `src/yaml/sensors/` — `chart_time_window`, `wind_forecast_flat`, `wind_forecast_next_hour`, `wind_gust_next_hour`, `wave_forecast_flat`, `wave_height_next_hour`, `wave_period_next_hour` — as the new module `src/yaml/sensors/forecast.yaml`. Without them the wind/wave charts, the "Waves" header row and the `forecast_days` term of both Open-Meteo REST URLs referenced undefined entities.
  - `build.py::build_sensors()` now merges the sensor modules per top-level YAML key instead of concatenating their text: two modules both declaring `template:` produced a duplicate mapping key, and `yaml.safe_load` (used by both Home Assistant and `helpers/deploy_sensors.sh`) silently kept only the last block. It also picks up every `src/yaml/sensors/*.yaml`, not just the two hardcoded filenames.

- **No-vendor refactor: every external artifact comes from a pinned repository (`deps.yaml`, `helpers/fetch_deps.py`):**
  - Added `deps.yaml` as the single source of truth for versions: the 6 HACS card bundles, the HACS release, the `nmea2000` HA integration (our fork `dnevera/ha-nmea2000` at tag `ydnu-02-usb-tcp-gw`) and the `nmea2000` Python library (our fork `dnevera/nmea2000` at tag `cpu-overload-fix`).
  - Added `helpers/fetch_deps.py`: the only downloader — retry + timeout + sha256 verification, artifacts land in the ordinary build directory `build/deps/` (gitignored, wiped together with `build/`). No `.cache/` as a deployment concept, and no silent use of stale local copies: a missing artifact is a hard error listing exactly what is missing.
  - Deleted `ha/sailing-dash/vendor/**` (7.7 MB of third-party bundles plus a full copy of `custom_components/nmea2000`) and added `vendor/` to `.gitignore`.
- **Patch mechanics removed — the library is installed from the fork's tag:**
  - `requirements.txt` / `requirements-ha.txt` now pin `nmea2000 @ git+https://github.com/dnevera/nmea2000.git@cpu-overload-fix` (both fixes are inside the tag: the `ioclient.py` EOF spin-loop and the PGN 126996 primary-key collision).
  - Deleted `patches/nmea2000_ioclient.py`, `patches/README.md`, `scripts/patch_ha_nmea2000_message.py`, `scripts/apply_ha_patch.sh` and the root `deploy.sh --patch-ha` mode; replaced by `deploy.sh --check-ha` / `verify_nmea2000_fork()`, a drift-guard on the *installed* library instead of a patch marker.
- **Generalized deploy targets and a self-contained subproject environment:**
  - Introduced named profiles in `ha/sailing-dash/.env` (`.env.template` in git, `.env` gitignored) — `HA_PROFILES` plus `<PROFILE>_TRANSPORT|SSH_HOST|CONTAINER|CONFIG_DIR|HA_URL|HA_TOKEN|GW_HOST|GW_DATA_PORT`, so several Pi5 boxes (stage and prod) can be described at once. The subproject no longer reads the root `.env`/`deploy.conf` (those stay the ydnu-02 manager's config), which removes the `HA_CONTAINER=homeassistant` leak into stage architecturally.
  - Added `helpers/lib/env_profile.sh` + `helpers/env_profile.py` loaders and `helpers/lib/ha_target.sh` as the single HA access layer (`ha_mkdir`/`ha_cat`/`ha_cp_to_container`/`ha_restart`, `local-docker` and `ssh-docker` transports); removed `targets.conf` and the hardcoded `local-ha`/`homeassistant` names.
  - `--target <profile>` added everywhere (`deploy.sh`, `helpers/deploy_sensors.sh`, `helpers/deploy_dashboard.sh`, `helpers/start_stage.py`, `helpers/stage_provisioner.py`, `helpers/map_nmea_sensors.py`); `--stage`/`--prod` remain aliases. `stage_provisioner.py` is transport-aware (docker exec/cp tunnelled over SSH).
- **Deduplication and a cleaner subproject root:**
  - `build.py` is invoked exactly once per run (was 6 times through the `run_stage.sh` → `start_stage.py` → `deploy.sh` → `deploy_sensors.sh`/`deploy_dashboard.sh` chain); the two `lovelace_resources` merge implementations collapsed into `helpers/merge_lovelace_resources.py`; `build_docker.sh` is now a thin wrapper over `run_stage.sh`.
  - Moved all helper scripts into `ha/sailing-dash/helpers/` (`build.py`, `build_docker.sh`, `deploy_sensors.sh`, `deploy_dashboard.sh`, `stage_provisioner.py`, `start_stage.py`, `map_nmea_sensors.py`, `fetch_deps.py`, `env_profile.py`, `lib/`), leaving only the entry points in the subproject root.
- **Prod from scratch: `--bootstrap`, a preflight gate and rollback (`deploy.sh`):**
  - `--bootstrap` checks SSH/Docker/container and delivers HACS, the card bundles and the pinned integration from `build/deps/` — identically for stage and prod, so the two environments cannot drift.
  - `--preflight` verifies container liveness, HACS delivery *and* activation, the integration, a config entry on the tcp-gw and the presence of raw `nmea2000` entities in the registry; on failure it stops and prints the exact list of manual actions instead of deploying a dashboard onto an empty registry.
  - `--rollback` restores `configuration.yaml` and `.storage/lovelace.*` from the newest timestamped backup and rotates old ones.
- **HACS: files automated, activation manual and *verified*:**
  - HACS delivery stays in the pipeline (`deps.yaml` → `fetch_deps.py` → `deploy_hacs_integration()`) and now runs for `ssh-docker` profiles too; a delivery failure is a step error, not a silent warning.
  - Added `helpers/stage_provisioner.py check-hacs --target <profile>`, which separates the two states that used to be conflated: DELIVERED (`custom_components/hacs/manifest.json`, domain `hacs`) vs ACTIVATED (a config entry for domain `hacs`, which only the GitHub device-flow can create). `deploy.sh --preflight` delegates to it.
- **`install_wizard.sh` rebuilt as a state machine with two blocking gates:**
  - GATE A — activate HACS in the UI, then `check-hacs` must pass; GATE B — the NMEA 2000 integration, the config entry on `GW_HOST:GW_DATA_PORT` (gateway type `text`) and bus traffic, then `deploy.sh --preflight` must pass. Each gate prints its checklist, waits for Enter, runs the check and loops on failure — it never "warns and carries on", and stage is no longer treated as advisory.
  - 8 steps with `--list`/`--from`/`--only`/`--dry-run`/`--yes` (the gates block even with `--yes`); `helpers/start_stage.py --provision-only` brings the container up and provisions HA but deliberately stops before deploying anything.
- **Fixes:**
  - `Unknown mode 'None' during migration` (`custom_components/nmea2000/__init__.py:58`): the provisioned config entry is now written as `version: 2` with the legacy `mode`/`device_type` keys, plus a backfill for existing entries.
  - `KeyError: 'radius'` crash-loop of the stage container: `provision_core_config()` declared `minor_version: 4` (so HA skipped its 1.4 migration) but wrote no `radius` key — now `radius: 100` (HA's `DEFAULT_RADIUS`) is included.
  - macOS bash 3.2 incompatibility (`${VAR^^}`) and an `args.gw-host` typo in `start_stage.py`.
- **Documentation:** `INSTALLATION.md` rewritten as "AUTO stage 1 → manual pause → AUTO stage 2" with a "Configuring targets" section (two Pi5 example), the wizard/gates description and a new "Examples" section (clean stage install with a full wipe, partial deploys, remote stage profile, end-to-end prod run); `HACS_SETUP.md`, `README.md`, `requirements-ha.txt`, the `nmea2000-setup` skill and `.agents/AGENTS.md` synchronized with the "forks by tag, no patches" model; the full audit and its status live in `ha/revision.md`.

## 2026-08-10

- **Stage HA Docker Build Script & Dockerfile (`build_docker.sh`, `local-ha/Dockerfile`):**
  - Added dedicated `build_docker.sh` script in `ha/sailing-dash/` that builds source modules, builds the custom Stage Home Assistant Docker image (`local-ha`), starts the container, and deploys build artifacts in one command.
  - Created `local-ha/Dockerfile` and updated `local-ha/docker-compose.yml` to specify custom build context for Stage HA.
  - Updated `start_stage.py` to invoke `docker compose up -d --build` during Stage environment launch.
  - Updated `README.md`, `INSTALLATION.md`, and `TEST.md` documentation.

- **Documentation & Environment Setup (`INSTALLATION.md`, `TEST.md`, `run_stage.sh`):**
  - Created `INSTALLATION.md` detailing system requirements, Python setup, local Stage HA Docker launch, and production deployment configuration.
  - Created `TEST.md` with step-by-step test suites covering build pipeline validation, NMEA 2000 PGN simulator decoding, Stage deploy verification, and Prod safety diff checks.
  - Added `run_stage.sh` shell script wrapper for executing pre-launch builds and starting `start_stage.py`.
  - Updated `README.md` to reflect the Stage HA environment architecture and stage deployment debugging workflow.
  - Removed obsolete static preview files (`start_preview.py`, `local-preview/` directory) and simplified `build.py`.

- **Stage & Prod Deployment System & Local Stage HA Environment (`start_stage.py`):**
  - Replaced the static offline `local-preview` harness with a full Home Assistant Stage environment (`local-ha`) running in Docker.
  - Implemented `start_stage.py`, the unified Stage environment orchestrator supporting `--demo` (background Python NMEA 2000 PGN emulator broadcasting on TCP :4001) and `--live` (connecting Stage HA to the vessel's TCP gateway).
  - Implemented `local-ha/mock_nmea_emulator.py`, a Python simulator broadcasting realistic STW, Depth, Apparent/True Wind, Position, COG/SOG, Heading, and Pressure PGN frames on TCP port 4001.
  - Updated `deploy.sh`, `deploy_dashboard.sh`, and `deploy_sensors.sh` to support `--stage` (direct local Docker deployment into `local-ha` without SSH) and `--prod` (SSH deployment to bumblebee Pi5).
  - Integrated live file watcher into `start_stage.py` that monitors `src/` for source edits and automatically invokes `build.py` and `deploy.sh --stage`.

- **Fixed build/dashboard-sailing.yaml drift vs the live HA dashboard:**
  - Pulled the live storage-mode config (`.storage/lovelace.dashboard_sailing`) from `bumblebee.local` via `docker exec`/`docker cp` and diffed it against `build/dashboard-sailing.yaml` to verify the section/grid/card composition still matches after the `$include:` refactor.
  - Found the only drift: `load_common_js_snippets()` was inlining the `//` doc-comment header of each `src/js/common/*.js` snippet into the generated `$fn ...` scalar, so every `$include:`-based card (`on_dblclick`, `shapes`) diverged from what is actually live (which never had those comments). Added `strip_leading_line_comments()` in `build.py` to drop the leading `//` comment block before wrapping a snippet back into `$fn`, keeping the doc comments in the source `.js` file for readers while producing byte-identical output to the live dashboard.
  - Re-ran `build.py` and confirmed a clean (zero-line) diff between the freshly pulled live config and `build/dashboard-sailing.yaml`.
- **Post-modularization audit — local-preview layout, JS/YAML dedup, cleanup:**
  - Fixed `build_preview_configs()` (`build.py`): it used to flatten every card from `src/yaml/dashboard/sections/*.yaml` into one plain list, discarding the section/grid grouping — `local-preview/render.js` then rendered every card as a single stacked block, unlike the real grid layout in `build/dashboard-sailing.yaml`. It now emits `window.PREVIEW_SECTIONS` (source file → `type: grid` blocks with their `grid_options.columns`/`column_span` → cards), and `render.js`/`index.html` were reworked to render one `.preview-section` (with heading) per source file and one CSS-grid `.preview-grid` per grid block, so local-preview visually matches the real dashboard layout. The old flat `window.PREVIEW_CARDS` array is still emitted for backwards compatibility.
  - Removed duplicated `$fn` JavaScript between `src/yaml/dashboard/sections/04_wind.yaml` and `05_waves.yaml`: the `on_dblclick` "reset zoom" handler and the `layout.shapes` touch-gesture patch were byte-for-byte identical in both files. Extracted them into `src/js/common/plotly_reset_on_dblclick.js` and `src/js/common/plotly_touch_patch_shapes.js`, and added an `$include:<name>` mechanism to `build.py` (`load_common_js_snippets()` / `resolve_includes()`) that substitutes these placeholders back into a `$fn ...` scalar at build time for both `build_dashboard()` and `build_preview_configs()`, so the shared code lives in one place instead of being copy-pasted per section.
  - Removed the stale `__pycache__/` directory left over from running `start_preview.py` directly (a build artifact, not source).
- **Modularization of `ha/sailing-dash/` and automated build pipeline (`build.py`):**
  - Reorganized project into modular source components under `src/`: reusable JS utilities (`src/js/common/` for color scales, data generators, Plotly vector helpers) and card implementations (`src/js/cards/`), section-based YAML templates (`src/yaml/dashboard/sections/`), sensors (`src/yaml/sensors/`), automations (`src/yaml/automations/`), and resources (`src/yaml/resources/`).
  - Implemented `build.py` script to compile modular source files from `src/` into target deployable artifacts inside `build/` (`dashboard-sailing.yaml`, `sensors-sailing.yaml`, `automations-sailing.yaml`, `lovelace-resources.yaml`, `cards/windy-boat-card.js`) and auto-generate `build/local-preview/card-configs.js` for offline preview testing without manual synchronization.
  - Updated deployment scripts (`deploy.sh`, `deploy_dashboard.sh`, `deploy_sensors.sh`) and local preview harness (`local-preview/index.html`, `render.js`) to consume artifacts directly from `build/`.
  - Removed obsolete monolithic root YAML files (`dashboard-sailing.yaml`, `sensors-sailing.yaml`, `automations-sailing.yaml`, `lovelace-resources.yaml`), legacy `cards/` root folder, and draft `wave-sensor.md`. Updated `local-preview/cards` symlink to point to `../build/cards`.
- **Added `start_preview.py` test server launcher and audited documentation:**
  - Implemented `start_preview.py` to audit build freshness, trigger `python3 build.py` automatically if source files changed, verify vendor dependencies, report build artifacts and card count, and launch an HTTP server for `local-preview` with detailed request logging and terminal instructions.
  - Audited and updated `ha/sailing-dash/README.md` and `local-preview/README.md` to accurately reflect the modular layout, build pipeline, and preview workflows.
- **Enhanced `local-preview` to generate and render all 22 dashboard cards:**
  - Refactored `build_preview_configs()` in `build.py` to recursively extract all cards (standard HA elements and custom cards) across all dashboard section YAML files (`src/yaml/dashboard/sections/*.yaml`).
  - Added `local-preview/mock-ha-cards.js` defining custom Web Components (`hui-heading-card`, `hui-gauge-card`, `hui-entity-card`, `hui-glance-card`, `hui-map-card`, `hui-tile-card`) and extended `mock-hass.js` with mock states for STW, Depth, device tracker, position, and wave forecasts.
  - Verified that all 22 dashboard cards render cleanly without errors in the preview test harness (`node local-preview/run-preview.js`).

## 2026-08-09

- **Pulled another manual re-layout from the HA UI:** section headings renamed
  ("Wind" -> "Sensors", "Weather & Forecast" -> "Conditions"), the pressure
  gauge moved from the sensors section into "Conditions", icons added to the
  "Wind Direction & Speed" (`mdi:weather-windy`) and "Waves" (`mdi:wave`)
  headings, and the Windy widget moved out into its own new wide "Forecast"
  section (`column_span: 3`, `aspect_ratio: 50%`, `rows: 7`, `columns: 36`).
  Applied as targeted edits so the explanatory YAML comments survive; live
  `.storage` config and the local YAML verified identical afterwards. No
  deploy — this is a pull, not a push.

- **Synced with a manual dashboard tweak made in the HA UI:** the wind
  `compass-card`'s `compass.ticks.radius` was changed 52 -> 95 on the live
  instance; pulled into `dashboard-sailing.yaml` (live `.storage` config and
  the local YAML verified identical afterwards). No deploy needed — this is a
  pull, not a push.

- **Mobile gestures on both `custom:plotly-graph` charts reworked: one finger
  pans, a long press shows the tooltip.** The previous scheme (one finger =
  tooltip, two fingers = pan) did not work on the phone — the two-finger pan
  never took effect. Now a single touch is *not* intercepted, so Plotly pans
  along X as usual; if the finger stays down for 400 ms without moving more
  than 10 px, the pan Plotly already started is aborted with a document-level
  `mouseup` (that is how Plotly's `dragElement` finishes a drag — nothing has
  moved yet, so nothing is relayouted), a short `navigator.vibrate(15)` marks
  the switch, and from then on `touchmove` is stopped in the capture phase and
  translated into synthetic `mouseover`/`mousemove`, so the shared
  (`hovermode: x unified`) tooltip follows the finger until it is lifted.
  Desktop mouse behaviour, the dashed cursor and the double-click reset are
  unchanged. Verified in `local-preview` with synthetic multi-touch: a quick
  one-finger drag moves `xaxis.range`, a 700 ms hold produces the 4-series
  tooltip and dragging afterwards changes the tooltip's timestamp
  (21:30 -> 09:30) while `xaxis.range` stays put. Test note: synthetic
  `TouchEvent`s must be created with `composed: true`, otherwise they never
  leave the card's shadow root and Plotly's document-level drag listeners
  never see them (this cost a debugging round, it is not a card bug).

- **Windy: the weather-detail button is now a toggle (`windy-boat-card` 1.2.0).**
  A second press closes the panel (`{showDetail: false}`, which the embed's
  `updateEmbed` handler maps to hiding the detail view). State is tracked in
  `_detailOpen` and also synced from the embed's own outgoing `updateDetail`
  message, so closing the panel inside the widget doesn't desync the toggle;
  "home" resets the flag since a re-render starts with the panel closed.
  Caught while testing: the first version read *any* `updateDetail` as "open",
  but per `embed2.js` that message is also emitted for `showMarker`,
  `coordinates` and unit changes with no `showDetail` field at all — the flag
  was therefore already true before the first click and the first press closed
  an already-closed panel. Fixed by only accepting a boolean `showDetail`.
  Verified with real clicks in the local harness: open / closed / open
  (`ondetail` true, false, true); deployed via `./deploy.sh --resources-only`,
  live HA serves version 1.2.0.

- **Windy: the "home" button no longer switches the view mode; weather detail
  is now its own button (`windy-boat-card` 1.1.0).** The v1.0.0 button sent
  `{showDetail: true, detailLat, detailLon}` over the embed's postMessage API,
  which is NOT a "set centre" request — read literally from
  `embed2.js` (v41.1.0), `showDetail` means *open the weather detail panel
  here*: `dn.on('detailRendered', ...)` then calls `Ff(coords, 180)`, panning
  the map so the point lands under that panel. That is exactly the reported
  behaviour: the widget flipped into weather-detail view and the boat ended up
  off-centre instead of home. The same handler additionally runs
  `payload.pressure ? isolines = 'pressure' : isolines = 'off'` on EVERY
  message, silently resetting the isobars toggle. There is no "set centre"
  message in the API at all (`showMarker` → `panToOffset`, Y axis only).
  - "Home" (`mdi:crosshairs-gps`) now re-renders the widget at the boat: the
    iframe `src` is rebuilt once, on the explicit press. To keep it from
    resetting the user's view, the card listens to the embed's *outgoing*
    `updateValues` message (payload `{coordinates, level, overlay, product,
    zoom}`) and reuses the last reported `overlay`/`product`/`level`/`zoom` in
    the new URL — so pressing home on the waves layer at zoom 11 comes back on
    the waves layer at zoom 11.
  - Weather detail moved to a second button (`mdi:weather-partly-cloudy`,
    above the home button), which is where `showDetail` genuinely belongs.
  - Normal map interaction is untouched: the `src` is never reassigned on
    `hass` updates, so nothing reloads unless a button is pressed.
  Verified in the local harness: `_view` fills in from `updateValues`; the
  home press rebuilds the `src` with the boat's coordinates plus the captured
  view (`lat=42.4712 lon=18.5731 zoom=11 overlay=waves product=ecmwfWaves`,
  exactly one iframe load); the detail panel is closed after home and open
  after the weather button. Deployed with `./deploy.sh --resources-only`;
  `/local/windy-boat-card.js` on the live HA reports version 1.1.0.
- Synced the manual layout tweak made in the HA UI before editing (Windy card
  `aspect_ratio` 50% → 90%, `grid_options.rows` 6 → 5); live `.storage` config
  and the local YAML match (`EQUAL: True`), so the dashboard itself did not
  need a redeploy.

- **Windy: own card `custom:windy-boat-card` replaces the iframe + overlay
  construction; the recenter button now actually works.** The previous
  "recenter" button did nothing useful — it could only rebuild the iframe
  `src`, i.e. reload the entire widget. Instead of guessing again, the live
  embed bundle (`embed.windy.com/v/41.1.0.emb.b79a/embed2.js`) was read and
  a **built-in, two-way `postMessage` API** was found — gated behind a URL
  parameter:

      Rf = { ..., embedMake: Gf(qh.embedMake), ... }
      Rf.embedMake && window.parent !== window && function () {
          window.onmessage = function (e) {  // type: 'updateEmbed'
              payload.showDetail ? dn.emit('rqstOpen','detail',{lat,lon}) : ...
              payload.showMarker ? dn.emit('rqstOpen','picker',{lat,lon}) : ...
              // + pressure / hideMessage / metricWind / metricRain / metricTemp
          };
          // and the widget posts `updateValues`/`updateDetail` back out
      }

  Verified in a real browser, not assumed: without `embedMake=true`
  `window.onmessage` inside the iframe is `null` and every message is
  silently ignored (`typeof` reports `'object'`); with it, it is a
  `function` and `{type:'updateEmbed', payload:{showDetail:true, detailLat,
  detailLon}}` re-centres the map on BOTH axes (42.066,18.600 →
  44.147,14.502 → 35.599,24.999), while `showMarker` pans the Y axis only
  (`panToOffset`). Sending `{showDetail:false}` right after closes the panel
  and the map keeps the new position.
  - HA's built-in `type: iframe` cannot send that message, hence a small card
    of our own: `ha/sailing-dash/cards/windy-boat-card.js` (config:
    `lat_entity`/`lon_entity`/`fallback_lat`/`fallback_lon`/`zoom`/`overlay`/
    `product`/`aspect_ratio`). It assigns the iframe `src` **once** and never
    again, so the widget never reloads and stays fully interactive; its own
    button posts the recenter message directly to the widget.
  - Removed: the `config-template-card` wrapper, the `card_mod` overlay grid,
    and both helper entities (`input_button.windy_recenter`,
    `input_boolean.windy_follow_gps` — pruned from the live instance via the
    `# DEPLOY-REMOVE:` directives).
  - Tooling: `deploy.sh` now looks for a card `.js` in `cards/` first and only
    then in `local-preview/vendor/`, so project-owned cards (committed to git)
    deploy through the same `--resources-only` path as the downloaded
    3rd-party bundles; `lovelace-resources.yaml` lists
    `/local/windy-boat-card.js?v=1.0.0`.
  - Local harness: `local-preview/cards` is a symlink to `../cards` (a `../`
    script path escapes the static server root and 404s), and the card builds
    on whichever of `setConfig`/`hass` arrives last (the harness sets `hass`
    first, HA does the opposite).
  - Known, accepted inaccuracy: the embed positions the requested point where
    its detail panel expects it, ~100px below the viewport centre, so the boat
    lands slightly low rather than dead centre (asking 44.500 settles the
    centre at 44.147). There is no "set centre" message in the API and
    `showMarker` cannot compensate (Y-axis only).
  Verified live in a browser against the harness: `embedMake=true` in the src,
  `window.onmessage` is a function, clicking the button moves the map while the
  iframe `src` stays byte-identical (no reload) and the detail panel ends up
  closed. Deployed via `./deploy.sh --resources-only` + `--sensors-only` +
  `--dashboard-only`; `/local/windy-boat-card.js` returns HTTP 200 from HA and
  the live `.storage` dashboard config is identical to the local YAML.

- **Windy widget reworked: fully interactive map + a single recenter-on-boat
  button; Follow-GPS toggle and "Center on my position" card removed.**
  Studied the actual embed bundle
  (`embed.windy.com/v/41.1.0.emb.b79a/embed2.js`) instead of guessing what
  the iframe can do, which changed the design:
  - the embed ships its own controls — `#embed-zoom` +/- buttons, search box
    (with a "my location" that uses the *browser's* position, i.e. the
    phone, not the boat), burger menu, all toggled by the
    `menu=`/`message=`/`marker=`/`detail=` URL params already in our src;
  - **the Windy logo IS the "open in windy.com" link**: on every
    `redrawFinished` the bundle assigns
    `#logo.href = 'https://www.windy.com/?<overlay>,<level>,<lat>,<lon>,<zoom>'`
    with `target="_top"`, i.e. the full site/app opens at the *current* map
    position. The full-area transparent overlay button that used to provide
    that link was therefore **removed** — it swallowed every click/drag,
    which is exactly why the embedded map could not be panned or zoomed.
  - the one thing the embed has **no** control for is "back to the boat":
    `location=coordinates` + lat/lon does set its internal `homeLocation`
    and a `back2home` handler exists, but nothing in map mode emits it and
    the iframe is cross-origin. Hence one small crosshair button overlaid in
    the bottom-right corner: it presses the new `input_button.windy_recenter`
    (the ONLY entity the widget's `config-template-card` subscribes to), the
    `${...}` templates re-run and the iframe `src` is rebuilt around the
    boat's current N2K GPS position. `_r=<press timestamp>` is a
    cache-buster so the src really changes (and the map really jumps home)
    even when the rounded coordinates did not move.
  - `input_boolean.windy_follow_gps` and the separate "Center on my
    position" card are gone: the widget is an interactive map the user pans
    freely, it must not follow the GPS, and the removed card only opened
    windy.com (duplicating the logo link) instead of recentering the widget.
  - the overlay grid's `card_mod` now passes pointer events through
    everything except the button's own `ha-card`, so the map stays draggable.
  - `deploy_sensors.sh` gained a `# DEPLOY-REMOVE: <key.path>` directive
    (declared in `sensors-sailing.yaml`): dict-valued top-level keys are
    merged, so a helper deleted from our YAML would otherwise linger on the
    live instance forever — the deploy now prunes it (confirmed in the
    deploy output: `Removed stale key input_boolean.windy_follow_gps`).
  Verified: `local-preview` headless run — all 5 cards OK, the iframe src
  resolves to the mock GPS position plus the `_r` cache-buster; deployed via
  `./deploy.sh --sensors-only` + `--dashboard-only` (pre-deploy diff showed
  only the intended changes), live `.storage` config identical to the local
  YAML afterwards, `input_button.windy_recenter` present on HA. Note: the
  crosshair button's CSS cannot be checked in `local-preview` — that harness
  only loads the 3rd-party bundles, HA's native `hui-button-card` is never
  registered there.
- **Fixed: the Windy widget reloaded nonstop.** `custom:config-template-card`
  re-renders its child card on every state change of every entity listed in
  `entities:`, and the entry below listed the two N2K GPS sensors there —
  those update several times per second, so the `iframe`'s `src` was
  reassigned continuously and the embedded Windy map never stopped reloading.
  The iframe card now subscribes to `input_boolean.windy_follow_gps` only;
  the `${...}` templates still read the GPS position through the global
  `states` object (no subscription needed), so the widget re-centers exactly
  once — when the Follow GPS toggle is flipped. The separate "Center on my
  position" button keeps its GPS subscription on purpose (it must open the
  current position, and re-rendering a plain button costs nothing — there is
  no iframe to reload). Deployed via `./deploy.sh --dashboard-only`
  (pre-deploy diff showed only the removed `entities:` lines).
- **Windy GPS-follow made opt-in (default OFF), plus a one-shot "locate me"
  button.** The entry directly below made the Windy widget/button always
  follow the boat's live GPS; that always-on behaviour was rejected. Added
  `input_boolean.windy_follow_gps` (`sensors-sailing.yaml`, default `off`)
  plus a "Follow GPS" `type: tile` toggle next to the widget; the Windy
  iframe/overlay-button's `config-template-card` `variables` now compute
  `lat`/`lon` as `vars['followOn'] ? <live GPS> : <anchorage>` — stays on
  the last known anchorage (42.43/18.60) unless the user opts in. A
  separate "Center on my position" tile (its own `custom:config-template-card`
  wrapping a plain `type: button`) always opens `windy.com` at the current
  live GPS regardless of the toggle, for a manual one-shot "locate me"
  action. Technical note: read the installed `config-template-card.js`
  bundle to confirm `variables` are evaluated top-to-bottom into a shared
  `vars` object visible inside later variables' own `eval()`'d expression
  strings, so `lat`/`lon` can reference an earlier `followOn` variable via
  `vars['followOn']` (a plain identifier `followOn` is only injected for
  the final `${...}` template body, not for other variable definitions).
  Verified in `local-preview/` (mock toggle state `off`): "Center on my
  position" resolves to the live-GPS mock coordinates while the
  widget/overlay button stay on the anchorage; deployed via `./deploy.sh
  --sensors-only` (diff showed only the new helper) then `./deploy.sh
  --dashboard-only` (pre-deploy diff showed only the
  toggle/button/`followOn` changes), live `.storage` config confirmed
  byte-identical to the local YAML after deploy. Also hardened
  `deploy_sensors.sh`'s config merge: dict-valued top-level keys (e.g.
  `input_boolean:`) are now merged key-by-key instead of replacing the
  whole block, so this and any future helper never clobbers one the user
  adds by hand directly on the live instance.
- **Windy widget/button now follow the boat's live GPS instead of a fixed
  anchorage.** All other coordinate-dependent pieces (map, `boat_latitude`/
  `boat_longitude`, both open-meteo `rest:` requests) were already
  templated off the boat's own N2K GPS in earlier sessions — the Windy
  iframe embed + its tap-to-open overlay button were the last remaining
  spot with the hardcoded 42.43/18.60 anchorage, because `type: iframe`/
  `type: button` don't support Jinja templating on `url`/`tap_action`.
  Wrapped both in `custom:config-template-card`
  (`iantrich/config-template-card` v1.3.6, manually installed the same way
  as `windrose-card`/`plotly-graph-card` — not in HACS default store as
  `thomasloven/lovelace-config-template-card`, which 404s; confirmed the
  correct fork/tag/template syntax by reading the actual bundle source,
  `${...}` is plain JS `eval`, not Jinja). `variables: {lat, lon}` read
  `sensor.boat_latitude`/`sensor.boat_longitude` with a fallback to
  42.43/18.60 if those are ever unavailable; both the iframe `url` and the
  button's `tap_action.url_path` are rebuilt from `lat`/`lon` via `${...}`.
  Existing `grid`/`card_mod` layout (the invisible overlay-button trick) is
  unchanged, just nested one level deeper under `card:`. Two bugs found and
  fixed while wiring this up: (1) the `variables` templates first read
  `sensor.boat_latitude`/`boat_longitude` — those are human-readable DMS
  strings ("42°26.07'N") built for the Position section, so `parseFloat()`
  on them silently truncated to whole degrees only (42/18); switched to the
  same raw decimal-degree N2K sensor the open-meteo `rest:` requests and
  `device_tracker.nevera` already use. (2) `./deploy.sh --resources-only`
  had a long-standing bug (present since the script was written, only
  surfaced now because it's the first time a 3rd manually-installed
  resource was added): the `.files` list handed from the embedded Python
  script to bash had no trailing newline, and `while IFS= read -r line; do
  ...; done < file` checks `read`'s exit status *before* running the loop
  body — `read` returns non-zero (failure) on a final line with no
  trailing `\n` even though it still populates the variable, so the LAST
  entry was always silently dropped from the upload loop. Confirmed live:
  only `windrose-card.js` (then, after this file's own resource was added,
  `windrose-card.js`+`plotly-graph-card.js`) ever actually reached
  `/config/www/` on any prior `--resources-only`/`--install`/`--update`
  run — `plotly-graph-card.js` in particular had been *registered* in
  `lovelace_resources` but its `.js` was never uploaded until this fix.
  Fixed by appending `"\n"` when writing the `.files` list. Verified live
  after both fixes: all 3 manually-installed bundles now land on
  `/config/www/` in one `--resources-only` run, `/local/config-template-card.js`
  returns HTTP 200, and the corrected N2K sensor returns a real decimal
  reading (42.4345672) instead of a DMS string.
- **Mobile gestures on both `custom:plotly-graph` cards: one finger shows
  the tooltip, two fingers pan.** `plotly-graph-card` has no touch hook
  (only `on_click` / `on_dblclick` / `on_legend_*`), so the listeners are
  attached from the `layout.shapes` `$fn` (it is re-evaluated on every
  redraw; a `__touchGesturePatched` flag on the graph div keeps it
  idempotent). A single-finger `touchstart`/`touchmove` is stopped in the
  **capture** phase, before Plotly's own handler on `.nsewdrag`, and the
  shared tooltip is driven by synthetic `mouseover` + `mousemove` on that
  same element. Two rejected attempts first: (1) setting
  `gd._fullLayout.dragmode = false` at touchstart still let Plotly start a
  drag, which sets `gd._dragging` and suppresses hover entirely — verified
  in `local-preview/`, the hover layer stayed empty even for a manual mouse
  dispatch afterwards; (2) dispatching `mousemove` alone does nothing —
  Plotly needs a preceding `mouseover`. Desktop mouse behaviour, the
  `config` block (`scrollZoom`/`displayModeBar`/`doubleClick: false`),
  `layout.dragmode: pan` and the double-click reset are unchanged. Verified
  in `local-preview/` with synthetic multi-touch events: one finger ->
  tooltip text present and `xaxis.range` unchanged (no pan); two fingers ->
  the event reaches Plotly and `gd._dragging` becomes true; all four cards
  still render OK. Deployed via `./deploy.sh --dashboard-only`.
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
