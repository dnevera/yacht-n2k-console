/**
 * Regression tests for ha/ais/src/js/ais-select-bridge.js.
 *
 * Covered:
 *   1. a click on an AIS marker selects the target instead of opening HA's
 *      empty geo_location more-info dialog;
 *   2. a `wheel` over the map (which is what a two-finger up/down drag becomes
 *      on a phone) never reaches Leaflet, so the map stops zooming by itself —
 *      and the page keeps scrolling, i.e. preventDefault is NOT called.
 *
 * Run: node tests/js/ais_select_bridge.test.js  (also driven from
 * tests/test_ais_dashboard.py)
 */
const fs = require('fs');
const path = require('path');

let ok = true;
const t = (name, c) => { console.log((c ? 'PASS' : 'FAIL') + ' ' + name); if (!c) ok = false; };

// ── Minimal browser stubs: only what the module actually touches ────────────
const listeners = {};
const calls = [];
const hass = {
  user: { name: 'Denn', id: 'abcdef0123' },
  callService: (domain, service, data) => calls.push([domain, service, data]),
};
global.window = { addEventListener: (type, fn, capture) => { (listeners[type] = listeners[type] || []).push({ fn, capture }); } };
global.document = { querySelector: (sel) => (sel === 'home-assistant' ? { hass } : null) };
global.customElements = { get: () => undefined, define: () => {} };
global.HTMLElement = class {};
global.console.info = () => {};

const SRC = path.join(__dirname, '..', '..', 'ha', 'ais', 'src', 'js', 'ais-select-bridge.js');
eval(fs.readFileSync(SRC, 'utf8'));

const fire = (type, ev) => listeners[type].forEach((l) => l.fn(ev));

// 1. marker click -> per-user helper gets the MMSI, HA's dialog is cancelled
let stopped = false, prevented = false;
fire('hass-more-info', {
  detail: { entityId: 'geo_location.ais_244123456' },
  stopImmediatePropagation: () => { stopped = true; },
  preventDefault: () => { prevented = true; },
});
t('marker click cancels the empty more-info dialog', stopped && prevented);
t('marker click writes the MMSI into the per-user helper',
  JSON.stringify(calls[0]) === JSON.stringify(['input_text', 'set_value',
    { entity_id: 'input_text.ais_selected_mmsi_denn', value: '244123456' }]));

// A non-AIS entity is left completely alone.
let otherStopped = false;
fire('hass-more-info', {
  detail: { entityId: 'sensor.wind_speed' },
  stopImmediatePropagation: () => { otherStopped = true; },
  preventDefault: () => {},
});
t('other entities keep their normal more-info dialog', !otherStopped && calls.length === 1);

// 2. wheel gate: every listener registered in the capture phase
t('wheel is intercepted in the capture phase', listeners.wheel[0].capture === true);

const wheelEv = (path) => {
  const ev = { stopped: false, prevented: false };
  ev.composedPath = () => path;
  ev.stopImmediatePropagation = () => { ev.stopped = true; };
  ev.preventDefault = () => { ev.prevented = true; };
  return ev;
};
const mapPath = [
  { classList: { contains: (c) => c === 'leaflet-marker-icon' } },
  { classList: { contains: (c) => c === 'leaflet-container' } },
  { /* shadow root: no classList at all */ },
];
const overMap = wheelEv(mapPath);
fire('wheel', overMap);
t('wheel over the map never reaches Leaflet (no self-zoom)', overMap.stopped);
t('wheel over the map still scrolls the page (no preventDefault)', !overMap.prevented);

const overTable = wheelEv([{ classList: { contains: () => false } }]);
fire('wheel', overTable);
t('wheel outside the map is untouched', !overTable.stopped && !overTable.prevented);

// A synthetic event without composedPath must not throw.
const bare = { stopImmediatePropagation: () => {}, preventDefault: () => {} };
let threw = false;
try { fire('wheel', bare); } catch (e) { threw = true; }
t('wheel without composedPath does not throw', !threw);

