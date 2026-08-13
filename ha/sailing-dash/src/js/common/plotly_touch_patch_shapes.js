// Shared plotly-graph "$fn" body for the `layout.shapes` field.
// Patches touch gestures on every plotly-graph chart so a long-press shows the
// hover tooltip instead of triggering pan, and draws the "now" marker line.
// Mouse-wheel/trackpad zoom is no longer patched here - it is disabled
// altogether at the card config level (`scrollZoom: false` in build.py's
// `apply_zoom_controls()`), so there is nothing left to patch: zoom now only
// happens through the deterministic +/-/reset modebar buttons.
// Also greys out (and disables clicks on) whichever of those two buttons is
// already at its limit - see `patchZoomButtons` below.
// Injected via build.py into cards that used to duplicate this snippet.
({ getFromConfig } = {}) => {
  getFromConfig = getFromConfig || (() => undefined);
  // `zoom_min_hours`/`zoom_max_hours` are only present on the card when
  // `forecast_min_scale`/`forecast_max_scale` are configured (see
  // `apply_zoom_controls()` in build.py); when neither is set this is a
  // no-op and nothing is patched. Reuses Plotly's own `plotly_relayout`/
  // `plotly_relayouting` events on the chart div - no polling, no DOM
  // scanning of its own.
  const patchZoomButtons = (gd) => {
    if (!gd || gd.__zoomButtonLimitPatched) return;
    const minHours = Number(getFromConfig('zoom_min_hours'));
    const maxHours = Number(getFromConfig('zoom_max_hours'));
    const minMs = Number.isFinite(minHours) && minHours > 0 ? minHours * 3600000 : 0;
    const maxMs = Number.isFinite(maxHours) && maxHours > 0 ? maxHours * 3600000 : Infinity;
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
    let timer = null, hover = false, sx = 0, sy = 0;
    const hoverAt = (t) => {
      const target = gd.querySelector('.nsewdrag') || gd;
      const opts = { clientX: t.clientX, clientY: t.clientY, bubbles: true, cancelable: true };
      target.dispatchEvent(new MouseEvent('mouseover', opts));
      target.dispatchEvent(new MouseEvent('mousemove', opts));
    };
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
    const abortPan = () => {
      document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    };
    gd.addEventListener('touchstart', (e) => {
      clear();
      hover = false;
      if (e.touches.length !== 1) return;
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
      const t = e.touches[0];
      if (hover) {
        e.stopPropagation();
        if (e.cancelable) e.preventDefault();
        if (t) hoverAt(t);
        return;
      }
      if (timer && t && (Math.abs(t.clientX - sx) > MOVE_TOL || Math.abs(t.clientY - sy) > MOVE_TOL)) clear();
    }, true);
    const end = () => { clear(); hover = false; };
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
