/*
 * ais-select-bridge.js — make a click on an AIS marker ON THE MAP open the
 * dashboard's own target-detail card.
 *
 * WHY THIS EXISTS
 * Home Assistant has NO more-info control for the `geo_location` domain:
 * `more-info-content` falls back to `more-info-default`, which literally
 * renders `nothing`. So clicking an AIS marker opened a dialog showing only
 * the entity state (the distance) — every vessel field lives in attributes
 * that dialog never displays.
 *
 * The map card gives no `tap_action` hook for its markers; it always fires a
 * `hass-more-info` event ({ bubbles: true, composed: true }). This module
 * listens for that event in the CAPTURE phase on `window`, so it runs before
 * the <home-assistant> element's own handler further down the tree. For an
 * `geo_location.ais_*` entity it cancels the event and instead writes the MMSI
 * into `input_text.ais_selected_mmsi` — exactly what a click in the target
 * table does — which makes the dashboard's detail overlay render that vessel.
 * Every other entity is left completely untouched.
 *
 * Deployed to /config/www/ais-select-bridge.js and registered as a Lovelace
 * module resource by ha/ais/deploy.sh (deploy_card_deps).
 */

const ENTITY_PREFIX = "geo_location.ais_";
const SELECTED_HELPER = "input_text.ais_selected_mmsi";
// Token the dashboard writes into entity_ids / templates; `custom:ais-user-scope`
// replaces it with "_<user slug>" for the CURRENT user (or "" as a fallback).
const USER_TOKEN = "__AIS_USER__";

function getHass() {
  const root = document.querySelector("home-assistant");
  return root && root.hass ? root.hass : null;
}

/*
 * PER-USER STATE
 * Home Assistant has no per-user entity state: a plain helper is global, so one
 * user's selection (and the expanded/collapsed table) was visible to everyone.
 * The state therefore STAYS ON THE SERVER — as required — but in one helper per
 * user: input_text.ais_selected_mmsi_<slug> / input_boolean.ais_table_expanded_
 * <slug>, provisioned by helpers/provision_helpers.py --auth.
 *
 * The slug must match that script exactly: HA-style slugified user name, or
 * `u_<first 8 chars of the user id>` when the name has no ASCII alphanumerics.
 */
