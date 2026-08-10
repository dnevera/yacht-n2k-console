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
    'sensor.speed_raymarine_20_442559_pk_b941014ae3e7110c03bb1cd071a22b76_speed_water_referenced': {
      entity_id: 'sensor.speed_raymarine_20_442559_pk_b941014ae3e7110c03bb1cd071a22b76_speed_water_referenced',
      state: '5.8',
      attributes: { unit_of_measurement: 'kn', friendly_name: 'STW' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth': {
      entity_id: 'sensor.water_depth_raymarine_20_442559_pk_f84fbd9ceeb9d458972daa61e19e4acf_depth',
      state: '12.4',
      attributes: { unit_of_measurement: 'm', friendly_name: 'Depth' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'device_tracker.nevera': {
      entity_id: 'device_tracker.nevera',
      state: 'home',
      attributes: { latitude: 42.4712, longitude: 18.5731, friendly_name: 'Nevera' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.boat_latitude': {
      entity_id: 'sensor.boat_latitude',
      state: '42° 28.272′ N',
      attributes: { friendly_name: 'Latitude' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.boat_longitude': {
      entity_id: 'sensor.boat_longitude',
      state: '18° 34.386′ E',
      attributes: { friendly_name: 'Longitude' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wind_forecast_next_hour': {
      entity_id: 'sensor.wind_forecast_next_hour',
      state: '14.2',
      attributes: { unit_of_measurement: 'kts', friendly_name: 'Forecast next 1h' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wind_gust_next_hour': {
      entity_id: 'sensor.wind_gust_next_hour',
      state: '19.5',
      attributes: { unit_of_measurement: 'kts', friendly_name: 'Gusts next 1h' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wave_height_next_hour': {
      entity_id: 'sensor.wave_height_next_hour',
      state: '0.85',
      attributes: { unit_of_measurement: 'm', friendly_name: 'Height next 1h' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wave_period_next_hour': {
      entity_id: 'sensor.wave_period_next_hour',
      state: '4.2',
      attributes: { unit_of_measurement: 's', friendly_name: 'Period next 1h' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.wave_forecast_flat': {
      entity_id: 'sensor.wave_forecast_flat',
      state: '0.85',
      attributes: {
        unit_of_measurement: 'm',
        friendly_name: 'Wave Forecast Flat',
        forecast_time: forecastTime,
        wave_height: [0.6, 0.8, 1.1, 0.9, 0.7, 0.8, 1.0, 1.2],
        wave_period: [3.5, 4.0, 4.5, 4.2, 3.8, 4.1, 4.3, 4.6],
        wave_direction: forecastDir,
      },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    // Raw decimal-degree N2K GPS position (same entity the open-meteo
    // rest: requests / device_tracker.nevera use), used by
    // config-template-card to build the Windy widget/button URLs (see the
    // "Windy widget" card entry below). NOT sensor.boat_latitude/
    // boat_longitude - those are human-readable DMS strings for the
    // Position section, parseFloat() on them would truncate to whole
    // degrees only.
    'sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_latitude': {
      entity_id: 'sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_latitude',
      state: '42.4712',
      attributes: { unit_of_measurement: '\u00b0', friendly_name: 'Latitude' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    'sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_longitude': {
      entity_id: 'sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_longitude',
      state: '18.5731',
      attributes: { unit_of_measurement: '\u00b0', friendly_name: 'Longitude' },
      last_changed: isoMinutesAgo(0),
      last_updated: isoMinutesAgo(0),
    },
    // Windy recenter button (input_button state is an ISO timestamp of the
    // last press; the widget's config-template-card subscribes to it only).
    'input_button.windy_recenter': {
      entity_id: 'input_button.windy_recenter',
      state: isoMinutesAgo(5),
      attributes: { friendly_name: 'Windy: recenter on boat', icon: 'mdi:crosshairs-gps' },
      last_changed: isoMinutesAgo(5),
      last_updated: isoMinutesAgo(5),
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

  // Minimal `loadCardHelpers()` mock — only needed for config-template-card
  // (it calls `_helpers.createCardElement(config)` to build the card it
  // wraps). Real HA ships this globally via the frontend bundle; here we
  // fake just enough of `createCardElement` to build the handful of native
  // card types the Windy widget uses (grid/iframe/button), recursively, so
  // this harness can exercise the real ${...} template substitution without
  // throwing. Not a faithful re-implementation of HA's actual hui-*-card
  // elements — good enough to catch config/template errors, not to check
  // pixel-perfect rendering.
  function fakeCreateCardElement(config) {
    const el = document.createElement('div');
    el.setAttribute('data-card-type', config.type);
    if (config.type === 'grid') {
      (config.cards || []).forEach((c) => el.appendChild(fakeCreateCardElement(c)));
    } else if (config.type === 'iframe') {
      const iframe = document.createElement('iframe');
      iframe.src = config.url;
      iframe.style.width = '100%';
      el.appendChild(iframe);
    } else if (config.type === 'button') {
      const btn = document.createElement('button');
      btn.textContent = 'button';
      if (config.tap_action && config.tap_action.action === 'url') {
        btn.setAttribute('data-url-path', config.tap_action.url_path);
      }
      el.appendChild(btn);
    }
    el.hass = window.mockHass;
    el.setConfig = function () {};
    return el;
  }
  window.loadCardHelpers = function () {
    return Promise.resolve({
      createCardElement: fakeCreateCardElement,
      createRowElement: fakeCreateCardElement,
      createHuiElement: fakeCreateCardElement,
    });
  };

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
