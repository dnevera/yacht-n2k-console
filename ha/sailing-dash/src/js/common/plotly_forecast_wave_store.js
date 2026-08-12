// Store wave direction and period in card vars for arrow annotations.
({ meta, vars }) => {
  vars.waveDir = meta.wave_direction || [];
  vars.wavePeriod = meta.wave_period || [];
  return {};
}
