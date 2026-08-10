# Changelog — Sailing Dashboard

All notable changes to `ha/sailing-dash/` (dashboard, sensors, deploy
tooling, `local-preview/` test rig). Dates are when the change was made
in this repo, not necessarily when it went live on `bumblebee.local` (some
entries are DRAFTs, not yet deployed — noted explicitly).

Format: reverse-chronological, one bullet per change. Full technical
write-ups/rationale for the entries below still live in `README.md` and
`local-preview/README.md` (this file is an index/summary, not a
replacement for that detail) and in `.agents/skills/nmea2000-setup/SKILL.md`.

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
