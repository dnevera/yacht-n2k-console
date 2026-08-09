// Card configs to preview. The first three are copy-pasted verbatim
// (translated from YAML to the equivalent JS object) from the LIVE
// dashboard-sailing.yaml so this stays a real regression check, not a
// guess at what might work - if these break here, they'd break on the
// real dashboard too. The last one is the plotly-graph-card DRAFT from
// README.md ("Wind vector/arrow chart - apexcharts-card limitation and
// the plotly-graph-card plan") that hasn't been installed on the live HA
// yet - this is exactly the kind of thing this harness exists to sanity
// check before spending a deploy cycle on it.
//
// tag: the custom element tag name the card's JS registers itself as.
// title: shown above the rendered card in the preview page.
// config: passed to element.setConfig(config) verbatim.
//
// IMPORTANT: whenever dashboard-sailing.yaml changes, re-sync the first
// three configs below by hand (there's no automated YAML->JS conversion
// here on purpose - keeping it simple/dependency-free for a local tool).

// Explicit wind-speed color stops (0-40kt), matching the marine convention
// used by windy.com's wind layer / NOAA wind maps: calm = light blue,
// rising through green/yellow/orange/red, gale-force = purple. Used for
// BOTH the dot markers' colorbar ("the thermometer on the right") and the
// direction-arrow colors below, so the whole chart uses one consistent
// color-to-speed meaning instead of an arbitrary/reversed default like
// `RdYlGn` (which only goes green->red and has no fixed 0-40 reference).
const WIND_SPEED_COLORSCALE = [
  [0, '#b0e2ff'],
  [0.125, '#61c4e0'],
  [0.25, '#4bbf7a'],
  [0.375, '#a8d048'],
  [0.5, '#f5e642'],
  [0.625, '#f2a93b'],
  [0.75, '#eb5c2a'],
  [0.875, '#d62828'],
  [1, '#8e1b8e'],
];

