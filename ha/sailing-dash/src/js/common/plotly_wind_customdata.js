// Hover text for the measured wind trace: "<compass point> <degrees>°".
//
// The direction comes from a SECOND entity (sensor.wind_direction_history,
// stored as `vars.dir` by an internal trace), so it must be looked up BY
// TIMESTAMP. The previous version indexed it positionally (`vars.dir.ys[i]`),
// which silently mislabels every point as soon as the two series differ in
// length — and they do: each series is resampled on its own recorder history,
// and a gap (HA restart, alias `unavailable`) in one of them shifts all
// following indices. Points with no direction within the tolerance say so
// instead of pretending the wind blows from due North (0°).
({ xs, vars }) => {
  const points = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
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
    if (d === null) return 'direction n/a';
    return points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '°';
  });
}
