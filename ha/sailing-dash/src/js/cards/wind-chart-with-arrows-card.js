/**
 * WindChartWithArrowsCard — a single wrapper card that draws the ApexCharts
 * wind chart AND the wind-direction arrow row + colour legend as ONE unit,
 * inside the SAME shadow DOM.
 *
 * Why a wrapper instead of two sibling cards: two separate `ha-card`
 * elements stacked with a negative CSS margin is fragile (any chart height
 * change breaks the overlap and the arrow row visibly reads as its own
 * "plaque" sitting under the chart). Here the nested `custom:apexcharts-card`
 * is created programmatically via `loadCardHelpers()` and the arrows/legend
 * are painted as an absolutely-positioned SVG layer directly on top of it,
 * in the exact same box — so they are genuinely part of the chart, not a
 * neighbour of it.
 *
 * Colour scale: `windSpeedColor(v)` mirrors the Plotly wind chart's "kt
 * scale" colourbar (`plotly_wind_annotations.js`) — same 8 stops, 0-40+ kt.
 * Kept in sync via tests/js/wind_chart_snippets.test.js.
 */

const WIND_SPEED_COLOR_STOPS = [
  [5, '#b0e2ff'], [10, '#61c4e0'], [15, '#4bbf7a'], [20, '#a8d048'],
  [25, '#f5e642'], [30, '#f2a93b'], [35, '#eb5c2a'], [40, '#d62828'],
];
const WIND_SPEED_OVER_MAX_COLOR = '#8e1b8e';

function windSpeedColor(v) {
  for (const [max, color] of WIND_SPEED_COLOR_STOPS) if (v < max) return color;
  return WIND_SPEED_OVER_MAX_COLOR;
}

class WindChartWithArrowsCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.chart_config) {
      throw new Error('wind-chart-with-arrows-card: "chart_config" is required');
    }
    this._config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    this._ensureLayout();
    this._buildChart();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._chartEl) this._chartEl.hass = hass;
    this._renderOverlay();
  }

  getCardSize() {
    return 4;
  }

  // Build the inner chart element once, via HA's standard card-creation
  // helper — this is the "official" way to embed one Lovelace card inside
  // another custom card, instead of hand-instantiating a specific element.
  async _buildChart() {
    if (this._chartEl) return;
    let helpers = null;
    try {
      helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
    } catch (e) {
      helpers = null;
    }
    const slot = this.shadowRoot.querySelector('.chart-slot');
    if (helpers && helpers.createCardElement) {
      this._chartEl = helpers.createCardElement(this._config.chart_config);
    } else {
      // Fallback for environments without the helper (should not happen in
      // a real HA frontend) — create the element directly by tag name.
      this._chartEl = document.createElement(this._config.chart_config.type.replace('custom:', ''));
      this._chartEl.setConfig(this._config.chart_config);
    }
    this._chartEl.style.display = 'block';
    if (this._hass) this._chartEl.hass = this._hass;
    slot.innerHTML = '';
    slot.appendChild(this._chartEl);
  }

  _ensureLayout() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .wrap { position: relative; }
        .chart-slot { display: block; }
        /* Arrow row: floats INSIDE the chart's own top padding, not below
           it — right offset leaves room for the colour legend, which now
           sits on the RIGHT (same side as Plotly's own colourbar). */
        .arrows {
          position: absolute;
          top: 34px;
          left: 8px;
          right: 54px;
          height: 24px;
          pointer-events: none; /* hover/drag still reach the chart below */
        }
        .arrow-item {
          position: absolute;
          top: 0;
          transform: translateX(-50%);
        }
        /* Colour legend: a vertical "kt" gradient bar on the RIGHT edge of
           the chart, mirroring where Plotly's own colourbar sits (default
           Plotly colourbar thickness is 30px - the bar below matches it
           exactly so both chart engines render the same visual weight). */
        .legend {
          position: absolute;
          top: 30px;
          right: 4px;
          width: 46px;
          bottom: 40px;
          display: flex;
          flex-direction: column;
          align-items: center;
          pointer-events: none;
        }
        .legend-bar {
          width: 30px;
          flex: 1;
          border-radius: 2px;
          background: linear-gradient(to top, ${WIND_SPEED_COLOR_STOPS.map(([max, color]) => `${color} ${(max / 40) * 100}%`).join(', ')}, ${WIND_SPEED_OVER_MAX_COLOR} 100%);
        }
        .legend-label {
          font-size: 8px;
          color: var(--secondary-text-color, #90a4ae);
          line-height: 1.2;
          text-align: center;
        }
      </style>
      <div class="wrap">
        <div class="chart-slot"></div>
        <div class="legend">
          <div class="legend-label">40+</div>
          <div class="legend-bar"></div>
          <div class="legend-label">0&nbsp;kt</div>
        </div>
        <div class="arrows"></div>
      </div>
    `;
  }

  _renderOverlay() {
    if (!this._hass) return;
    const cfg = this._config;
    const entityId = cfg.entity || 'sensor.wind_forecast_flat';
    const forecastEntity = this._hass.states[entityId];

    // Time window MUST mirror the chart underneath (same source of truth:
    // config.yaml's time_window, injected into both by build.py).
    const historyHours = Number(cfg.history_hours) || 4;
    const forecastHours = Number(cfg.forecast_hours) || 72;
    const spacingHours = Number(cfg.arrow_spacing_hours) || 3;
    const rangeStart = Date.now() - historyHours * 3600000;
    const rangeEnd = Date.now() + forecastHours * 3600000;
    const rangeMs = rangeEnd - rangeStart;

    const times = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_time) || [];
    const speeds = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_wind) || [];
    const dirs = (forecastEntity && forecastEntity.attributes && forecastEntity.attributes.forecast_dir) || [];

    let arrowHtml = '';
    let lastPlottedMs = -Infinity;
    const minGapMs = spacingHours * 3600000;
    for (let i = 0; i < times.length; i++) {
      const t = new Date(times[i].endsWith('Z') ? times[i] : times[i] + 'Z').getTime();
      if (!Number.isFinite(t) || t < rangeStart || t > rangeEnd) continue;
      if (t - lastPlottedMs < minGapMs) continue;
      lastPlottedMs = t;

      const spd = Number(speeds[i]) || 0;
      const dir = Number(dirs[i]) || 0;
      const leftPct = ((t - rangeStart) / rangeMs) * 100;
      const color = windSpeedColor(spd);
      const rotation = (dir + 180) % 360; // arrow points where wind blows TO

      arrowHtml += `
        <div class="arrow-item" style="left:${leftPct.toFixed(2)}%" title="${spd.toFixed(1)} kts @ ${Math.round(dir)}\u00b0">
          <svg width="16" height="22" viewBox="0 0 16 22">
            <g transform="rotate(${rotation} 8 11)">
              <line x1="8" y1="20" x2="8" y2="6" stroke="${color}" stroke-width="2" />
              <polygon points="8,2 4,9 12,9" fill="${color}" />
            </g>
          </svg>
        </div>
      `;
    }

    const arrowsEl = this.shadowRoot.querySelector('.arrows');
    if (arrowsEl) arrowsEl.innerHTML = arrowHtml;
  }
}

customElements.define('wind-chart-with-arrows-card', WindChartWithArrowsCard);
