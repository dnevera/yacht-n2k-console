/**
 * Plotly Wind Vector Chart card configuration module.
 *
 * NOT part of the build: build.py compiles the dashboard from
 * src/yaml/dashboard/sections/04_wind.yaml (+ the shared snippets in
 * src/js/common/), and only copies windy-boat-card.js from this directory.
 * This file is a stale snapshot kept for reference — it still contains the
 * index-based direction lookup and the missing non-finite filter that made the
 * chart render nonsense (see CHANGELOG 2026-08-11); do not copy code out of it
 * without re-checking against the YAML section, which is the source of truth.
 */

const PLOTLY_WIND_CARD_CONFIG = {
  type: 'custom:plotly-graph',
  hours_to_show: '$fn ({ hass }) => { const a = (hass.states[\'sensor.chart_time_window\'] || { attributes: {} }).attributes; return Number(a.history_hours || 4) + Number(a.forecast_hours || 24); }',
  time_offset: '$fn ({ hass }) => ((hass.states[\'sensor.chart_time_window\'] || { attributes: {} }).attributes.forecast_hours || 24) + \'h\'',
  entities: [
    {
      entity: 'sensor.wind_direction_history',
      internal: true,
      filters: [{ resample: '30m' }, { map_y: 'parseFloat(y)' }, { store_var: 'dir' }],
    },
    {
      entity: 'sensor.boat_wind_speed',
      name: 'Measured',
      mode: 'markers',
      filters: [
        { resample: '30m' },
        { map_y: 'parseFloat(y)' },
        { store_var: 'speed' },
      ],
      customdata: "$fn ({ xs, vars }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirYs = (vars.dir && vars.dir.ys) || [];\n  return xs.map((_, i) => {\n    const d = dirYs[i] || 0;\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '°';\n  });\n}",
      hovertemplate: '%{y:.1f} kt · %{customdata}<extra>Measured</extra>',
      marker: { size: 6, symbol: 'circle', color: '#4fc3f7', line: { width: 0 } },
    },
    {
      entity: 'sensor.wind_forecast_flat',
      name: 'Forecast',
      mode: 'markers',
      extend_to_present: false,
      filters: [
        { fn: '({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),\n  ys: (meta.forecast_wind || []),\n})' },
        { fn: '({ meta, vars }) => { vars.forecastDir = meta.forecast_dir || []; return {}; }' },
        { store_var: 'forecastSpeed' },
      ],
      customdata: "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  return (meta.forecast_dir || []).map((d) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '°');\n}",
      hovertemplate: '%{y:.1f} kt · %{customdata}<extra>Forecast</extra>',
      marker: { size: 6, symbol: 'diamond', color: '#ff7043', line: { width: 0 } },
    },
    {
      entity: 'sensor.boat_wind_speed',
      name: 'kt scale',
      mode: 'markers',
      showlegend: false,
      hoverinfo: 'skip',
      extend_to_present: false,
      filters: [{ resample: '30m' }, { map_y: 'parseFloat(y)' }, { fn: '() => ({ xs: [], ys: [] })' }],
      marker: {
        size: 0.1,
        opacity: 0,
        color: '$ex ys',
        colorscale: [
          [0, '#b0e2ff'],
          [0.125, '#61c4e0'],
          [0.25, '#4bbf7a'],
          [0.375, '#a8d048'],
          [0.5, '#f5e642'],
          [0.625, '#f2a93b'],
          [0.75, '#eb5c2a'],
          [0.875, '#d62828'],
          [1, '#8e1b8e'],
        ],
        cmin: 0,
        cmax: 40,
        showscale: true,
        colorbar: { title: { text: 'kt', side: 'top' }, ticksuffix: ' kt', len: 0.9 },
      },
    },
    {
      entity: 'sensor.boat_wind_speed',
      name: 'Gusts (measured)',
      mode: 'lines',
      filters: [
        { map_y: 'parseFloat(y)' },
        { fn: '({ xs, ys }) => {\n  const windowMs = 10 * 60 * 1000;\n  const outXs = [];\n  const outYs = [];\n  let bucketStart = null;\n  let bucketMax = null;\n  for (let i = 0; i < xs.length; i++) {\n    const t = new Date(xs[i]).getTime();\n    if (bucketStart === null || t - bucketStart >= windowMs) {\n      if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n      bucketStart = t;\n      bucketMax = ys[i];\n    } else if (ys[i] > bucketMax) {\n      bucketMax = ys[i];\n    }\n  }\n  if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n  return { xs: outXs, ys: outYs };\n}' },
      ],
      hovertemplate: '%{y:.1f} kt<extra>Gusts (measured)</extra>',
      line: { dash: 'dot', width: 1, color: '#b0bec5' },
    },
    {
      entity: 'sensor.wind_forecast_flat',
      name: 'Gusts (forecast)',
      mode: 'lines',
      extend_to_present: false,
      filters: [{ fn: '({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),\n  ys: (meta.forecast_gust || []),\n})' }],
      hovertemplate: '%{y:.1f} kt<extra>Gusts (forecast)</extra>',
      line: { dash: 'dot', width: 1, color: '#78909c' },
    },
  ],
  on_dblclick: "$fn () => () => {\n  const found = [];\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));\n    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });\n  };\n  walk(document);\n  found.forEach((el) => {\n    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');\n    if (btn && !btn.classList.contains('hidden')) btn.click();\n  });\n}",
  config: { scrollZoom: false, displayModeBar: false, doubleClick: false },
  layout: {
    dragmode: 'pan',
    hovermode: 'x unified',
    hoverdistance: -1,
    xaxis: {
      showspikes: true,
      spikemode: 'across',
      spikedash: 'dash',
      spikethickness: 1,
      spikecolor: '#90a4ae',
      spikesnap: 'cursor',
    },
    yaxis: { title: 'Wind speed (kts)', rangemode: 'tozero', autorange: true, fixedrange: true, showspikes: false },
    legend: { orientation: 'h', x: 0.5, xanchor: 'center', y: -0.3 },
    margin: { b: 70 },
    annotations: "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const windSpeedColor = (v) => {\n    const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 10 + (ys[i] || 0);\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad),\n      axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: windSpeedColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const arrows = [\n    ...toArrows(vars.speed.xs, vars.speed.ys, vars.dir.ys),\n    ...toArrows(vars.forecastSpeed.xs, vars.forecastSpeed.ys, vars.forecastDir),\n  ];\n  return [\n    ...arrows,\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '▲ N &nbsp;&nbsp; ▼ S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}",
    shapes: "$fn () => {\n  const patchTouch = (gd) => {\n    if (!gd || gd.__touchGestureLongPress) return;\n    gd.__touchGestureLongPress = true;\n    const HOLD_MS = 400;\n    const MOVE_TOL = 10;\n    let timer = null, hover = false, sx = 0, sy = 0;\n    const hoverAt = (t) => {\n      const target = gd.querySelector('.nsewdrag') || gd;\n      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };\n      target.dispatchEvent(new MouseEvent('mouseover', opts));\n      target.dispatchEvent(new MouseEvent('mousemove', opts));\n    };\n    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };\n    const abortPan = () => {\n      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));\n    };\n    gd.addEventListener('touchstart', (e) => {\n      clear();\n      hover = false;\n      if (e.touches.length !== 1) return;\n      const t = e.touches[0];\n      sx = t.clientX; sy = t.clientY;\n      timer = setTimeout(() => {\n        timer = null;\n        hover = true;\n        abortPan();\n        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }\n        hoverAt({ clientX: sx, clientY: sy });\n      }, HOLD_MS);\n    }, true);\n    gd.addEventListener('touchmove', (e) => {\n      const t = e.touches[0];\n      if (hover) {\n        e.stopPropagation();\n        if (e.cancelable) e.preventDefault();\n        if (t) hoverAt(t);\n        return;\n      }\n      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();\n    }, true);\n    const end = () => { clear(); hover = false; };\n    gd.addEventListener('touchend', end, true);\n    gd.addEventListener('touchcancel', end, true);\n  };\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];\n}",
  },
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = PLOTLY_WIND_CARD_CONFIG;
}
