/**
 * Plotly Wave Height & Direction Chart card configuration module.
 */

const PLOTLY_WAVE_CARD_CONFIG = {
  type: 'custom:plotly-graph',
  hours_to_show: '$fn ({ hass }) => { const a = (hass.states[\'sensor.chart_time_window\'] || { attributes: {} }).attributes; return Number(a.history_hours || 4) + Number(a.forecast_hours || 24); }',
  time_offset: '$fn ({ hass }) => ((hass.states[\'sensor.chart_time_window\'] || { attributes: {} }).attributes.forecast_hours || 24) + \'h\'',
  entities: [
    {
      entity: 'sensor.wave_forecast_flat',
      name: 'Wave height (forecast)',
      mode: 'markers',
      extend_to_present: false,
      filters: [
        { fn: '({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),\n  ys: (meta.wave_height || []),\n})' },
        { fn: '({ meta, vars }) => { vars.waveDir = meta.wave_direction || []; vars.wavePeriod = meta.wave_period || []; return {}; }' },
        { store_var: 'waveHeight' },
      ],
      customdata: "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirs = meta.wave_direction || [];\n  const periods = meta.wave_period || [];\n  return dirs.map((d, i) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '° · ' + (periods[i] != null ? Math.round(periods[i] * 10) / 10 + ' s' : '– s'));\n}",
      hovertemplate: '%{y:.2f} m · %{customdata}<extra>Wave (forecast)</extra>',
      marker: { size: 6, symbol: 'diamond', color: '#4fc3f7', line: { width: 0 } },
    },
    {
      entity: 'sensor.wave_forecast_flat',
      name: 'Period (s)',
      mode: 'lines',
      extend_to_present: false,
      visible: 'legendonly',
      filters: [
        { fn: '({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),\n  ys: (meta.wave_period || []),\n})' },
      ],
      hovertemplate: '%{y:.1f} s<extra>Period</extra>',
      line: { dash: 'dot', width: 1, color: '#b0bec5' },
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
    yaxis: { title: 'Wave height (m)', rangemode: 'tozero', autorange: true, fixedrange: true, showspikes: false },
    legend: { orientation: 'h', x: 0.5, xanchor: 'center', y: -0.3 },
    margin: { b: 70 },
    annotations: "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const waveHeightColor = (v) => {\n    const stops = [[0.3,'#b0e2ff'],[0.6,'#61c4e0'],[1,'#4bbf7a'],[1.5,'#a8d048'],[2,'#f5e642'],[3,'#f2a93b'],[4,'#eb5c2a'],[5,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 14;\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad),\n      axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: waveHeightColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const wh = vars.waveHeight || { xs: [], ys: [] };\n  return [\n    ...toArrows(wh.xs, wh.ys, vars.waveDir || []),\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '▲ N &nbsp;&nbsp; ▼ S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}",
    shapes: "$fn () => {\n  const patchTouch = (gd) => {\n    if (!gd || gd.__touchGestureLongPress) return;\n    gd.__touchGestureLongPress = true;\n    const HOLD_MS = 400;\n    const MOVE_TOL = 10;\n    let timer = null, hover = false, sx = 0, sy = 0;\n    const hoverAt = (t) => {\n      const target = gd.querySelector('.nsewdrag') || gd;\n      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };\n      target.dispatchEvent(new MouseEvent('mouseover', opts));\n      target.dispatchEvent(new MouseEvent('mousemove', opts));\n    };\n    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };\n    const abortPan = () => {\n      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));\n    };\n    gd.addEventListener('touchstart', (e) => {\n      clear();\n      hover = false;\n      if (e.touches.length !== 1) return;\n      const t = e.touches[0];\n      sx = t.clientX; sy = t.clientY;\n      timer = setTimeout(() => {\n        timer = null;\n        hover = true;\n        abortPan();\n        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }\n        hoverAt({ clientX: sx, clientY: sy });\n      }, HOLD_MS);\n    }, true);\n    gd.addEventListener('touchmove', (e) => {\n      const t = e.touches[0];\n      if (hover) {\n        e.stopPropagation();\n        if (e.cancelable) e.preventDefault();\n        if (t) hoverAt(t);\n        return;\n      }\n      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();\n    }, true);\n    const end = () => { clear(); hover = false; };\n    gd.addEventListener('touchend', end, true);\n    gd.addEventListener('touchcancel', end, true);\n  };\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];\n}",
  },
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = PLOTLY_WAVE_CARD_CONFIG;
}
