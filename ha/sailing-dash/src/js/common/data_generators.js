/**
 * Data generators and transformation utilities for ApexCharts and Plotly charts.
 */

const COMPASS_POINTS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

/**
 * Converts a degree angle (0-360) into a 16-point compass direction abbreviation.
 * @param {number} d - Angle in degrees.
 * @returns {string} Compass point string (e.g., 'N', 'SW').
 */
function compassPoint(d) {
  return COMPASS_POINTS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
}

/**
 * Formats a direction angle with compass abbreviation and rounded degrees.
 * @param {number} d - Angle in degrees.
 * @returns {string} Formatted string (e.g., "SW 225°").
 */
function formatCompassDirection(d) {
  const deg = Math.round(d || 0);
  return `${compassPoint(deg)} ${deg}°`;
}

/**
 * Generates time series data array filtered by a lookback window for ApexCharts.
 * @param {Array<string>} times - ISO timestamp strings.
 * @param {Array<number>} values - Numeric values.
 * @param {number} [hoursBack=2] - Lookback window in hours.
 * @returns {Array<[number, number]>} Filtered [timestampMs, roundedValue] tuples.
 */
function generateTimeSeriesData(times, values, hoursBack = 2) {
  const rangeStart = Date.now() - hoursBack * 3600000;
  const timeArray = times || [];
  const valArray = values || [];
  return timeArray
    .map((t, i) => [new Date(t + 'Z').getTime(), Math.round((valArray[i] || 0) * 10) / 10])
    .filter((p) => p[0] >= rangeStart);
}

/**
 * Aggregates wind gust measurements into time buckets (default 10 min max).
 * @param {Array<string|Date>} xs - Timestamps.
 * @param {Array<number>} ys - Measured values.
 * @param {number} [windowMinutes=10] - Aggregation window in minutes.
 * @returns {{ xs: Array<Date>, ys: Array<number> }} Aggregated time series.
 */
function aggregateGusts(xs, ys, windowMinutes = 10) {
  const windowMs = windowMinutes * 60 * 1000;
  const outXs = [];
  const outYs = [];
  let bucketStart = null;
  let bucketMax = null;

  for (let i = 0; i < xs.length; i++) {
    const t = new Date(xs[i]).getTime();
    if (bucketStart === null || t - bucketStart >= windowMs) {
      if (bucketStart !== null) {
        outXs.push(new Date(bucketStart + windowMs));
        outYs.push(bucketMax);
      }
      bucketStart = t;
      bucketMax = ys[i];
    } else if (ys[i] > bucketMax) {
      bucketMax = ys[i];
    }
  }
  if (bucketStart !== null) {
    outXs.push(new Date(bucketStart + windowMs));
    outYs.push(bucketMax);
  }
  return { xs: outXs, ys: outYs };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    COMPASS_POINTS,
    compassPoint,
    formatCompassDirection,
    generateTimeSeriesData,
    aggregateGusts,
  };
}
