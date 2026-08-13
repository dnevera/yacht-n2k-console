/**
 * Regression tests for the wind chart's shared JS snippets
 * (ha/sailing-dash/src/js/common/).
 *
 * These cover exactly the two defects that made the deployed wind chart show
 * nonsense while the sensors themselves were fine:
 *   1. `unknown`/`unavailable` recorder states became NaN and poisoned the
 *      resampled traces, the colour scale and the Y axis.
 *   2. wind direction was looked up positionally (`vars.dir.ys[i]`) although it
 *      comes from a separate, independently resampled entity — any gap shifted
 *      every following index, so dots/arrows showed another moment's direction.
 *
 * Run: node tests/js/wind_chart_snippets.test.js  (also driven from
 * tests/test_sailing_dash.py::test_wind_chart_js_snippets)
 */
const fs=require('fs');
const path=require('path');
const D=path.join(__dirname,'..','..','ha','sailing-dash','src','js','common')+path.sep;
// Snippets are bare JS expressions prefixed with a `//` doc comment block —
// exactly what build.py strips before inlining them into the dashboard.
const load=(n)=>{const lines=fs.readFileSync(D+n,'utf8').split('\n'); let i=0; while(lines[i].trim().startsWith('//')) i++;
  return eval('('+lines.slice(i).join('\n')+')');};
const historySeries=load('plotly_history_series.js');
const cd=load('plotly_direction_label.js');
const ann=load('plotly_chart_annotations.js');
global.document={querySelectorAll:()=>[],};
let ok=true; const t=(name,c)=>{console.log((c?'PASS':'FAIL')+' '+name); if(!c) ok=false;};

// 1. plotly_history_series: keeps finite points <= Date.now(), drops non-finite and future points
const pastTime = new Date(Date.now() - 60000).toISOString();
const futureTime = new Date(Date.now() + 60000).toISOString();
const rHistory = historySeries({xs:[pastTime, pastTime, pastTime, futureTime], ys:[1, NaN, 'unknown', 10]});
t('historySeries keeps finite <= Date.now() and drops future/non-finite', JSON.stringify(rHistory) === JSON.stringify({xs:[pastTime], ys:[1]}));

// 2. customdata: dir series shifted/shorter -> matched by time, not index
const T=(m)=>new Date(Date.UTC(2026,0,1,0,m)).toISOString();
const speed={xs:[T(0),T(30),T(60)]};
const dir={xs:[T(30),T(60)],ys:[90,180]};   // first sample missing
const labels=cd({xs:speed.xs,vars:{dir}});
t('no dir in window -> n/a',labels[0]==='direction n/a');
t('t=30 -> E 90 with flow arrow',labels[1]==='← E 90°');
t('t=60 -> S 180 with flow arrow',labels[2]==='↑ S 180°');
// The SAME snippet formats the forecast rows, so measured and forecast look
// identical in the unified tooltip (arrow glyph + cardinal point + degrees).
const fLabels=cd({meta:{forecast_dir:[0,270,'x']}});
t('forecast label carries the arrow glyph too',fLabels[0]==='↓ N 0°'&&fLabels[1]==='→ W 270°');
t('forecast without direction says n/a',fLabels[2]==='direction n/a');
const wLabels=cd({meta:{wave_direction:[45],wave_period:[6.25]}});
t('wave label: arrow + cardinal + period',wLabels[0]==='↙ NE 45° · 6.3 s');

// 3. arrows: skip points without direction / non-finite speed, angle by time
const vars={speed:{xs:[T(0),T(30),T(60)],ys:[NaN,5,10]},dir,forecastSpeed:{xs:[T(120)],ys:[7]},forecastDir:[270]};
const out=ann({vars});
const arrows=out.filter(a=>a.showarrow);
t('arrows: 2 measured (NaN point dropped) + 1 forecast',arrows.length===3);
const a=arrows[0];
t('arrow anchored at measured point y=5',a.y===5);
// dir 90 (from East) -> tail to the east (+x), ax>0
t('dir 90 -> tail east (ax>0)',a.ax>0.01&&Math.abs(a.ay)<1e-9);
const f=arrows[2];
t('forecast dir 270 -> tail west (ax<0)',f.ax<-0.01);
t('Now marker present',out.some(o=>o.text==='Now'));

