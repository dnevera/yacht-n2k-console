// Shared plotly-graph "$fn" body for the `layout.shapes` field.
// Patches touch gestures on every plotly-graph chart so a long-press shows the
// hover tooltip instead of triggering pan, and draws the "now" marker line.
// Zoom itself is NOT implemented here. The card already ships a touch
// controller (`disable_pinch_to_zoom`) that turns a two-finger pinch - and a
// double-tap-then-drag - into a SYNTHETIC `wheel` event aimed at the drag
// layer; `scrollZoom: true` (build.py's `apply_zoom_controls()`) is what makes
// Plotly act on it. Two things are patched around that:
//  - a real (`isTrusted`) wheel event is swallowed before it reaches Plotly,
//    so a desktop mouse/trackpad still cannot zoom (that input is where all
//    the "twitchy/inertia" complaints came from) while pinch still can;
//  - a pinch-derived wheel that would cross `zoom_min_hours`/`zoom_max_hours`
//    is swallowed the same way.
// A previous revision implemented pinch by hand here; it fought the card's own
// controller for the axis, which is exactly why pinch felt broken on a phone.
// Also greys out (and disables clicks on) whichever of those two buttons is
// already at its limit - see `patchZoomButtons` below.
// Injected via build.py into cards that used to duplicate this snippet.
({ getFromConfig } = {}) => {
  getFromConfig = getFromConfig || (() => undefined);
  const zoomHours = (key) => {
    const h = Number(getFromConfig(key));
    return Number.isFinite(h) && h > 0 ? h * 3600000 : null;
  };
  const MIN_MS = zoomHours('zoom_min_hours') || 0;
  const MAX_MS = zoomHours('zoom_max_hours') || Infinity;
  // `zoom_min_hours`/`zoom_max_hours` are only present on the card when
  // `forecast_min_scale`/`forecast_max_scale` are configured (see
  // `apply_zoom_controls()` in build.py); when neither is set this is a
  // no-op and nothing is patched. Reuses Plotly's own `plotly_relayout`/
  // `plotly_relayouting` events on the chart div - no polling, no DOM
  // scanning of its own.
  const patchZoomButtons = (gd) => {
    if (!gd || gd.__zoomButtonLimitPatched) return;
    const minMs = MIN_MS;
    const maxMs = MAX_MS;
    if (minMs <= 0 && maxMs === Infinity) return;
    gd.__zoomButtonLimitPatched = true;
    const setBtnState = (el, disabled) => {
      if (!el) return;
      el.style.opacity = disabled ? '0.3' : '';
      el.style.pointerEvents = disabled ? 'none' : '';
      el.style.cursor = disabled ? 'not-allowed' : '';
    };
    const TOLERANCE_MS = 1000;
    const update = () => {
      const range = (gd._fullLayout && gd._fullLayout.xaxis && gd._fullLayout.xaxis.range)
        || (gd.layout && gd.layout.xaxis && gd.layout.xaxis.range);
      if (!Array.isArray(range) || range.length !== 2) return;
      const t0 = +new Date(range[0]);
      const t1 = +new Date(range[1]);
      if (!Number.isFinite(t0) || !Number.isFinite(t1)) return;
      const width = t1 - t0;
      if (width <= 0) return;
      setBtnState(gd.querySelector('[data-title="Zoom in"]'), width <= minMs + TOLERANCE_MS);
      setBtnState(gd.querySelector('[data-title="Zoom out"]'), maxMs !== Infinity && width >= maxMs - TOLERANCE_MS);
    };
    if (typeof gd.on === 'function') {
      gd.on('plotly_relayout', update);
      gd.on('plotly_relayouting', update);
      // The very first call below runs before the card has actually plotted
      // anything (there is no `_fullLayout`/`layout.xaxis.range` yet), so it
      // is a no-op and the "-" button stayed active even when the initial
      // window was already at/over the limit. `plotly_afterplot` fires once
      // the chart has actually drawn (including the first draw), so check
      // again then.
      gd.on('plotly_afterplot', update);
    }
    update();
  };
  const patchTouch = (gd) => {
    patchZoomButtons(gd);
    if (!gd || gd.__touchGestureLongPress) return;
    gd.__touchGestureLongPress = true;
    const HOLD_MS = 400;
    const MOVE_TOL = 10;
    // `multi` stays true from the moment a second finger lands until every
    // finger is off again: while a pinch is in progress neither the tooltip
    // nor Plotly's one-finger pan may be alive.
    let timer = null, hover = false, sx = 0, sy = 0, multi = false;
    const dragLayer = () => gd.querySelector('.nsewdrag') || gd;
    let selfHover = false;
    const hoverAt = (t) => {
      const target = dragLayer();
      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };
      selfHover = true;
      try {
        target.dispatchEvent(new MouseEvent('mouseover', opts));
        target.dispatchEvent(new MouseEvent('mousemove', opts));
      } finally { selfHover = false; }
    };
    // Plotly treats a plain tap as a mouse hover and pops the tooltip up for
    // it; the tooltip is supposed to be the reward for a deliberate long
    // press, so a short tap explicitly takes it back down again.
    const unhoverAt = () => {
      const target = dragLayer();
      const opts = { bubbles: true, cancelable: true };
      selfHover = true;
      try {
        target.dispatchEvent(new MouseEvent('mouseout', opts));
        target.dispatchEvent(new MouseEvent('mouseleave', opts));
      } finally { selfHover = false; }
    };
    // After a touch sequence the browser replays it as synthetic mouse events
    // (mouseover/mousemove/click) - which is why a tooltip popped up on the
    // RELEASE of a tap or of a pinch, after `unhoverAt()` had already run.
    // Swallow those for a short window; our own long-press hover is dispatched
    // with `selfHover` set, so it still gets through.
    const MOUSE_MUTE_MS = 700;
    let muteUntil = 0;
    ['mouseover', 'mousemove', 'mouseenter', 'mouseout', 'mouseleave'].forEach((type) => {
      gd.addEventListener(type, (e) => {
        if (selfHover || Date.now() >= muteUntil) return;
        e.stopPropagation();
        if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
      }, true);
    });
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
    const abortPan = () => {
      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    };
    const inModebar = (el) => !!(el && typeof el.closest === 'function' && el.closest('.modebar'));
    const TOLERANCE_MS = 1000;
    const currentRange = () => {
      const ax = gd.layout && gd.layout.xaxis;
      const range = ax && ax.range;
      if (!Array.isArray(range) || range.length !== 2) return null;
      const t0 = +new Date(range[0]);
      const t1 = +new Date(range[1]);
      if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) return null;
      return [t0, t1];
    };
    // The card's own touch controller (see the card bundle: `touchController`,
    // capture listeners on this very div) claims a second tap that lands
    // within 250 ms as the start of its double-tap zoom gesture and
    // `stopPropagation()`s it - including taps on the modebar, which is a
    // child of the div. That is what made a burst of +/- taps feel dead.
    // These listeners sit on the PARENT, so in the capture phase they run
    // before the card's and can keep modebar taps out of its way.
    const outer = gd.parentNode || gd;
    outer.addEventListener('touchstart', (e) => {
      if (inModebar(e.target)) e.stopPropagation();
    }, true);
    // Wheel gate, also in front of Plotly's own (`scrollZoom: true`) handler.
    outer.addEventListener('wheel', (e) => {
      // A real mouse/trackpad must not zoom; no `preventDefault()` here, so
      // the page still scrolls over the chart exactly as it used to.
      if (e.isTrusted !== false) { e.stopPropagation(); e.stopImmediatePropagation(); return; }
      // Synthetic = the card's pinch. Only the configured zoom limits apply.
      const range = currentRange();
      if (!range) return;
      const width = range[1] - range[0];
      const zoomIn = e.deltaY < 0;
      if ((zoomIn && width <= MIN_MS + TOLERANCE_MS)
        || (!zoomIn && MAX_MS !== Infinity && width >= MAX_MS - TOLERANCE_MS)) {
        e.stopPropagation();
        e.stopImmediatePropagation();
      }
    }, true);
    gd.addEventListener('touchstart', (e) => {
      // The modebar (+/-/reset) lives inside `gd`, so without this guard a
      // finger resting on a button for >HOLD_MS turned the tap into a
      // long-press hover gesture and the click never happened.
      if (inModebar(e.target)) return;
      muteUntil = Date.now() + MOUSE_MUTE_MS;
      clear();
      if (e.touches.length > 1) {
        // Second finger = pinch. Plotly already started a pan on the first
        // one and, if the finger had rested there for >HOLD_MS, a tooltip is
        // up as well - both are false positives of the pinch, so take them
        // down before the card's controller starts zooming.
        multi = true;
        if (hover) unhoverAt();
        hover = false;
        abortPan();
        return;
      }
      hover = false;
      if (multi) return;
      const t = e.touches[0];
      sx = t.clientX; sy = t.clientY;
      timer = setTimeout(() => {
        timer = null;
        hover = true;
        abortPan();
        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }
        hoverAt({ clientX: sx, clientY: sy });
      }, HOLD_MS);
    }, true);
    gd.addEventListener('touchmove', (e) => {
      if (inModebar(e.target)) return;
      const t = e.touches[0];
      if (multi || e.touches.length > 1) {
        clear();
        if (hover) { unhoverAt(); hover = false; }
        return;
      }
      if (hover) {
        e.stopPropagation();
        if (e.cancelable) e.preventDefault();
        if (t) hoverAt(t);
        return;
      }
      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();
    }, true);
    const end = (e) => {
      if (e && inModebar(e.target)) return;
      const wasHover = hover;
      const pending = !!timer;
      const wasMulti = multi;
      muteUntil = Date.now() + MOUSE_MUTE_MS;
      clear();
      hover = false;
      // A finger leaving a pinch must not turn the remaining one back into a
      // tap/long-press; only a fully released hand resets the gesture.
      if (e && e.touches && e.touches.length > 0) return;
      multi = false;
      // Neither a long press nor a pinch: whatever tooltip Plotly showed for
      // the tap goes away again.
      if (wasMulti || (!wasHover && pending)) unhoverAt();
    };
    gd.addEventListener('touchend', end, true);
    gd.addEventListener('touchcancel', end, true);
  };
  const walk = (root) => {
    root.querySelectorAll('plotly-graph').forEach((el) => {
      if (el.shadowRoot) el.shadowRoot.querySelectorAll('.js-plotly-plot').forEach(patchTouch);
    });
    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  try { walk(document); } catch (e) {}
  return [{ type: 'line', xref: 'x', yref: 'paper', x0: new Date(), x1: new Date(), y0: 0, y1: 1, line: { color: '#ffffff', width: 1, dash: 'dash' } }];
}
