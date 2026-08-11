// plotly-graph `filters: - fn:` step — drop points whose timestamp is in the future (> Date.now()).
//
// Running this AFTER `resample` ensures that bucket boundary rounding (e.g. a 30m
// resample bucket at 18:30 when `now` is 18:19) does not leave points past "Now"
// that would show up in hover tooltips in the forecast region.
({ xs, ys }) => {
  const outXs = [];
  const outYs = [];
  const now = Date.now();
  for (let i = 0; i < xs.length; i++) {
    const t = new Date(xs[i]).getTime();
    if (Number.isFinite(t) && t > now) continue;
    outXs.push(xs[i]);
    outYs.push(ys[i]);
  }
  return { xs: outXs, ys: outYs };
}
