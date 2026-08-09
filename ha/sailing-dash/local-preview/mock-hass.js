// Minimal fake `hass` object good enough to feed to standalone Lovelace
// custom card elements (apexcharts-card, windrose-card, compass-card,
// plotly-graph-card) without a real Home Assistant backend.
//
// This is NOT the real HA frontend object — just the handful of properties
// these specific cards read: hass.states[entity_id], hass.callWS (history
// API used by windrose-card/plotly-graph-card), hass.localize, hass.language,
// hass.locale, hass.config.
//
// Fake time-series data (history + open-meteo-shaped forecast) is generated
// with a fixed seed-like approach (Math.sin) so every reload looks the same.

(function () {
  const NOW = new Date();

  function isoMinutesAgo(mins) {
    return new Date(NOW.getTime() - mins * 60000).toISOString();
  }

  function isoHoursFromNow(hrs) {
    return new Date(NOW.getTime() + hrs * 3600000).toISOString().slice(0, 19);
  }

  // Deterministic pseudo-wave, so mock wind/direction data look plausible
  // (oscillating speed 8-18kt, direction drifting 250-340deg) without a RNG.
  function wave(i, base, amp, period) {
    return base + amp * Math.sin((i / period) * Math.PI * 2);
  }

  const HISTORY_POINTS = 96; // last 24h @ 15min
  const windSpeedHistory = [];
  const windDirHistory = [];
  for (let i = 0; i < HISTORY_POINTS; i++) {
    windSpeedHistory.push({
      t: isoMinutesAgo((HISTORY_POINTS - i) * 15),
      speed: Math.max(0, wave(i, 12, 5, 24)),
      dir: (wave(i, 290, 40, 30) + 360) % 360,
    });
  }

  const FORECAST_HOURS = 48;
  const forecastTime = [];
  const forecastWind = [];
  const forecastGust = [];
  const forecastDir = [];
  for (let i = 0; i < FORECAST_HOURS; i++) {
    forecastTime.push(isoHoursFromNow(i));
    forecastWind.push(Math.max(0, wave(i, 13, 6, 12)));
    forecastGust.push(Math.max(0, wave(i, 18, 7, 12)));
    forecastDir.push((wave(i, 300, 35, 16) + 360) % 360);
  }

  const latestWind = windSpeedHistory[windSpeedHistory.length - 1];

  const states = {
    // Single source of truth for the chart time window + the open-meteo
    // request horizon (mirrors sensor.chart_time_window from sensors-sailing.yaml).
    'sensor.chart_time_window': {
      entity_id: 'sensor.chart_time_window',
      state: '4h back / 48h ahead',
      attributes: { history_hours: 4, forecast_hours: 48 },
      last_changed: NOW.toISOString(),
      last_updated: NOW.toISOString(),
    },
    // Entity ids below match dashboard-sailing.yaml exactly (copy-pasted
    // from the live config), so the previewed cards behave the same way
    // as on the real dashboard.
    'sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed': {
      entity_id: 'sensor.wind_data_raymarine_20_442559_pk_a00872849cc8b861a8f51deb51cc1cd2_wind_speed',
      state: latestWind.speed.toFixed(1),
      attributes: { unit_of_measurement: 'kts', friendly_name: 'Wind Speed', icon: 'mdi:weather-windy' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wind_direction_history': {
      entity_id: 'sensor.wind_direction_history',
      state: latestWind.dir.toFixed(0),
      attributes: { unit_of_measurement: '\u00b0', friendly_name: 'Wind Direction (history)', icon: 'mdi:compass-outline' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wind_forecast_flat': {
      entity_id: 'sensor.wind_forecast_flat',
      state: forecastWind[0].toFixed(1),
      attributes: {
        unit_of_measurement: 'kts',
        friendly_name: 'Wind Forecast Flat',
        icon: 'mdi:weather-windy',
        forecast_time: forecastTime,
        forecast_wind: forecastWind,
        forecast_gust: forecastGust,
        forecast_dir: forecastDir,
      },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog': {
      entity_id: 'sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_cog',
      state: '215',
      attributes: { unit_of_measurement: '\u00b0', friendly_name: 'COG', icon: 'mdi:compass-outline' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_sog': {
      entity_id: 'sensor.cog_sog_rapid_update_raymarine_display_1180407_pk_3b6721c745c17891811fa7e601a6aa50_sog',
      state: '6.4',
      attributes: { unit_of_measurement: 'kts', friendly_name: 'SOG' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.barometer_mmhg': {
      entity_id: 'sensor.barometer_mmhg',
      state: '757',
      attributes: { unit_of_measurement: 'mmHg', friendly_name: 'Pressure' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
  };

  // Fake `history/history_during_period` shape, good enough for
  // windrose-card, which calls hass.callWS(...) for recorder history
  // instead of reading hass.states directly.
  function fakeHistoryFor(entityId) {
    if (entityId.indexOf('wind_speed') !== -1) {
      return windSpeedHistory.map((p) => ({ s: p.speed.toFixed(1), lu: Math.floor(new Date(p.t).getTime() / 1000) }));
    }
    if (entityId.indexOf('wind_direction') !== -1) {
      return windSpeedHistory.map((p) => ({ s: p.dir.toFixed(0), lu: Math.floor(new Date(p.t).getTime() / 1000) }));
    }
    return [];
  }

  // Fake `history/period/<start>?filter_entity_id=<id>&end_time=<end>` REST
  // shape (an array-of-arrays, one inner array per requested entity_id),
  // good enough for plotly-graph-card, which calls hass.callApi('GET', ...)
  // instead of hass.callWS - a *different* history transport than
  // windrose-card, so it needed its own mock (this was the actual reason
  // plotly-graph-card rendered an empty chart even after the custom-element
  // tag mismatch was fixed).
  function fakeHistoryStatesFor(entityId) {
    const entity = states[entityId];
    const unit = entity ? entity.attributes.unit_of_measurement : undefined;
    if (entityId.indexOf('wind_speed') !== -1) {
      return windSpeedHistory.map((p) => ({
        entity_id: entityId,
        state: p.speed.toFixed(1),
        attributes: { unit_of_measurement: unit },
        last_changed: p.t,
        last_updated: p.t,
      }));
    }
    if (entityId.indexOf('wind_direction') !== -1) {
      return windSpeedHistory.map((p) => ({
        entity_id: entityId,
        state: p.dir.toFixed(0),
        attributes: { unit_of_measurement: unit },
        last_changed: p.t,
        last_updated: p.t,
      }));
    }
    return entity ? [entity] : [];
  }

  window.mockHass = {
    language: 'en',
    locale: { language: 'en', number_format: 'comma_decimal', time_format: '24' },
    config: { latitude: 42.43, longitude: 18.6, unit_system: { length: 'km' }, time_zone: 'UTC' },
    states: states,
    themes: { darkMode: false },
    localize: function (key) { return key; },
    callWS: function (msg) {
      if (msg.type === 'history/history_during_period') {
        const result = {};
        (msg.entity_ids || []).forEach((id) => {
          result[id] = fakeHistoryFor(id);
        });
        return Promise.resolve(result);
      }
      return Promise.resolve({});
    },
    callApi: function (method, path) {
      // Parses e.g. "history/period/2026-01-01T00:00:00Z?filter_entity_id=sensor.x&end_time=..."
      const m = /^history\/period\/[^?]*\?(.*)$/.exec(path || '');
      if (m) {
        const params = new URLSearchParams(m[1]);
        const entityId = params.get('filter_entity_id');
        return Promise.resolve(entityId ? [fakeHistoryStatesFor(entityId)] : []);
      }
      return Promise.resolve([]);
    },
    connection: {
      subscribeMessage: function () { return () => {}; },
    },
  };
})();
