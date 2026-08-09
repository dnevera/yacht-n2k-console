// Card configs to preview. The first three are copy-pasted verbatim
// (translated from YAML to the equivalent JS object) from the LIVE
// dashboard-sailing.yaml so this stays a real regression check, not a
// guess at what might work - if these break here, they would break on the
// real dashboard too. The 4th one (plotly-graph-card) mirrors the live
// dashboard config as well and must be re-synced whenever
// dashboard-sailing.yaml changes (kept as its own entry historically
// since it started as a DRAFT before being deployed to bumblebee.local).
//
// tag: the custom element tag name the card JS registers itself as.
// title: shown above the rendered card in the preview page.
// config: passed to element.setConfig(config) verbatim.
//
// IMPORTANT: whenever dashboard-sailing.yaml changes, re-sync all four
// configs below by hand (there is no automated YAML->JS conversion here
// on purpose - keeping it simple/dependency-free for a local tool).

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
      graph_span: '26h',
      span: { start: 'minute', offset: '-2h' },
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
            'const times=entity.attributes.forecast_time||[];const speeds=entity.attributes.forecast_wind||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+"Z").getTime(),Math.round(speeds[i]*10)/10]).filter(p=>p[0]>=rangeStart);',
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
            'const times=entity.attributes.forecast_time||[];const gusts=entity.attributes.forecast_gust||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+"Z").getTime(),Math.round(gusts[i]*10)/10]).filter(p=>p[0]>=rangeStart);',
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
    // Bundle registers itself as `plotly-graph` (not `plotly-graph-card`).
    tag: 'plotly-graph',
    title: 'Wind Direction & Speed — Vector Chart (live dashboard config)',
    config: {"type": "custom:plotly-graph", "hours_to_show": 28, "time_offset": "24h", "entities": [{"entity": "sensor.wind_direction_history", "internal": true, "filters": [{"resample": "30m"}, {"map_y": "parseFloat(y)"}, {"store_var": "dir"}]}, {"entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed", "name": "Measured", "mode": "markers", "filters": [{"resample": "30m"}, {"map_y": "parseFloat(y)"}, {"store_var": "speed"}], "customdata": "$fn ({ xs, vars }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirYs = (vars.dir && vars.dir.ys) || [];\n  return xs.map((_, i) => {\n    const d = dirYs[i] || 0;\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '°';\n  });\n}", "hovertemplate": "%{y:.1f} kt · %{customdata}<extra>Measured</extra>", "marker": {"size": 5, "color": "$ex ys", "colorscale": [[0, "#b0e2ff"], [0.125, "#61c4e0"], [0.25, "#4bbf7a"], [0.375, "#a8d048"], [0.5, "#f5e642"], [0.625, "#f2a93b"], [0.75, "#eb5c2a"], [0.875, "#d62828"], [1, "#8e1b8e"]], "cmin": 0, "cmax": 40, "showscale": true, "colorbar": {"title": {"text": "kt", "side": "top"}, "ticksuffix": " kt", "len": 0.9}}}, {"entity": "sensor.wind_forecast_flat", "name": "Forecast", "mode": "markers", "extend_to_present": false, "filters": [{"fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.forecast_wind || []),\n})"}, {"fn": "({ meta, vars }) => { vars.forecastDir = meta.forecast_dir || []; return {}; }"}, {"store_var": "forecastSpeed"}], "customdata": "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  return (meta.forecast_dir || []).map((d) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '°');\n}", "hovertemplate": "%{y:.1f} kt · %{customdata}<extra>Forecast</extra>", "marker": {"size": 5, "color": "$ex ys", "colorscale": [[0, "#b0e2ff"], [0.125, "#61c4e0"], [0.25, "#4bbf7a"], [0.375, "#a8d048"], [0.5, "#f5e642"], [0.625, "#f2a93b"], [0.75, "#eb5c2a"], [0.875, "#d62828"], [1, "#8e1b8e"]], "cmin": 0, "cmax": 40}}, {"entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed", "name": "Gusts (measured)", "mode": "lines", "filters": [{"map_y": "parseFloat(y)"}, {"fn": "({ xs, ys }) => {\n  const windowMs = 10 * 60 * 1000;\n  const outXs = [];\n  const outYs = [];\n  let bucketStart = null;\n  let bucketMax = null;\n  for (let i = 0; i < xs.length; i++) {\n    const t = new Date(xs[i]).getTime();\n    if (bucketStart === null || t - bucketStart >= windowMs) {\n      if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n      bucketStart = t;\n      bucketMax = ys[i];\n    } else if (ys[i] > bucketMax) {\n      bucketMax = ys[i];\n    }\n  }\n  if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n  return { xs: outXs, ys: outYs };\n}"}], "hovertemplate": "%{y:.1f} kt<extra>Gusts (measured)</extra>", "line": {"dash": "dot", "width": 1, "color": "#b0bec5"}}, {"entity": "sensor.wind_forecast_flat", "name": "Gusts (forecast)", "mode": "lines", "extend_to_present": false, "filters": [{"fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.forecast_gust || []),\n})"}], "hovertemplate": "%{y:.1f} kt<extra>Gusts (forecast)</extra>", "line": {"dash": "dot", "width": 1, "color": "#78909c"}}], "on_dblclick": "$fn () => () => {\n  const found = [];\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));\n    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });\n  };\n  walk(document);\n  found.forEach((el) => {\n    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');\n    if (btn && !btn.classList.contains('hidden')) btn.click();\n  });\n}", "config": {"scrollZoom": false, "displayModeBar": false, "doubleClick": false}, "layout": {"dragmode": "pan", "hovermode": "x unified", "hoverdistance": -1, "xaxis": {"showspikes": true, "spikemode": "across", "spikedash": "dash", "spikethickness": 1, "spikecolor": "#90a4ae", "spikesnap": "cursor"}, "yaxis": {"title": "Wind speed (kts)", "rangemode": "tozero", "autorange": true, "fixedrange": true, "showspikes": false}, "legend": {"orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.3}, "margin": {"b": 70}, "annotations": "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const windSpeedColor = (v) => {\n    const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const compassPoint = (d) => {\n    const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 10 + (ys[i] || 0);\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: windSpeedColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const arrows = [\n    ...toArrows(vars.speed.xs, vars.speed.ys, vars.dir.ys),\n    ...toArrows(vars.forecastSpeed.xs, vars.forecastSpeed.ys, vars.forecastDir),\n  ];\n  return [\n    ...arrows,\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '▲ N &nbsp;&nbsp; ▼ S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}", "shapes": "$fn () => [{ type: \"line\", xref: \"x\", yref: \"paper\", x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: \"#ffffff\", width: 1, dash: \"dash\" } }]"}, "grid_options": {"columns": 36}},
  },
];
