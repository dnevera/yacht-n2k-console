/**
 * Mock Custom Elements for Native Home Assistant Cards in local-preview.
 *
 * Defines lightweight web components for standard HA card elements:
 * - <hui-heading-card>
 * - <hui-gauge-card>
 * - <hui-entity-card>
 * - <hui-glance-card>
 * - <hui-map-card>
 * - <hui-tile-card>
 *
 * Allows local-preview to render the complete dashboard layout without errors.
 */

(function () {
  function getEntityState(hass, entityId) {
    if (!hass || !hass.states || !entityId) return { state: 'N/A', unit: '', name: entityId || 'Unknown' };
    const ent = hass.states[entityId];
    if (!ent) return { state: 'N/A', unit: '', name: entityId };
    const unit = (ent.attributes && ent.attributes.unit_of_measurement) || '';
    const name = (ent.attributes && ent.attributes.friendly_name) || entityId;
    return { state: ent.state, unit, name };
  }

  // ---------------------------------------------------------------------------
  // 1. Heading Card (<hui-heading-card>)
  // ---------------------------------------------------------------------------
  class HuiHeadingCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }
    setConfig(config) {
      this._config = config || {};
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const cfg = this._config || {};
      const heading = cfg.heading || 'Header';
      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; padding: 8px 0; }
          .heading {
            font-size: 18px;
            font-weight: 600;
            color: var(--primary-text-color, #ffffff);
            border-bottom: 1px solid var(--divider-color, #333);
            padding-bottom: 6px;
            margin-bottom: 8px;
          }
        </style>
        <div class="heading">${heading}</div>
      `;
    }
  }

  // ---------------------------------------------------------------------------
  // 2. Gauge Card (<hui-gauge-card>)
  // ---------------------------------------------------------------------------
  class HuiGaugeCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }
    setConfig(config) {
      this._config = config || {};
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const cfg = this._config || {};
      const info = getEntityState(this._hass, cfg.entity);
      const title = cfg.name || info.name;
      const val = parseFloat(info.state);
      const min = cfg.min !== undefined ? cfg.min : 0;
      const max = cfg.max !== undefined ? cfg.max : 100;
      const percent = isNaN(val) ? 0 : Math.min(100, Math.max(0, ((val - min) / (max - min)) * 100));

      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          ha-card {
            background: var(--card-background-color, #1c1e24);
            border-radius: 12px;
            padding: 16px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
          }
          .title { font-size: 13px; color: var(--secondary-text-color, #9aa0a6); margin-bottom: 8px; }
          .val-container { font-size: 24px; font-weight: bold; color: var(--primary-text-color, #e1e1e1); }
          .unit { font-size: 14px; font-weight: normal; margin-left: 4px; color: #9aa0a6; }
          .bar-bg { background: #333; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 10px; }
          .bar-fill { background: #4fc3f7; height: 100%; transition: width 0.3s; }
        </style>
        <ha-card>
          <div class="title">${title}</div>
          <div class="val-container">${info.state}<span class="unit">${info.unit}</span></div>
          <div class="bar-bg"><div class="bar-fill" style="width: ${percent}%;"></div></div>
        </ha-card>
      `;
    }
  }

  // ---------------------------------------------------------------------------
  // 3. Entity Card (<hui-entity-card>)
  // ---------------------------------------------------------------------------
  class HuiEntityCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }
    setConfig(config) {
      this._config = config || {};
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const cfg = this._config || {};
      const info = getEntityState(this._hass, cfg.entity);
      const title = cfg.name || info.name;

      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          ha-card {
            background: var(--card-background-color, #1c1e24);
            border-radius: 12px;
            padding: 14px 16px;
          }
          .name { font-size: 13px; color: var(--secondary-text-color, #9aa0a6); }
          .value { font-size: 20px; font-weight: bold; color: var(--primary-text-color, #e1e1e1); margin-top: 4px; }
        </style>
        <ha-card>
          <div class="name">${title}</div>
          <div class="value">${info.state} ${info.unit}</div>
        </ha-card>
      `;
    }
  }

  // ---------------------------------------------------------------------------
  // 4. Glance Card (<hui-glance-card>)
  // ---------------------------------------------------------------------------
  class HuiGlanceCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }
    setConfig(config) {
      this._config = config || {};
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const cfg = this._config || {};
      const entities = cfg.entities || [];
      const itemsHtml = entities.map((e) => {
        const entId = typeof e === 'string' ? e : e.entity;
        const info = getEntityState(this._hass, entId);
        const name = (typeof e === 'object' && e.name) ? e.name : info.name;
        return `
          <div class="item">
            <div class="name">${name}</div>
            <div class="val">${info.state} ${info.unit}</div>
          </div>
        `;
      }).join('');

      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          ha-card {
            background: var(--card-background-color, #1c1e24);
            border-radius: 12px;
            padding: 12px 16px;
            display: flex;
            justify-content: space-around;
          }
          .item { text-align: center; flex: 1; }
          .name { font-size: 12px; color: var(--secondary-text-color, #9aa0a6); }
          .val { font-size: 18px; font-weight: bold; color: #4fc3f7; margin-top: 4px; }
        </style>
        <ha-card>${itemsHtml}</ha-card>
      `;
    }
  }

  // ---------------------------------------------------------------------------
  // 5. Map Card (<hui-map-card>)
  // ---------------------------------------------------------------------------
  class HuiMapCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }
    setConfig(config) {
      this._config = config || {};
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const cfg = this._config || {};
      const entities = cfg.entities || [];
      const primaryEnt = entities[0] ? (typeof entities[0] === 'string' ? entities[0] : entities[0].entity) : '';
      const info = getEntityState(this._hass, primaryEnt);

      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          ha-card {
            background: #14171d;
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            border: 1px dashed #333;
          }
          .map-title { font-size: 14px; font-weight: bold; color: #4fc3f7; }
          .map-info { font-size: 12px; color: #9aa0a6; margin-top: 6px; }
        </style>
        <ha-card>
          <div class="map-title">📍 Map Preview (${cfg.default_zoom || '14'}x zoom)</div>
          <div class="map-info">Tracking: ${primaryEnt || 'boat'} (${info.state})</div>
        </ha-card>
      `;
    }
  }

  // ---------------------------------------------------------------------------
  // 6. Tile Card (<hui-tile-card>)
  // ---------------------------------------------------------------------------
  class HuiTileCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }
    setConfig(config) {
      this._config = config || {};
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const cfg = this._config || {};
      const info = getEntityState(this._hass, cfg.entity);

      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          ha-card {
            background: var(--card-background-color, #1c1e24);
            border-radius: 12px;
            padding: 12px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
          }
          .title { font-size: 13px; color: var(--secondary-text-color, #9aa0a6); }
          .state { font-size: 16px; font-weight: bold; color: var(--primary-text-color, #e1e1e1); }
        </style>
        <ha-card>
          <div class="title">${info.name}</div>
          <div class="state">${info.state} ${info.unit}</div>
        </ha-card>
      `;
    }
  }

  // Register all mock custom elements
  customElements.define('hui-heading-card', HuiHeadingCard);
  customElements.define('hui-gauge-card', HuiGaugeCard);
  customElements.define('hui-entity-card', HuiEntityCard);
  customElements.define('hui-glance-card', HuiGlanceCard);
  customElements.define('hui-map-card', HuiMapCard);
  customElements.define('hui-tile-card', HuiTileCard);
})();
