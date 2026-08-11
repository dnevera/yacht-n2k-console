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
const cd=load('plotly_wind_customdata.js');
const ann=load('plotly_wind_annotations.js');
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
t('t=30 -> E 90',labels[1]==='E 90°');
t('t=60 -> S 180',labels[2]==='S 180°');

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
process.exit(ok?0:1);