// 3. dismissing the detail card: tap on the map / Escape clear the helper
const HELPER = 'input_text.ais_selected_mmsi_denn';
hass.states = { [HELPER]: { state: '244123456' } };
calls.length = 0;

const clickEv = (path) => {
  const ev = {};
  ev.composedPath = () => path;
  ev.stopImmediatePropagation = () => {};
  ev.preventDefault = () => {};
  return ev;
};
const cleared = () => JSON.stringify(calls[calls.length - 1]) ===
  JSON.stringify(['input_text', 'set_value', { entity_id: HELPER, value: '' }]);

fire('click', clickEv(mapPath));
t('click on a marker does not clear the selection', calls.length === 0);

fire('click', clickEv([{ classList: { contains: (c) => c === 'leaflet-container' } }]));
t('click on the map closes the detail card', calls.length === 1 && cleared());

hass.states[HELPER].state = '';
fire('click', clickEv([{ classList: { contains: (c) => c === 'leaflet-container' } }]));
t('click on the map with nothing selected writes nothing', calls.length === 1);

hass.states[HELPER].state = '244123456';
fire('click', clickEv([{ classList: { contains: () => false } }]));
t('click outside the map is ignored', calls.length === 1);

fire('keydown', { key: 'Escape' });
t('Escape closes the detail card', calls.length === 2 && cleared());

fire('keydown', { key: 'a' });
t('other keys are ignored', calls.length === 2);

hass.states[HELPER].state = 'unknown';
fire('keydown', { key: 'Escape' });
t('Escape with no selection writes nothing', calls.length === 2);

// 4. own boat highlight: found by the `is_own_ship` attribute, NOT by a MMSI
//    hardcoded in the dashboard (it already lives in the integration config).
const OWN = 'geo_location.ais_244111111';
const OTHER = 'geo_location.ais_244999999';
hass.states[OWN] = { attributes: { is_own_ship: true } };
hass.states[OTHER] = { attributes: { is_own_ship: false } };

const fakeMarker = (entityId) => {
  const classes = new Set();
  return {
    classes,
    getAttribute: (n) => (n === 'entity-id' ? entityId : null),
    getRootNode: () => shadow,
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
  };
};
const ownMarker = fakeMarker(OWN);
const otherMarker = fakeMarker(OTHER);
const shadow = {
  styles: [],
  querySelectorAll: (sel) => (sel === 'ha-entity-marker' ? [ownMarker, otherMarker] : []),
  querySelector: (sel) => shadow.styles.find((s) => '#' + s.id === sel) || null,
  appendChild: (el) => shadow.styles.push(el),
};
global.document.createElement = () => ({});

t('own boat marker gets the highlight class', markOwnShipMarkers(shadow, hass) === 1 && ownMarker.classes.has('ais-own-ship'));
t('other targets stay plain', !otherMarker.classes.has('ais-own-ship'));
t('the ring stylesheet is injected once', shadow.styles.length === 1 && shadow.styles[0].id === 'ais-own-ship-style');

markOwnShipMarkers(shadow, hass);
t('a second pass injects no duplicate stylesheet', shadow.styles.length === 1);

// Own boat gone from the state machine -> the stale ring is dropped.
delete hass.states[OWN].attributes.is_own_ship;
markOwnShipMarkers(shadow, hass);
t('a stale highlight is removed when the own target is gone', !ownMarker.classes.has('ais-own-ship'));

// 5. picking a target in the table pans the map to it and blinks its marker
const SEL = '244999999';
const selMarker = fakeMarker('geo_location.ais_' + SEL);
const views = [];
const leafletMap = {
  getZoom: () => 8,
  setView: (pos, zoom) => views.push([pos, zoom]),
};
const mapHost = { leafletMap };
const mapRoot = {
  styles: [],
  querySelectorAll: (sel) => (sel === 'ha-entity-marker' ? [selMarker] : [mapHost]),
  querySelector: (sel) => mapRoot.styles.find((s) => '#' + s.id === sel) || null,
  appendChild: (el) => mapRoot.styles.push(el),
};
selMarker.getRootNode = () => mapRoot;

