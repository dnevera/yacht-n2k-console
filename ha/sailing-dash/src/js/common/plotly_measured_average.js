// Average a measured (recorder) series over the grid step of the ACTIVE
// Open-Meteo model, so measurements and forecast are comparable.
//
// Why: the forecast is an hourly (or 3-hourly, depending on the model) mean,
// while a raw NMEA series jumps by tens of degrees between two consecutive
// samples. Drawn side by side the measured arrows look like they disagree with
// the forecast even when their mean matches it exactly.
//
// The window is not hard-coded: it is derived from the spacing of the forecast
// timestamps themselves, so switching the model in the dashboard automatically
// changes the averaging window too (fallback: 60 min if no forecast is loaded).
//
// Directions (unit "°") are averaged as VECTORS via sin/cos. A plain arithmetic
// mean of 350° and 10° yields 180° — the exact opposite of the true 0°.
({ xs, ys, meta, hass }) => {
  const stepOf = (entityId) => {
    const times = ((hass && hass.states[entityId]) || {}).attributes || {};
    const list = times.forecast_time || [];
    for (let i = 1; i < list.length; i++) {
      const a = new Date(list[i - 1] + 'Z').getTime();
      const b = new Date(list[i] + 'Z').getTime();
      const d = b - a;
      if (Number.isFinite(d) && d > 0) return d;
    }
    return 0;
  };
  const step =
    stepOf('sensor.wind_forecast_flat') ||
    stepOf('sensor.wave_forecast_flat') ||
    60 * 60 * 1000;

  const circular = ((meta || {}).unit_of_measurement || '') === '°';
  const buckets = new Map();
  for (let i = 0; i < ys.length; i++) {
    const v = Number(ys[i]);
    const t = new Date(xs[i]).getTime();
    if (!Number.isFinite(v) || !Number.isFinite(t)) continue;
    const key = Math.floor(t / step);
    let b = buckets.get(key);
    if (!b) {
      b = { n: 0, sum: 0, u: 0, w: 0 };
      buckets.set(key, b);
    }
    b.n += 1;
    if (circular) {
      const rad = (v * Math.PI) / 180;
      b.u += Math.sin(rad);
      b.w += Math.cos(rad);
    } else {
      b.sum += v;
    }
  }

  const outXs = [];
  const outYs = [];
  // Anchor each bucket at its centre: the average describes the whole window,
  // not its left edge, and this keeps it aligned with the forecast samples.
  for (const key of [...buckets.keys()].sort((a, b) => a - b)) {
    const b = buckets.get(key);
    let value;
    if (circular) {
      if (Math.abs(b.u) < 1e-9 && Math.abs(b.w) < 1e-9) continue;
      value = ((Math.atan2(b.u, b.w) * 180) / Math.PI + 360) % 360;
    } else {
      value = b.sum / b.n;
    }
    outXs.push(new Date(key * step + step / 2));
    outYs.push(value);
  }

  return { xs: outXs, ys: outYs };
}
