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

function getHass() {
  const root = document.querySelector("home-assistant");
  return root && root.hass ? root.hass : null;
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
      entity_id: SELECTED_HELPER,
      value: entityId.slice(ENTITY_PREFIX.length),
    });
  },
  true
);

console.info("%c AIS-SELECT-BRIDGE %c loaded ", "background:#0288d1;color:#fff", "");