// 4. arrow layout styles: one shared annotation layer serves BOTH charts and
// both styles (config.yaml's global chart_style), reading `arrow_layout`,
// `arrow_spacing_hours` and `arrow_kind` from the card config at runtime.
const cfg = (opts) => (key) => opts[key];
const onPoint = ann({ vars, getFromConfig: cfg({ arrow_layout: 'on_point' }) }).filter(a => a.showarrow);
t('on_point: arrow anchored to the data value', onPoint[0].y === 5 && onPoint[0].yref === 'y');

const topRow = ann({ vars, getFromConfig: cfg({ arrow_layout: 'top_row', measured_arrows_on_line: false }) })
  .filter(a => a.showarrow);
t('top_row: all arrows share one paper-space row', topRow.length === 3
  && topRow.every(a => a.yref === 'paper' && a.y === topRow[0].y));
t('top_row: colour still follows wind speed', topRow[1].arrowcolor !== topRow[2].arrowcolor);

// measured_arrows_on_line (default true): in the top_row style the MEASURED
// arrows stay anchored on the measured value line so the direction of the
// measured wind is unambiguous, while forecast arrows keep the top row.
const mixed = ann({ vars, getFromConfig: cfg({ arrow_layout: 'top_row' }) }).filter(a => a.showarrow);
t('measured arrows on the measured line by default in top_row',
  mixed[0].yref === 'y' && mixed[0].y === 5 && mixed[1].yref === 'y' && mixed[1].y === 10);
t('forecast arrows stay in the top row', mixed[2].yref === 'paper');
t('measured_arrows_on_line: false pushes measured arrows into the row too',
  topRow.every(a => a.yref === 'paper'));
t('plotly style unaffected by measured_arrows_on_line',
  ann({ vars, getFromConfig: cfg({ arrow_layout: 'on_point', measured_arrows_on_line: false }) })
    .filter(a => a.showarrow).every(a => a.yref === 'y'));

// The annotation anchor is the arrow HEAD and the tail is a pixel offset, so
// without centring a northerly arrow stuck a whole shaft up while an easterly
// one stayed flat: the row looked wavy / out of sync with the chart. Note the
// opposite sign conventions: `ay` is positive DOWN, `yshift` is positive UP.
const centre = (a) => a.yshift - a.ay / 2;
t('arrows are centred on their anchor horizontally',
  topRow.every(a => Math.abs(a.xshift + a.ax / 2) < 1e-9));
t('measured arrows of every direction share one row line',
  Math.abs(centre(topRow[0]) - centre(topRow[1])) < 1e-9);
t('forecast arrows land exactly on the top row line', centre(topRow[2]) === 0);
// Measured and forecast arrows sit in two separate lanes so the open-meteo
// history arrows never overlap the measured ones in the shared history zone.
t('measured arrows sit in a lower lane than the forecast ones',
  centre(topRow[0]) < centre(topRow[2]) - 30 && centre(topRow[2]) === 0);
t('arrows drawn ON the measured line get no lane offset',
  Math.abs(mixed[0].yshift - mixed[0].ay / 2) < 1e-9);

// Spacing thins the row out but keeps the first arrow of each window.
const spaced = ann({ vars, getFromConfig: cfg({ arrow_layout: 'top_row', arrow_spacing_hours: 1 }) })
  .filter(a => a.showarrow);
t('arrow_spacing_hours: 1h keeps t=30 and t=120, drops t=60', spaced.length === 2
  && spaced[0].x === T(30) && spaced[1].x === T(120));
// Measured and forecast arrows are thinned INDEPENDENTLY: a shared window
// counter let the denser measured arrows swallow every overlapping window, so
// the open-meteo forecast had no direction arrows at all left of "Now".
const overlapVars = { speed: vars.speed, dir, forecastSpeed: { xs: [T(45)], ys: [7] }, forecastDir: [270] };
const overlap = ann({ vars: overlapVars, getFromConfig: cfg({ arrow_layout: 'top_row', arrow_spacing_hours: 1 }) })
  .filter(a => a.showarrow);
t('forecast arrows survive inside the measured (history) zone',
  overlap.some(a => a.x === T(45)));

// arrow_length_scale amplifies the shaft length, which is the pixel distance
// between the anchor (x/y) and the tail (ax/ay).
const shaft = (a) => Math.hypot(a.ax, a.ay);
const scaled = (opts) => ann({ vars, getFromConfig: cfg(Object.assign({ arrow_layout: 'top_row' }, opts)) })
  .filter(a => a.showarrow);
