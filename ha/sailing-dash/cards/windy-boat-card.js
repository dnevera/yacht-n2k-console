/*
 * windy-boat-card — a tiny Lovelace card wrapping the official Windy embed
 * widget (embed.windy.com/embed2.html) with a working "back to the boat"
 * button.
 *
 * Why this card exists (2026-08-09)
 * ---------------------------------
 * The dashboard used HA's built-in `type: iframe` plus overlay cards
 * (`config-template-card` + a `card_mod`-positioned `button`). Two problems:
 *   - the button could only rebuild the iframe `src`, i.e. RELOAD the whole
 *     Windy widget, which is both slow and loses the user's map state;
 *   - overlaying HA cards on an iframe is a CSS hack that never reliably
 *     produced a clickable, correctly placed control.
 *
 * The Windy embed has a documented-by-code, two-way `postMessage` API, but
 * it is gated behind the `embedMake` query parameter — verified by reading
 * the live bundle (embed.windy.com/v/41.1.0.emb.b79a/embed2.js):
 *
 *     Rf = { ..., embedMake: Gf(qh.embedMake), ... }
 *     Rf.embedMake && window.parent !== window && function () {
 *         ...
 *         window.onmessage = function (e) {
 *             if (e.data.type === 'updateEmbed') {
 *                 payload.showDetail ? dn.emit('rqstOpen', 'detail', {lat, lon})
 *                                    : dn.emit('rqstClose', 'detail');
 *                 payload.showMarker ? dn.emit('rqstOpen', 'picker', {lat, lon})
 *                                    : dn.emit('rqstClose', 'picker');
 *                 ...pressure / hideMessage / metricWind / metricRain / metricTemp
 *             }
 *         };
 *     }
 *
 * Without `embedMake=true` in the URL, `window.onmessage` inside the iframe
 * is simply `null` and every message is ignored (confirmed live in a real
 * browser: `typeof window.onmessage === 'object'` before, `'function'` after
 * adding the parameter).
 *
 * Recentering: only the `detail` request pans the map on BOTH axes
 * (`rqstOpen picker` calls `panToOffset`, which pans vertically only — also
 * confirmed live: latitude moved, longitude did not). So the button sends
 * `{showDetail: true, detailLat, detailLon}` to recenter, then
 * `{showDetail: false}` a moment later to close the detail panel again — the
 * map keeps the new position (verified: 42.066,18.600 -> 44.147,14.502 with
 * the panel closed afterwards).
 *
 * The iframe `src` is assigned ONCE, on first render. Later `hass` updates
 * never touch it, so the widget never reloads and stays fully interactive
 * (its own zoom buttons, search, menu, and the Windy logo link to
 * windy.com at the current map position all keep working).
 *
 * Config:
 *   type: custom:windy-boat-card
 *   lat_entity: sensor.<n2k gps latitude>    # decimal degrees
 *   lon_entity: sensor.<n2k gps longitude>   # decimal degrees
 *   fallback_lat: 42.43                      # used while GPS is unavailable
 *   fallback_lon: 18.60
 *   zoom: 8
 *   overlay: wind                            # any Windy overlay
 *   product: ecmwf
 *   aspect_ratio: 50%                        # height as % of width
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
    // HA always calls setConfig() before assigning hass, but some hosts (our
    // local-preview harness included) do it the other way round — build as
    // soon as BOTH are present, whichever arrives last.
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
    // Whatever the user last switched the widget to (overlay/product/level/
    // zoom), reported by the embed itself through its outgoing `updateValues`
    // message — so a recenter keeps the current view mode instead of
    // resetting it back to the configured defaults.
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
      // Enables the embed's two-way postMessage API (see the header comment);
      // without it the recenter button below cannot work at all.
      embedMake: 'true',
    };
    const query = Object.keys(params)
      .map((k) => `${k}=${encodeURIComponent(params[k])}`)
      .join('&');
    return `https://embed.windy.com/embed2.html?${query}`;
  }

  // "Home": put the boat back in the centre of the map.
  //
  // NOT done over postMessage: the embed's `updateEmbed` message has no "set
  // centre" request at all. The only one that moves the map on both axes is
  // `showDetail`, which literally means "open the weather detail panel here",
  // and its `detailRendered` handler then pans the map so the point sits under
  // that panel (`Ff(coords, 180)` in embed2.js) — that is why the button used
  // to switch the view into weather detail and land off-centre. Worse, EVERY
  // `updateEmbed` message also runs `payload.pressure ? isolines=pressure :
  // isolines=off`, silently resetting the isobars toggle.
  // So "home" simply re-renders the widget at the boat's position, preserving
  // the view mode the user is currently on (see `_src()`/`_view`). The iframe
  // src is otherwise never reassigned, so this is the only moment the widget
  // reloads — on an explicit button press.
  _goHome() {
    if (!this._iframe) return;
    // A re-render starts with the panel closed, so keep the toggle in sync.
    this._detailOpen = false;
    this._iframe.src = this._src();
  }

  // "Weather here": explicitly open the embed's own weather detail panel at
  // the boat's position. This is what `showDetail` is for, and here the panel
  // (and the pan that comes with it) is exactly what the user asked for.
  // Toggle: the second press closes the panel again (`showDetail: false`,
  // which the embed's own handler maps to hiding the detail view).
  _showDetail() {
    const iframe = this._iframe;
    if (!iframe || !iframe.contentWindow) return;
    const post = (payload) =>
      iframe.contentWindow.postMessage({ type: 'updateEmbed', payload }, '*');

    if (this._detailOpen) {
      this._detailOpen = false;
      post({
        showDetail: false,
        // Re-assert these, otherwise the handler above resets them.
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
      // The embed reports detail open/close itself (`detailRendered` ->
      // `{showDetail: true}`, `pluginClosed('detail')` -> `{showDetail: false}`),
      // so closing the panel from inside the widget doesn't desync our toggle.
      // Careful: the same `updateDetail` message is also used for unrelated
      // things (`showMarker`, `coordinates`, unit changes) with no `showDetail`
      // field at all — those must be ignored, not read as "open".
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
    // Assigned once — never reassigned on hass updates, so the widget does
    // not reload and the user keeps whatever they panned/zoomed to.
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

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'windy-boat-card',
  name: 'Windy (boat)',
  description:
    'Windy embed widget with a "center on the boat" button (re-renders at the boat, keeping the current view mode) and a "weather detail here" button.',
});

console.info(
  `%c WINDY-BOAT-CARD %c ${CARD_VERSION} `,
  'color: white; background: #1a73e8; font-weight: 700;',
  'color: #1a73e8; background: white; font-weight: 700;'
);
