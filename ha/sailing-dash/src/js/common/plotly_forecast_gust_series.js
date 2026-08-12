// Parse wind forecast gust times and speeds from Open-Meteo REST metadata.
({ meta }) => ({
  xs: (meta.forecast_time || []).map((t) => new Date(t + "Z")),
  ys: (meta.forecast_gust || []),
})
