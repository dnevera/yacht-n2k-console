// Generated automatically by build.py — DO NOT EDIT MANUALLY

window.PREVIEW_SECTIONS = [
  {
    "source": "01_sensors.yaml",
    "title": "01 Sensors",
    "grids": [
      {
        "column_span": null,
        "columns": null,
        "cards": [
          {
            "tag": "hui-heading-card",
            "title": "Heading: Sensors (heading from 01_sensors.yaml)",
            "config": {
              "type": "heading",
              "heading_style": "title",
              "heading": "Sensors"
            }
          },
          {
            "tag": "hui-gauge-card",
            "title": "STW (kn) (gauge from 01_sensors.yaml)",
            "config": {
              "type": "gauge",
              "name": "STW (kn)",
              "entity": "sensor.speed_raymarine_20_442559_pk_b941014ae3e7110c03bb1cd071a22b76_speed_water_referenced",
              "min": 0,
              "max": 12,
              "needle": true,
              "severity": {
                "green": 0,
                "yellow": 8,
                "red": 10
              }
            }
          },
          {
            "tag": "hui-gauge-card",
            "title": "Depth (m) (gauge from 01_sensors.yaml)",
            "config": {
              "type": "gauge",
              "name": "Depth (m)",
              "entity": "sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth",
              "min": 0,
              "max": 30,
              "needle": true,
              "severity": {
                "red": 0,
                "yellow": 3,
                "green": 5
              },
              "card_mod": {
                "style": "ha-card {\n  --accent-color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }};\n}\n\nha-gauge {\n  --gauge-color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }} !important;\n  --primary-text-color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }} !important;\n}\n\nha-card {\n  color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }};\n}"
              }
            }
          },
          {
            "tag": "hui-gauge-card",
            "title": "SOG (kn) (gauge from 01_sensors.yaml)",
            "config": {
              "type": "gauge",
              "name": "SOG (kn)",
              "entity": "sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_sog",
              "min": 0,
              "max": 12,
              "needle": true,
              "severity": {
                "green": 0,
                "yellow": 8,
                "red": 10
              }
            }
          }
        ]
      }
    ]
  },
  {
    "source": "02_position.yaml",
    "title": "02 Position",
    "grids": [
      {
        "column_span": null,
        "columns": null,
        "cards": [
          {
            "tag": "hui-heading-card",
            "title": "Heading: Position (heading from 02_position.yaml)",
            "config": {
              "type": "heading",
              "heading": "Position"
            }
          },
          {
            "tag": "compass-card",
            "title": "compass-card (custom:compass-card from 02_position.yaml)",
            "config": {
              "type": "custom:compass-card",
              "header": {
                "title": {
                  "value": "COG"
                },
                "icon": {
                  "value": "mdi:compass-outline"
                }
              },
              "compass": {
                "circle": {
                  "color": "#37474f"
                },
                "ticks": {
                  "show": true,
                  "color": "#90a4ae",
                  "radius": 95
                },
                "north": {
                  "show": true
                },
                "east": {
                  "show": true
                },
                "south": {
                  "show": true
                },
                "west": {
                  "show": true
                }
              },
              "indicator_sensors": [
                {
                  "sensor": "sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog",
                  "indicator": {
                    "image": "arrow_inward",
                    "color": "#ff7043"
                  }
                }
              ],
              "value_sensors": [
                {
                  "sensor": "sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog"
                }
              ]
            }
          },
          {
            "tag": "hui-map-card",
            "title": "hui-map-card (map from 02_position.yaml)",
            "config": {
              "type": "map",
              "entities": [
                {
                  "entity": "device_tracker.nevera"
                }
              ],
              "default_zoom": 14,
              "aspect_ratio": "16x9"
            }
          },
          {
            "tag": "hui-entity-card",
            "title": "Latitude (entity from 02_position.yaml)",
            "config": {
              "type": "entity",
              "entity": "sensor.boat_latitude",
              "name": "Latitude",
              "card_mod": {
                "style": "ha-card {\n  font-size: 14px;\n  font-weight: bold;\n  color: var(--grey-color);\n}\n\n.name {\n  font-size: 14px !important;\n  font-weight: bold !important;\n  color: var(--grey-color) !important;\n}\n\n.value {\n  font-size: calc(14px * 1.75) !important;\n}\n\n.measurement {\n  font-size: 14px !important;\n  color: var(--grey-color) !important;\n}"
              }
            }
          },
          {
            "tag": "hui-entity-card",
            "title": "Longitude (entity from 02_position.yaml)",
            "config": {
              "type": "entity",
              "entity": "sensor.boat_longitude",
              "name": "Longitude",
              "card_mod": {
                "style": "ha-card {\n  font-size: 13px;\n  font-weight: bold;\n  color: var(--grey-color);\n}\n\n.name {\n  font-size: 13px !important;\n  font-weight: bold !important;\n  color: var(--grey-color) !important;\n}\n\n.value {\n  font-size: calc(14px * 1.75) !important;\n}\n\n.measurement {\n  font-size: 14px !important;\n  color: var(--grey-color) !important;\n}"
              }
            }
          }
        ]
      }
    ]
  },
  {
    "source": "03_conditions.yaml",
    "title": "03 Conditions",
    "grids": [
      {
        "column_span": 1,
        "columns": null,
        "cards": [
          {
            "tag": "hui-heading-card",
            "title": "Heading: Conditions (heading from 03_conditions.yaml)",
            "config": {
              "type": "heading",
              "heading_style": "title",
              "heading": "Conditions"
            }
          },
          {
            "tag": "windrose-card",
            "title": "windrose-card (custom:windrose-card from 03_conditions.yaml)",
            "config": {
              "type": "custom:windrose-card",
              "windspeed_bar_location": "right",
              "data_period": {
                "period_back": "-24h"
              },
              "wind_direction_entity": {
                "entity": "sensor.wind_direction_history"
              },
              "windspeed_entities": [
                {
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
                  "name": "Speed"
                }
              ],
              "current_direction": {
                "show_arrow": true
              },
              "corner_info": {
                "top_right": {
                  "label": "Wind Speed",
                  "unit": " kn",
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed"
                }
              }
            }
          },
          {
            "tag": "hui-gauge-card",
            "title": "Pressure (mmHg) (gauge from 03_conditions.yaml)",
            "config": {
              "type": "gauge",
              "name": "Pressure (mmHg)",
              "entity": "sensor.barometer_mmhg",
              "min": 720,
              "max": 790,
              "needle": true,
              "severity": {
                "red": 720,
                "yellow": 745,
                "green": 760
              },
              "grid_options": {
                "rows": 3,
                "columns": 12
              }
            }
          },
          {
            "tag": "hui-tile-card",
            "title": "sensor.barometer_mmhg (tile from 03_conditions.yaml)",
            "config": {
              "type": "tile",
              "entity": "sensor.barometer_mmhg",
              "features": [
                {
                  "type": "trend-graph"
                }
              ],
              "grid_options": {
                "columns": 12,
                "rows": 2
              }
            }
          }
        ]
      }
    ]
  },
  {
    "source": "04_wind.yaml",
    "title": "04 Wind",
    "grids": [
      {
        "column_span": 3,
        "columns": null,
        "cards": [
          {
            "tag": "hui-heading-card",
            "title": "Heading: Wind Direction & Speed (heading from 04_wind.yaml)",
            "config": {
              "type": "heading",
              "heading_style": "title",
              "heading": "Wind Direction & Speed",
              "icon": "mdi:weather-windy"
            }
          },
          {
            "tag": "hui-glance-card",
            "title": "Glance metrics (glance from 04_wind.yaml)",
            "config": {
              "type": "glance",
              "show_icon": false,
              "state_color": false,
              "columns": 3,
              "entities": [
                {
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
                  "name": "Measured now",
                  "card_mod": {
                    "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #4fc3f7 !important;\n}\n"
                  }
                },
                {
                  "entity": "sensor.wind_forecast_next_hour",
                  "name": "Forecast next 1h",
                  "card_mod": {
                    "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #b0bec5 !important;\n}\n"
                  }
                },
                {
                  "entity": "sensor.wind_gust_next_hour",
                  "name": "Gusts next 1h",
                  "card_mod": {
                    "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #ff7043 !important;\n}\n"
                  }
                }
              ],
              "card_mod": {
                "style": "ha-card { padding: 10px 8px 6px !important; }\n.entities { padding: 0 !important; }\n.entity {\n  flex-direction: column-reverse !important;\n}\n"
              },
              "grid_options": {
                "columns": 36
              }
            }
          },
          {
            "tag": "plotly-graph",
            "title": "plotly-graph (custom:plotly-graph from 04_wind.yaml)",
            "config": {
              "type": "custom:plotly-graph",
              "hours_to_show": "$fn ({ hass }) => { const a = (hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes; return Number(a.history_hours || 4) + Number(a.forecast_hours || 24); }",
              "time_offset": "$fn ({ hass }) => ((hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes.forecast_hours || 24) + 'h'",
              "entities": [
                {
                  "entity": "sensor.wind_direction_history",
                  "internal": true,
                  "filters": [
                    {
                      "resample": "30m"
                    },
                    {
                      "map_y": "parseFloat(y)"
                    },
                    {
                      "store_var": "dir"
                    }
                  ]
                },
                {
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
                  "name": "Measured",
                  "mode": "markers",
                  "filters": [
                    {
                      "resample": "30m"
                    },
                    {
                      "map_y": "parseFloat(y)"
                    },
                    {
                      "store_var": "speed"
                    }
                  ],
                  "customdata": "$fn ({ xs, vars }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirYs = (vars.dir && vars.dir.ys) || [];\n  return xs.map((_, i) => {\n    const d = dirYs[i] || 0;\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '\u00b0';\n  });\n}",
                  "hovertemplate": "%{y:.1f} kt \u00b7 %{customdata}<extra>Measured</extra>",
                  "marker": {
                    "size": 6,
                    "symbol": "circle",
                    "color": "#4fc3f7",
                    "line": {
                      "width": 0
                    }
                  }
                },
                {
                  "entity": "sensor.wind_forecast_flat",
                  "name": "Forecast",
                  "mode": "markers",
                  "extend_to_present": false,
                  "filters": [
                    {
                      "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.forecast_wind || []),\n})"
                    },
                    {
                      "fn": "({ meta, vars }) => { vars.forecastDir = meta.forecast_dir || []; return {}; }"
                    },
                    {
                      "store_var": "forecastSpeed"
                    }
                  ],
                  "customdata": "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  return (meta.forecast_dir || []).map((d) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '\u00b0');\n}",
                  "hovertemplate": "%{y:.1f} kt \u00b7 %{customdata}<extra>Forecast</extra>",
                  "marker": {
                    "size": 6,
                    "symbol": "diamond",
                    "color": "#ff7043",
                    "line": {
                      "width": 0
                    }
                  }
                },
                {
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
                  "name": "kt scale",
                  "mode": "markers",
                  "showlegend": false,
                  "hoverinfo": "skip",
                  "extend_to_present": false,
                  "filters": [
                    {
                      "resample": "30m"
                    },
                    {
                      "map_y": "parseFloat(y)"
                    },
                    {
                      "fn": "() => ({ xs: [], ys: [] })"
                    }
                  ],
                  "marker": {
                    "size": 0.1,
                    "opacity": 0,
                    "color": "$ex ys",
                    "colorscale": [
                      [
                        0,
                        "#b0e2ff"
                      ],
                      [
                        0.125,
                        "#61c4e0"
                      ],
                      [
                        0.25,
                        "#4bbf7a"
                      ],
                      [
                        0.375,
                        "#a8d048"
                      ],
                      [
                        0.5,
                        "#f5e642"
                      ],
                      [
                        0.625,
                        "#f2a93b"
                      ],
                      [
                        0.75,
                        "#eb5c2a"
                      ],
                      [
                        0.875,
                        "#d62828"
                      ],
                      [
                        1,
                        "#8e1b8e"
                      ]
                    ],
                    "cmin": 0,
                    "cmax": 40,
                    "showscale": true,
                    "colorbar": {
                      "title": {
                        "text": "kt",
                        "side": "top"
                      },
                      "ticksuffix": " kt",
                      "len": 0.9
                    }
                  }
                },
                {
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
                  "name": "Gusts (measured)",
                  "mode": "lines",
                  "filters": [
                    {
                      "map_y": "parseFloat(y)"
                    },
                    {
                      "fn": "({ xs, ys }) => {\n  const windowMs = 10 * 60 * 1000;\n  const outXs = [];\n  const outYs = [];\n  let bucketStart = null;\n  let bucketMax = null;\n  for (let i = 0; i < xs.length; i++) {\n    const t = new Date(xs[i]).getTime();\n    if (bucketStart === null || t - bucketStart >= windowMs) {\n      if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n      bucketStart = t;\n      bucketMax = ys[i];\n    } else if (ys[i] > bucketMax) {\n      bucketMax = ys[i];\n    }\n  }\n  if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n  return { xs: outXs, ys: outYs };\n}"
                    }
                  ],
                  "hovertemplate": "%{y:.1f} kt<extra>Gusts (measured)</extra>",
                  "line": {
                    "dash": "dot",
                    "width": 1,
                    "color": "#b0bec5"
                  }
                },
                {
                  "entity": "sensor.wind_forecast_flat",
                  "name": "Gusts (forecast)",
                  "mode": "lines",
                  "extend_to_present": false,
                  "filters": [
                    {
                      "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.forecast_gust || []),\n})"
                    }
                  ],
                  "hovertemplate": "%{y:.1f} kt<extra>Gusts (forecast)</extra>",
                  "line": {
                    "dash": "dot",
                    "width": 1,
                    "color": "#78909c"
                  }
                }
              ],
              "on_dblclick": "$fn () => () => {\n  const found = [];\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));\n    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });\n  };\n  walk(document);\n  found.forEach((el) => {\n    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');\n    if (btn && !btn.classList.contains('hidden')) btn.click();\n  });\n}",
              "config": {
                "scrollZoom": false,
                "displayModeBar": false,
                "doubleClick": false
              },
              "layout": {
                "dragmode": "pan",
                "hovermode": "x unified",
                "hoverdistance": -1,
                "xaxis": {
                  "showspikes": true,
                  "spikemode": "across",
                  "spikedash": "dash",
                  "spikethickness": 1,
                  "spikecolor": "#90a4ae",
                  "spikesnap": "cursor"
                },
                "yaxis": {
                  "title": "Wind speed (kts)",
                  "rangemode": "tozero",
                  "autorange": true,
                  "fixedrange": true,
                  "showspikes": false
                },
                "legend": {
                  "orientation": "h",
                  "x": 0.5,
                  "xanchor": "center",
                  "y": -0.3
                },
                "margin": {
                  "b": 70
                },
                "annotations": "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const windSpeedColor = (v) => {\n    const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const compassPoint = (d) => {\n    const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 10 + (ys[i] || 0);\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: windSpeedColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const arrows = [\n    ...toArrows(vars.speed.xs, vars.speed.ys, vars.dir.ys),\n    ...toArrows(vars.forecastSpeed.xs, vars.forecastSpeed.ys, vars.forecastDir),\n  ];\n  return [\n    ...arrows,\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '\u25b2 N &nbsp;&nbsp; \u25bc S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}",
                "shapes": "$fn () => {\n  const patchTouch = (gd) => {\n    if (!gd || gd.__touchGestureLongPress) return;\n    gd.__touchGestureLongPress = true;\n    const HOLD_MS = 400;\n    const MOVE_TOL = 10;\n    let timer = null, hover = false, sx = 0, sy = 0;\n    const hoverAt = (t) => {\n      const target = gd.querySelector('.nsewdrag') || gd;\n      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };\n      target.dispatchEvent(new MouseEvent('mouseover', opts));\n      target.dispatchEvent(new MouseEvent('mousemove', opts));\n    };\n    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };\n    const abortPan = () => {\n      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));\n    };\n    gd.addEventListener('touchstart', (e) => {\n      clear();\n      hover = false;\n      if (e.touches.length !== 1) return;\n      const t = e.touches[0];\n      sx = t.clientX; sy = t.clientY;\n      timer = setTimeout(() => {\n        timer = null;\n        hover = true;\n        abortPan();\n        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }\n        hoverAt({ clientX: sx, clientY: sy });\n      }, HOLD_MS);\n    }, true);\n    gd.addEventListener('touchmove', (e) => {\n      const t = e.touches[0];\n      if (hover) {\n        e.stopPropagation();\n        if (e.cancelable) e.preventDefault();\n        if (t) hoverAt(t);\n        return;\n      }\n      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();\n    }, true);\n    const end = () => { clear(); hover = false; };\n    gd.addEventListener('touchend', end, true);\n    gd.addEventListener('touchcancel', end, true);\n  };\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];\n}"
              },
              "grid_options": {
                "columns": 36
              }
            }
          }
        ]
      },
      {
        "column_span": null,
        "columns": null,
        "cards": [
          {
            "tag": "apexcharts-card",
            "title": "Wind \u2014 History & Forecast (apexcharts-card)",
            "config": {
              "type": "custom:apexcharts-card",
              "cache": false,
              "graph_span": "26h",
              "span": {
                "start": "minute",
                "offset": "-2h"
              },
              "now": {
                "show": true,
                "label": "Now",
                "color": "#ffffff"
              },
              "header": {
                "show": true,
                "title": "Wind \u2014 History & Forecast",
                "show_states": true,
                "colorize_states": true
              },
              "apex_config": {
                "chart": {
                  "height": 300,
                  "width": 600
                },
                "legend": {
                  "position": "bottom"
                },
                "yaxis": {
                  "min": 0,
                  "decimalsInFloat": 1
                }
              },
              "series": [
                {
                  "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
                  "name": "Measured (kts)",
                  "type": "area",
                  "color": "#00bcd4",
                  "stroke_width": 2,
                  "fill_raw": "null",
                  "unit": "kts",
                  "show": {
                    "extremas": true
                  }
                },
                {
                  "entity": "sensor.wind_forecast_flat",
                  "name": "Forecast (kts)",
                  "type": "line",
                  "color": "#4fc3f7",
                  "stroke_width": 2,
                  "stroke_dash": 5,
                  "unit": "kts",
                  "data_generator": "const times=entity.attributes.forecast_time||[];const speeds=entity.attributes.forecast_wind||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+\"Z\").getTime(),Math.round(speeds[i]*10)/10]).filter(p=>p[0]>=rangeStart);"
                },
                {
                  "entity": "sensor.wind_forecast_flat",
                  "name": "Gusts (kts)",
                  "type": "line",
                  "color": "#ff7043",
                  "stroke_width": 1,
                  "stroke_dash": 4,
                  "unit": "kts",
                  "opacity": 0.9,
                  "data_generator": "const times=entity.attributes.forecast_time||[];const gusts=entity.attributes.forecast_gust||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+\"Z\").getTime(),Math.round(gusts[i]*10)/10]).filter(p=>p[0]>=rangeStart);"
                }
              ]
            }
          }
        ]
      }
    ]
  },
  {
    "source": "05_waves.yaml",
    "title": "05 Waves",
    "grids": [
      {
        "column_span": 3,
        "columns": null,
        "cards": [
          {
            "tag": "hui-heading-card",
            "title": "Heading: Waves (heading from 05_waves.yaml)",
            "config": {
              "type": "heading",
              "heading_style": "title",
              "heading": "Waves",
              "icon": "mdi:wave"
            }
          },
          {
            "tag": "hui-glance-card",
            "title": "Glance metrics (glance from 05_waves.yaml)",
            "config": {
              "type": "glance",
              "show_icon": false,
              "state_color": false,
              "columns": 2,
              "entities": [
                {
                  "entity": "sensor.wave_height_next_hour",
                  "name": "Height next 1h",
                  "card_mod": {
                    "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #4fc3f7 !important;\n}\n"
                  }
                },
                {
                  "entity": "sensor.wave_period_next_hour",
                  "name": "Period next 1h",
                  "card_mod": {
                    "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #b0bec5 !important;\n}\n"
                  }
                }
              ],
              "card_mod": {
                "style": "ha-card { padding: 10px 8px 6px !important; }\n.entities { padding: 0 !important; }\n.entity {\n  flex-direction: column-reverse !important;\n}\n"
              },
              "grid_options": {
                "columns": 36
              }
            }
          },
          {
            "tag": "plotly-graph",
            "title": "plotly-graph (custom:plotly-graph from 05_waves.yaml)",
            "config": {
              "type": "custom:plotly-graph",
              "hours_to_show": "$fn ({ hass }) => { const a = (hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes; return Number(a.history_hours || 4) + Number(a.forecast_hours || 24); }",
              "time_offset": "$fn ({ hass }) => ((hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes.forecast_hours || 24) + 'h'",
              "entities": [
                {
                  "entity": "sensor.wave_forecast_flat",
                  "name": "Wave height (forecast)",
                  "mode": "markers",
                  "extend_to_present": false,
                  "filters": [
                    {
                      "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.wave_height || []),\n})"
                    },
                    {
                      "fn": "({ meta, vars }) => { vars.waveDir = meta.wave_direction || []; vars.wavePeriod = meta.wave_period || []; return {}; }"
                    },
                    {
                      "store_var": "waveHeight"
                    }
                  ],
                  "customdata": "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirs = meta.wave_direction || [];\n  const periods = meta.wave_period || [];\n  return dirs.map((d, i) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '\u00b0 \u00b7 ' + (periods[i] != null ? Math.round(periods[i] * 10) / 10 + ' s' : '\u2013 s'));\n}",
                  "hovertemplate": "%{y:.2f} m \u00b7 %{customdata}<extra>Wave (forecast)</extra>",
                  "marker": {
                    "size": 6,
                    "symbol": "diamond",
                    "color": "#4fc3f7",
                    "line": {
                      "width": 0
                    }
                  }
                },
                {
                  "entity": "sensor.wave_forecast_flat",
                  "name": "Period (s)",
                  "mode": "lines",
                  "extend_to_present": false,
                  "visible": "legendonly",
                  "filters": [
                    {
                      "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.wave_period || []),\n})"
                    }
                  ],
                  "hovertemplate": "%{y:.1f} s<extra>Period</extra>",
                  "line": {
                    "dash": "dot",
                    "width": 1,
                    "color": "#b0bec5"
                  }
                }
              ],
              "on_dblclick": "$fn () => () => {\n  const found = [];\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));\n    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });\n  };\n  walk(document);\n  found.forEach((el) => {\n    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');\n    if (btn && !btn.classList.contains('hidden')) btn.click();\n  });\n}",
              "config": {
                "scrollZoom": false,
                "displayModeBar": false,
                "doubleClick": false
              },
              "layout": {
                "dragmode": "pan",
                "hovermode": "x unified",
                "hoverdistance": -1,
                "xaxis": {
                  "showspikes": true,
                  "spikemode": "across",
                  "spikedash": "dash",
                  "spikethickness": 1,
                  "spikecolor": "#90a4ae",
                  "spikesnap": "cursor"
                },
                "yaxis": {
                  "title": "Wave height (m)",
                  "rangemode": "tozero",
                  "autorange": true,
                  "fixedrange": true,
                  "showspikes": false
                },
                "legend": {
                  "orientation": "h",
                  "x": 0.5,
                  "xanchor": "center",
                  "y": -0.3
                },
                "margin": {
                  "b": 70
                },
                "annotations": "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const waveHeightColor = (v) => {\n    const stops = [[0.3,'#b0e2ff'],[0.6,'#61c4e0'],[1,'#4bbf7a'],[1.5,'#a8d048'],[2,'#f5e642'],[3,'#f2a93b'],[4,'#eb5c2a'],[5,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 14;\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: waveHeightColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const wh = vars.waveHeight || { xs: [], ys: [] };\n  return [\n    ...toArrows(wh.xs, wh.ys, vars.waveDir || []),\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '\u25b2 N &nbsp;&nbsp; \u25bc S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}",
                "shapes": "$fn () => {\n  const patchTouch = (gd) => {\n    if (!gd || gd.__touchGestureLongPress) return;\n    gd.__touchGestureLongPress = true;\n    const HOLD_MS = 400;\n    const MOVE_TOL = 10;\n    let timer = null, hover = false, sx = 0, sy = 0;\n    const hoverAt = (t) => {\n      const target = gd.querySelector('.nsewdrag') || gd;\n      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };\n      target.dispatchEvent(new MouseEvent('mouseover', opts));\n      target.dispatchEvent(new MouseEvent('mousemove', opts));\n    };\n    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };\n    const abortPan = () => {\n      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));\n    };\n    gd.addEventListener('touchstart', (e) => {\n      clear();\n      hover = false;\n      if (e.touches.length !== 1) return;\n      const t = e.touches[0];\n      sx = t.clientX; sy = t.clientY;\n      timer = setTimeout(() => {\n        timer = null;\n        hover = true;\n        abortPan();\n        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }\n        hoverAt({ clientX: sx, clientY: sy });\n      }, HOLD_MS);\n    }, true);\n    gd.addEventListener('touchmove', (e) => {\n      const t = e.touches[0];\n      if (hover) {\n        e.stopPropagation();\n        if (e.cancelable) e.preventDefault();\n        if (t) hoverAt(t);\n        return;\n      }\n      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();\n    }, true);\n    const end = () => { clear(); hover = false; };\n    gd.addEventListener('touchend', end, true);\n    gd.addEventListener('touchcancel', end, true);\n  };\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];\n}"
              },
              "grid_options": {
                "columns": 36
              }
            }
          }
        ]
      }
    ]
  },
  {
    "source": "06_forecast.yaml",
    "title": "06 Forecast",
    "grids": [
      {
        "column_span": 3,
        "columns": null,
        "cards": [
          {
            "tag": "hui-heading-card",
            "title": "Heading: Forecast (heading from 06_forecast.yaml)",
            "config": {
              "type": "heading",
              "icon": "mdi:weather-windy-variant",
              "heading_style": "title",
              "heading": "Forecast"
            }
          },
          {
            "tag": "windy-boat-card",
            "title": "windy-boat-card (custom:windy-boat-card from 06_forecast.yaml)",
            "config": {
              "type": "custom:windy-boat-card",
              "lat_entity": "sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_latitude",
              "lon_entity": "sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_longitude",
              "fallback_lat": 42.43,
              "fallback_lon": 18.6,
              "zoom": 8,
              "overlay": "wind",
              "product": "ecmwf",
              "aspect_ratio": "50%",
              "grid_options": {
                "rows": 7,
                "columns": 36
              }
            }
          }
        ]
      }
    ]
  }
];
window.PREVIEW_CARDS = [
  {
    "tag": "hui-heading-card",
    "title": "Heading: Sensors (heading from 01_sensors.yaml)",
    "config": {
      "type": "heading",
      "heading_style": "title",
      "heading": "Sensors"
    }
  },
  {
    "tag": "apexcharts-card",
    "title": "Wind \u2014 History & Forecast (apexcharts-card)",
    "config": {
      "type": "custom:apexcharts-card",
      "cache": false,
      "graph_span": "26h",
      "span": {
        "start": "minute",
        "offset": "-2h"
      },
      "now": {
        "show": true,
        "label": "Now",
        "color": "#ffffff"
      },
      "header": {
        "show": true,
        "title": "Wind \u2014 History & Forecast",
        "show_states": true,
        "colorize_states": true
      },
      "apex_config": {
        "chart": {
          "height": 300,
          "width": 600
        },
        "legend": {
          "position": "bottom"
        },
        "yaxis": {
          "min": 0,
          "decimalsInFloat": 1
        }
      },
      "series": [
        {
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
          "name": "Measured (kts)",
          "type": "area",
          "color": "#00bcd4",
          "stroke_width": 2,
          "fill_raw": "null",
          "unit": "kts",
          "show": {
            "extremas": true
          }
        },
        {
          "entity": "sensor.wind_forecast_flat",
          "name": "Forecast (kts)",
          "type": "line",
          "color": "#4fc3f7",
          "stroke_width": 2,
          "stroke_dash": 5,
          "unit": "kts",
          "data_generator": "const times=entity.attributes.forecast_time||[];const speeds=entity.attributes.forecast_wind||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+\"Z\").getTime(),Math.round(speeds[i]*10)/10]).filter(p=>p[0]>=rangeStart);"
        },
        {
          "entity": "sensor.wind_forecast_flat",
          "name": "Gusts (kts)",
          "type": "line",
          "color": "#ff7043",
          "stroke_width": 1,
          "stroke_dash": 4,
          "unit": "kts",
          "opacity": 0.9,
          "data_generator": "const times=entity.attributes.forecast_time||[];const gusts=entity.attributes.forecast_gust||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+\"Z\").getTime(),Math.round(gusts[i]*10)/10]).filter(p=>p[0]>=rangeStart);"
        }
      ]
    }
  },
  {
    "tag": "hui-gauge-card",
    "title": "STW (kn) (gauge from 01_sensors.yaml)",
    "config": {
      "type": "gauge",
      "name": "STW (kn)",
      "entity": "sensor.speed_raymarine_20_442559_pk_b941014ae3e7110c03bb1cd071a22b76_speed_water_referenced",
      "min": 0,
      "max": 12,
      "needle": true,
      "severity": {
        "green": 0,
        "yellow": 8,
        "red": 10
      }
    }
  },
  {
    "tag": "hui-gauge-card",
    "title": "Depth (m) (gauge from 01_sensors.yaml)",
    "config": {
      "type": "gauge",
      "name": "Depth (m)",
      "entity": "sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth",
      "min": 0,
      "max": 30,
      "needle": true,
      "severity": {
        "red": 0,
        "yellow": 3,
        "green": 5
      },
      "card_mod": {
        "style": "ha-card {\n  --accent-color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }};\n}\n\nha-gauge {\n  --gauge-color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }} !important;\n  --primary-text-color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }} !important;\n}\n\nha-card {\n  color: {{ '#ff1100' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 3 else ('#ff8866' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) < 4.5 else ('var(--green-color)' if states('sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth') | float(0) >= 4.5 else '#888888')) }};\n}"
      }
    }
  },
  {
    "tag": "hui-gauge-card",
    "title": "SOG (kn) (gauge from 01_sensors.yaml)",
    "config": {
      "type": "gauge",
      "name": "SOG (kn)",
      "entity": "sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_sog",
      "min": 0,
      "max": 12,
      "needle": true,
      "severity": {
        "green": 0,
        "yellow": 8,
        "red": 10
      }
    }
  },
  {
    "tag": "hui-heading-card",
    "title": "Heading: Position (heading from 02_position.yaml)",
    "config": {
      "type": "heading",
      "heading": "Position"
    }
  },
  {
    "tag": "compass-card",
    "title": "compass-card (custom:compass-card from 02_position.yaml)",
    "config": {
      "type": "custom:compass-card",
      "header": {
        "title": {
          "value": "COG"
        },
        "icon": {
          "value": "mdi:compass-outline"
        }
      },
      "compass": {
        "circle": {
          "color": "#37474f"
        },
        "ticks": {
          "show": true,
          "color": "#90a4ae",
          "radius": 95
        },
        "north": {
          "show": true
        },
        "east": {
          "show": true
        },
        "south": {
          "show": true
        },
        "west": {
          "show": true
        }
      },
      "indicator_sensors": [
        {
          "sensor": "sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog",
          "indicator": {
            "image": "arrow_inward",
            "color": "#ff7043"
          }
        }
      ],
      "value_sensors": [
        {
          "sensor": "sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog"
        }
      ]
    }
  },
  {
    "tag": "hui-map-card",
    "title": "hui-map-card (map from 02_position.yaml)",
    "config": {
      "type": "map",
      "entities": [
        {
          "entity": "device_tracker.nevera"
        }
      ],
      "default_zoom": 14,
      "aspect_ratio": "16x9"
    }
  },
  {
    "tag": "hui-entity-card",
    "title": "Latitude (entity from 02_position.yaml)",
    "config": {
      "type": "entity",
      "entity": "sensor.boat_latitude",
      "name": "Latitude",
      "card_mod": {
        "style": "ha-card {\n  font-size: 14px;\n  font-weight: bold;\n  color: var(--grey-color);\n}\n\n.name {\n  font-size: 14px !important;\n  font-weight: bold !important;\n  color: var(--grey-color) !important;\n}\n\n.value {\n  font-size: calc(14px * 1.75) !important;\n}\n\n.measurement {\n  font-size: 14px !important;\n  color: var(--grey-color) !important;\n}"
      }
    }
  },
  {
    "tag": "hui-entity-card",
    "title": "Longitude (entity from 02_position.yaml)",
    "config": {
      "type": "entity",
      "entity": "sensor.boat_longitude",
      "name": "Longitude",
      "card_mod": {
        "style": "ha-card {\n  font-size: 13px;\n  font-weight: bold;\n  color: var(--grey-color);\n}\n\n.name {\n  font-size: 13px !important;\n  font-weight: bold !important;\n  color: var(--grey-color) !important;\n}\n\n.value {\n  font-size: calc(14px * 1.75) !important;\n}\n\n.measurement {\n  font-size: 14px !important;\n  color: var(--grey-color) !important;\n}"
      }
    }
  },
  {
    "tag": "hui-heading-card",
    "title": "Heading: Conditions (heading from 03_conditions.yaml)",
    "config": {
      "type": "heading",
      "heading_style": "title",
      "heading": "Conditions"
    }
  },
  {
    "tag": "windrose-card",
    "title": "windrose-card (custom:windrose-card from 03_conditions.yaml)",
    "config": {
      "type": "custom:windrose-card",
      "windspeed_bar_location": "right",
      "data_period": {
        "period_back": "-24h"
      },
      "wind_direction_entity": {
        "entity": "sensor.wind_direction_history"
      },
      "windspeed_entities": [
        {
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
          "name": "Speed"
        }
      ],
      "current_direction": {
        "show_arrow": true
      },
      "corner_info": {
        "top_right": {
          "label": "Wind Speed",
          "unit": " kn",
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed"
        }
      }
    }
  },
  {
    "tag": "hui-gauge-card",
    "title": "Pressure (mmHg) (gauge from 03_conditions.yaml)",
    "config": {
      "type": "gauge",
      "name": "Pressure (mmHg)",
      "entity": "sensor.barometer_mmhg",
      "min": 720,
      "max": 790,
      "needle": true,
      "severity": {
        "red": 720,
        "yellow": 745,
        "green": 760
      },
      "grid_options": {
        "rows": 3,
        "columns": 12
      }
    }
  },
  {
    "tag": "hui-tile-card",
    "title": "sensor.barometer_mmhg (tile from 03_conditions.yaml)",
    "config": {
      "type": "tile",
      "entity": "sensor.barometer_mmhg",
      "features": [
        {
          "type": "trend-graph"
        }
      ],
      "grid_options": {
        "columns": 12,
        "rows": 2
      }
    }
  },
  {
    "tag": "hui-heading-card",
    "title": "Heading: Wind Direction & Speed (heading from 04_wind.yaml)",
    "config": {
      "type": "heading",
      "heading_style": "title",
      "heading": "Wind Direction & Speed",
      "icon": "mdi:weather-windy"
    }
  },
  {
    "tag": "hui-glance-card",
    "title": "Glance metrics (glance from 04_wind.yaml)",
    "config": {
      "type": "glance",
      "show_icon": false,
      "state_color": false,
      "columns": 3,
      "entities": [
        {
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
          "name": "Measured now",
          "card_mod": {
            "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #4fc3f7 !important;\n}\n"
          }
        },
        {
          "entity": "sensor.wind_forecast_next_hour",
          "name": "Forecast next 1h",
          "card_mod": {
            "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #b0bec5 !important;\n}\n"
          }
        },
        {
          "entity": "sensor.wind_gust_next_hour",
          "name": "Gusts next 1h",
          "card_mod": {
            "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #ff7043 !important;\n}\n"
          }
        }
      ],
      "card_mod": {
        "style": "ha-card { padding: 10px 8px 6px !important; }\n.entities { padding: 0 !important; }\n.entity {\n  flex-direction: column-reverse !important;\n}\n"
      },
      "grid_options": {
        "columns": 36
      }
    }
  },
  {
    "tag": "plotly-graph",
    "title": "plotly-graph (custom:plotly-graph from 04_wind.yaml)",
    "config": {
      "type": "custom:plotly-graph",
      "hours_to_show": "$fn ({ hass }) => { const a = (hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes; return Number(a.history_hours || 4) + Number(a.forecast_hours || 24); }",
      "time_offset": "$fn ({ hass }) => ((hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes.forecast_hours || 24) + 'h'",
      "entities": [
        {
          "entity": "sensor.wind_direction_history",
          "internal": true,
          "filters": [
            {
              "resample": "30m"
            },
            {
              "map_y": "parseFloat(y)"
            },
            {
              "store_var": "dir"
            }
          ]
        },
        {
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
          "name": "Measured",
          "mode": "markers",
          "filters": [
            {
              "resample": "30m"
            },
            {
              "map_y": "parseFloat(y)"
            },
            {
              "store_var": "speed"
            }
          ],
          "customdata": "$fn ({ xs, vars }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirYs = (vars.dir && vars.dir.ys) || [];\n  return xs.map((_, i) => {\n    const d = dirYs[i] || 0;\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '\u00b0';\n  });\n}",
          "hovertemplate": "%{y:.1f} kt \u00b7 %{customdata}<extra>Measured</extra>",
          "marker": {
            "size": 6,
            "symbol": "circle",
            "color": "#4fc3f7",
            "line": {
              "width": 0
            }
          }
        },
        {
          "entity": "sensor.wind_forecast_flat",
          "name": "Forecast",
          "mode": "markers",
          "extend_to_present": false,
          "filters": [
            {
              "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.forecast_wind || []),\n})"
            },
            {
              "fn": "({ meta, vars }) => { vars.forecastDir = meta.forecast_dir || []; return {}; }"
            },
            {
              "store_var": "forecastSpeed"
            }
          ],
          "customdata": "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  return (meta.forecast_dir || []).map((d) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '\u00b0');\n}",
          "hovertemplate": "%{y:.1f} kt \u00b7 %{customdata}<extra>Forecast</extra>",
          "marker": {
            "size": 6,
            "symbol": "diamond",
            "color": "#ff7043",
            "line": {
              "width": 0
            }
          }
        },
        {
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
          "name": "kt scale",
          "mode": "markers",
          "showlegend": false,
          "hoverinfo": "skip",
          "extend_to_present": false,
          "filters": [
            {
              "resample": "30m"
            },
            {
              "map_y": "parseFloat(y)"
            },
            {
              "fn": "() => ({ xs: [], ys: [] })"
            }
          ],
          "marker": {
            "size": 0.1,
            "opacity": 0,
            "color": "$ex ys",
            "colorscale": [
              [
                0,
                "#b0e2ff"
              ],
              [
                0.125,
                "#61c4e0"
              ],
              [
                0.25,
                "#4bbf7a"
              ],
              [
                0.375,
                "#a8d048"
              ],
              [
                0.5,
                "#f5e642"
              ],
              [
                0.625,
                "#f2a93b"
              ],
              [
                0.75,
                "#eb5c2a"
              ],
              [
                0.875,
                "#d62828"
              ],
              [
                1,
                "#8e1b8e"
              ]
            ],
            "cmin": 0,
            "cmax": 40,
            "showscale": true,
            "colorbar": {
              "title": {
                "text": "kt",
                "side": "top"
              },
              "ticksuffix": " kt",
              "len": 0.9
            }
          }
        },
        {
          "entity": "sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed",
          "name": "Gusts (measured)",
          "mode": "lines",
          "filters": [
            {
              "map_y": "parseFloat(y)"
            },
            {
              "fn": "({ xs, ys }) => {\n  const windowMs = 10 * 60 * 1000;\n  const outXs = [];\n  const outYs = [];\n  let bucketStart = null;\n  let bucketMax = null;\n  for (let i = 0; i < xs.length; i++) {\n    const t = new Date(xs[i]).getTime();\n    if (bucketStart === null || t - bucketStart >= windowMs) {\n      if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n      bucketStart = t;\n      bucketMax = ys[i];\n    } else if (ys[i] > bucketMax) {\n      bucketMax = ys[i];\n    }\n  }\n  if (bucketStart !== null) { outXs.push(new Date(bucketStart + windowMs)); outYs.push(bucketMax); }\n  return { xs: outXs, ys: outYs };\n}"
            }
          ],
          "hovertemplate": "%{y:.1f} kt<extra>Gusts (measured)</extra>",
          "line": {
            "dash": "dot",
            "width": 1,
            "color": "#b0bec5"
          }
        },
        {
          "entity": "sensor.wind_forecast_flat",
          "name": "Gusts (forecast)",
          "mode": "lines",
          "extend_to_present": false,
          "filters": [
            {
              "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.forecast_gust || []),\n})"
            }
          ],
          "hovertemplate": "%{y:.1f} kt<extra>Gusts (forecast)</extra>",
          "line": {
            "dash": "dot",
            "width": 1,
            "color": "#78909c"
          }
        }
      ],
      "on_dblclick": "$fn () => () => {\n  const found = [];\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));\n    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });\n  };\n  walk(document);\n  found.forEach((el) => {\n    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');\n    if (btn && !btn.classList.contains('hidden')) btn.click();\n  });\n}",
      "config": {
        "scrollZoom": false,
        "displayModeBar": false,
        "doubleClick": false
      },
      "layout": {
        "dragmode": "pan",
        "hovermode": "x unified",
        "hoverdistance": -1,
        "xaxis": {
          "showspikes": true,
          "spikemode": "across",
          "spikedash": "dash",
          "spikethickness": 1,
          "spikecolor": "#90a4ae",
          "spikesnap": "cursor"
        },
        "yaxis": {
          "title": "Wind speed (kts)",
          "rangemode": "tozero",
          "autorange": true,
          "fixedrange": true,
          "showspikes": false
        },
        "legend": {
          "orientation": "h",
          "x": 0.5,
          "xanchor": "center",
          "y": -0.3
        },
        "margin": {
          "b": 70
        },
        "annotations": "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const windSpeedColor = (v) => {\n    const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const compassPoint = (d) => {\n    const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 10 + (ys[i] || 0);\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: windSpeedColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const arrows = [\n    ...toArrows(vars.speed.xs, vars.speed.ys, vars.dir.ys),\n    ...toArrows(vars.forecastSpeed.xs, vars.forecastSpeed.ys, vars.forecastDir),\n  ];\n  return [\n    ...arrows,\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '\u25b2 N &nbsp;&nbsp; \u25bc S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}",
        "shapes": "$fn () => {\n  const patchTouch = (gd) => {\n    if (!gd || gd.__touchGestureLongPress) return;\n    gd.__touchGestureLongPress = true;\n    const HOLD_MS = 400;\n    const MOVE_TOL = 10;\n    let timer = null, hover = false, sx = 0, sy = 0;\n    const hoverAt = (t) => {\n      const target = gd.querySelector('.nsewdrag') || gd;\n      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };\n      target.dispatchEvent(new MouseEvent('mouseover', opts));\n      target.dispatchEvent(new MouseEvent('mousemove', opts));\n    };\n    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };\n    const abortPan = () => {\n      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));\n    };\n    gd.addEventListener('touchstart', (e) => {\n      clear();\n      hover = false;\n      if (e.touches.length !== 1) return;\n      const t = e.touches[0];\n      sx = t.clientX; sy = t.clientY;\n      timer = setTimeout(() => {\n        timer = null;\n        hover = true;\n        abortPan();\n        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }\n        hoverAt({ clientX: sx, clientY: sy });\n      }, HOLD_MS);\n    }, true);\n    gd.addEventListener('touchmove', (e) => {\n      const t = e.touches[0];\n      if (hover) {\n        e.stopPropagation();\n        if (e.cancelable) e.preventDefault();\n        if (t) hoverAt(t);\n        return;\n      }\n      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();\n    }, true);\n    const end = () => { clear(); hover = false; };\n    gd.addEventListener('touchend', end, true);\n    gd.addEventListener('touchcancel', end, true);\n  };\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];\n}"
      },
      "grid_options": {
        "columns": 36
      }
    }
  },
  {
    "tag": "hui-heading-card",
    "title": "Heading: Waves (heading from 05_waves.yaml)",
    "config": {
      "type": "heading",
      "heading_style": "title",
      "heading": "Waves",
      "icon": "mdi:wave"
    }
  },
  {
    "tag": "hui-glance-card",
    "title": "Glance metrics (glance from 05_waves.yaml)",
    "config": {
      "type": "glance",
      "show_icon": false,
      "state_color": false,
      "columns": 2,
      "entities": [
        {
          "entity": "sensor.wave_height_next_hour",
          "name": "Height next 1h",
          "card_mod": {
            "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #4fc3f7 !important;\n}\n"
          }
        },
        {
          "entity": "sensor.wave_period_next_hour",
          "name": "Period next 1h",
          "card_mod": {
            "style": "div.name {\n  font-size: 12px !important;\n  font-weight: 400 !important;\n  line-height: 14px !important;\n  color: var(--secondary-text-color) !important;\n}\ndiv:not(.name) {\n  font-size: 26px !important;\n  font-weight: 500 !important;\n  line-height: 30px !important;\n  color: #b0bec5 !important;\n}\n"
          }
        }
      ],
      "card_mod": {
        "style": "ha-card { padding: 10px 8px 6px !important; }\n.entities { padding: 0 !important; }\n.entity {\n  flex-direction: column-reverse !important;\n}\n"
      },
      "grid_options": {
        "columns": 36
      }
    }
  },
  {
    "tag": "plotly-graph",
    "title": "plotly-graph (custom:plotly-graph from 05_waves.yaml)",
    "config": {
      "type": "custom:plotly-graph",
      "hours_to_show": "$fn ({ hass }) => { const a = (hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes; return Number(a.history_hours || 4) + Number(a.forecast_hours || 24); }",
      "time_offset": "$fn ({ hass }) => ((hass.states['sensor.chart_time_window'] || { attributes: {} }).attributes.forecast_hours || 24) + 'h'",
      "entities": [
        {
          "entity": "sensor.wave_forecast_flat",
          "name": "Wave height (forecast)",
          "mode": "markers",
          "extend_to_present": false,
          "filters": [
            {
              "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.wave_height || []),\n})"
            },
            {
              "fn": "({ meta, vars }) => { vars.waveDir = meta.wave_direction || []; vars.wavePeriod = meta.wave_period || []; return {}; }"
            },
            {
              "store_var": "waveHeight"
            }
          ],
          "customdata": "$fn ({ meta }) => {\n  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];\n  const dirs = meta.wave_direction || [];\n  const periods = meta.wave_period || [];\n  return dirs.map((d, i) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '\u00b0 \u00b7 ' + (periods[i] != null ? Math.round(periods[i] * 10) / 10 + ' s' : '\u2013 s'));\n}",
          "hovertemplate": "%{y:.2f} m \u00b7 %{customdata}<extra>Wave (forecast)</extra>",
          "marker": {
            "size": 6,
            "symbol": "diamond",
            "color": "#4fc3f7",
            "line": {
              "width": 0
            }
          }
        },
        {
          "entity": "sensor.wave_forecast_flat",
          "name": "Period (s)",
          "mode": "lines",
          "extend_to_present": false,
          "visible": "legendonly",
          "filters": [
            {
              "fn": "({ meta }) => ({\n  xs: (meta.forecast_time || []).map((t) => new Date(t + \"Z\")),\n  ys: (meta.wave_period || []),\n})"
            }
          ],
          "hovertemplate": "%{y:.1f} s<extra>Period</extra>",
          "line": {
            "dash": "dot",
            "width": 1,
            "color": "#b0bec5"
          }
        }
      ],
      "on_dblclick": "$fn () => () => {\n  const found = [];\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));\n    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });\n  };\n  walk(document);\n  found.forEach((el) => {\n    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');\n    if (btn && !btn.classList.contains('hidden')) btn.click();\n  });\n}",
      "config": {
        "scrollZoom": false,
        "displayModeBar": false,
        "doubleClick": false
      },
      "layout": {
        "dragmode": "pan",
        "hovermode": "x unified",
        "hoverdistance": -1,
        "xaxis": {
          "showspikes": true,
          "spikemode": "across",
          "spikedash": "dash",
          "spikethickness": 1,
          "spikecolor": "#90a4ae",
          "spikesnap": "cursor"
        },
        "yaxis": {
          "title": "Wave height (m)",
          "rangemode": "tozero",
          "autorange": true,
          "fixedrange": true,
          "showspikes": false
        },
        "legend": {
          "orientation": "h",
          "x": 0.5,
          "xanchor": "center",
          "y": -0.3
        },
        "margin": {
          "b": 70
        },
        "annotations": "$fn ({ vars }) => {\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      const sr = el.shadowRoot;\n      if (!sr || sr.querySelector('style[data-now-radius]')) return;\n      const st = document.createElement('style');\n      st.setAttribute('data-now-radius', '');\n      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';\n      sr.appendChild(st);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  const waveHeightColor = (v) => {\n    const stops = [[0.3,'#b0e2ff'],[0.6,'#61c4e0'],[1,'#4bbf7a'],[1.5,'#a8d048'],[2,'#f5e642'],[3,'#f2a93b'],[4,'#eb5c2a'],[5,'#d62828']];\n    for (const [max, color] of stops) if (v < max) return color;\n    return '#8e1b8e';\n  };\n  const toArrows = (xs, ys, dirs) => (xs || []).map((x, i) => {\n    const d = dirs[i] || 0;\n    const rad = ((d + 180) * Math.PI) / 180;\n    const len = 14;\n    return {\n      x, y: ys[i], xref: 'x', yref: 'y',\n      ax: -len * Math.sin(rad), ay: len * Math.cos(rad), axref: 'pixel', ayref: 'pixel',\n      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: waveHeightColor(ys[i] || 0),\n      captureevents: false,\n    };\n  });\n  const wh = vars.waveHeight || { xs: [], ys: [] };\n  return [\n    ...toArrows(wh.xs, wh.ys, vars.waveDir || []),\n    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },\n    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '\u25b2 N &nbsp;&nbsp; \u25bc S', showarrow: false, font: { color: '#90a4ae', size: 10 } },\n  ];\n}",
        "shapes": "$fn () => {\n  const patchTouch = (gd) => {\n    if (!gd || gd.__touchGestureLongPress) return;\n    gd.__touchGestureLongPress = true;\n    const HOLD_MS = 400;\n    const MOVE_TOL = 10;\n    let timer = null, hover = false, sx = 0, sy = 0;\n    const hoverAt = (t) => {\n      const target = gd.querySelector('.nsewdrag') || gd;\n      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };\n      target.dispatchEvent(new MouseEvent('mouseover', opts));\n      target.dispatchEvent(new MouseEvent('mousemove', opts));\n    };\n    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };\n    const abortPan = () => {\n      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));\n    };\n    gd.addEventListener('touchstart', (e) => {\n      clear();\n      hover = false;\n      if (e.touches.length !== 1) return;\n      const t = e.touches[0];\n      sx = t.clientX; sy = t.clientY;\n      timer = setTimeout(() => {\n        timer = null;\n        hover = true;\n        abortPan();\n        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }\n        hoverAt({ clientX: sx, clientY: sy });\n      }, HOLD_MS);\n    }, true);\n    gd.addEventListener('touchmove', (e) => {\n      const t = e.touches[0];\n      if (hover) {\n        e.stopPropagation();\n        if (e.cancelable) e.preventDefault();\n        if (t) hoverAt(t);\n        return;\n      }\n      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();\n    }, true);\n    const end = () => { clear(); hover = false; };\n    gd.addEventListener('touchend', end, true);\n    gd.addEventListener('touchcancel', end, true);\n  };\n  const walk = (root) => {\n    root.querySelectorAll('plotly-graph').forEach((el) => {\n      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);\n    });\n    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });\n  };\n  try { walk(document); } catch (e) {}\n  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];\n}"
      },
      "grid_options": {
        "columns": 36
      }
    }
  },
  {
    "tag": "hui-heading-card",
    "title": "Heading: Forecast (heading from 06_forecast.yaml)",
    "config": {
      "type": "heading",
      "icon": "mdi:weather-windy-variant",
      "heading_style": "title",
      "heading": "Forecast"
    }
  },
  {
    "tag": "windy-boat-card",
    "title": "windy-boat-card (custom:windy-boat-card from 06_forecast.yaml)",
    "config": {
      "type": "custom:windy-boat-card",
      "lat_entity": "sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_latitude",
      "lon_entity": "sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_longitude",
      "fallback_lat": 42.43,
      "fallback_lon": 18.6,
      "zoom": 8,
      "overlay": "wind",
      "product": "ecmwf",
      "aspect_ratio": "50%",
      "grid_options": {
        "rows": 7,
        "columns": 36
      }
    }
  }
];
