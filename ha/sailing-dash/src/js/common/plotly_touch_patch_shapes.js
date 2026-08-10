// Shared plotly-graph "$fn" body for the `layout.shapes` field.
// Patches touch gestures on every plotly-graph chart so a long-press shows the
// hover tooltip instead of triggering pan, and draws the "now" marker line.
// Injected via build.py into cards that used to duplicate this snippet.
() => {
  const patchTouch = (gd) => {
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
