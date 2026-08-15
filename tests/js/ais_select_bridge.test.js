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

console.log(ok ? 'ALL TESTS PASSED' : 'SOME TESTS FAILED');
process.exit(ok ? 0 : 1);
