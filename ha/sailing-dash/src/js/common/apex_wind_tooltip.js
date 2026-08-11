// Custom ApexCharts tooltip for the wind chart, aligned with the Plotly
// wind chart's tooltip style: a single unified box with the date/time
// header and one coloured line per series ("Measured"/"Forecast"/"Gusts
// (forecast)"), all values in knots — instead of ApexCharts' default
// `tooltip.shared` box, which uses a different font/spacing and never
// matched the rest of the dashboard.
function ({ series, seriesIndex, dataPointIndex, w }) {
  const ts = w.globals.seriesX[seriesIndex][dataPointIndex];
  const date = new Date(ts);
  const header = date.toLocaleString(undefined, {
    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
  let rows = '';
  w.config.series.forEach((s, i) => {
    const v = series[i][dataPointIndex];
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return;
    const color = (w.config.colors && w.config.colors[i]) || (s.color) || '#90a4ae';
    rows += `<div style="display:flex;justify-content:space-between;gap:10px;padding:1px 0;">`
      + `<span style="color:${color};">&#9679; ${s.name}</span>`
      + `<span>${Number(v).toFixed(1)} kts</span>`
      + `</div>`;
  });
  return `<div style="background:#1c1c1c;color:#fff;padding:6px 10px;border-radius:4px;font-size:12px;min-width:150px;">`
    + `<div style="font-weight:600;margin-bottom:4px;">${header}</div>${rows}</div>`;
}