window.PREVIEW_CARDS = [
  {
    tag: 'windrose-card',
    title: 'Wind & Forecast — windrose-card (live dashboard config)',
    config: {
      type: 'custom:windrose-card',
      windspeed_bar_location: 'right',
      data_period: { period_back: '-24h' },
      wind_direction_entity: { entity: 'sensor.wind_direction_history' },
      windspeed_entities: [
        { entity: 'sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed', name: 'Speed' },
      ],
      current_direction: { show_arrow: true },
      corner_info: {
        top_right: {
          label: 'Wind Speed',
          unit: ' kn',
          entity: 'sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed',
        },
      },
    },
  },
  {
    tag: 'apexcharts-card',
    title: 'Wind — History & Forecast (live dashboard config)',
    config: {
      type: 'custom:apexcharts-card',
      cache: false,
      graph_span: '30h',
      span: { start: 'hour', offset: '-6h' },
      now: { show: true, label: 'Now', color: '#ffffff' },
      header: { show: true, title: 'Wind — History & Forecast', show_states: true, colorize_states: true },
      apex_config: {
        chart: { height: 300, width: 600 },
        legend: { position: 'bottom' },
        yaxis: { min: 0, decimalsInFloat: 1 },
      },
      series: [
        {
          entity: 'sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed',
          name: 'Measured (kts)',
          type: 'area',
          color: '#00bcd4',
          stroke_width: 2,
          fill_raw: 'null',
          unit: 'kts',
          show: { extremas: true },
        },
        {
          entity: 'sensor.wind_forecast_flat',
          name: 'Forecast (kts)',
          type: 'line',
          color: '#4fc3f7',
          stroke_width: 2,
          stroke_dash: 5,
          unit: 'kts',
          data_generator:
            'const times=entity.attributes.forecast_time||[];const speeds=entity.attributes.forecast_wind||[];const rangeStart=Date.now()-6*3600000;return times.map((t,i)=>[new Date(t+"Z").getTime(),Math.round(speeds[i]*10)/10]).filter(p=>p[0]>=rangeStart);',
        },
        {
          entity: 'sensor.wind_forecast_flat',
          name: 'Gusts (kts)',
          type: 'line',
          color: '#ff7043',
          stroke_width: 1,
          stroke_dash: 4,
          unit: 'kts',
          opacity: 0.9,
          data_generator:
            'const times=entity.attributes.forecast_time||[];const gusts=entity.attributes.forecast_gust||[];const rangeStart=Date.now()-6*3600000;return times.map((t,i)=>[new Date(t+"Z").getTime(),Math.round(gusts[i]*10)/10]).filter(p=>p[0]>=rangeStart);',
        },
      ],
    },
  },
  {
    tag: 'compass-card',
    title: 'COG compass-card (live dashboard config)',
    config: {
      type: 'custom:compass-card',
      header: { title: { value: 'COG' }, icon: { value: 'mdi:compass-outline' } },
      compass: {
        circle: { color: '#37474f' },
        ticks: { show: true, color: '#90a4ae', radius: 52 },
        north: { show: true }, east: { show: true }, south: { show: true }, west: { show: true },
      },
      indicator_sensors: [
        {
          sensor: 'sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog',
          indicator: { image: 'arrow_inward', color: '#ff7043' },
        },
      ],
      value_sensors: [
        { sensor: 'sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog' },
      ],
    },
  },
  {
    // NOTE: the bundle registers itself as `plotly-graph` (not
    // `plotly-graph-card`) - see `var d3=!0;var ON=d3?"plotly-graph":...`
    // in vendor/plotly-graph-card.js. Using the wrong tag here made
    // customElements.whenDefined() time out forever, even though the
    // bundle loaded and registered fine - not a real card bug.
    tag: 'plotly-graph',
    title: 'DRAFT (not deployed): Wind vector/arrow chart — plotly-graph-card',
    config: {
      type: 'custom:plotly-graph',
      hours_to_show: 30,
      entities: [
        {
          entity: 'sensor.wind_direction_history',
          internal: true,
          filters: [{ resample: '30m' }, { map_y: 'parseFloat(y)' }, { store_var: 'dir' }],
        },
        {
          entity: 'sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed',
          name: 'Measured',
          mode: 'markers',
          // REVERTED (2026-08-09): tried drawing the arrow as this trace's
          // own `marker.symbol: 'arrow'` so legend-toggle would natively
          // hide it too - but the user immediately (and correctly) called
          // this out as "triangles again": Plotly's `arrow` symbol is, at
          // a glance, visually the same solid-triangle-with-a-point shape
          // as the already-rejected `triangle-up`/`arrow-bar-up` (it has
          // no real shaft), so it has the exact direction-ambiguity problem
          // those were rejected for. Reverted to plain colored dots here;
          // the real shaft+arrowhead vector is drawn separately below via
          // `layout.annotations` (see "Why annotation arrows, not a
          // rotated marker" in README.md) - that design IS visually
          // correct, it just doesn't react to legend clicks (a real,
          // currently-unresolved limitation of this card - see the long
          // comment on `annotations` below).
          filters: [{ resample: '30m' }, { map_y: 'parseFloat(y)' }, { store_var: 'speed' }],
          // Fixed 0-40kt scale + an explicit color stop table (not the
          // arbitrary default `RdYlGn`) so the colorbar ("the thermometer
          // on the right") has a stable, learnable meaning - calm=light
          // blue through gale=purple, the same convention used by marine
          // wind-speed charts (windy.com's wind layer, NOAA wind maps),
          // rather than shifting its color range to whatever's currently
          // on screen. See `WIND_SPEED_COLORSCALE`/`windSpeedColor` below.
          marker: {
            size: 5,
            color: '$ex ys',
            colorscale: WIND_SPEED_COLORSCALE,
            cmin: 0,
            cmax: 40,
            showscale: true,
            colorbar: { title: { text: 'kt', side: 'top' }, ticksuffix: ' kt', len: 0.9 },
          },
        },
        {
          entity: 'sensor.wind_forecast_flat',
          name: 'Forecast',
          mode: 'markers',
          extend_to_present: false,
          filters: [
            {
              fn: `({ meta }) => ({
                xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),
                ys: (meta.forecast_wind || []),
              })`,
            },
            { fn: `({ meta, vars }) => { vars.forecastDir = meta.forecast_dir || []; return {}; }` },
            { store_var: 'forecastSpeed' },
          ],
          marker: { size: 5, color: '$ex ys', colorscale: WIND_SPEED_COLORSCALE, cmin: 0, cmax: 40 },
        },
      ],
      layout: {
        yaxis: { title: 'Wind speed (kts)', rangemode: 'tozero' },
        // Fix (2026-08-09): "Now" wasn't sitting ~2h from the left edge like
        // it does on the apexcharts-card chart above - that chart's window
        // is pinned by an explicit `span: {start: minute, offset: -2h}` +
        // `graph_span: 26h`, but this card has no such "pin the window"
        // option; without an explicit `xaxis.range` it autoranges over the
        // *entire* fetched history+forecast extent, so "now" ends up
        // wherever the data happens to start/end, not at a fixed offset
        // from the left edge. Re-evaluated via `$fn` on every render (like
        // the "Now" line below) so the window always tracks the real
        // current time, matching the same -2h/+24h anchor as the chart above.
        xaxis: { range: '$fn () => [new Date(Date.now() - 2 * 3600000), new Date(Date.now() + 24 * 3600000)]' },
        // Match the bottom-legend style used by the apexcharts-card
        // ("Wind — History & Forecast") above it - Plotly defaults to a
        // vertical legend on the right, which looked inconsistent with
        // every other chart on this dashboard.
        legend: { orientation: 'h', x: 0.5, xanchor: 'center', y: -0.3 },
        // Fix (2026-08-09): the legend (now at y: -0.3, moved further down
        // to make room) was overlapping the x-axis's two-row date+time tick
        // labels underneath the default margin - the card doesn't
        // auto-grow its bottom margin for the legend, so it has to be
        // reserved explicitly.
        margin: { b: 70 },
        // The card auto-themes its plot/paper background + font color from
        // the surrounding HA theme's `--card-background-color`/
        // `--secondary-text-color` (see getCSSVars() in the bundle), so on
        // the real dashboard this already matches every other card's dark
        // background - no explicit bgcolor needed here. (The local-preview
        // harness originally scoped those CSS vars to an `ha-card`
        // selector that this card is never nested inside, which is why it
        // rendered white there - fixed in index.html, not a card bug.)
        //
        // Direction arrows, real Plotly *annotation* arrows (a proper
        // shaft ending in an arrowhead, `->`, NOT a rotated marker symbol -
        // two earlier attempts (`triangle-up`, `arrow-bar-up`) and one later
        // one (`marker.symbol: 'arrow'`) were all rejected by the user for
        // looking like a plain triangle with no visible shaft, i.e.
        // direction-ambiguous; only a real annotation arrow has an actual
        // line-shaft, so this is going back to that design).
        // KNOWN LIMITATION (2026-08-09, unresolved): because this is a
        // separate `layout.annotations` array (not part of either data
        // trace), it does NOT react to clicking "Measured"/"Forecast" in
        // the legend - a legend click only ever native-toggles a *trace*'s
        // visibility (Plotly restyle), and the `$fn` below has no way to
        // read that state back (its call signature is
        // `{getFromConfig, get, hass, vars, path, css_vars, xs, ys,
        // statistics, states, meta}` - confirmed by reading the bundled
        // source - there is no `gd`/trace-visibility argument). So the
        // arrows currently always show regardless of the legend toggle.
        // A real fix would need e.g. an `input_boolean` HA helper read via
        // `hass` inside this `$fn` plus a toggle switch added to the
        // dashboard (not done here - out of scope for this draft).
        annotations: `$fn ({ vars }) => {
          const windSpeedColor = (v) => {
            const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];
            for (const [max, color] of stops) if (v < max) return color;
            return '#8e1b8e';
          };
          const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {
            const rad = ((dirs[i] || 0) * Math.PI) / 180;
            const len = 10 + (ys[i] || 0);
            return {
              x, y: ys[i], xref: 'x', yref: 'y',
              ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: 'pixel', ayref: 'pixel',
              showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: windSpeedColor(ys[i] || 0),
            };
          });
          const arrows = [
            ...toArrows(vars.speed.xs, vars.speed.ys, vars.dir.ys),
            ...toArrows(vars.forecastSpeed.xs, vars.forecastSpeed.ys, vars.forecastDir),
          ];
          return [
            ...arrows,
            { xref: 'x', yref: 'paper', x: new Date(), y: 1, yanchor: 'bottom', text: 'Now', showarrow: false, font: { color: '#ffffff', size: 10 } },
            { xref: 'paper', yref: 'paper', x: 0, y: 1, xanchor: 'left', yanchor: 'bottom', text: '▲ N &nbsp;&nbsp; ▼ S', showarrow: false, font: { color: '#90a4ae', size: 10 } },
          ];
        }`,
        shapes: '$fn () => [{ type: "line", xref: "x", yref: "paper", x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: "#ffffff", width: 1, dash: "dot" } }]',
      },
    },
  },
];
