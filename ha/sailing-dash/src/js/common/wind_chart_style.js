// Shared wind chart visual style: colour scale, series colours and hover
// format, used by BOTH chart engines (Plotly `plotly_wind_annotations.js`
// and the ApexCharts `wind-arrows-card.js` overlay / `apex-wind.js`), so a
// palette change only has to be made in one place instead of drifting
// between the two implementations.
//
// `windSpeedColor(v)` maps a wind speed in knots to the same 8-stop scale
// used by the Plotly "kt scale" colourbar trace (blue -> green -> yellow ->
// orange -> red -> violet, saturating above 40 kt).
(() => {
  const STOPS = [
    [5, '#b0e2ff'],
    [10, '#61c4e0'],
    [15, '#4bbf7a'],
    [20, '#a8d048'],
    [25, '#f5e642'],
    [30, '#f2a93b'],
    [35, '#eb5c2a'],
    [40, '#d62828'],
  ];
  const MAX_SPEED = 40;
  const OVER_MAX_COLOR = '#8e1b8e';

  const windSpeedColor = (v) => {
    for (const [max, color] of STOPS) if (v < max) return color;
    return OVER_MAX_COLOR;
  };

  // Series colours shared by the ApexCharts wind chart and its arrow/legend
  // overlay, kept visually consistent with the Plotly card's palette.
  const SERIES_COLORS = {
    measured: '#4fc3f7',
    forecast: '#ff7043',
    gustForecast: '#78909c',
    gustMeasured: '#b0bec5',
  };

  const api = { windSpeedColor, STOPS, MAX_SPEED, OVER_MAX_COLOR, SERIES_COLORS };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof window !== 'undefined') {
    window.WindChartStyle = api;
  }
})();
