// plotly-graph `filters: - fn:` step — drop points whose value is not a finite
// number, keeping xs/ys aligned.
//
// Recorder history is NOT purely numeric: every HA restart writes `unknown`,
// and a template sensor with an `availability` template reports `unavailable`
// while its N2K source is quiet. `map_y: parseFloat(y)` turns both into NaN.
// A single NaN inside a `resample` bucket propagates to the bucket's value, and
// from there into the trace, the `$ex ys` colour scale and the autoranged Y
// axis — which is what made the wind chart look random. Run this right after
// `map_y` and before `resample` so a missing sample stays a gap.
({ xs, ys }) => {
  const outXs = [];
  const outYs = [];
  for (let i = 0; i < ys.length; i++) {
    const v = Number(ys[i]);
    if (Number.isFinite(v)) {
      outXs.push(xs[i]);
      outYs.push(v);
    }
  }
  return { xs: outXs, ys: outYs };
}
