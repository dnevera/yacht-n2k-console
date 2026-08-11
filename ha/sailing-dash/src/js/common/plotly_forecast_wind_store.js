// Store forecast wind direction in card vars for arrow annotations.
({ meta, vars }) => {
  vars.forecastDir = meta.forecast_dir || [];
  return {};
}
