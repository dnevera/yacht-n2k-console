// Format compass cardinal direction, degrees, and wave period for forecast wave points.
({ meta }) => {
  const points = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const dirs = meta.wave_direction || [];
  const periods = meta.wave_period || [];
  return dirs.map(
    (d, i) =>
      points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] +
      ' ' +
      Math.round(d) +
      '° · ' +
      (periods[i] != null ? Math.round(periods[i] * 10) / 10 + ' s' : '– s')
  );
}