function slugify(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function userSuffix(hass) {
  const user = hass && hass.user;
  if (!user) {
    // Unknown user: fall back to the un-suffixed (shared) helpers rather than
    // writing into a helper that does not exist.
    return "";
  }
  const slug = slugify(user.name) || "u_" + String(user.id || "").slice(0, 8);
  return slug ? "_" + slug : "";
}

function selectedHelper(hass) {
  return SELECTED_HELPER + userSuffix(hass);
}

window.addEventListener(
  "hass-more-info",
  (ev) => {
    const entityId = ev.detail && ev.detail.entityId;
    if (!entityId || !entityId.startsWith(ENTITY_PREFIX)) {
      return;
    }

    const hass = getHass();
    if (!hass) {
      // Without a hass object we cannot select the target — let Home Assistant
      // open its (empty) dialog rather than swallowing the click silently.
      return;
    }

    ev.stopImmediatePropagation();
    ev.preventDefault();

    hass.callService("input_text", "set_value", {
      entity_id: selectedHelper(hass),
      value: entityId.slice(ENTITY_PREFIX.length),
    });
  },
  true
);

/*
 * <ais-user-scope> — renders ANY card config with USER_TOKEN replaced by the
 * current user's helper suffix.
 *
 * Lovelace has no way to template an entity_id per user (conditional cards and
 * flex-table-card's tap_action both need a literal entity_id), so the dashboard
 * writes e.g. `input_text.ais_selected_mmsi__AIS_USER__` and this wrapper
 * resolves it in the browser, for the user actually looking at the dashboard.
 * The card holds no state of its own — the state stays in the per-user helpers
 * on the server, so it survives a reload and follows the user across devices.
 *
 * `display: contents` is essential: the wrapper must not create a box of its
 * own, otherwise it would take part in the vertical-stack's flex column and the
 * absolutely-positioned overlays inside it would reserve height.
 */
function substitute(config, suffix) {
  if (typeof config === "string") {
    return config.split(USER_TOKEN).join(suffix);
  }
  if (Array.isArray(config)) {
    return config.map((item) => substitute(item, suffix));
  }
  if (config && typeof config === "object") {
    const out = {};
    for (const [key, value] of Object.entries(config)) {
      out[substitute(key, suffix)] = substitute(value, suffix);
    }
    return out;
  }
  return config;
}

class AisUserScope extends HTMLElement {
  setConfig(config) {
    if (!config || !config.card) {
      throw new Error("ais-user-scope: a `card` config is required");
    }
    // The bridge is a plain module deployed as-is (no build-time templating),
    // so the one numeric setting it needs — the zoom the "home" button lands
    // on — travels through this wrapper's config instead.
    if (config.home_zoom !== undefined) {
      window.__aisHomeZoom = Number(config.home_zoom);
    }
    this._config = config;
    this._card = null;
    this._suffix = null;
    this.style.display = "contents";
  }

  set hass(hass) {
    this._hass = hass;
    const suffix = userSuffix(hass);
    if (suffix !== this._suffix) {
      // The user is only known once hass arrives, so the child card is built
      // (and rebuilt, should the user ever change) here rather than in
      // setConfig.
      this._suffix = suffix;
      this._build(substitute(this._config.card, suffix));
      return;
    }
    if (this._card) {
      this._card.hass = hass;
    }
  }

  async _build(cardConfig) {
    const helpers = await window.loadCardHelpers();
    const card = helpers.createCardElement(cardConfig);
    if (this._hass) {
      card.hass = this._hass;
    }
    this.innerHTML = "";
    this.appendChild(card);
    this._card = card;
  }

  getCardSize() {
    if (this._card && this._card.getCardSize) {
      return this._card.getCardSize();
    }
    return 1;
  }
}

if (!customElements.get("ais-user-scope")) {
  customElements.define("ais-user-scope", AisUserScope);
}

/*
 * MAP ZOOM GESTURES
 * On a touch device (HA Companion) a two-finger drag UP/DOWN is delivered to
 * the page as a `wheel` event — and Leaflet's scrollWheelZoom handler turns
 * every one of those into a zoom step. So scrolling the dashboard with two
 * fingers over the map silently zoomed it, which is what "the map zoom goes
 * crazy" looked like.
 *
 * Zoom is therefore left to the map's own +/- buttons and to a real pinch
 * (Leaflet's touchZoom, which reacts to the distance BETWEEN the fingers and is
 * not touched here). The wheel event is swallowed in the CAPTURE phase on
 * `window`, i.e. before it reaches the `.leaflet-container` listener inside
 * `ha-map`'s shadow root (wheel events are composed, so they do pass by here).
 *
 * NO preventDefault: the event is only kept away from Leaflet, the browser
 * still scrolls the dashboard exactly as it would over any other card. Leaflet
 * itself calls preventDefault, so this actually restores the page scroll.
 */
const LEAFLET_CONTAINER_CLASS = "leaflet-container";

function isOverLeafletMap(ev) {
  const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  for (const node of path) {
    if (node && node.classList && node.classList.contains(LEAFLET_CONTAINER_CLASS)) {
      return true;
    }
  }
  return false;
}

window.addEventListener(
  "wheel",
  (ev) => {
    if (isOverLeafletMap(ev)) {
      ev.stopImmediatePropagation();
    }
  },
  true
);

/*
 * DISMISSING THE DETAIL CARD
 * The detail overlay could only be closed with its own Close tile, which is a
 * long way from where the finger already is. It now also closes on:
 *   * a click/tap on the MAP itself — but not on a marker (that selects another
 *     target via `hass-more-info` above) and not on Leaflet's own controls;
 *   * the Escape key, the usual "dismiss the thing on top" gesture.
 *
 * Both simply clear the per-user helper — exactly what the Close tile does —
 * so the conditional card in the dashboard hides itself. Nothing is written
 * when there is no selection, to keep pointless service calls off the bus.
 */
const LEAFLET_IGNORED_CLASSES = [
  "leaflet-marker-icon",
  "leaflet-popup",
  "leaflet-control",
  "leaflet-control-container",
];

function hasClassInPath(ev, classNames) {
  const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  for (const node of path) {
    if (!node || !node.classList) {
      continue;
    }
    for (const name of classNames) {
      if (node.classList.contains(name)) {
        return true;
      }
    }
  }
  return false;
}

function hasSelection(hass) {
  const state = hass && hass.states && hass.states[selectedHelper(hass)];
  const value = state && state.state;
  return Boolean(value) && value !== "unknown" && value !== "unavailable";
}

function clearSelection() {
  const hass = getHass();
  if (!hass || !hasSelection(hass)) {
    return;
  }
  hass.callService("input_text", "set_value", {
    entity_id: selectedHelper(hass),
    value: "",
  });
}

window.addEventListener(
  "click",
  (ev) => {
    if (!isOverLeafletMap(ev)) {
      return;
    }
    if (hasClassInPath(ev, LEAFLET_IGNORED_CLASSES)) {
      return;
    }
    clearSelection();
  },
  true
);

window.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") {
    return;
  }
  clearSelection();
});

