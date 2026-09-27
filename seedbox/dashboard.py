"""Self-contained HTML dashboard: data embedded as JSON, rendered client-side.

No network access needed to open it; every value is inserted with textContent,
never as HTML.
"""

import json

from seedbox import __version__

CSS = """
:root{
  --bg:#f6f6f4; --panel:#ffffff; --line:#dcdcd6; --text:#1d2023; --muted:#666c73;
  --ok:#2f8a5b; --warn:#b7801b; --ko:#c0463a; --accent:#3f6d95; --fill:rgba(63,109,149,.14);
  color-scheme:light;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#16191c; --panel:#1d2125; --line:#2d3339; --text:#e7e5e0; --muted:#8e959c;
    --ok:#4ea87a; --warn:#d9a441; --ko:#d4665a; --accent:#6f9cc4; --fill:rgba(111,156,196,.18);
    color-scheme:dark;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-variant-numeric:tabular-nums}
.wrap{max-width:1180px;margin:0 auto;padding:32px 16px 72px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
h2{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin:36px 0 12px}
.sub,.muted{color:var(--muted);font-size:13px}
.kpi{display:flex;align-items:baseline;gap:14px;margin:24px 0 6px}
.kpi b{font-size:56px;font-weight:600;letter-spacing:-1px;line-height:1}
.gauge{display:flex;height:20px;border-radius:3px;overflow:hidden;border:1px solid var(--line);margin:14px 0 8px}
.legend{display:flex;flex-wrap:wrap;gap:18px;color:var(--muted);font-size:13px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-weight:600;color:var(--muted);font-size:12px;padding:8px 10px;
  border-bottom:1px solid var(--line);white-space:nowrap;user-select:none}
th[data-sort]{cursor:pointer} th[data-sort]:hover{color:var(--text)}
td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
td.num,th.num{text-align:right;white-space:nowrap}
tr:hover td{background:var(--panel)}
.bar{height:8px;background:var(--line);border-radius:2px;overflow:hidden;min-width:80px}
.bar i{display:block;height:100%;background:var(--accent)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px}
input,select,button{font:inherit;font-size:13px;background:var(--panel);color:var(--text);
  border:1px solid var(--line);border-radius:4px;padding:6px 11px}
input[type=search]{flex:1;min-width:180px}
button{color:var(--muted);cursor:pointer}
button[aria-pressed=true]{color:var(--text);border-color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
ul.warn{margin:0;padding-left:18px} ul.warn li{margin:2px 0}
details summary{cursor:pointer;color:var(--muted);font-size:13px}
.foot{color:var(--muted);font-size:12px;margin-top:36px;border-top:1px solid var(--line);padding-top:14px}
@media(max-width:700px){.kpi b{font-size:44px} .opt{display:none}}
"""

