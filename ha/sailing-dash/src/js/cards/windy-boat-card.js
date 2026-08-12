/*
 * windy-boat-card — a tiny Lovelace card wrapping the official Windy embed
 * widget (embed.windy.com/embed2.html) with a working "back to the boat"
 * button.
 */

const CARD_VERSION = '1.2.0';

class WindyBoatCard extends HTMLElement {
  setConfig(config) {
    if (!config.lat_entity || !config.lon_entity) {
      throw new Error('windy-boat-card: lat_entity and lon_entity are required');
    }
    this._config = {
      fallback_lat: 42.43,
      fallback_lon: 18.6,
      zoom: 8,
      overlay: 'wind',
      product: 'ecmwf',
      aspect_ratio: '50%',
      ...config,
    };
    this._built = false;
    this.innerHTML = '';
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built && this._config) this._build();
  }

  getCardSize() {
    return 6;
  }

  _coords() {
    const c = this._config;
    const read = (entityId, fallback) => {
      const st = this._hass && this._hass.states[entityId];
      const value = st ? parseFloat(st.state) : NaN;
      return Number.isFinite(value) ? value : fallback;
    };
    return {
      lat: read(c.lat_entity, c.fallback_lat),
      lon: read(c.lon_entity, c.fallback_lon),
    };
  }

  _src() {
    const c = this._config;
    const { lat, lon } = this._coords();
    const view = this._view || {};
    const params = {
      lat: lat.toFixed(4),
      lon: lon.toFixed(4),
      detailLat: lat.toFixed(4),
      detailLon: lon.toFixed(4),
      width: 650,
      height: 450,
      zoom: view.zoom || c.zoom,
      level: view.level || 'surface',
      overlay: view.overlay || c.overlay,
      product: view.product || c.product,
      menu: '',
      message: 'true',
      marker: 'true',
      calendar: 'now',
      pressure: '',
      type: 'map',
      location: 'coordinates',
      detail: '',
      metricWind: 'kt',
      metricTemp: '\u00B0C',
      radarRange: -1,
      embedMake: 'true',
    };
    const query = Object.keys(params)
      .map((k) => `${k}=${encodeURIComponent(params[k])}`)
      .join('&');
    return `https://embed.windy.com/embed2.html?${query}`;
  }

  _goHome() {
    if (!this._iframe) return;
    this._detailOpen = false;
    this._iframe.src = this._src();
  }

  _showDetail() {
    const iframe = this._iframe;
    if (!iframe || !iframe.contentWindow) return;
    const post = (payload) =>
      iframe.contentWindow.postMessage({ type: 'updateEmbed', payload }, '*');

    if (this._detailOpen) {
      this._detailOpen = false;
      post({
        showDetail: false,
        pressure: this._view && this._view.pressure,
      });
      return;
    }

    const { lat, lon } = this._coords();
    this._detailOpen = true;
    post({
      showDetail: true,
      detailLat: lat,
      detailLon: lon,
      metricWind: 'kt',
      pressure: this._view && this._view.pressure,
    });
  }

  _listenToWidget() {
    if (this._listening) return;
    this._listening = true;
    this._onMessage = (ev) => {
      const data = ev && ev.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'updateDetail') {
        const payload = data.payload || {};
        if (typeof payload.showDetail === 'boolean') {
          this._detailOpen = payload.showDetail;
        }
        return;
      }
      if (data.type !== 'updateValues') return;
      const p = data.payload || {};
      this._view = {
        overlay: p.overlay,
        product: p.product,
        level: p.level,
        zoom: p.zoom,
      };
    };
    window.addEventListener('message', this._onMessage);
  }

  disconnectedCallback() {
    if (this._onMessage) window.removeEventListener('message', this._onMessage);
    this._listening = false;
  }

  _iconButton(iconName, title, onClick, bottom) {
    const button = document.createElement('button');
    button.setAttribute('title', title);
    button.setAttribute('aria-label', title);
    Object.assign(button.style, {
      position: 'absolute',
      right: '10px',
      bottom,
      width: '40px',
      height: '40px',
      padding: '0',
      border: 'none',
      borderRadius: '50%',
      background: 'rgba(0, 0, 0, 0.55)',
      color: '#ffffff',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: '2',
    });
    const icon = document.createElement('ha-icon');
    icon.setAttribute('icon', iconName);
    icon.style.setProperty('--mdc-icon-size', '22px');
    button.appendChild(icon);
    button.addEventListener('click', (ev) => {
      ev.stopPropagation();
      onClick();
    });
    return button;
  }

  _build() {
    const c = this._config;
    const card = document.createElement('ha-card');
    card.style.overflow = 'hidden';
    card.style.position = 'relative';

    const wrap = document.createElement('div');
    wrap.style.position = 'relative';
    wrap.style.width = '100%';
    wrap.style.paddingTop = c.aspect_ratio;

    const iframe = document.createElement('iframe');
    iframe.setAttribute('title', 'Windy');
    iframe.setAttribute('frameborder', '0');
    iframe.setAttribute('allow', 'fullscreen');
    Object.assign(iframe.style, {
      position: 'absolute',
      border: 'none',
      inset: '0',
      width: '100%',
      height: '100%',
    });
    iframe.src = this._src();
    this._iframe = iframe;

    this._listenToWidget();

    const homeButton = this._iconButton(
      'mdi:crosshairs-gps',
      'Center on the boat',
      () => this._goHome(),
      '10px'
    );
    const detailButton = this._iconButton(
      'mdi:weather-partly-cloudy',
      'Weather detail at the boat',
      () => this._showDetail(),
      '58px'
    );

    wrap.appendChild(iframe);
    wrap.appendChild(homeButton);
    wrap.appendChild(detailButton);
    card.appendChild(wrap);
    this.appendChild(card);
    this._built = true;
  }
}

if (!customElements.get('windy-boat-card')) {
  customElements.define('windy-boat-card', WindyBoatCard);
}

if (typeof window !== 'undefined') {
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: 'windy-boat-card',
    name: 'Windy (boat)',
    description:
      'Windy embed widget with a "center on the boat" button and a "weather detail here" button.',
  });
}

console.info(
  `%c WINDY-BOAT-CARD %c ${CARD_VERSION} `,
  'color: white; background: #1a73e8; font-weight: 700;',
  'color: #1a73e8; background: white; font-weight: 700;'
);

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    CARD_VERSION,
    WindyBoatCard,
  };
}
