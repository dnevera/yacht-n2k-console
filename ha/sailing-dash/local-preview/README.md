# Local browser preview of custom Lovelace cards

A small, dependency-light test rig that renders the **real, unmodified**
custom card JS bundles (`apexcharts-card`, `windrose-card`, `compass-card`,
`plotly-graph-card`) used by `dashboard-sailing.yaml`, fed a **fake** `hass`
object, entirely offline — no live Home Assistant instance, no deploy, no
network needed after the one-time download of the vendor `.js` files.

Purpose: catch card **schema/config errors** (the kind of
`Configuration error: value.series[3] is not a ChartCardSeriesExternalConfig`
bugs that came up repeatedly while working on this dashboard) and sanity
check **new/draft card configs** (e.g. the `plotly-graph-card` wind-vector
sketch from the main `README.md`) *before* spending a deploy-and-reload
cycle on the real HA instance at `bumblebee.local`.

## What's here

```
local-preview/
  index.html          - the page; open this in a browser, or drive it headlessly
  mock-hass.js         - fake `hass` object (states + fake history/callWS)
  card-configs.js      - list of {tag, title, config} entries to render
  render.js             - creates each custom element, sets hass/config, shows errors on-page
  run-preview.js        - headless Playwright runner (serves the folder, screenshots, exits non-zero on failure)
  vendor/               - real card bundles downloaded from their GitHub releases (not committed logic, just 3rd-party JS)
```

## Option A — open it in a real browser (no Node needed)

Custom elements/ES modules don't reliably work from `file://` (module
scripts are blocked by CORS on `file://` in Chrome), so serve the folder
over plain HTTP, e.g.:

```bash
cd ha/sailing-dash/local-preview
python3 -m http.server 8977
# then open http://localhost:8977/index.html in your browser
```

Each card gets its own block with a green "OK - rendered without throwing"
or a red error message with the exact exception (config validation errors,
missing-entity errors, etc. are shown verbatim, same as HA's own
`hui-error-card` would show).

## Option B — headless check (for a quick pass/fail, e.g. before a deploy)

```bash
cd ha/sailing-dash/local-preview
npm install playwright   # one-time; needs `npx playwright install chromium` too if not already present
node run-preview.js [optional/output/screenshot.png]
```

Prints each card's status + the full browser console (including any
`pageerror`s from uncaught exceptions inside the card bundles themselves -
this is what caught the real "chart stuck loading forever" runtime bug
documented in the main `README.md`), saves a full-page screenshot, and
exits with code 1 if any card failed.

## Editing / adding cards to check

Edit `card-configs.js` - each entry is `{ tag, title, config }`. Keep the
first N entries **byte-for-byte in sync with `dashboard-sailing.yaml`**
(there's no automatic YAML->JS conversion here, on purpose - this stays a
tiny, dependency-free tool) so this remains a real regression check; add
new/draft configs (not yet on the live dashboard) below those, clearly
labeled `DRAFT` in the title like the `plotly-graph-card` entry.

If `sensors-sailing.yaml`/the dashboard start referencing a new entity,
add a matching fake state (and, if the card reads recorder history via
`hass.callWS`, a fake history row) to `mock-hass.js`.

## Known limitations

- **Not a substitute for a real HA check.** This mocks only the handful of
  `hass` properties (`states`, `callWS`, `localize`, `locale`, `config`)
  that these four specific cards happen to read - it will NOT catch bugs
  that depend on real HA frontend machinery (theming, `card-mod` postcss
  processing, `lovelace_resources` registration order, WebSocket
  subscriptions, real recorder aggregation, etc.).
- **`windrose-card`'s `corner_info` lookup logs a (non-fatal) console error**
  against the mock (`checkCornerInfo` expects a slightly different
  internal entity-state shape than this mock provides) even though the
  card still initializes fine - this is a mock-fidelity gap, not a real bug
  (the live dashboard's identical config works fine on the real HA
  instance, confirmed via the WebSocket `lovelace/config` check documented
  in the main README).