/*
 * OUR OWN BOAT ON THE MAP
 * Our boat is an ordinary AIS target (same geo_location `source`), so on the
 * map it looked exactly like every other circle. The integration now puts a ⛵
 * glyph into its `map_label`; this block adds the second half — a double ring
 * in the accent colour around that one marker.
 *
 * WHY IN JAVASCRIPT AND NOT IN card_mod CSS: a CSS rule would have to name the
 * marker by entity id, i.e. our MMSI would have to be repeated in the
 * dashboard config, while it already lives in the `ais_targets` config entry.
 * Instead the own target is found at runtime by its `is_own_ship` attribute —
 * no duplication anywhere.
 *
 * The markers are `ha-entity-marker` elements inside `ha-map`'s shadow root,
 * so both the lookup and the <style> injection have to walk shadow roots.
 */
const OWN_SHIP_CLASS = "ais-own-ship";
const OWN_SHIP_STYLE_ID = "ais-own-ship-style";
const OWN_SHIP_SCAN_MS = 2000;
const OWN_SHIP_MAX_DEPTH = 12;
// Two rings: the inner one in the card background separates the marker from
// the accent ring, so it reads as a double outline over any map tile.
const OWN_SHIP_CSS = `
  ha-entity-marker.${OWN_SHIP_CLASS} {
    border-radius: 50%;
    box-shadow:
      0 0 0 2px var(--card-background-color, #fff),
      0 0 0 5px var(--ais-own-ship-color, var(--accent-color, #ff9800)),
      0 0 12px 3px rgba(255, 152, 0, 0.55);
  }
`;

function ownShipEntityId(hass) {
  const states = (hass && hass.states) || {};
  for (const entityId of Object.keys(states)) {
    if (!entityId.startsWith(ENTITY_PREFIX)) {
      continue;
    }
    const state = states[entityId];
    if (state && state.attributes && state.attributes.is_own_ship) {
      return entityId;
    }
  }
  return null;
}

function collectMarkers(root, out, depth) {
  if (!root || typeof root.querySelectorAll !== "function" || depth > OWN_SHIP_MAX_DEPTH) {
    return out;
  }
  for (const marker of root.querySelectorAll("ha-entity-marker")) {
    out.push(marker);
  }
  for (const el of root.querySelectorAll("*")) {
    if (el.shadowRoot) {
      collectMarkers(el.shadowRoot, out, depth + 1);
    }
  }
  return out;
}

function injectOwnShipStyle(root) {
  if (!root || typeof root.querySelector !== "function" || typeof root.appendChild !== "function") {
    return;
  }
  if (root.querySelector("#" + OWN_SHIP_STYLE_ID)) {
    return;
  }
  const style = document.createElement("style");
  style.id = OWN_SHIP_STYLE_ID;
  style.textContent = OWN_SHIP_CSS;
  root.appendChild(style);
}

function markOwnShipMarkers(root, hass) {
  const ownId = ownShipEntityId(hass);
  const markers = collectMarkers(root, [], 0);
  let marked = 0;
  for (const marker of markers) {
    if (!marker.classList || typeof marker.getAttribute !== "function") {
      continue;
    }
    if (ownId && marker.getAttribute("entity-id") === ownId) {
      injectOwnShipStyle(typeof marker.getRootNode === "function" ? marker.getRootNode() : null);
      marker.classList.add(OWN_SHIP_CLASS);
      marked += 1;
    } else {
      // The map recycles markers between updates, so a stale ring has to go.
      marker.classList.remove(OWN_SHIP_CLASS);
    }
  }
  return marked;
}

// Leaflet re-creates markers on every position update and gives us no hook for
// it, so the ring is re-applied on a slow poll (the map itself refreshes far
// less often than this).
setInterval(() => {
  try {
    markOwnShipMarkers(document, getHass());
  } catch (err) {
    /* never let a DOM hiccup break the rest of the bridge */
  }
}, OWN_SHIP_SCAN_MS);

