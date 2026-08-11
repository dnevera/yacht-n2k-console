/**
 * OpenMeteoWindCard — Custom Lovelace Card for Open-Meteo ECMWF Wind Forecast & NMEA Sensor Overlay.
 *
 * Renders forecast wind speed area, gust lines, overlaid NMEA measured wind data,
 * and top vector arrow row where arrow length scales with wind speed.
 */

class OpenMeteoWindCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  setConfig(config) {
    this._config = config || {};
    this.render();
  }

  getCardSize() {
    return 5;
  }

  render() {
    if (!this._hass || !this._config) return;

    const forecastEntity = this._hass.states['sensor.wind_forecast_flat'];
    const measuredSpeedEntity = this._hass.states['sensor.boat_wind_speed'];

    const times = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_time) || [];
    const speeds = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_wind) || [];
    const gusts = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_gust) || [];
    const dirs = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_dir) || [];

    const measuredVal = measuredSpeedEntity ? parseFloat(measuredSpeedEntity.state) : NaN;

    // Render top vector wind arrows (arrow length scales proportionally with wind speed)
    let arrowHtml = '';
    const numArrowPoints = Math.min(times.length, 12);
    for (let i = 0; i < numArrowPoints; i++) {
      const spd = Number(speeds[i]) || 0;
      const dir = Number(dirs[i]) || 0;
      // Arrow vector length scales from 12px (0 kt) up to 38px (40+ kt)
      const arrowLen = Math.round(12 + Math.min(spd, 40) * 0.65);
      const rotation = (dir + 180) % 360;

      arrowHtml += `
        <div class="arrow-item" title="${spd.toFixed(1)} kt @ ${Math.round(dir)}°">
          <svg width="22" height="${arrowLen}" viewBox="0 0 22 ${arrowLen}">
            <g transform="rotate(${rotation} 11 ${arrowLen / 2})">
              <line x1="11" y1="${arrowLen}" x2="11" y2="4" stroke="#4fc3f7" stroke-width="2" />
              <polygon points="11,0 7,8 15,8" fill="#4fc3f7" />
            </g>
          </svg>
          <span class="arrow-speed">${Math.round(spd)}k</span>
        </div>
      `;
    }

    // Build SVG Chart
    const width = 560;
    const height = 180;
    const padding = { top: 25, right: 20, bottom: 30, left: 35 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    let chartSvg = '';

    if (times.length > 1) {
      const timeMsList = times.map((t) => new Date(t.endsWith('Z') ? t : t + 'Z').getTime());
      const minTime = timeMsList[0];
      const maxTime = timeMsList[timeMsList.length - 1];
      const timeSpan = Math.max(1, maxTime - minTime);

      const allSpeeds = [...speeds, ...gusts];
      if (Number.isFinite(measuredVal)) allSpeeds.push(measuredVal);
      const maxSpd = Math.max(25, ...allSpeeds.map((s) => Number(s) || 0)) * 1.1;

      const getX = (tMs) => padding.left + ((tMs - minTime) / timeSpan) * chartW;
      const getY = (spd) => padding.top + chartH - (Math.max(0, Number(spd) || 0) / maxSpd) * chartH;

      // Speed Area & Line Points
      const spdPts = times.map((_, i) => `${getX(timeMsList[i]).toFixed(1)},${getY(speeds[i]).toFixed(1)}`);
      const gustPts = times.map((_, i) => `${getX(timeMsList[i]).toFixed(1)},${getY(gusts[i]).toFixed(1)}`);

      const areaD = `M ${padding.left},${padding.top + chartH} L ${spdPts.join(' L ')} L ${padding.left + chartW},${padding.top + chartH} Z`;
      const spdD = `M ${spdPts.join(' L ')}`;
      const gustD = `M ${gustPts.join(' L ')}`;

      // Grid & Axes
      let gridY = '';
      for (let s = 0; s <= maxSpd; s += 10) {
        const y = getY(s);
        gridY += `
          <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="rgba(255,255,255,0.08)" stroke-width="1" />
          <text x="${padding.left - 5}" y="${y + 3}" fill="#90a4ae" font-size="9" text-anchor="end">${s}</text>
        `;
      }

      // Time ticks (X-axis)
      let ticksX = '';
      const step = Math.max(1, Math.floor(times.length / 5));
      for (let i = 0; i < times.length; i += step) {
        const x = getX(timeMsList[i]);
        const d = new Date(timeMsList[i]);
        const label = `${d.getHours().toString().padStart(2, '0')}:00`;
        ticksX += `
          <line x1="${x}" y1="${padding.top + chartH}" x2="${x}" y2="${padding.top + chartH + 4}" stroke="#90a4ae" stroke-width="1" />
          <text x="${x}" y="${padding.top + chartH + 16}" fill="#90a4ae" font-size="9" text-anchor="middle">${label}</text>
        `;
      }

      // "Now" Marker Line
      const nowMs = Date.now();
      let nowSvg = '';
      if (nowMs >= minTime && nowMs <= maxTime) {
        const nowX = getX(nowMs);
        nowSvg = `
          <line x1="${nowX}" y1="${padding.top}" x2="${nowX}" y2="${padding.top + chartH}" stroke="#ffeb3b" stroke-width="1.5" stroke-dasharray="3,3" />
          <text x="${nowX}" y="${padding.top - 6}" fill="#ffeb3b" font-size="9" font-weight="bold" text-anchor="middle">Now</text>
        `;
      }

      // NMEA Measured Overlay Point
      let measuredSvg = '';
      if (Number.isFinite(measuredVal) && nowMs >= minTime && nowMs <= maxTime) {
        const mx = getX(nowMs);
        const my = getY(measuredVal);
        measuredSvg = `
          <circle cx="${mx}" cy="${my}" r="5" fill="#26c6da" stroke="#ffffff" stroke-width="1.5" />
          <text x="${mx + 7}" y="${my + 3}" fill="#26c6da" font-size="10" font-weight="bold">${measuredVal.toFixed(1)} kt</text>
        `;
      }

      chartSvg = `
        <svg viewBox="0 0 ${width} ${height}" class="chart-svg">
          ${gridY}
          ${ticksX}
          <path d="${areaD}" fill="rgba(79, 195, 247, 0.15)" />
          <path d="${spdD}" fill="none" stroke="#4fc3f7" stroke-width="2" />
          <path d="${gustD}" fill="none" stroke="#ff7043" stroke-width="2" stroke-dasharray="4,4" />
          ${nowSvg}
          ${measuredSvg}
        </svg>
      `;
    }

    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 16px;
          background: var(--ha-card-background, var(--card-background-color, #1c1c1e));
          color: var(--primary-text-color, #ffffff);
          font-family: system-ui, -apple-system, sans-serif;
        }
        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
        }
        .title {
          font-size: 15px;
          font-weight: 600;
          color: var(--primary-text-color, #ffffff);
        }
        .badge {
          background: rgba(79, 195, 247, 0.15);
          color: #4fc3f7;
          padding: 3px 8px;
          border-radius: 4px;
          font-size: 11px;
          font-weight: 500;
        }
        .arrows-row {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          padding: 6px 0 10px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.08);
          margin-bottom: 8px;
        }
        .arrow-item {
          display: flex;
          flex-direction: column;
          align-items: center;
          font-size: 9px;
          color: #90a4ae;
        }
        .arrow-speed {
          margin-top: 3px;
        }
        .chart-container {
          width: 100%;
          margin-top: 4px;
        }
        .chart-svg {
          width: 100%;
          height: auto;
          overflow: visible;
        }
        .legend {
          display: flex;
          justify-content: center;
          gap: 16px;
          margin-top: 8px;
          font-size: 11px;
          color: #b0bec5;
        }
        .legend-item {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .legend-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
        }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">Wind — Open-Meteo SDK</div>
          <div class="badge">ECMWF Model</div>
        </div>
        <div class="arrows-row">
          ${arrowHtml}
        </div>
        <div class="chart-container">
          ${chartSvg}
        </div>
        <div class="legend">
          <div class="legend-item">
            <span class="legend-dot" style="background: #26c6da;"></span>
            <span>Measured (NMEA)</span>
          </div>
          <div class="legend-item">
            <span class="legend-dot" style="background: #4fc3f7;"></span>
            <span>Forecast Speed</span>
          </div>
          <div class="legend-item">
            <span class="legend-dot" style="background: #ff7043;"></span>
            <span>Forecast Gusts</span>
          </div>
        </div>
      </ha-card>
    `;
  }
}

if (!customElements.get('openmeteo-wind-card')) {
  customElements.define('openmeteo-wind-card', OpenMeteoWindCard);
}