- **Fixed: the `plotly-graph-card` entry used the wrong custom-element tag**
  (`plotly-graph-card` instead of `plotly-graph`) which made
  `customElements.whenDefined()` time out forever, even though the bundle
  loaded fine - not a real card bug, just a naming mismatch in this
  harness. The bundle self-registers as `plotly-graph` (see
  `var d3=!0;var ON=d3?"plotly-graph":"plotly-graph-dev"` in
  `vendor/plotly-graph-card.js`); `card-configs.js` now uses the correct
  tag. Also fixed: this card fetches history via `hass.callApi('GET',
  'history/period/...')`, a *different* transport than `windrose-card`'s
  `hass.callWS('history/history_during_period', ...)` - `mock-hass.js` now
  implements both, so the DRAFT chart renders real wind-arrow history +
  forecast data instead of an empty plot.
- A one-off, harmless `pageerror: Cannot read properties of undefined
  (reading 'setHtmlElements')` may still appear in the console for
  `plotly-graph-card` (likely a `ResizeObserver`/upgrade-timing quirk of
  Plotly.js in this minimal harness) - it does not prevent the card from
  rendering or block the test (status stays "OK"), so it's noted here as a
  known cosmetic log line, not a functional bug.
- **Fixed: `index.html`'s theme CSS vars (`--card-background-color` etc.)
  were scoped to an `ha-card` selector**, but `render.js` appends every
  card element directly into `.card-slot`, never inside an actual
  `<ha-card>` wrapper - so only cards that build their own internal
  `ha-card` and read the var by inheritance happened to pick it up.
  `plotly-graph-card` calls `getComputedStyle()` on its own content
  element instead and got nothing, rendering with Plotly's default white
  background (visibly inconsistent with every other card's dark theme).
  The vars are now declared on `:root` in `index.html`, so they inherit
  into every card regardless of DOM wrapping - `plotly-graph-card` now
  themes itself the same dark colors as the rest of the dashboard, with no
  card-side config needed (confirmed via `getCSSVars()` in the bundle).
- **Wind-direction arrows on the `plotly-graph-card` DRAFT went through 3
  designs after user feedback**, each one rejected for being unclear about
  which way the wind actually blows: (1) `marker.symbol: triangle-up` +
  `marker.angle` - a plain triangle looks nearly the same rotated 0° vs.
  180°; (2) `arrow-bar-up` (arrowhead + a straight bar across the tail) -
  visually read as "a chord cut across a triangle", not a directional
  vector. (3) **Final:** real Plotly `annotation` arrows (`showarrow: true`
  with `ax`/`ay` in pixel space for the tail, `x`/`y` for the arrowhead) -
  an actual shaft+arrowhead, unambiguous as a vector - built per data point
  in `layout.annotations` via `$fn`, combining `vars.speed`/
  `vars.forecastSpeed` (stored by the two visible dot traces) with
  `vars.dir`/`vars.forecastDir`. The same `$fn` also emits the "Now"
  vertical-line label + a fixed "▲ N / ▼ S" legend (`plotly-graph-card` has
  no built-in `now:` feature like `apexcharts-card`). Also bumped the
  direction/speed `resample` filter from `5m` to `30m` - at 30h of history
  and 5-minute points, ~360 overlapping arrows per trace rendered as a
  solid blob; 30-minute spacing keeps individual arrows visually distinct.
  See the main `README.md`'s "Wind vector/arrow chart" section for the
  full config and the compass-orientation math (0°=North=up the screen,
  clockwise, confirmed visually here).
- **Fixed (2026-08-09): three DRAFT `plotly-graph-card` issues caught in this
  harness.** (1) "Now" wasn't ~2h from the left edge — added an explicit
  `layout.xaxis.range` `$fn` (this card has no `span`/`graph_span`
  equivalent, so without a range it autoranges over the whole fetched
  extent). (2) the bottom legend overlapped the x-axis's two-row
  date+time labels — added `layout.margin.b`. (3) toggling
  "Measured"/"Forecast" in the legend didn't actually hide the direction
  arrows — they were a separate `layout.annotations` layer computed once
  from `vars`, which a legend click (a native Plotly *trace* restyle)
  never touched. Fixed by drawing the arrow as `marker.symbol: 'arrow'` +
  per-point `marker.angle` directly on each data trace instead, so hiding
  a trace via the legend now natively hides its arrows too.
- Vendor bundles in `vendor/` are 3rd-party release artifacts (not
  hand-written code) - re-download the matching version if
  `requirements-ha.txt`'s pinned versions change.