/*
 * FOLLOWING THE SELECTION ON THE MAP
 * Picking a target in the table only opened the detail card — the vessel itself
 * could stay far outside the visible part of the map, so the selection told you
 * nothing about WHERE it is. Now every change of the per-user helper (whether it
 * came from the table, from a marker tap or from a script) makes the map:
 *   1. pan to that target's coordinates, keeping the current zoom (only zooming
 *      IN to a sane minimum when the map is fully zoomed out), and
 *   2. blink its marker a few times, so the eye catches it among the others.
 *
 * The helper is polled rather than subscribed to: `hass` is replaced wholesale
 * on every state change and there is no event we may hook without a card, while
 * a 400 ms poll of one object property costs nothing.
 *
 * `ha-map` keeps its Leaflet instance on the element (`leafletMap`, `_leafletMap`
 * in older frontends); both names are tried and a miss is simply ignored — the
 * blink still works without panning.
 */
const SELECT_POLL_MS = 400;
const BLINK_CLASS = "ais-blink";
const BLINK_STYLE_ID = "ais-blink-style";
const BLINK_MS = 2600;
// Never zoom out for the user; only pull a fully zoomed-out map closer so the
// target is actually distinguishable when it arrives in view.
const FOCUS_MIN_ZOOM = 12;
const BLINK_CSS = `
  @keyframes ais-blink-pulse {
    0%   { transform: scale(1);    filter: brightness(1); }
    50%  { transform: scale(1.35); filter: brightness(1.8); }
    100% { transform: scale(1);    filter: brightness(1); }
  }
  ha-entity-marker.${BLINK_CLASS} {
    animation: ais-blink-pulse 0.65s ease-in-out 4;
    z-index: 1000;
  }
`;

function injectStyle(root, id, css) {
  if (!root || typeof root.querySelector !== "function" || typeof root.appendChild !== "function") {
    return;
  }
  if (root.querySelector("#" + id)) {
    return;
  }
  const style = document.createElement("style");
  style.id = id;
  style.textContent = css;
  root.appendChild(style);
}

function collectLeafletMaps(root, out, depth) {
  if (!root || typeof root.querySelectorAll !== "function" || depth > OWN_SHIP_MAX_DEPTH) {
    return out;
  }
  for (const el of root.querySelectorAll("*")) {
    const map = el.leafletMap || el._leafletMap;
    if (map && typeof map.setView === "function") {
      out.push(map);
    }
    if (el.shadowRoot) {
      collectLeafletMaps(el.shadowRoot, out, depth + 1);
    }
  }
  return out;
}

function targetPosition(hass, mmsi) {
  const state = hass && hass.states && hass.states[ENTITY_PREFIX + mmsi];
  const attrs = (state && state.attributes) || {};
  const lat = attrs.latitude;
  const lon = attrs.longitude;
  if (typeof lat !== "number" || typeof lon !== "number") {
    return null;
  }
  return [lat, lon];
}

function panMapsTo(root, position) {
  let panned = 0;
  for (const map of collectLeafletMaps(root, [], 0)) {
    const current = typeof map.getZoom === "function" ? map.getZoom() : null;
    const zoom = typeof current === "number" ? Math.max(current, FOCUS_MIN_ZOOM) : FOCUS_MIN_ZOOM;
    map.setView(position, zoom, { animate: true });
    panned += 1;
  }
  return panned;
}

/*
 * THE "HOME" BUTTON — CENTRE ON OUR OWN BOAT AT A KNOWN ZOOM
 *
 * The map card's own button (`ui.panel.lovelace.cards.map.reset_focus`, the
 * last `ha-icon-button` inside `div#buttons` of `hui-map-card`'s shadow root)
 * calls `fitMap()`, i.e. it FITS ALL MARKERS. With a dozen AIS targets around
 * that means an arbitrary — usually very far out — zoom, and the card's
 * `default_zoom` is not applied at all in that case (it only ever matters when
 * the map holds 0-1 point). That is why `default_zoom` "did nothing" no matter
 * what it was set to, and why the option is now called `home_zoom`: it is OUR
 * setting, honoured by the handler below and by nothing else.
 *
 * The click is taken in the CAPTURE phase on `window`, before Lit's own
 * `@click` binding inside the shadow root, and replaced with:
 *     setView(own boat position, home_zoom)
 * The own boat is found by the `is_own_ship` attribute — the MMSI stays in the
 * `ais_targets` config entry and is not repeated anywhere on the frontend.
 * If we cannot find our boat (no position yet), the event is left alone and the
 * stock fit-all behaviour happens, which is a sane fallback.
 */