hass.states['geo_location.ais_' + SEL] = { attributes: { latitude: 43.1, longitude: 16.4 } };
t('the selected target pans the map, zooming in but never out',
  focusSelectedTarget(mapRoot, hass, SEL) &&
  views.length === 1 && views[0][0][0] === 43.1 && views[0][1] === 12);
t('the selected marker blinks', selMarker.classes.has('ais-blink'));
t('the blink stylesheet is injected once',
  mapRoot.styles.length === 1 && mapRoot.styles[0].id === 'ais-blink-style');

// No coordinates yet -> no pan, and nothing throws.
hass.states['geo_location.ais_' + SEL] = { attributes: {} };
t('a target without coordinates does not move the map',
  focusSelectedTarget(mapRoot, hass, SEL) === false && views.length === 1);

// The very first pass only PRIMES the state: a selection left over from a
// previous session must not act as a fresh pick (that folded the table away
// the moment the user opened it).
hass.states[HELPER].state = SEL;
t('the first poll only primes the remembered selection', pollSelection(mapRoot, hass) === false);
t('the primed selection is not re-focused', pollSelection(mapRoot, hass) === false);

// The poll only reacts to a CHANGE of the helper.
hass.states[HELPER].state = '';
pollSelection(mapRoot, hass);
hass.states[HELPER].state = SEL;
t('a new selection is picked up by the poll', pollSelection(mapRoot, hass) === true);
t('an unchanged selection re-focuses nothing', pollSelection(mapRoot, hass) === false);
hass.states[HELPER].state = '';
t('clearing the selection focuses nothing', pollSelection(mapRoot, hass) === false);

// 6. picking a target folds the FULL table away — animated, and only when it
//    is actually expanded.
const EXPANDED = 'input_boolean.ais_table_expanded_denn';
const tableClasses = new Set();
const tableEl = {
  classList: { add: (c) => tableClasses.add(c), remove: (c) => tableClasses.delete(c), contains: (c) => tableClasses.has(c) },
  getRootNode: () => tableRoot,
};
const tableRoot = {
  styles: [],
  querySelectorAll: (sel) => (sel === 'flex-table-card' ? [tableEl] : []),
  querySelector: (sel) => tableRoot.styles.find((s) => '#' + s.id === sel) || null,
  appendChild: (el) => tableRoot.styles.push(el),
};

calls.length = 0;
hass.states[EXPANDED] = { state: 'off' };
t('a collapsed table is left alone', collapseTableAfterSelect(tableRoot, hass) === false && calls.length === 0);

hass.states[EXPANDED] = { state: 'on' };
const realTimeout = global.setTimeout;
const pending = [];
global.setTimeout = (fn) => { pending.push(fn); return 0; };
t('the expanded table is folded', collapseTableAfterSelect(tableRoot, hass) === true);
t('the fold-out animation runs before the state flips',
  tableClasses.has('ais-table-collapsing') && calls.length === 0);
t('the fold stylesheet is injected once',
  tableRoot.styles.length === 1 && tableRoot.styles[0].id === 'ais-table-collapse-style');
pending.forEach((fn) => fn());
t('the toggle is switched off after the animation',
  JSON.stringify(calls[calls.length - 1]) ===
    JSON.stringify(['input_boolean', 'turn_off', { entity_id: EXPANDED }]));
t('the fold class does not outlive the animation', !tableClasses.has('ais-table-collapsing'));
global.setTimeout = realTimeout;

// A leftover fold class on a REUSED card (HA does not always destroy it) made
// the table invisible on the next `on` — the poll sweeps it away.
tableClasses.add('ais-table-collapsing');
pollSelection(tableRoot, hass);
t('a leftover fold class is swept on the next poll', !tableClasses.has('ais-table-collapsing'));

console.log(ok ? 'ALL TESTS PASSED' : 'SOME TESTS FAILED');
process.exit(ok ? 0 : 1);
