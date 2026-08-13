// Shared plotly-graph "$fn" body for `config.modeBarButtons`.
// Replaces Plotly's built-in zoomIn2d/zoomOut2d/resetScale2d with custom
// buttons that keep the exact same look (icons copied verbatim from the
// bundled Plotly.js so they render identically - this bundle does not
// expose the `Plotly` module as a global, so `Plotly.Icons` is not
// reachable from here) but fix two long-standing complaints:
//  - Plotly hardcodes the zoom step at x0.5 (in)/x2 (out) per click, which
//    is too coarse - IN_FACTOR/OUT_FACTOR below are a gentler step.
//  - the native resetScale2d restores the range Plotly captured once at
//    mount time, which does not track "now" - after the dashboard has been
//    open a while it visibly jumped to a stale window instead of doing a
//    proper reset. The custom reset button instead clicks the SAME hidden
//    `button#reset` the card's own `on_dblclick` handler uses
//    (`plotly_reset_on_dblclick.js`), so both behave identically.
//
// Zoom in/out mutate the plot div's own `layout.xaxis.range` - exactly what
// the card's `getVisibleRange()` reads back - then call the card's own
// public `enterBrowsingMode()`/`plot()`, the same mechanism its native
// zoom/pan already goes through. No `Plotly.relayout` call is needed.
//
// `zoom_min_hours`/`zoom_max_hours` (set by `apply_zoom_controls()` in
// build.py whenever `forecast_min_scale`/`forecast_max_scale` is configured)
// only gate whether a click does anything: once the window is already at or
// past a limit, that side's click is a no-op - there is no resize/snap-back
// any more. `patchZoomButtons()` in `plotly_touch_patch_shapes.js` is what
// greys the button out, so this is rarely even reachable.
({ getFromConfig }) => {
  const minHours = Number(getFromConfig('zoom_min_hours'));
  const maxHours = Number(getFromConfig('zoom_max_hours'));
  const minMs = Number.isFinite(minHours) && minHours > 0 ? minHours * 3600000 : 0;
  const maxMs = Number.isFinite(maxHours) && maxHours > 0 ? maxHours * 3600000 : Infinity;
  const TOLERANCE_MS = 1000;
  // Plotly's own zoomIn2d/zoomOut2d hardcode a x0.5/x2 step per click.
  const IN_FACTOR = 0.8;
  const OUT_FACTOR = 1.25;
  const ICONS = {
    zoom_plus: { width: 875, height: 1000, path: 'm1 787l0-875 875 0 0 875-875 0z m687-500l-187 0 0-187-125 0 0 187-188 0 0 125 188 0 0 187 125 0 0-187 187 0 0-125z', transform: 'matrix(1 0 0 -1 0 850)' },
    zoom_minus: { width: 875, height: 1000, path: 'm0 788l0-876 875 0 0 876-875 0z m688-500l-500 0 0 125 500 0 0-125z', transform: 'matrix(1 0 0 -1 0 850)' },
    home: { width: 928.6, height: 1000, path: 'm786 296v-267q0-15-11-26t-25-10h-214v214h-143v-214h-214q-15 0-25 10t-11 26v267q0 1 0 2t0 2l321 264 321-264q1-1 1-4z m124 39l-34-41q-5-5-12-6h-2q-7 0-12 3l-386 322-386-322q-7-4-13-4-7 2-12 7l-35 41q-4 5-3 13t6 12l401 334q18 15 42 15t43-15l136-114v109q0 8 5 13t13 5h107q8 0 13-5t5-13v-227l122-102q5-5 6-12t-4-13z', transform: 'matrix(1 0 0 -1 0 850)' },
  };
  const currentRange = (gd) => {
    const ax = gd && gd.layout && gd.layout.xaxis;
    const range = ax && ax.range;
    if (!Array.isArray(range) || range.length !== 2) return null;
    const t0 = +new Date(range[0]);
    const t1 = +new Date(range[1]);
    if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) return null;
    return [t0, t1];
  };
  const zoom = (gd, factor) => {
    const range = currentRange(gd);
    if (!range) return;
    const width = range[1] - range[0];
    if (factor < 1 && width <= minMs + TOLERANCE_MS) return;
    if (factor > 1 && maxMs !== Infinity && width >= maxMs - TOLERANCE_MS) return;
    const center = (range[0] + range[1]) / 2;
    const newWidth = width * factor;
    gd.layout.xaxis.range = [
      new Date(center - newWidth / 2).toISOString(),
      new Date(center + newWidth / 2).toISOString(),
    ];
    const host = typeof gd.getRootNode === 'function' && gd.getRootNode().host;
    if (host && typeof host.enterBrowsingMode === 'function') host.enterBrowsingMode();
    if (host && typeof host.plot === 'function') host.plot({ should_fetch: true });
  };
  const reset = (gd) => {
    const host = typeof gd.getRootNode === 'function' && gd.getRootNode().host;
    const btn = host && host.shadowRoot && host.shadowRoot.querySelector('button#reset');
    if (btn && !btn.classList.contains('hidden')) btn.click();
  };
  return [[
    { name: 'zoomIn2d', title: 'Zoom in', icon: ICONS.zoom_plus, click: (gd) => zoom(gd, IN_FACTOR) },
    { name: 'zoomOut2d', title: 'Zoom out', icon: ICONS.zoom_minus, click: (gd) => zoom(gd, OUT_FACTOR) },
    { name: 'resetScale2d', title: 'Reset', icon: ICONS.home, click: (gd) => reset(gd) },
  ]];
}
