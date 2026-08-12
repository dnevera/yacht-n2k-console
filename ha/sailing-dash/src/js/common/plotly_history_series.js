// Filter and prepare historical/measured series for dashboard charts (wind, waves, etc.).
//
// Processes measurement time-series from sensors:
// 1. Excludes non-numeric values (NaN, null, unknown, unavailable) from HA restarts or quiet NMEA bus.
// 2. Up to "Now" (t <= Date.now()): preserves all measured points for display in tooltips and charts alongside forecasts.
// 3. After "Now" (t > Date.now()): strictly truncates history so measurements do not spill into the forecast area/tooltip.
({ xs, ys }) => {
  const outXs = [];
  const outYs = [];
  const now = Date.now();

  for (let i = 0; i < ys.length; i++) {
    // Value validation: skip non-numeric points
    const val = Number(ys[i]);
    if (!Number.isFinite(val)) continue;

    // Time validation: exclude points in the future (> Date.now())
    const timestamp = new Date(xs[i]).getTime();
    if (Number.isFinite(timestamp) && timestamp > now) continue;

    outXs.push(xs[i]);
    outYs.push(val);
  }

  return { xs: outXs, ys: outYs };
}