const DEFAULT_HOME_ZOOM = 14;

function homeZoom() {
  // Published by <ais-user-scope> from the dashboard config (${AIS_HOME_ZOOM}).
  const value = Number(window.__aisHomeZoom);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_HOME_ZOOM;
}

function ownShipPosition(hass) {
  const entityId = ownShipEntityId(hass);
  if (!entityId) {
    return null;
  }
  return targetPosition(hass, entityId.slice(ENTITY_PREFIX.length));
}

function isMapHomeButton(ev) {
  const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  for (const node of path) {
    if (!node || node.localName !== "ha-icon-button") {
      continue;
    }
    const parent = node.parentElement;
    if (!parent || parent.id !== "buttons") {
      continue;
    }
    // `div#buttons` holds the optional grouping toggle first and the
    // reset-focus ("home") button last, so position identifies it without
    // depending on a localised label.
    return parent.lastElementChild === node;
  }
  return false;
}

function goHome(root, hass) {
  const position = ownShipPosition(hass);
  if (!position) {
    return false;
  }
  const zoom = homeZoom();
  let moved = 0;
  for (const map of collectLeafletMaps(root, [], 0)) {
    map.setView(position, zoom, { animate: true });
    moved += 1;
  }
  return moved > 0;
}

window.addEventListener(
  "click",
  (ev) => {
    if (!isMapHomeButton(ev)) {
      return;
    }
    const hass = getHass();
    if (!hass || !goHome(document, hass)) {
      // Nothing we can centre on — let the card do its usual fit-all.
      return;
    }
    ev.stopImmediatePropagation();
    ev.preventDefault();
  },
  true
);

function blinkMarker(root, entityId) {
  let blinked = 0;
  for (const marker of collectMarkers(root, [], 0)) {
    if (!marker.classList || typeof marker.getAttribute !== "function") {
      continue;
    }
    if (marker.getAttribute("entity-id") !== entityId) {
      marker.classList.remove(BLINK_CLASS);
      continue;
    }
    injectStyle(typeof marker.getRootNode === "function" ? marker.getRootNode() : null, BLINK_STYLE_ID, BLINK_CSS);
    // Restart the animation even when the class is already there (re-picking
    // the same target must blink again).
    marker.classList.remove(BLINK_CLASS);
    marker.classList.add(BLINK_CLASS);
    setTimeout(() => marker.classList.remove(BLINK_CLASS), BLINK_MS);
    blinked += 1;
  }
  return blinked;
}

function focusSelectedTarget(root, hass, mmsi, onBlink) {
  if (!mmsi) {
    return false;
  }
  const position = targetPosition(hass, mmsi);
  if (position) {
    panMapsTo(root, position);
  }
  let notified = false;
  const blinked = () => {
    if (notified) {
      return;
    }
    notified = true;
    if (typeof onBlink === "function") {
      onBlink();
    }
  };
  // The marker may not exist yet right after the pan; a single retry covers the
  // usual case without turning this into a watcher. The callback fires after
  // that retry EVEN IF the marker was never found (a target outside the
  // rendered set, a map still loading): the fold must not depend on the blink
  // succeeding, otherwise picking a row silently leaves the table open.
  if (blinkMarker(root, ENTITY_PREFIX + mmsi)) {
    blinked();
  } else {
    setTimeout(() => {
      try {
        blinkMarker(root, ENTITY_PREFIX + mmsi);
      } catch (err) {
        /* ignore */
      }
      blinked();
    }, SELECT_POLL_MS);
  }
  return Boolean(position);
}

/*
 * FOLDING THE FULL TABLE AWAY AFTER A PICK
 * The expanded table covers most of the map, so the target we have just
 * centred on was usually hidden UNDER it. Picking a row therefore folds the
 * table back to its compact side-bar — but not abruptly: the card is first
 * animated out (it shrinks towards its top-right corner, which is where the
 * toggle sits), and only when that animation is over does the helper flip, so
 * the conditional card is removed after the motion, not in the middle of it.
 *
 * Only the FULL table is folded; the compact side-bar is left alone (it is the
 * state we are folding INTO).
 */
