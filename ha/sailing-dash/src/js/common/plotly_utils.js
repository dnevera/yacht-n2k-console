/**
 * Plotly graph helper utilities for arrow vector annotations and gesture handling.
 */

/**
 * Builds vector arrow annotations for Plotly chart annotations.
 * @param {Array} xs - Array of X coordinates (timestamps).
 * @param {Array<number>} ys - Array of Y coordinates (values/speeds).
 * @param {Array<number>} dirs - Array of direction angles in degrees.
 * @param {Function} colorFn - Function mapping value to color string.
 * @param {number} [baseLen=10] - Base pixel length of the arrow.
 * @param {boolean} [addValueToLen=true] - Whether to add Y value to base arrow length.
 * @returns {Array<Object>} Array of Plotly annotation arrow objects.
 */
function toPlotlyArrows(xs, ys, dirs, colorFn, baseLen = 10, addValueToLen = true) {
  return (xs || []).map((x, i) => {
    const d = (dirs && dirs[i]) || 0;
    const rad = ((d + 180) * Math.PI) / 180;
    const len = addValueToLen ? baseLen + ((ys && ys[i]) || 0) : baseLen;
    return {
      x,
      y: ys && ys[i],
      xref: 'x',
      yref: 'y',
      ax: -len * Math.sin(rad),
      ay: len * Math.cos(rad),
      axref: 'pixel',
      ayref: 'pixel',
      showarrow: true,
      arrowhead: 2,
      arrowsize: 1,
      arrowwidth: 1.5,
      arrowcolor: colorFn ? colorFn((ys && ys[i]) || 0) : '#ffffff',
      captureevents: false,
    };
  });
}

/**
 * Returns common touch-gesture patching script string for Plotly mobile long-press tooltips.
 * @returns {string} Touch patch script code.
 */
function getPlotlyTouchPatchScript() {
  return `const patchTouch = (gd) => {
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
try { walk(document); } catch (e) {}`;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    toPlotlyArrows,
    getPlotlyTouchPatchScript,
  };
}