const s1 = scaled({ arrow_length_scale: 1 });
const s5 = scaled({ arrow_length_scale: 5 });
t('arrow_length_scale amplifies the shaft length', shaft(s5[1]) > shaft(s1[1]) + 1);
t('shaft still grows with the value at a given scale', shaft(s1[1]) > shaft(s1[0]));
t('arrow_length_scale is capped so arrows stay inside the chart',
  scaled({ arrow_length_scale: 1000 }).every(a => shaft(a) <= 60.001));
t('arrow_length_scale defaults to 3',
  Math.abs(shaft(scaled({})[1]) - shaft(scaled({ arrow_length_scale: 3 })[1])) < 0.001);

t('missing getFromConfig falls back to on_point without throwing',
  ann({ vars }).filter(a => a.showarrow).length === 3);

// 5. the wave flavour of the same layer: index-aligned wave height/direction,
// its own colour scale, and the very same global layout/spacing options.
const waveVars = { waveHeight: { xs: [T(0), T(60), T(120)], ys: [0.4, NaN, 3.2] }, waveDir: [90, 180, 270] };
const waveOnPoint = ann({ vars: waveVars, getFromConfig: cfg({ arrow_kind: 'wave', arrow_layout: 'on_point' }) })
  .filter(a => a.showarrow);
t('wave: non-finite height dropped, arrow on its own value', waveOnPoint.length === 2
  && waveOnPoint[0].y === 0.4 && waveOnPoint[0].yref === 'y');
t('wave: colour follows wave height scale', waveOnPoint[0].arrowcolor !== waveOnPoint[1].arrowcolor);

const waveTopRow = ann({ vars: waveVars, getFromConfig: cfg({ arrow_kind: 'wave', arrow_layout: 'top_row', arrow_spacing_hours: 3 }) })
  .filter(a => a.showarrow);
t('wave: top_row + spacing applies the global chart style too', waveTopRow.length === 1
  && waveTopRow[0].yref === 'paper' && waveTopRow[0].x === T(0));
t('wave: Now marker present', ann({ vars: waveVars, getFromConfig: cfg({ arrow_kind: 'wave' }) })
  .some(o => o.text === 'Now'));

// 6. the "Now" label / X-axis time tick and the faded forecast history arrows.
const nowOf = (opts) => ann({ vars, getFromConfig: cfg(Object.assign({ arrow_layout: 'top_row' }, opts)) });
const nowLabel = nowOf({}).find(o => o.text === 'Now');
t('Now label is translucent by default', /^rgba\(255,255,255,0\.55\)$/.test(nowLabel.bgcolor));
t('now_label_opacity is configurable',
  nowOf({ now_label_opacity: 0.2 }).find(o => o.text === 'Now').bgcolor === 'rgba(255,255,255,0.2)');
// The tick sits on the X axis (paper y = 0) right where the dashed Now line
// crosses it and carries the current time.
const tick = nowOf({}).find(o => o.yref === 'paper' && o.y === 0);
t('time tick on the X axis under the Now line', !!tick && /^\d{1,2}:\d{2}/.test(tick.text));

// The forecast arrows of the (fixed, past) fixture all fall left of "Now", so
// they must be faded — and dropped entirely at opacity 0.
const fadedForecast = nowOf({ forecast_history_arrow_opacity: 0.4 }).filter(a => a.showarrow).pop();
t('forecast arrows left of Now are faded', /^rgba\(\d+,\d+,\d+,0\.4\)$/.test(fadedForecast.arrowcolor));
t('opacity 0 hides the forecast history arrows completely',
  nowOf({ forecast_history_arrow_opacity: 0 }).filter(a => a.showarrow).length === 2);
