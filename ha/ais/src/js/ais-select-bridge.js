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

console.info("%c AIS-SELECT-BRIDGE %c loaded ", "background:#0288d1;color:#fff", "");
