// Wind vector arrows + the "Now" marker for the wind chart.
//
// Arrows are a Plotly annotation layer (one shaft per data point): `x`/`y` is
// the data point (arrow head), `ax`/`ay` is the tail in pixels, so the shaft
// angle encodes the wind direction and its colour the wind speed.
//
// Two rules this layer has to respect, both of which used to be broken:
//   1. Direction belongs to a DIFFERENT entity than speed. It must be matched
//      by timestamp, not by array index: the two series are resampled from
//      their own recorder histories, so one gap (HA restart, alias going
//      `unavailable`) shifts every following index and points the arrows at
//      unrelated moments in time.
//   2. A missing value is not zero. `dirs[i] || 0` drew a due-North arrow for
//      every point without a direction, and a non-finite speed (an `unknown`
//      state parsed by parseFloat) produced an arrow at NaN. Such points are
//      skipped now.
//
// Two chart styles share this single layer (see `sections.wind.chart_style` in
// config.yaml, injected by build.py as card-level options):
//   * `plotly`     - arrow_layout `on_point`: every arrow sits ON its own data
//                    point, i.e. the arrow head follows the speed line.
//   * `open_meteo` - arrow_layout `top_row`: arrows line up in one straight row
//                    just under the chart's top edge (paper coordinates), the
//                    way open-meteo.com's own forecast preview draws them,
//                    while the values themselves stay as plain chart lines.
// `arrow_spacing_hours` thins the row out: without it a recorder history of a
// few hundred points would draw a solid, unreadable wall of arrows.
({ vars, getFromConfig }) => {
  const readConfig = (key, fallback) => {
    try {
      const value = getFromConfig(key);
      return value === undefined || value === null ? fallback : value;
    } catch (e) {
      return fallback;
    }
  };
  const arrowLayout = String(readConfig('arrow_layout', 'on_point'));
  const topRow = arrowLayout === 'top_row';
  const spacingMs = Math.max(0, Number(readConfig('arrow_spacing_hours', 0)) || 0) * 3600 * 1000;
  const TOP_ROW_Y = 0.93;
  const walk = (root) => {
    root.querySelectorAll('plotly-graph').forEach((el) => {
      const sr = el.shadowRoot;
      if (!sr || sr.querySelector('style[data-now-radius]')) return;
      const st = document.createElement('style');
      st.setAttribute('data-now-radius', '');
      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';
      sr.appendChild(st);
    });
    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  try { walk(document); } catch (e) {}
  const windSpeedColor = (v) => {
    const stops = [[5,'#b0e2ff'],[10,'#61c4e0'],[15,'#4bbf7a'],[20,'#a8d048'],[25,'#f5e642'],[30,'#f2a93b'],[35,'#eb5c2a'],[40,'#d62828']];
    for (const [max, color] of stops) if (v < max) return color;
    return '#8e1b8e';
  };
  const arrow = (x, y, d) => {
    const rad = ((d + 180) * Math.PI) / 180;
    const len = 10 + y;
    return {
      x,
      y: topRow ? TOP_ROW_Y : y,
      xref: 'x',
      yref: topRow ? 'paper' : 'y',
      ax: -len * Math.sin(rad), ay: len * Math.cos(rad),
      axref: 'pixel', ayref: 'pixel',
      showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5,
      arrowcolor: windSpeedColor(y),
      captureevents: false,
    };
  };
  // Arrows for a speed series whose directions come from a separate series:
  // matched by timestamp within a tolerance, never by index.
  const arrowsByTime = (speed, dir, toleranceMs) => {
    const xs = (speed && speed.xs) || [];
    const ys = (speed && speed.ys) || [];
    const dirXs = ((dir && dir.xs) || []).map((t) => new Date(t).getTime());
    const dirYs = (dir && dir.ys) || [];
    const out = [];
    for (let i = 0; i < xs.length; i++) {
      const y = Number(ys[i]);
      if (!Number.isFinite(y)) continue;
      const t = new Date(xs[i]).getTime();
      let best = -1;
      let bestDelta = Infinity;
      for (let j = 0; j < dirXs.length; j++) {
        const delta = Math.abs(dirXs[j] - t);
        if (delta < bestDelta) { bestDelta = delta; best = j; }
      }
      if (best < 0 || bestDelta > toleranceMs) continue;
      const d = Number(dirYs[best]);
      if (!Number.isFinite(d)) continue;
      out.push(arrow(xs[i], y, d));
    }
    return out;
  };
  // Arrows for the forecast series: speed and direction come from the SAME
  // open-meteo `hourly` payload, i.e. they are index-aligned by construction —
  // but a short/ragged array must still not produce a bogus 0° arrow.
  const arrowsByIndex = (speed, dirs) => {
    const xs = (speed && speed.xs) || [];
    const ys = (speed && speed.ys) || [];
    const ds = dirs || [];
    const out = [];
    for (let i = 0; i < xs.length; i++) {
      const y = Number(ys[i]);
      const d = Number(ds[i]);
      if (!Number.isFinite(y) || !Number.isFinite(d)) continue;
      out.push(arrow(xs[i], y, d));
    }
    return out;
  };
  // Keep at most one arrow per `arrow_spacing_hours` window, preserving the
  // first arrow of each window so measured and forecast arrows stay on the
  // same grid instead of drifting apart.
  const thin = (list) => {
    if (!spacingMs) return list;
    const out = [];
    let lastT = -Infinity;
    for (const a of list) {
      const t = new Date(a.x).getTime();
      if (!Number.isFinite(t)) continue;
      if (t - lastT < spacingMs) continue;
      lastT = t;
      out.push(a);
    }
    return out;
  };
  const arrows = thin([
    ...arrowsByTime(vars.speed, vars.dir, 15 * 60 * 1000),
    ...arrowsByIndex(vars.forecastSpeed, vars.forecastDir),
  ]);
  return [
    ...arrows,
    { xref: 'x', yref: 'paper', x: new Date(), y: 0.99, yanchor: 'top', xanchor: 'right', text: 'Now', textangle: -90, showarrow: false, xshift: -2, bgcolor: '#ffffff', borderpad: 4, font: { color: '#000000', size: 10 } },
    { xref: 'paper', yref: 'paper', x: 0.01, y: 0.97, xanchor: 'left', yanchor: 'top', text: '▲ N &nbsp;&nbsp; ▼ S', showarrow: false, font: { color: '#90a4ae', size: 10 } },
  ];
}