t('opacity 1 keeps the plain scale colour',
  /^#[0-9a-f]{6}$/i.test(nowOf({ forecast_history_arrow_opacity: 1 }).filter(a => a.showarrow).pop().arrowcolor));


// 7. measured series averaged over the grid step of the ACTIVE forecast model.
// The window is read from the forecast timestamps themselves, and a DIRECTION
// (unit "°") must be averaged as a vector: the arithmetic mean of 350° and 10°
// is 180°, i.e. the exact opposite of the correct 0°.
const avg=load('plotly_measured_average.js');
const hass1h={states:{'sensor.wind_forecast_flat':{attributes:{forecast_time:['2026-01-01T00:00','2026-01-01T01:00','2026-01-01T02:00']}}}};
const hass3h={states:{'sensor.wind_forecast_flat':{attributes:{forecast_time:['2026-01-01T00:00','2026-01-01T03:00']}}}};
const dirSeries={xs:[T(0),T(10),T(20),T(70)],ys:[350,10,'unknown',180]};
const avgDir=avg(Object.assign({meta:{unit_of_measurement:'°'}},{hass:hass1h},dirSeries));
t('direction averaged as a vector, not arithmetically',
  avgDir.ys.length===2 && Math.abs(avgDir.ys[0])<1e-6 && Math.abs(avgDir.ys[1]-180)<1e-6);
t('one point per model step, anchored at the bucket centre',
  new Date(avgDir.xs[0]).getTime()===new Date(T(30)).getTime());
const avgSpeed=avg(Object.assign({meta:{unit_of_measurement:'kts'}},{hass:hass1h},{xs:[T(0),T(30)],ys:[4,6]}));
t('speed averaged arithmetically', avgSpeed.ys.length===1 && Math.abs(avgSpeed.ys[0]-5)<1e-6);
t('window follows the model step (3h model merges into one bucket)',
  avg(Object.assign({meta:{}},{hass:hass3h},{xs:[T(0),T(70)],ys:[4,6]})).ys.length===1);
t('no forecast loaded falls back to a 1h window',
  avg(Object.assign({meta:{}},{hass:{states:{}}},{xs:[T(0),T(70)],ys:[4,6]})).ys.length===2);

// 8. plotly_touch_patch_shapes.js no longer touches wheel events at all -
// wheel/trackpad/pinch zoom is disabled entirely at the card config level
// (`scrollZoom: false`), so the snippet must not install any wheel listener
// or leave behind the old per-chart wheel-patch marker.
class FakeGd extends EventTarget {
  constructor() {
    super();
    this.layout = { xaxis: { range: [] } };
    this._handlers = {};
  }
  querySelector(sel) {
    if (sel === '[data-title="Zoom in"]') return this.zoomInBtn || (this.zoomInBtn = { style: {} });
    if (sel === '[data-title="Zoom out"]') return this.zoomOutBtn || (this.zoomOutBtn = { style: {} });
    return this;
  }
  on(event, cb) { (this._handlers[event] = this._handlers[event] || []).push(cb); }
  emit(event) { (this._handlers[event] || []).forEach((cb) => cb()); }
}
const gd = new FakeGd();
const shadowRoot = { querySelectorAll: (sel) => (sel === '.js-plotly-plot' ? [gd] : []) };
const plotlyGraphEl = { shadowRoot };
global.document = {
  querySelectorAll: (sel) => (sel === 'plotly-graph' ? [plotlyGraphEl] : []),
};
const touchPatch = load('plotly_touch_patch_shapes.js');
touchPatch({ getFromConfig: () => undefined });
t('touch patch no longer installs a wheel-gesture patch', gd.__wheelGesturePatched === undefined);
t('long-press touch patch is still installed', gd.__touchGestureLongPress === true);
t('no zoom-limit config -> button-disable patch is not installed', gd.__zoomButtonLimitPatched === undefined);

// 9. plotly_touch_patch_shapes.js: the +/- modebar buttons must grey out
// (and stop accepting clicks) exactly when the current window width has
// reached the configured zoom_min_hours/zoom_max_hours - and re-enable once
// the user zooms/pans back away from the limit.
const gd2 = new FakeGd();
const shadowRoot2 = { querySelectorAll: (sel) => (sel === '.js-plotly-plot' ? [gd2] : []) };
global.document = {
  querySelectorAll: (sel) => (sel === 'plotly-graph' ? [{ shadowRoot: shadowRoot2 }] : []),
};
const zoomLimits = { zoom_min_hours: 1, zoom_max_hours: 24 };
load('plotly_touch_patch_shapes.js')({ getFromConfig: (k) => zoomLimits[k] });
t('zoom-limit config -> button-disable patch is installed', gd2.__zoomButtonLimitPatched === true);
const t0ms = new Date(T(0)).getTime();
gd2.layout.xaxis.range = [T(0), new Date(t0ms + 30 * 60000).toISOString()]; // 30 min < 1h min
gd2.emit('plotly_relayout');
t('zoom-in button disabled at the minimum span', gd2.zoomInBtn.style.pointerEvents === 'none');
t('zoom-out button enabled well within bounds', gd2.zoomOutBtn.style.pointerEvents !== 'none');
gd2.layout.xaxis.range = [T(0), new Date(t0ms + 25 * 3600000).toISOString()]; // 25h > 24h max
gd2.emit('plotly_relayout');
t('zoom-out button disabled at the maximum span', gd2.zoomOutBtn.style.pointerEvents === 'none');
t('zoom-in button re-enabled once away from the minimum', gd2.zoomInBtn.style.pointerEvents !== 'none');

// 10. plotly_zoom_step_buttons.js: custom zoomIn2d/zoomOut2d/resetScale2d
// for `config.modeBarButtons`. A click resizes `gd.layout.xaxis.range` by a
// gentler factor than Plotly's own hardcoded x0.5/x2, then drives the card
// through its own public `enterBrowsingMode()`/`plot()` API - no resize/
// snap-back once a configured zoom_min_hours/zoom_max_hours limit is hit,
// the click is simply ignored on that side. The reset button clicks the
// same hidden `button#reset` the dblclick handler uses.
const zoomStepButtons = load('plotly_zoom_step_buttons.js');
const makeGd = (rangeIso, resetHidden) => {
  const resetBtn = { classList: { contains: () => !!resetHidden }, clicked: 0, click() { this.clicked++; } };
  const host = {
    browsing: false,
    plotCalls: [],
    enterBrowsingMode() { this.browsing = true; },
    plot(opts) { this.plotCalls.push(opts); },
    shadowRoot: { querySelector: (sel) => (sel === 'button#reset' ? resetBtn : null) },
  };
  return {
    layout: { xaxis: { range: rangeIso.slice() } },
    getRootNode: () => ({ host }),
    host,
    resetBtn,
  };
};
const buttons = zoomStepButtons({ getFromConfig: () => undefined })[0];
t('exposes exactly zoomIn2d/zoomOut2d/resetScale2d', buttons.map(b => b.name).join(',') === 'zoomIn2d,zoomOut2d,resetScale2d');

const g1 = makeGd([T(0), new Date(new Date(T(0)).getTime() + 4 * 3600000).toISOString()]);
const widthBefore = new Date(g1.layout.xaxis.range[1]) - new Date(g1.layout.xaxis.range[0]);
buttons[0].click(g1); // zoomIn2d
const widthAfterIn = new Date(g1.layout.xaxis.range[1]) - new Date(g1.layout.xaxis.range[0]);
t('zoom in shrinks the window by a gentler factor than native x0.5',
  Math.abs(widthAfterIn / widthBefore - 0.8) < 1e-6);
t('zoom in drives the card via enterBrowsingMode + plot({should_fetch:true})',
  g1.host.browsing === true && g1.host.plotCalls.length === 1 && g1.host.plotCalls[0].should_fetch === true);

const g2 = makeGd([T(0), new Date(new Date(T(0)).getTime() + 4 * 3600000).toISOString()]);
const widthBefore2 = new Date(g2.layout.xaxis.range[1]) - new Date(g2.layout.xaxis.range[0]);
buttons[1].click(g2); // zoomOut2d
const widthAfterOut = new Date(g2.layout.xaxis.range[1]) - new Date(g2.layout.xaxis.range[0]);
t('zoom out widens the window by a gentler factor than native x2',
  Math.abs(widthAfterOut / widthBefore2 - 1.25) < 1e-6);

const limits = { zoom_min_hours: 1, zoom_max_hours: 24 };
const limitedButtons = zoomStepButtons({ getFromConfig: (k) => limits[k] })[0];
const gAtMin = makeGd([T(0), new Date(new Date(T(0)).getTime() + 60 * 60000).toISOString()]); // exactly 1h
const rangeBeforeMin = gAtMin.layout.xaxis.range.slice();
limitedButtons[0].click(gAtMin);
t('zoom-in click at the configured minimum is a no-op (no resize/snap-back)',
  JSON.stringify(gAtMin.layout.xaxis.range) === JSON.stringify(rangeBeforeMin) && gAtMin.host.plotCalls.length === 0);
const gAtMax = makeGd([T(0), new Date(new Date(T(0)).getTime() + 24 * 3600000).toISOString()]); // exactly 24h
const rangeBeforeMax = gAtMax.layout.xaxis.range.slice();
limitedButtons[1].click(gAtMax);
t('zoom-out click at the configured maximum is a no-op (no resize/snap-back)',
  JSON.stringify(gAtMax.layout.xaxis.range) === JSON.stringify(rangeBeforeMax) && gAtMax.host.plotCalls.length === 0);

const gReset = makeGd([T(0), T(60)]);
buttons[2].click(gReset); // resetScale2d
t('reset button clicks the same hidden reset button the dblclick handler uses', gReset.resetBtn.clicked === 1);
const gResetHidden = makeGd([T(0), T(60)], true);
buttons[2].click(gResetHidden);
t('reset button does nothing while the card reset button is hidden', gResetHidden.resetBtn.clicked === 0);

process.exit(ok?0:1);