JS = r"""
var D = JSON.parse(document.getElementById('data').textContent);
var H = JSON.parse(document.getElementById('history').textContent);
var COLOR = {seeded:'var(--ok)', incomplete:'var(--warn)', orphan:'var(--ko)'};
var LABEL = {seeded:'seeded', incomplete:'incomplete', orphan:'on no tracker'};
var GIB = Math.pow(1024,3), TIB = Math.pow(1024,4);

function el(tag, attrs, kids){
  var n = document.createElement(tag);
  for (var k in (attrs||{})) {
    if (k === 'text') n.textContent = attrs[k];
    else if (k === 'style') n.style.cssText = attrs[k];
    else n.setAttribute(k, attrs[k]);
  }
  (kids||[]).forEach(function(c){ if (c) n.appendChild(c); });
  return n;
}
function $(id){ return document.getElementById(id); }
function fix(v, d){ return Number(v).toFixed(d); }
function names(keys){
  var byKey = {}; D.trackers.forEach(function(t){ byKey[t.key] = t.name; });
  return keys.map(function(k){ return byKey[k] || k; });
}

var S = D.summary, total = Math.max(S.entries, 1);
$('sub').textContent = S.entries + ' entries · ' + fix(S.size/TIB,2) + ' TiB on disk · '
  + fix(S.uploaded/TIB,2) + ' TiB uploaded · ' + S.torrents + ' torrents · generated '
  + new Date(D.generated).toLocaleString();
$('coverage').textContent = fix(S.coverage_pct, 0) + ' %';
['seeded','incomplete','orphan'].forEach(function(k){
  if (S[k]) $('gauge').appendChild(el('i', {style:'width:'+(S[k]/total*100)+'%;background:'+COLOR[k]}));
  $('legend').appendChild(el('span', {}, [el('i',{'class':'dot',style:'background:'+COLOR[k]}),
    document.createTextNode(S[k] + ' ' + LABEL[k])]));
});

// Trackers
var tb = $('trackers');
D.trackers.forEach(function(t){
  var pct = t.entries / total * 100, state, color;
  if (!t.in_prowlarr) { state = 'not in Prowlarr'; color = 'var(--warn)'; }
  else if (!t.enabled) { state = 'disabled'; color = 'var(--muted)'; }
  else if (t.failing) { state = 'failing'; color = 'var(--ko)'; }
  else if (!t.entries) { state = 'nothing seeded'; color = 'var(--warn)'; }
  else { state = 'ok'; color = 'var(--ok)'; }
  tb.appendChild(el('tr', {}, [
    el('td', {}, [el('i',{'class':'dot',style:'background:'+color}), document.createTextNode(t.name)]),
    el('td', {'class':'muted', text: state}),
    el('td', {'class':'num', text: t.entries}),
    el('td', {}, [el('div',{'class':'bar'},[el('i',{style:'width:'+pct+'%'})])]),
    el('td', {'class':'num', text: fix(pct,0)+' %'}),
    el('td', {'class':'num opt', text: fix(t.uploaded/TIB,2)}),
    el('td', {'class':'num opt', text: t.in_prowlarr ? t.grabs : '—'})
  ]));
});
if (!D.trackers.length) tb.appendChild(el('tr',{},[el('td',{colspan:7,'class':'muted',text:'No tracker found.'})]));
if (!S.prowlarr) $('noprowlarr').hidden = false;

// Warnings
if (D.warnings.length) {
  $('warnings-block').hidden = false;
  D.warnings.forEach(function(w){ $('warnings').appendChild(el('li',{text:w})); });
}

// History
(function(){
  var box = $('history-chart');
  if (H.length < 2) { box.appendChild(el('p',{'class':'muted',text:'The curve appears from the second run.'})); return; }
  var W = 980, h = 110, ns = 'http://www.w3.org/2000/svg';
  var vals = H.map(function(r){ return parseFloat(r.coverage_pct) || 0; });
  var step = W / (vals.length - 1);
  var pts = vals.map(function(v,i){ return fix(i*step,1)+','+fix(h - v/100*(h-18) - 9,1); }).join(' ');
  var svg = document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 '+W+' '+h); svg.setAttribute('width','100%'); svg.setAttribute('height',h);
  svg.setAttribute('preserveAspectRatio','none'); svg.setAttribute('role','img');
  svg.setAttribute('aria-label','Coverage over time');
  var area = document.createElementNS(ns,'polygon');
  area.setAttribute('points','0,'+h+' '+pts+' '+W+','+h); area.setAttribute('fill','var(--fill)');
  var line = document.createElementNS(ns,'polyline');
  line.setAttribute('points',pts); line.setAttribute('fill','none'); line.setAttribute('stroke','var(--accent)');
  line.setAttribute('stroke-width','2'); line.setAttribute('vector-effect','non-scaling-stroke');
  svg.appendChild(area); svg.appendChild(line); box.appendChild(svg);
  box.appendChild(el('div',{'class':'legend'},[el('span',{text:H[0].date.slice(0,10)}),
    el('span',{style:'margin-left:auto',text:H[H.length-1].date.slice(0,10)+' · '+fix(vals[vals.length-1],1)+' %'})]));
})();

// Entries
var status = 'all', missing = '', sortKey = 'size', desc = true;
var sel = $('missing');
D.trackers.filter(function(t){ return t.in_prowlarr ? t.enabled : true; }).forEach(function(t){
  sel.appendChild(el('option',{value:t.key,text:'Missing on '+t.name}));
});
D.entries.forEach(function(e){ e._t = names(e.trackers).join(', '); e._k = (e.name+' '+e.category+' '+e._t).toLowerCase(); });

function render(){
  var q = $('q').value.toLowerCase();
  var rows = D.entries.filter(function(e){
    return (status === 'all' || e.status === status) && (!missing || e.trackers.indexOf(missing) < 0)
      && (!q || e._k.indexOf(q) >= 0);
  });
  rows.sort(function(a,b){
    var x = a[sortKey], y = b[sortKey];
    if (sortKey === 'trackers') { x = a.trackers.length; y = b.trackers.length; }
    if (x < y) return desc ? 1 : -1;
    if (x > y) return desc ? -1 : 1;
    return 0;
  });
  var body = $('entries'); body.textContent = '';
  var frag = document.createDocumentFragment();
  rows.forEach(function(e){
    frag.appendChild(el('tr',{},[
      el('td',{},[el('i',{'class':'dot',style:'background:'+COLOR[e.status],title:e.status}),document.createTextNode(e.name)]),
      el('td',{'class':'muted opt',text:e.category}),
      el('td',{'class':'opt',text:e._t || '—'}),
      el('td',{'class':'num',text:fix(e.size/GIB,1)}),
      el('td',{'class':'num',text:fix(e.uploaded/GIB,1)})
    ]));
  });
  body.appendChild(frag);
  $('count').textContent = rows.length + ' of ' + D.entries.length;
  $('empty').hidden = rows.length > 0;
}
$('q').addEventListener('input', render);
sel.addEventListener('change', function(){ missing = sel.value; render(); });
Array.prototype.forEach.call(document.querySelectorAll('[data-status]'), function(b){
  b.addEventListener('click', function(){
    status = b.dataset.status;
    Array.prototype.forEach.call(document.querySelectorAll('[data-status]'), function(x){
      x.setAttribute('aria-pressed', x === b ? 'true' : 'false');
    });
    render();
  });
});
Array.prototype.forEach.call(document.querySelectorAll('th[data-sort]'), function(th){
  th.addEventListener('click', function(){
    var k = th.dataset.sort; desc = (k === sortKey) ? !desc : true; sortKey = k; render();
  });
});
render();

// Torrents outside the library
if (D.unmatched.length) {
  $('unmatched-block').hidden = false;
  $('unmatched-count').textContent = D.unmatched.length;
  D.unmatched.forEach(function(u){
    $('unmatched').appendChild(el('li',{},[document.createTextNode(u.name+' '),
      el('span',{'class':'muted',text:u.path || 'unknown path'})]));
  });
}
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seeding coverage</title><style>{css}</style></head><body>
<div class="wrap">
<h1>Seeding coverage</h1>
<p class="sub" id="sub"></p>

<div class="kpi"><b id="coverage"></b><span class="muted">of the library is shared on at least one tracker</span></div>
<div class="gauge" id="gauge"></div>
<div class="legend" id="legend"></div>

<h2>Trackers</h2>
<p class="muted" id="noprowlarr" hidden>Prowlarr not configured: only trackers seen in qBittorrent are listed.</p>
<div class="scroll"><table>
<thead><tr><th>Tracker</th><th>State</th><th class="num">Entries</th><th>Share of library</th><th class="num">%</th>
<th class="num opt">Uploaded TiB</th><th class="num opt">Grabs</th></tr></thead>
<tbody id="trackers"></tbody></table></div>

<div id="warnings-block" hidden><h2>Warnings</h2><ul class="warn" id="warnings"></ul></div>

<h2>Coverage over time</h2>
<div id="history-chart"></div>

<h2>Library</h2>
<div class="controls">
  <input type="search" id="q" placeholder="Filter by name, category or tracker" aria-label="Filter">
  <button data-status="all" aria-pressed="true">All</button>
  <button data-status="seeded" aria-pressed="false">Seeded</button>
  <button data-status="incomplete" aria-pressed="false">Incomplete</button>
  <button data-status="orphan" aria-pressed="false">Orphans</button>
  <select id="missing" aria-label="Missing on tracker"><option value="">Any tracker</option></select>
  <span class="muted" id="count"></span>
</div>
<div class="scroll"><table>
<thead><tr><th data-sort="name">Name</th><th class="opt" data-sort="category">Category</th>
<th class="opt" data-sort="trackers">Trackers</th><th class="num" data-sort="size">Size GiB</th>
<th class="num" data-sort="uploaded">Uploaded GiB</th></tr></thead>
<tbody id="entries"></tbody></table></div>
<p class="muted" id="empty" hidden>No entry matches.</p>

<div id="unmatched-block" hidden><h2>Torrents outside the library</h2>
<details><summary><span id="unmatched-count"></span> torrents match no library entry
(downloading, deleted content, or stored outside the configured roots)</summary>
<ul class="muted" id="unmatched"></ul></details></div>

<p class="foot">Matched by inode: content hardlinked by cross-seed counts as seeded wherever the
torrent points. seedbox {version}</p>
</div>
<script type="application/json" id="data">{data}</script>
<script type="application/json" id="history">{history}</script>
<script>{js}</script>
</body></html>
"""


def _embed(value):
    # "<" escaped so the JSON can never close the <script> element.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def render(snap, history):
    data = {k: v for k, v in snap.items() if k != "entries"}
    data["entries"] = [{k: v for k, v in e.items() if k != "path"} for e in snap["entries"]]
    return PAGE.format(css=CSS, js=JS, data=_embed(data), history=_embed(history), version=__version__)
