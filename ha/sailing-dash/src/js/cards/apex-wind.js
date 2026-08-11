/**
 * ApexCharts Wind History & Forecast card configuration module.
 *
 * Colours mirror `src/js/common/wind_chart_style.js` / `wind-arrows-card.js`
 * (measured=#4fc3f7, forecast=#ff7043, gust forecast=#78909c) so this
 * standalone config module stays visually consistent with the wind section
 * actually used by build.py (`04_wind_apexcharts.yaml`).
 */

const APEX_WIND_CARD_CONFIG = {
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
      entity: 'sensor.boat_wind_speed',
      name: 'Measured (kts)',
      type: 'area',
      color: '#4fc3f7',
      stroke_width: 2,
      fill_raw: 'null',
      unit: 'kts',
      show: { extremas: true },
    },
    {
      entity: 'sensor.wind_forecast_flat',
      name: 'Forecast (kts)',
      type: 'line',
      color: '#ff7043',
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
      color: '#78909c',
      stroke_width: 1,
      stroke_dash: 4,
      unit: 'kts',
      opacity: 0.9,
      data_generator:
        'const times=entity.attributes.forecast_time||[];const gusts=entity.attributes.forecast_gust||[];const rangeStart=Date.now()-2*3600000;return times.map((t,i)=>[new Date(t+"Z").getTime(),Math.round(gusts[i]*10)/10]).filter(p=>p[0]>=rangeStart);',
    },
  ],
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = APEX_WIND_CARD_CONFIG;
}
