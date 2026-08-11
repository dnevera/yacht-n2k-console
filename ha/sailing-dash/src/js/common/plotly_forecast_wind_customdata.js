// Format compass cardinal direction and degrees for forecast wind points.
({ meta }) => {
  const points = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return (meta.forecast_dir || []).map(
    (d) => points[Math.round((((d % 360) + 360) % 360) / 22.5) % 16] + ' ' + Math.round(d) + '°'
  );
}
