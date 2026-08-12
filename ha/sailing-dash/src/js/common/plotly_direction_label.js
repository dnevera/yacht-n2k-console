// Hover label of a direction: "<flow arrow> <compass point> <degrees>°".
//
// ONE snippet for every trace that shows a direction in the unified tooltip,
// so the measured and the forecast rows read exactly the same and the glyph /
// cardinal-point logic is never copy-pasted per chart:
//   * measured wind  - direction lives in a SECOND entity stored as `vars.dir`
//                      by an internal trace, so it is matched BY TIMESTAMP.
//                      Indexing it positionally silently mislabels every point
//                      as soon as the two recorder series differ in length.
//   * forecast wind  - `meta.forecast_dir`, index-aligned with the values by
//                      construction (same open-meteo `hourly` payload).
//   * forecast wave  - `meta.wave_direction`, plus the wave period appended.
// A missing direction says so instead of pretending the wind blows from due
// North (0°).
({ xs, meta, vars }) => {
  const points = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  // Glyphs point where the wind/wave GOES (i.e. `from` direction + 180°),
  // exactly like the vector arrows drawn on the chart itself.
  const glyphs = ['↓', '↙', '←', '↖', '↑', '↗', '→', '↘'];
  const label = (d) => {
    const deg = ((Number(d) % 360) + 360) % 360;
    return glyphs[Math.round(deg / 45) % 8] + ' ' + points[Math.round(deg / 22.5) % 16] + ' ' + Math.round(deg) + '°';
  };
  const m = meta || {};
  if (m.wave_direction) {
    const periods = m.wave_period || [];
    return m.wave_direction.map((d, i) => {
      if (!Number.isFinite(Number(d))) return 'direction n/a';
      const p = periods[i];
      return label(d) + ' · ' + (p != null ? Math.round(p * 10) / 10 + ' s' : '– s');
    });
  }
  if (m.forecast_dir) {
    return m.forecast_dir.map((d) => (Number.isFinite(Number(d)) ? label(d) : 'direction n/a'));
  }
  const dir = (vars && vars.dir) || { xs: [], ys: [] };
  const times = (dir.xs || []).map((t) => new Date(t).getTime());
  const tolerance = 15 * 60 * 1000;
  const dirAt = (x) => {
    const t = new Date(x).getTime();
    let best = -1;
    let bestDelta = Infinity;
    for (let i = 0; i < times.length; i++) {
      const delta = Math.abs(times[i] - t);
      if (delta < bestDelta) { bestDelta = delta; best = i; }
    }
    if (best < 0 || bestDelta > tolerance) return null;
    const v = Number((dir.ys || [])[best]);
    return Number.isFinite(v) ? v : null;
  };
  return (xs || []).map((x) => {
    const d = dirAt(x);
    return d === null ? 'direction n/a' : label(d);
  });
}
