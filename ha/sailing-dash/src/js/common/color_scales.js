/**
 * Color scales and utility functions for wind speed and wave height visualizations.
 */

const WIND_SPEED_COLORSCALE = [
  [0, '#b0e2ff'],
  [0.125, '#61c4e0'],
  [0.25, '#4bbf7a'],
  [0.375, '#a8d048'],
  [0.5, '#f5e642'],
  [0.625, '#f2a93b'],
  [0.75, '#eb5c2a'],
  [0.875, '#d62828'],
  [1, '#8e1b8e'],
];

/**
 * Returns color hex code for a given wind speed in knots.
 * @param {number} v - Wind speed in knots.
 * @returns {string} Hex color string.
 */
function windSpeedColor(v) {
  const stops = [
    [5, '#b0e2ff'],
    [10, '#61c4e0'],
    [15, '#4bbf7a'],
    [20, '#a8d048'],
    [25, '#f5e642'],
    [30, '#f2a93b'],
    [35, '#eb5c2a'],
    [40, '#d62828'],
  ];
  for (const [max, color] of stops) {
    if (v < max) return color;
  }
  return '#8e1b8e';
}

/**
 * Returns color hex code for a given wave height in meters.
 * @param {number} v - Wave height in meters.
 * @returns {string} Hex color string.
 */
function waveHeightColor(v) {
  const stops = [
    [0.3, '#b0e2ff'],
    [0.6, '#61c4e0'],
    [1, '#4bbf7a'],
    [1.5, '#a8d048'],
    [2, '#f5e642'],
    [3, '#f2a93b'],
    [4, '#eb5c2a'],
    [5, '#d62828'],
  ];
  for (const [max, color] of stops) {
    if (v < max) return color;
  }
  return '#8e1b8e';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    WIND_SPEED_COLORSCALE,
    windSpeedColor,
    waveHeightColor,
  };
}