const EXPANDED_HELPER = "input_boolean.ais_table_expanded";
const TABLE_TAG = "flex-table-card";
const COLLAPSE_CLASS = "ais-table-collapsing";
const COLLAPSE_STYLE_ID = "ais-table-collapse-style";
const COLLAPSE_MS = 220;
const COLLAPSE_CSS = `
  @keyframes ais-table-fold {
    from { opacity: 1; transform: scale(1); }
    to   { opacity: 0; transform: scale(0.92) translateY(-8px); }
  }
  ${TABLE_TAG}.${COLLAPSE_CLASS} {
    transform-origin: top right;
    animation: ais-table-fold ${COLLAPSE_MS}ms ease-in forwards;
    pointer-events: none;
  }
`;

function expandedHelper(hass) {
  return EXPANDED_HELPER + userSuffix(hass);
}

function collectTables(root, out, depth) {
  if (!root || typeof root.querySelectorAll !== "function" || depth > OWN_SHIP_MAX_DEPTH) {
    return out;
  }
  for (const el of root.querySelectorAll(TABLE_TAG)) {
    out.push(el);
  }
  for (const el of root.querySelectorAll("*")) {
    if (el.shadowRoot) {
      collectTables(el.shadowRoot, out, depth + 1);
    }
  }
  return out;
}

/*
 * The fold class MUST NOT outlive the animation. Home Assistant does not always
 * throw the card away when the conditional turns false — it often REUSES the
 * very same `flex-table-card` element the next time the toggle goes on. A
 * left-over class then replays the fold-out immediately (`forwards` keeps
 * opacity 0 and pointer-events off), so the table looked dead on the SECOND
 * press of the toggle.
 */
function clearCollapseMarks(root) {
  let cleared = 0;
  for (const table of collectTables(root, [], 0)) {
    if (table.classList && table.classList.contains(COLLAPSE_CLASS)) {
      table.classList.remove(COLLAPSE_CLASS);
      cleared += 1;
    }
  }
  return cleared;
}

let collapseInProgress = false;

function collapseTableAfterSelect(root, hass) {
  const helper = expandedHelper(hass);
  const state = hass && hass.states && hass.states[helper];
  if (!state || state.state !== "on") {
    return false;
  }
  const tables = collectTables(root, [], 0);
  for (const table of tables) {
    if (!table.classList) {
      continue;
    }
    injectStyle(
      typeof table.getRootNode === "function" ? table.getRootNode() : null,
      COLLAPSE_STYLE_ID,
      COLLAPSE_CSS
    );
    table.classList.add(COLLAPSE_CLASS);
  }
  collapseInProgress = true;
  setTimeout(() => {
    try {
      hass.callService("input_boolean", "turn_off", { entity_id: helper });
    } catch (err) {
      /* ignore */
    }
    for (const table of tables) {
      if (table.classList) {
        table.classList.remove(COLLAPSE_CLASS);
      }
    }
    collapseInProgress = false;
  }, COLLAPSE_MS);
  return true;
}

let lastSelectedMmsi = null;
// The helper KEEPS the previous pick across reloads and view switches, so the
// very first poll would otherwise look like a fresh selection and fold the
// table the instant the user opens it. The first pass only remembers the
// current value and does nothing.
let selectionPrimed = false;

function pollSelection(root, hass) {
  const state = hass && hass.states && hass.states[selectedHelper(hass)];
  const value = (state && state.state) || "";
  const mmsi = value && value !== "unknown" && value !== "unavailable" ? value : "";
  // Defensive sweep: a table that carries the fold class while no fold is
  // running is a leftover (re-used element) and would render invisible.
  if (!collapseInProgress) {
    clearCollapseMarks(root);
  }
  if (!selectionPrimed) {
    selectionPrimed = true;
    lastSelectedMmsi = mmsi;
    return false;
  }
  if (mmsi === lastSelectedMmsi) {
    return false;
  }
  lastSelectedMmsi = mmsi;
  if (!mmsi) {
    return false;
  }
  // Fold the full table away only at the moment the marker actually starts
  // blinking: by then the map has been centred and the vessel is visible, so
  // the table is minimised as the last step of the sequence — never on a bare
  // on/off toggle.
  return focusSelectedTarget(root, hass, mmsi, () => collapseTableAfterSelect(root, hass)) || true;
}

setInterval(() => {
  try {
    pollSelection(document, getHass());
  } catch (err) {
    /* never let a DOM hiccup break the rest of the bridge */
  }
}, SELECT_POLL_MS);

console.info("%c AIS-SELECT-BRIDGE %c loaded ", "background:#0288d1;color:#fff", "");
