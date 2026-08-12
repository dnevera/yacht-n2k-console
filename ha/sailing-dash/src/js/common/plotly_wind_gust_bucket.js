// Gusts max bucketing over 10-minute windows capped at Date.now().
({ xs, ys }) => {
  const windowMs = 10 * 60 * 1000;
  const now = Date.now();
  const outXs = [];
  const outYs = [];
  let bucketStart = null;
  let bucketMax = null;
  for (let i = 0; i < xs.length; i++) {
    const t = new Date(xs[i]).getTime();
    if (t > now) continue;
    if (bucketStart === null || t - bucketStart >= windowMs) {
      if (bucketStart !== null) {
        outXs.push(new Date(Math.min(bucketStart + windowMs, now)));
        outYs.push(bucketMax);
      }
      bucketStart = t;
      bucketMax = ys[i];
    } else if (ys[i] > bucketMax) {
      bucketMax = ys[i];
    }
  }
  if (bucketStart !== null) {
    outXs.push(new Date(Math.min(bucketStart + windowMs, now)));
    outYs.push(bucketMax);
  }
  return { xs: outXs, ys: outYs };
}
