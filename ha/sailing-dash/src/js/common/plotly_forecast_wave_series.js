// Parse wave forecast times and heights from Open-Meteo REST metadata.
({ meta }) => ({
  xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),
  ys: (meta.wave_height || []),
})
