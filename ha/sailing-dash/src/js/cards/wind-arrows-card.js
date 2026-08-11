/**
 * WindArrowsRowCard — Custom Lovelace Card rendering ONLY the top row of
 * wind direction vector arrows (Open-Meteo forecast style preview).
 *
 * Standalone/reusable: any chart engine (ApexCharts, Plotly, ...) can be
 * placed right below this card to get the same "arrows above the chart"
 * layout as https://open-meteo.com preview charts, without that chart
 * library having to know anything about arrow rendering.
 *
 * Arrow length scales with wind speed at that forecast point (same mapping
 * used by openmeteo-wind-card.js), read from `sensor.wind_forecast_flat`
 * attributes (`forecast_time`, `forecast_wind`, `forecast_dir`).
 */

class WindArrowsRowCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
    }
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  getCardSize() {
    return 1;
  }

  render() {
    if (!this._hass) return;

    const entityId = this._config.entity || 'sensor.wind_forecast_flat';
    const maxPoints = Number(this._config.max_points) || 12;
    const forecastEntity = this._hass.states[entityId];

    const times = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_time) || [];
    const speeds = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_wind) || [];
    const dirs = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_dir) || [];

    let arrowHtml = '';
    const numPoints = Math.min(times.length, maxPoints);
    for (let i = 0; i < numPoints; i++) {
      const spd = Number(speeds[i]) || 0;
      const dir = Number(dirs[i]) || 0;
      // Arrow length scales from 12px (0 kt) up to 38px (40+ kt).
      const arrowLen = Math.round(12 + Math.min(spd, 40) * 0.65);
      // Arrows point in the direction the wind blows TO (dir is "from").
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

    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 6px 10px 10px;
        }
        .arrows-row {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
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
      </style>
      <ha-card>
        <div class="arrows-row">
          ${arrowHtml}
        </div>
      </ha-card>
    `;
  }
}

customElements.define('wind-arrows-row-card', WindArrowsRowCard);
