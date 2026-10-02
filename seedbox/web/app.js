'use strict';
// Seedbox control plane. Data comes embedded (snapshot of the last collection);
// live parts (qBittorrent activity, jobs, system metrics) come from the API of
// `seedbox run`. Every text from the data is inserted with textContent.

var D = JSON.parse(document.getElementById('data').textContent);
var H = JSON.parse(document.getElementById('history').textContent);
var LIVE = location.protocol !== 'file:';
var GIB = Math.pow(1024, 3), TIB = Math.pow(1024, 4);
// Categorical dark steps, fixed order; a tracker keeps its slot whatever the filters.
var SLOTS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9', '#e66767', '#008300'];
var C = {ok: '#81c995', warn: '#fde293', ko: '#f28b82', primary: '#8ab4f8', neutral: 'rgba(255,255,255,0.38)'};
// Status colour of a value against thresholds, worst first: [[limit, colour], ...], else the last colour.
function level(v, steps, last) { for (var i = 0; i < steps.length; i++) if (v < steps[i][0]) return steps[i][1]; return last; }
function sharedColor(p) { return level(p, [[20, C.ko], [50, C.warn], [66, C.primary]], C.ok); }
function volumeColor(p) { return level(p, [[50, C.ok], [75, C.primary], [90, C.warn]], C.ko); }
function ratioColor(r) { return level(r, [[0.5, C.ko], [1, C.warn], [2, C.primary]], C.ok); }
// Trackers in one chart: shades of one blue, darkest for the biggest.
var BLUES = ['#1f5fae', '#3987e5', '#5c9ded', '#80b3f2', '#a3c9f6', '#c6def9'];

// ---------- helpers
function el(tag, attrs, kids) {
  var n = document.createElement(tag);
  for (var k in (attrs || {})) {
    var v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') n.textContent = v;
    else if (k === 'style') n.style.cssText = v;
    else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  (kids || []).forEach(function (c) {
    if (c === null || c === undefined || c === false) return;
    n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return n;
}
var SVGNS = 'http://www.w3.org/2000/svg';
function sv(tag, attrs, kids) {
  var n = document.createElementNS(SVGNS, tag);
  for (var k in (attrs || {})) {
    if (attrs[k] === null || attrs[k] === undefined) continue;
    if (k === 'text') n.textContent = attrs[k];
    else n.setAttribute(k, attrs[k]);
  }
  (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
  return n;
}
function $(id) { return document.getElementById(id); }
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
function fix(v, d) { return Number(v || 0).toFixed(d); }
function bytes(n) {
  n = Number(n || 0);
  if (n >= TIB) return fix(n / TIB, 2) + ' TiB';
  if (n >= GIB) return fix(n / GIB, 1) + ' GiB';
  if (n >= 1048576) return fix(n / 1048576, 1) + ' MiB';
  if (n >= 1024) return fix(n / 1024, 0) + ' KiB';
  return fix(n, 0) + ' B';
}
function rate(n) { return bytes(n) + '/s'; }
function pct(v, d) { return fix(v, d === undefined ? 0 : d) + ' %'; }
function when(iso) { var d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(); }
function ago(ts) {
  var s = (Date.now() - ts) / 1000;
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 129600) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' days ago';
}
function short(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }
// Last part of an entry name; a season folder keeps its show ("Show / season-08").
function label(name) {
  var parts = name.split('/'), last = parts[parts.length - 1];
  return parts.length > 1 && /^(season|saison|s)[ ._-]?\d{1,2}$/i.test(last) ? parts[parts.length - 2] + ' / ' + last : last;
}

// Icons: outlined strokes, filled variant through CSS on the active rail item.
var ICONS = {
  overview: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  activity: 'M7 4v16M7 4L3 8M7 4l4 4M17 20V4M17 20l-4-4M17 20l4-4',
  library: 'M3 7h14v13H3zM7 3h14v13M8 11v5l4-2.5z',
  duplicates: 'M8 8h12v12H8zM4 16V4h12',
  system: 'M3 12h4l2-5 4 10 2-5h6',
  logs: 'M5 5h14M5 9.5h14M5 14h9M5 18.5h9',
  outside: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  search: 'M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5L20 20',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',
  collect: 'M12 4v10M8 10l4 4 4-4M5 18h14',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  chevron: 'M6 9l6 6 6-6',
  move: 'M3 6h6l2 2h10v11H3zM10 13.5h7M14 10.5l3 3-3 3',
  recheck: 'M12 3l7 3v6c0 4.2-3 7.4-7 9-4-1.6-7-4.8-7-9V6zM8.5 12l2.5 2.5 4.5-5',
  start: 'M8 5v14l11-7z',
  remove: 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13',
  copy: 'M9 9h11v11H9zM5 15V4h11',
  extras: 'M4 6h16M4 12h10M4 18h7M17 15l4 4M21 15l-4 4',
  warn: 'M12 4l9 16H3zM12 10v4M12 17.5v.01',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.01',
  disk: 'M4 6h16v5H4zM4 13h16v5H4zM7.5 8.5v.01M7.5 15.5v.01',
  up: 'M12 19V5M6 11l6-6 6 6',
  down: 'M12 5v14M6 13l6 6 6-6'
};
function icon(name, cls) {
  return sv('svg', {'class': 'icon ' + (cls || ''), viewBox: '0 0 24 24', 'aria-hidden': 'true'}, [sv('path', {d: ICONS[name] || ''})]);
}

// ---------- tooltip (inverse surface) and toast
var TIP = $('tooltip');
function tip(evt, title, rows) {
  clear(TIP);
  if (title) TIP.appendChild(el('div', {'class': 'tt-title', text: title}));
  (rows || []).forEach(function (r) {
    TIP.appendChild(el('div', {'class': 'tt-row'}, [
      r.color ? el('i', {'class': 'tt-key', style: 'background:' + r.color}) : null,
      el('b', {text: r.value}), el('span', {text: r.label || ''})
    ]));
  });
  TIP.style.display = 'block';
  var x = evt.clientX + 16, y = evt.clientY + 16, w = TIP.offsetWidth, h = TIP.offsetHeight;
  if (x + w > window.innerWidth - 8) x = evt.clientX - w - 16;
  if (y + h > window.innerHeight - 8) y = evt.clientY - h - 16;
  TIP.style.left = x + 'px'; TIP.style.top = y + 'px';
}
function untip() { TIP.style.display = 'none'; }
var toastTimer;
// Clipboard API only exists on secure pages (https, localhost): on http://nas:8080
// fall back to a temporary textarea, then to selecting the text for Cmd/Ctrl+C.
function copyText(text, selectable) {
  function fallback() {
    var ta = el('textarea', {style: 'position:fixed;top:0;left:0;opacity:0'});
    ta.value = text; document.body.appendChild(ta); ta.focus(); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    if (ok) { toast('Copied.'); return; }
    if (selectable) {
      var range = document.createRange(); range.selectNodeContents(selectable);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    }
    toast('Copy blocked by the browser: the text is selected, press Cmd+C or Ctrl+C.');
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(function () { toast('Copied.'); }, fallback);
  } else fallback();
}
function toast(msg) {
  var t = $('toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.style.display = 'none'; }, 5000);
}

// ---------- shared lookups
var TRACKERS = D.trackers.slice().sort(function (a, b) { return a.key < b.key ? -1 : 1; });
var TCOLOR = {}, TNAME = {};
TRACKERS.forEach(function (t, i) { TCOLOR[t.key] = SLOTS[i % SLOTS.length]; TNAME[t.key] = t.name; });
var BYHASH = {};
D.torrents.forEach(function (t) { BYHASH[t.hash] = t; });
var S = D.summary;
var FOLDERS = D.folders || [];

function trackerChip(key) {
  var c = TCOLOR[key];
  // Same look as the status badges: the colour faded behind, full on the dot.
  return el('span', {'class': 'tk', style: c ? 'background:' + c + '2e' : null}, [el('i', {style: 'background:' + (c || C.neutral)}), TNAME[key] || key]);
}
var COVER = {
  everywhere: ['ok', 'Everywhere'], partial: ['info', 'Partial'], none: ['', 'Not seeded']
};
function statusBadge(e) {
  if (e.status === 'incomplete') return el('span', {'class': 'badge warn', text: 'Incomplete'});
  var c = COVER[e.coverage] || COVER.none;
  return el('span', {'class': 'badge ' + c[0], text: c[1]});
}
var SEARCH = {
  absent: ['warn', 'searched, not there: upload opportunity'], other_release: ['warn', 'only another release there'],
  found: ['info', 'match found, not in qBittorrent'], unsearched: ['', 'not searched yet']
};
function isProblem(i) { return i.code !== 'versions' && i.code !== 'episodes'; }
function isDup(i) { return i.code === 'same_tracker' || i.code === 'versions' || i.code === 'episodes'; }
function stateGroup(s) {
  if (/^(stalledUP|uploading|forcedUP|queuedUP)$/.test(s)) return 'seeding';
  if (/^checking/.test(s) || s === 'moving') return 'busy';
  if (/^(stopped|paused)/.test(s)) return 'stopped';
  if (s === 'error' || s === 'missingFiles') return 'error';
  return 'downloading';
}
var SG = {
  seeding: ['ok', 'Seeding', C.ok], downloading: ['info', 'Downloading', C.primary], busy: ['warn', 'Checking or moving', C.warn],
  stopped: ['', 'Stopped', C.neutral], error: ['ko', 'Error', C.ko]
};
function stateBadge(state) {
  var g = SG[stateGroup(state)];
  return el('span', {'class': 'badge ' + g[0], title: g[1], text: state});
}

// ---------- charts
function donut(box, segs, center, sub) {
  clear(box);
  var total = segs.reduce(function (a, s) { return a + s.value; }, 0);
  var size = 176, r = 68, w = 20, c = 2 * Math.PI * r, gap = total ? 2 : 0;
  var svg = sv('svg', {viewBox: '0 0 ' + size + ' ' + size, width: size, height: size, role: 'img',
    'aria-label': segs.map(function (s) { return s.label + ' ' + s.value; }).join(', ')});
  svg.appendChild(sv('circle', {cx: 88, cy: 88, r: r, fill: 'none', stroke: 'var(--s3)', 'stroke-width': w}));
  var off = 0;
  segs.forEach(function (s) {
    if (!s.value) return;
    var len = Math.max(s.value / total * c - gap, 1);
    var arc = sv('circle', {'class': 'mark', cx: 88, cy: 88, r: r, fill: 'none', stroke: s.color, 'stroke-width': w,
      'stroke-dasharray': len + ' ' + (c - len), 'stroke-dashoffset': -off, transform: 'rotate(-90 88 88)', tabindex: 0});
    arc.addEventListener('pointermove', function (e) { tip(e, s.label, [{value: s.value + ' (' + pct(s.value / total * 100) + ')'}]); });
    arc.addEventListener('pointerleave', untip);
    svg.appendChild(arc);
    off += s.value / total * c;
  });
  svg.appendChild(sv('text', {x: 88, y: 86, 'text-anchor': 'middle', fill: 'var(--t1)', 'font-size': 28, text: center}));
  svg.appendChild(sv('text', {x: 88, y: 108, 'text-anchor': 'middle', fill: 'var(--t2)', 'font-size': 12, text: sub}));
  // Legend on the left, donut on the right.
  var wrap = el('div', {style: 'display:flex;align-items:center;justify-content:space-between;gap:16px'});
  svg.style.flex = '0 1 176px'; svg.style.minWidth = '120px'; svg.style.height = 'auto';
  var legend = el('div', {'class': 'legend', style: 'flex-direction:column;margin:0'});
  segs.forEach(function (s) {
    legend.appendChild(el('span', {}, [el('i', {'class': 'key', style: 'background:' + s.color}),
      el('b', {style: 'color:var(--t1);font-weight:500;min-width:40px', text: String(s.value)}), s.label]));
  });
  wrap.appendChild(legend);
  wrap.appendChild(svg);
  box.appendChild(wrap);
}

function gauge(box, value, label, color) {
  clear(box);
  var v = Math.max(0, Math.min(100, value || 0)), r = 40, c = Math.PI * r;
  var svg = sv('svg', {viewBox: '0 0 100 58', width: 112, height: 64, role: 'img', 'aria-label': label + ' ' + pct(v)});
  svg.appendChild(sv('path', {d: 'M10 50a40 40 0 0 1 80 0', fill: 'none', stroke: 'var(--s3)', 'stroke-width': 10, 'stroke-linecap': 'round'}));
  svg.appendChild(sv('path', {d: 'M10 50a40 40 0 0 1 80 0', fill: 'none', stroke: color, 'stroke-width': 10, 'stroke-linecap': 'round',
    'stroke-dasharray': (v / 100 * c) + ' ' + c}));
  box.appendChild(svg);
}

function hbars(box, rows, total) {
  clear(box);
  if (!rows.length) { box.appendChild(el('p', {'class': 'nodata', text: 'No tracker yet.'})); return; }
  var grid = el('div', {'class': 'hbar'});
  rows.forEach(function (r) {
    var share = total ? r.value / total * 100 : 0;
    var bar = el('div', {'class': 'track', tabindex: 0}, [el('i', {style: 'width:' + share + '%;background:' + r.color})]);
    bar.addEventListener('pointermove', function (e) { tip(e, r.label, r.tip); });
    bar.addEventListener('pointerleave', untip);
    grid.appendChild(el('div', {'class': 'lab', title: r.label}, [el('span', {'class': 'dot', style: 'background:' + r.color + ';margin-right:8px'}), r.label]));
    grid.appendChild(bar);
    grid.appendChild(el('div', {'class': 'val', text: pct(share)}));
  });
  box.appendChild(grid);
}

function niceMax(v) {
  if (v <= 0) return 1;
  var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

// Line / area chart on one axis, crosshair tooltip listing every series.
function lineChart(box, o) {
  clear(box);
  if (!o.xs.length) { box.appendChild(el('p', {'class': 'nodata', text: o.empty || 'No data yet.'})); return; }
  var W = Math.max(box.clientWidth, 280), Hh = o.height || 200, m = {l: 48, r: 16, t: 8, b: 24};
  var iw = W - m.l - m.r, ih = Hh - m.t - m.b;
  var x0 = o.xs[0], x1 = o.xs[o.xs.length - 1], span = Math.max(x1 - x0, 1);
  // Stacked: each series drawn on top of the previous ones; tops[k] = cumulated values.
  var tops = [], run = o.xs.map(function () { return 0; });
  o.series.forEach(function (s) {
    run = run.map(function (v, k) { return v + (o.stack ? s.values[k] || 0 : 0); });
    tops.push(o.stack ? run.slice() : s.values);
  });
  var maxV = o.yMax || niceMax(Math.max.apply(null, tops.map(function (vals) {
    return Math.max.apply(null, vals.map(function (v) { return v || 0; }));
  })) * 1.05);
  function X(v) { return m.l + (v - x0) / span * iw; }
  function Y(v) { return m.t + ih - (v || 0) / maxV * ih; }
  var svg = sv('svg', {width: W, height: Hh, role: 'img', 'aria-label': o.label || ''});
  var grid = sv('g', {'class': 'grid'}), axis = sv('g', {'class': 'axis'});
  for (var i = 0; i <= 4; i++) {
    var yv = maxV / 4 * i;
    grid.appendChild(sv('line', {x1: m.l, x2: W - m.r, y1: Y(yv), y2: Y(yv)}));
    axis.appendChild(sv('text', {x: m.l - 8, y: Y(yv) + 4, 'text-anchor': 'end', text: o.yFmt(yv)}));
  }
  var ticks = Math.min(5, o.xs.length);
  for (var j = 0; j < ticks; j++) {
    var xv = x0 + span * j / Math.max(ticks - 1, 1);
    axis.appendChild(sv('text', {x: X(xv), y: Hh - 4, 'text-anchor': j === 0 ? 'start' : j === ticks - 1 ? 'end' : 'middle', text: o.xFmt(xv)}));
  }
  svg.appendChild(grid); svg.appendChild(axis);
  o.series.forEach(function (s, n) {
    var pts = [], top = tops[n];
    o.xs.forEach(function (x, k) { if (top[k] !== null && top[k] !== undefined) pts.push(fix(X(x), 1) + ',' + fix(Y(top[k]), 1)); });
    if (!pts.length) return;
    if (o.stack) {
      var below = n ? tops[n - 1] : o.xs.map(function () { return 0; });
      var back = o.xs.map(function (x, k) { return fix(X(x), 1) + ',' + fix(Y(below[k]), 1); }).reverse();
      svg.appendChild(sv('polygon', {points: pts.concat(back).join(' '), fill: s.color, opacity: 0.55}));
    } else if (s.area) {
      svg.appendChild(sv('polygon', {points: fix(X(o.xs[0]), 1) + ',' + Y(0) + ' ' + pts.join(' ') + ' ' + fix(X(x1), 1) + ',' + Y(0),
        fill: s.color, opacity: 0.16}));
    }
    svg.appendChild(sv('polyline', {points: pts.join(' '), fill: 'none', stroke: s.color, 'stroke-width': 1.5,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': s.dash || null}));
  });
  var xh = sv('line', {'class': 'xhair', y1: m.t, y2: m.t + ih, visibility: 'hidden'});
  var dots = sv('g');
  svg.appendChild(xh); svg.appendChild(dots);
  var hit = sv('rect', {'class': 'hit', x: m.l, y: m.t, width: iw, height: ih});
  hit.addEventListener('pointermove', function (e) {
    var rect = svg.getBoundingClientRect(), px = e.clientX - rect.left, best = 0, bd = Infinity;
    o.xs.forEach(function (x, k) { var d = Math.abs(X(x) - px); if (d < bd) { bd = d; best = k; } });
    var cx = X(o.xs[best]);
    xh.setAttribute('x1', cx); xh.setAttribute('x2', cx); xh.setAttribute('visibility', 'visible');
    clear(dots);
    var rows = [], total = 0;
    o.series.forEach(function (s, n) {
      var v = s.values[best];
      if (v === null || v === undefined) return;
      total += v;
      dots.appendChild(sv('circle', {cx: cx, cy: Y(tops[n][best]), r: 4, fill: s.color, stroke: 'var(--s1)', 'stroke-width': 2}));
      rows.push({color: s.color, value: o.yFmt(v, true), label: s.name});
    });
    if (o.stack) rows.reverse().push({color: 'transparent', value: o.yFmt(total, true), label: 'Total'});
    tip(e, o.tipFmt ? o.tipFmt(o.xs[best]) : o.xFmt(o.xs[best]), rows);
  });
  hit.addEventListener('pointerleave', function () { untip(); xh.setAttribute('visibility', 'hidden'); clear(dots); });
  svg.appendChild(hit);
  box.appendChild(svg);
  if (o.series.length > 1) {
    var legend = el('div', {'class': 'legend'});
    o.series.forEach(function (s) {
      legend.appendChild(el('span', {}, [el('i', {'class': 'key line', style: 'background:' + s.color}), s.name]));
    });
    box.appendChild(legend);
  }
}

// Stacked vertical bars, per-bar tooltip.
function stackedBars(box, o) {
  clear(box);
  var W = Math.max(box.clientWidth, 280), Hh = o.height || 200, m = {l: 40, r: 8, t: 8, b: 24};
  var iw = W - m.l - m.r, ih = Hh - m.t - m.b, n = o.labels.length;
  var totals = o.labels.map(function (_, i) { return o.series.reduce(function (a, s) { return a + (s.values[i] || 0); }, 0); });
  var maxV = niceMax(Math.max.apply(null, totals.concat([1])));
  var bw = Math.max(iw / n - 2, 1);
  var svg = sv('svg', {width: W, height: Hh, role: 'img', 'aria-label': o.label || ''});
  var grid = sv('g', {'class': 'grid'}), axis = sv('g', {'class': 'axis'});
  for (var g = 0; g <= 4; g++) {
    var y = m.t + ih - ih * g / 4;
    grid.appendChild(sv('line', {x1: m.l, x2: W - m.r, y1: y, y2: y}));
    axis.appendChild(sv('text', {x: m.l - 8, y: y + 4, 'text-anchor': 'end', text: String(Math.round(maxV * g / 4))}));
  }
  [0, Math.floor(n / 2), n - 1].forEach(function (i, k) {
    axis.appendChild(sv('text', {x: m.l + i * (iw / n) + bw / 2, y: Hh - 4, 'text-anchor': k === 0 ? 'start' : k === 2 ? 'end' : 'middle', text: o.xFmt(o.labels[i])}));
  });
  svg.appendChild(grid); svg.appendChild(axis);
  o.labels.forEach(function (lab, i) {
    var x = m.l + i * (iw / n), base = m.t + ih;
    var group = sv('g', {'class': 'mark'});
    o.series.forEach(function (s, k) {
      var v = s.values[i] || 0;
      if (!v) return;
      var h = v / maxV * ih;
      var top = k === o.series.length - 1 || !o.series.slice(k + 1).some(function (z) { return z.values[i]; });
      group.appendChild(sv('rect', {x: x, y: base - h, width: bw, height: Math.max(h - 1, 1), fill: s.color, rx: top ? 2 : 0}));
      base -= h;
    });
    var hit = sv('rect', {'class': 'hit', x: x - 1, y: m.t, width: bw + 2, height: ih});
    hit.addEventListener('pointermove', function (e) {
      group.classList.add('hover');
      tip(e, o.tipFmt(lab), o.series.map(function (s) { return {color: s.color, value: String(s.values[i] || 0), label: s.name}; }));
    });
    hit.addEventListener('pointerleave', function () { group.classList.remove('hover'); untip(); });
    svg.appendChild(group); svg.appendChild(hit);
  });
  box.appendChild(svg);
  var legend = el('div', {'class': 'legend'});
  o.series.forEach(function (s) { legend.appendChild(el('span', {}, [el('i', {'class': 'key', style: 'background:' + s.color}), s.name])); });
  box.appendChild(legend);
}

// ---------- API
function api(path, body) {
  var opts = {cache: 'no-store'};
  if (body) opts = {method: 'POST', cache: 'no-store', headers: {'Content-Type': 'application/json', 'X-Seedbox': '1'}, body: JSON.stringify(body)};
  return fetch(path, opts).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (j) {
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      return j;
    });
  });
}

// Confirmation dialog; resolves with the checkbox value (or false), rejects on cancel.
function confirmDialog(title, body, okLabel, checkbox, checked) {
  return new Promise(function (resolve, reject) {
    var d = $('dialog'); clear(d);
    var box = checkbox ? el('input', {type: 'checkbox'}) : null;
    if (box && checked) box.checked = true;
    var cancel = el('button', {'class': 'btn', text: 'Cancel', type: 'button'});
    var ok = el('button', {'class': 'btn filled', text: okLabel, type: 'button'});
    d.appendChild(el('h3', {text: title}));
    d.appendChild(el('div', {'class': 'body'}, [el('div', {text: body}), checkbox ? el('label', {}, [box, checkbox]) : null]));
    d.appendChild(el('div', {'class': 'actions'}, [cancel, ok]));
    cancel.onclick = function () { d.close(); reject(); };
    ok.onclick = function () { d.close(); resolve(box ? box.checked : false); };
    d.showModal();
  });
}

var ACTION_TEXT = {inject: 'Inject release', rename: 'Rename file', create: 'Create .torrent', seed: 'Seed created torrent', upload: 'Upload to tracker',
  move: 'Move', recheck: 'Recheck', start: 'Start', skip_extras: 'Skip missing extras', remove: 'Remove',
  set_category: 'Set category', apply_category: 'Apply category folder'
};
function isTransient(t) { return !!(D.transient_qbt && t && (t.content_path + '/').indexOf(D.transient_qbt + '/') === 0); }
function act(action, hashes, extra, label) {
  if (!D.actions) { toast('Actions are disabled: set [service] actions = true.'); return Promise.resolve(); }
  if (!LIVE) { toast('Open the dashboard from seedbox run to use actions.'); return Promise.resolve(); }
  var body = Object.assign({action: action, hashes: hashes}, extra || {});
  return api('api/action', body).then(function (r) {
    toast((label || ACTION_TEXT[action]) + ': ' + r.jobs.length + ' job(s) sent to qBittorrent.');
    refreshLive();
  }).catch(function (e) { toast('Failed: ' + e.message); });
}
// withFiles: the "also delete" box starts ticked (link torrents whose removal would
// otherwise leave orphan link files behind).
function confirmAct(action, hashes, title, body, extra, withFiles) {
  var linkOnly = hashes.every(function (h) { return BYHASH[h] && BYHASH[h].link; });
  var transient = hashes.every(function (h) { return isTransient(BYHASH[h]); });
  var box = action !== 'remove' ? null : linkOnly ? 'Also delete their cross-seed link files (the library copy is kept)'
    : transient ? 'Also delete their files (transient download folder)' : null;
  return confirmDialog(title, body, ACTION_TEXT[action], box, withFiles && !!box).then(function (checked) {
    return act(action, hashes, Object.assign({}, extra || {}, action === 'remove' ? {delete_files: checked} : {}));
  }, function () {});
}
function fixButton(fix, issue, entry) {
  var h = issue.torrent ? [issue.torrent] : [];
  var t = issue.torrent && BYHASH[issue.torrent];
  if (fix === 'remove_extra') {
    return el('button', {'class': 'btn sm', type: 'button', onclick: function (e) {
      e.stopPropagation();
      var keep = BYHASH[issue.keep];
      confirmAct('remove', issue.remove, 'Remove ' + issue.remove.length + ' redundant torrent(s)?',
        'Keeps "' + (keep ? keep.name : issue.keep) + '" (' + (keep ? keep.seeds + ' seeds, ' + bytes(keep.uploaded) + ' uploaded' : '') +
        '). The library file is never touched; unticked, their link files would stay behind as orphans.', null, true);
    }}, [icon('remove', 'sm'), 'Remove ' + issue.remove.length + ' extra']);
  }
  var names = {start: ['start', 'Start'], recheck: ['recheck', 'Recheck'], remove: ['remove', 'Remove torrent'], skip_extras: ['extras', 'Skip missing extras']};
  if (fix === 'move') return el('span', {'class': 'faint small', text: 'Use Move below'});
  var n = names[fix];
  if (!n) return null;
  return el('button', {'class': 'btn sm' + (fix === 'remove' ? ' danger' : ''), type: 'button', onclick: function (e) {
    e.stopPropagation();
    if (fix === 'remove') confirmAct('remove', h, 'Remove this torrent?', (t ? t.name : '') + ' on ' + (t ? TNAME[t.tracker] || t.tracker : '') + '. The library file is never touched.');
    else act(fix, h);
  }}, [icon(n[0], 'sm'), n[1]]);
}

// ---------- hero and top bar
// Library page header: what the library holds and what can be done with it.
function renderLibraryHero() {
  var sr = D.search || {};
  $('hero-sub').textContent = S.entries + ' entries · ' + bytes(S.size) + ' on disk · ' + S.duplicates + ' in duplicates · collected ' + when(D.generated);
  var meta = $('hero-meta'); clear(meta);
  if (D.search) meta.appendChild(el('span', {'class': 'badge info', text: sr.opportunity + ' upload opportunities'}));
  meta.appendChild(el('span', {'class': 'badge ' + (S.problems ? 'warn' : 'ok'), text: S.problems + ' with problems'}));
  meta.appendChild(el('span', {'class': 'badge ' + (D.actions ? 'info' : ''), text: D.actions ? 'Actions enabled' : 'Read-only'}));
}
function renderHero() {
  $('hero-sub').textContent = S.entries + ' entries · ' + bytes(S.size) + ' on disk · ' + S.torrents + ' torrents (' +
    S.cross_seed_torrents + ' cross-seed) · collected ' + when(D.generated) + ' in ' + fix(D.duration_s, 0) + ' s';
  var meta = $('hero-meta'); clear(meta);
  meta.appendChild(el('span', {'class': 'badge ' + (S.prowlarr ? 'ok' : 'warn'), text: S.prowlarr ? 'Prowlarr connected' : 'Prowlarr not configured'}));
  meta.appendChild(el('span', {'class': 'badge ' + (D.actions ? 'info' : ''), text: D.actions ? 'Actions enabled' : 'Read-only'}));
  meta.appendChild(el('span', {'class': 'badge', text: 'Everywhere = ' + S.target_trackers.map(function (k) { return TNAME[k] || k; }).join(', ')}));
  if (!LIVE) meta.appendChild(el('span', {'class': 'badge warn', text: 'Offline copy: live data unavailable'}));
}

// ---------- overview
function kpi(box, label, iconName, value, unit, foot, onclick) {
  clear(box);
  if (onclick) { box.classList.add('link'); box.onclick = onclick; box.tabIndex = 0; }
  box.appendChild(el('div', {'class': 'label'}, [icon(iconName, 'sm'), label]));
  box.appendChild(el('div', {'class': 'value'}, [String(value), unit ? el('small', {text: unit}) : null]));
  if (foot) box.appendChild(el('div', {'class': 'foot', text: foot}));
}
function renderOverview() {
  var cov = $('k-coverage'); clear(cov);
  cov.appendChild(el('div', {'class': 'label'}, [icon('library', 'sm'), 'Library shared']));
  var g = el('div'); gauge(g, S.coverage_pct, 'Coverage', sharedColor(S.coverage_pct));
  cov.appendChild(el('div', {'class': 'gauge-wrap'}, [g, el('div', {'class': 'value', text: pct(S.coverage_pct)})]));
  cov.appendChild(el('div', {'class': 'foot', text: S.everywhere + ' everywhere · ' + S.partial + ' partial · ' + S.none + ' not seeded'}));

  renderRatios();
  kpi($('k-problems'), 'Problems', 'warn', S.problems, 'entries', 'Stopped, failed matches, tracker errors, missing extras, redundant uploads, lone films',
    function () { goLibrary('problems'); });
  kpi($('k-dups'), 'Duplicates', 'duplicates', S.duplicates, 'entries', D.duplicates.length + ' groups: same tracker, versions, episodes',
    function () { location.href = 'library.html#duplicates'; });
  renderErrorsTile();
  renderCategoriesTile();
  renderUndeclaredTile();
  var sr = D.search;
  if (sr) {
    kpi($('k-opportunity'), 'Upload opportunities', 'up', sr.opportunity, 'entries',
      'Absent from a tracker cross-seed searched · ' + (sr.other_release || 0) + ' more with only another release there (dupe risk)',
      function () { goLibrary('opportunity'); });
    kpi($('k-unsearched'), 'Not searched yet', 'search', sr.unsearched + sr.not_indexed, 'entries',
      sr.unsearched + ' waiting for cross-seed · ' + sr.not_indexed + ' outside its data folders', function () { goLibrary('unsearched'); });
  } else {
    kpi($('k-opportunity'), 'Upload opportunities', 'up', '—', '', 'cross-seed database not mounted');
    kpi($('k-unsearched'), 'Not searched yet', 'search', '—', '', 'cross-seed database not mounted');
  }
  var ix = $('k-indexers'); clear(ix);
  ix.appendChild(el('div', {'class': 'label'}, [icon('activity', 'sm'), 'cross-seed indexers']));
  var list = el('div', {'class': 'chips', style: 'margin-top:8px'});
  ((D.cross_seed && D.cross_seed.indexers) || []).forEach(function (i) {
    var okState = i.status === 'OK' || !i.status, hhmm = function (ms) { return new Date(ms).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'}); };
    // cross-seed keeps the status until it queries the indexer again: past its retry time, the limit is over.
    var over = !okState && i.retry_after && i.retry_after < Date.now();
    var text = okState ? 'ok' : over ? 'limit over since ' + hhmm(i.retry_after) : (i.status || '').toLowerCase().replace('_', ' ') +
      (i.retry_after ? ' until ' + hhmm(i.retry_after) : '');
    list.appendChild(el('span', {'class': 'badge ' + (okState ? 'ok' : over ? 'info' : 'warn'),
      title: i.status + (i.retry_after ? ', retry after ' + new Date(i.retry_after).toLocaleString() : '')}, [(i.name || i.key) + ': ' + text]));
  });
  if (!list.firstChild) list.appendChild(el('span', {'class': 'foot', text: 'cross-seed database not mounted'}));
  ix.appendChild(list);
  ix.appendChild(el('div', {'class': 'foot', style: 'margin-top:8px', text: 'Rate limited: searches on hold until the retry time; the status only changes when cross-seed queries again'}));

  var parts = {everywhere: 0, partial: 0, incomplete: 0, none: 0};
  D.entries.forEach(function (e) { parts[e.status === 'incomplete' ? 'incomplete' : e.coverage]++; });
  donut($('c-status'), [
    {label: 'Seeded on every tracker', value: parts.everywhere, color: C.ok},
    {label: 'Seeded on some trackers', value: parts.partial, color: C.primary},
    {label: 'Downloading', value: parts.incomplete, color: C.warn},
    {label: 'On disk, not seeded', value: parts.none, color: C.neutral}
  ], pct(S.coverage_pct), 'shared');

  var byCount = D.trackers.slice().sort(function (a, b) { return b.entries - a.entries; }).map(function (t) { return t.key; });
  hbars($('c-trackers'), D.trackers.map(function (t) {
    var st = !t.in_prowlarr ? 'not in Prowlarr' : !t.enabled ? 'disabled' : t.failing ? 'failing' : 'ok';
    return {label: t.name, value: t.entries, color: BLUES[Math.min(byCount.indexOf(t.key), BLUES.length - 1)], tip: [
      {value: String(t.entries), label: 'entries'}, {value: bytes(t.size), label: 'shared'},
      {value: bytes(t.uploaded), label: 'uploaded'}, {value: st, label: ''}]};
  }), S.entries);

  var states = {};
  Object.keys(D.states).forEach(function (s) { var g2 = stateGroup(s); states[g2] = (states[g2] || 0) + D.states[s]; });
  donut($('c-states'), Object.keys(SG).filter(function (k) { return states[k]; }).map(function (k) {
    return {label: SG[k][1], value: states[k], color: SG[k][2]};
  }), String(S.torrents), 'torrents');

  var tl = D.timeline || [];
  var tlBox = $('c-timeline');
  var series = [];
  TRACKERS.forEach(function (t) {
    if (!tl.some(function (p) { return p.trackers[t.name]; })) return;
    series.push({name: t.name, color: TCOLOR[t.key], values: tl.map(function (p) { return p.trackers[t.name] || 0; })});
  });
  lineChart(tlBox, {
    xs: tl.map(function (p) { return new Date(p.date).getTime(); }), series: series, label: 'Seeded entries over time', stack: true,
    yFmt: function (v) { return String(Math.round(v)); },
    xFmt: function (x) { return new Date(x).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}); },
    tipFmt: function (x) { return new Date(x).toLocaleDateString(); }
  });
  var added = D.added || [];
  stackedBars($('c-added'), {
    labels: added.map(function (a) { return a.date; }), label: 'Torrents added per day',
    series: [{name: 'Cross-seed', color: SLOTS[0], values: added.map(function (a) { return a.cross_seed; })},
             {name: 'Other (downloads, own uploads)', color: SLOTS[2], values: added.map(function (a) { return a.other; })}],
    xFmt: function (d) { return new Date(d).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}); },
    tipFmt: function (d) { return new Date(d).toLocaleDateString(); }
  });
  // Coverage since the first torrent: rebuilt from add dates (entries seeded then / entries now),
  // from 0 the day before, then the values measured at each collection.
  var rebuilt = {}, measured = {};
  if (tl.length) {
    rebuilt[new Date(tl[0].date).getTime() - 86400000] = 0;
    tl.forEach(function (p) { rebuilt[new Date(p.date).getTime()] = p.seeded / Math.max(S.entries, 1) * 100; });
  }
  H.forEach(function (r) { measured[new Date(r.date).getTime()] = parseFloat(r.coverage_pct) || 0; });
  var hx = Object.keys(rebuilt).concat(Object.keys(measured)).map(Number).sort(function (a, b) { return a - b; })
    .filter(function (x, k, a) { return !k || a[k - 1] !== x; });
  lineChart($('c-history'), {
    xs: hx, label: 'Library coverage', height: 200, yMax: 100, yFmt: function (v) { return pct(v, 0); },
    series: [{name: 'Rebuilt from add dates', color: C.ok, area: true, values: hx.map(function (x) { return x in rebuilt ? rebuilt[x] : null; })},
             {name: 'Measured at collections', color: C.primary, values: hx.map(function (x) { return x in measured ? measured[x] : null; })}],
    empty: 'No torrent yet.',
    xFmt: function (x) { return new Date(x).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}); },
    tipFmt: function (x) { return new Date(x).toLocaleString(); }
  });
}

// One tile per declared tracker (Prowlarr / cross-seed) plus the other trackers:
// ratio of the torrents now in qBittorrent, their volumes, upload over 7 and 30 days.
function renderRatios() {
  document.querySelectorAll('.ratio-tile').forEach(function (n) { n.remove(); });
  var slot = $('ratio-slot'), day = function (iso) { return new Date(iso).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}); };
  (D.ratios || []).forEach(function (r) {
    var box = el('div', {'class': 'card kpi c3 ratio-tile'});
    slot.parentNode.insertBefore(box, slot);
    var ratio = r.down ? fix(r.up / r.down, 2) : r.up ? '∞' : '—';
    var win = [7, 30].map(function (d) {
      var up = r['up_' + d + 'd'], since = r['up_' + d + 'd_since'];
      if (up === null || up === undefined) return null;
      var short = since && (Date.now() - new Date(since).getTime()) < (d - 1) * 86400000;
      return '↑ ' + d + ' d ' + bytes(up) + (short ? ' (since ' + day(since) + ')' : '');
    }).filter(Boolean);
    kpi(box, r.key === 'other' ? 'Ratio, other trackers' : 'Ratio ' + (TNAME[r.key] || r.name), 'up', ratio, '',
      '↑ ' + bytes(r.up) + ' · ↓ ' + bytes(r.down) + ' · ' + r.torrents + ' torrents' + (win.length ? '\n' + win.join(' · ') : ''));
    if (r.up || r.down) box.querySelector('.value').style.color = r.down ? ratioColor(r.up / r.down) : C.ok;
    box.title = r.down ? '' : 'Nothing downloaded on this tracker by the torrents in qBittorrent (cross-seeded): the ratio the tracker shows also counts past downloads.';
    if (r.key !== 'other' && TCOLOR[r.key]) box.querySelector('.label').prepend(el('i', {'class': 'dot', style: 'background:' + TCOLOR[r.key]}));
  });
}

function renderCategoriesTile() {
  var box = $('k-categories'), c = D.categories;
  if (!c) { kpi(box, 'Categories', 'library', '—', '', 'Not collected yet'); return; }
  var ko = c.counts.ko || 0, warn = c.counts.warn || 0;
  kpi(box, 'Categories', 'library', c.counts.ok, 'OK', ko + ' to fix · ' + warn + ' waiting (move pending, finished transient)');
  if (ko) box.querySelector('.value').style.color = C.warn;
  var fixable = c.issues.filter(function (i) { return i.fix === 'set_category'; });
  if (fixable.length) {
    box.appendChild(el('div', {}, [el('button', {'class': 'btn sm', type: 'button', onclick: function () {
      var groups = {};
      fixable.forEach(function (i) { (groups[i.suggest] = groups[i.suggest] || []).push(i.hash); });
      confirmDialog('Set the category of ' + fixable.length + ' torrent(s)?', Object.keys(groups).map(function (g) { return g + ': ' + groups[g].length; }).join(' · ') +
        '. The category matches where the files already are: nothing moves.', 'Set')
        .then(function () { Object.keys(groups).forEach(function (g) { act('set_category', groups[g], {category: g}); }); }, function () {});
    }}, [icon('check', 'sm'), 'Fix ' + fixable.length + ' categories'])]));
  }
  box.appendChild(dropList(c.issues, function (i) {
    var t = BYHASH[i.hash] || {name: i.hash};
    var btn = i.fix === 'set_category' ? el('button', {'class': 'btn sm', type: 'button', text: i.suggest, title: 'Set category ' + i.suggest,
      onclick: function () { act('set_category', [i.hash], {category: i.suggest}); }})
      : i.fix === 'apply_category' ? el('button', {'class': 'btn sm', type: 'button', text: 'Move', title: 'Auto management on: qBittorrent moves it to the category folder',
        onclick: function () { act('apply_category', [i.hash]); }}) : null;
    return el('div', {'class': 'item'}, [el('span', {'class': 'badge ' + (i.status === 'ko' ? 'ko' : 'warn'), text: t.category || 'none'}),
      el('span', {'class': 'name', title: t.name + '\n' + i.text, text: t.name}), btn]);
  }));
}
function duration(sec) { return sec >= 86400 ? fix(sec / 86400, 1) + ' d' : fix(sec / 3600, 1) + ' h'; }
function renderUndeclaredTile() {
  var box = $('k-undeclared'), list = (D.undeclared || []).map(function (h) { return BYHASH[h]; }).filter(Boolean);
  kpi(box, 'Outside declared trackers', 'outside', list.length, 'torrents', 'Trackers Prowlarr does not know: public or one-off sharing. Clean them once done.');
  var done = list.filter(function (t) { return t.progress >= 1; });
  if (done.length) {
    box.appendChild(el('div', {}, [el('button', {'class': 'btn sm danger', type: 'button', onclick: function () {
      confirmAct('remove', done.map(function (t) { return t.hash; }), 'Clean ' + done.length + ' finished torrent(s)?',
        'Removes them from qBittorrent. Files are deleted only if they sit in the transient folder.');
    }}, [icon('remove', 'sm'), 'Clean ' + done.length + ' finished'])]));
  }
  box.appendChild(dropList(list, function (t) {
    return el('div', {'class': 'item'}, [trackerChip(t.tracker), el('span', {'class': 'name', title: t.name, text: t.name}),
      el('span', {'class': 'faint small', text: 'ratio ' + fix(t.ratio, 2) + ' · ' + duration(t.seeding_time)}),
      el('button', {'class': 'btn sm danger', type: 'button', title: 'Remove', 'aria-label': 'Remove', onclick: function () {
        confirmAct('remove', [t.hash], 'Remove this torrent?', t.name + ' (' + (TNAME[t.tracker] || t.tracker || 'no tracker') + ', ratio ' + fix(t.ratio, 2) + ').');
      }}, [icon('remove', 'sm')])]);
  }));
}
function renderErrorsTile() {
  var box = $('k-errors'), hashes = S.error_torrents || [];
  kpi(box, 'Torrents in error', 'warn', hashes.length, 'torrents', 'Deleted by the tracker, tracker errors, failed matches, missing files');
  if (hashes.length) box.querySelector('.value').style.color = C.ko;
  // Deleted by the tracker: dead weight, announces and disk for nothing.
  var gone = hashes.filter(function (h) { return BYHASH[h] && BYHASH[h].issues.some(function (i) { return i.code === 'unregistered'; }); });
  if (gone.length) {
    box.appendChild(el('div', {}, [el('button', {'class': 'btn sm danger', type: 'button', onclick: function () {
      confirmAct('remove', gone, 'Remove ' + gone.length + ' torrent(s) deleted by their tracker?',
        'They no longer exist on the tracker (unregistered, 404…). Files are deleted only if they sit in the transient folder.');
    }}, [icon('remove', 'sm'), 'Remove ' + gone.length + ' deleted by tracker'])]));
  }
  box.appendChild(dropList(hashes.map(function (h) { return BYHASH[h]; }).filter(Boolean), function (t) {
    var issue = t.issues[0] || {};
    return el('div', {'class': 'item'}, [el('span', {'class': 'badge ko', text: (issue.code || t.state).replace('_', ' ')}),
      el('span', {'class': 'name', title: t.name + '\n' + (issue.text || ''), text: t.name}),
      el('button', {'class': 'btn sm danger', type: 'button', title: 'Remove', 'aria-label': 'Remove', onclick: function () {
        confirmAct('remove', [t.hash], 'Remove this torrent?', t.name + ' on ' + (TNAME[t.tracker] || t.tracker) + '. ' + (issue.text || '') + ' The library file is never touched.');
      }}, [icon('remove', 'sm')])]);
  }));
}

// ---------- live: activity, jobs, rechecks, logs
var L = null, autoTimer = null;
function openJobs() {
  var jobs = (L && L.jobs || []).filter(function (j) { return j.status === 'pending' || j.status === 'running'; });
  var logMoves = (L && L.moves || []).filter(function (m) { return m.status === 'pending' || m.status === 'running'; });
  return {jobs: jobs, logMoves: logMoves};
}
function jobBadge(status) {
  var cls = {pending: 'warn', running: 'info', done: 'ok', failed: 'ko', cancelled: ''}[status] || '';
  return el('span', {'class': 'badge ' + cls}, [status === 'running' ? icon('refresh', 'sm spin') : null, status]);
}
function dropList(items, render, label) {
  var list = el('div', {'class': 'drop-list'});
  if (!items.length) list.appendChild(el('div', {'class': 'item faint', text: 'Nothing pending.'}));
  items.forEach(function (it) { list.appendChild(render(it)); });
  return el('details', {'class': 'drop'}, [el('summary', {}, [label || 'Show list', icon('chevron', 'sm chev')]), list]);
}
function renderQueueTiles() {
  var q = $('k-queue'), r = $('k-rechecks');
  if (!L) {
    kpi(q, 'Queued moves & removals', 'move', '—', '', LIVE ? 'Loading…' : 'Live data needs seedbox run');
    kpi(r, 'Rechecks pending', 'recheck', '—', '', LIVE ? 'Loading…' : 'Live data needs seedbox run');
    return;
  }
  var o = openJobs(), moves = o.jobs.filter(function (j) { return j.action === 'move'; }).length;
  var removes = o.jobs.filter(function (j) { return j.action === 'remove'; }).length;
  var others = o.jobs.length - moves - removes;
  var logOnly = o.logMoves.filter(function (m) { return !o.jobs.some(function (j) { return j.name === m.name; }); });
  kpi(q, 'Queued moves & removals', 'move', o.jobs.length + logOnly.length, '', 'Actions not finished yet, sent from here or seen in the log: ' +
    moves + ' moves · ' + removes + ' removals · ' + others + ' other' + (logOnly.length ? ' · ' + logOnly.length + ' moves from the log' : ''));
  q.appendChild(dropList(o.jobs.concat(logOnly.map(function (m) { return {action: 'move', status: m.status, name: m.name, submitted: Date.parse(m.time) / 1000}; })),
    function (j) {
      return el('div', {'class': 'item'}, [jobBadge(j.status), el('span', {'class': 'badge', text: j.action}),
        el('span', {'class': 'name', title: j.name, text: j.name}), el('span', {'class': 'faint small', text: ago(j.submitted * 1000)})]);
    }));
  var checking = L.busy.filter(function (b) { return /^checking/.test(b.state); });
  var running = L.checking.running || 0;
  kpi(r, 'Rechecks pending', 'recheck', L.checking.count, '', running + ' running, ' + (L.checking.count - running) + ' waiting their turn · ' +
    bytes(L.checking.bytes) + ' left to read');
  r.appendChild(dropList(checking, function (b) {
    return el('div', {'class': 'item'}, [el('span', {'class': 'badge ' + (b.progress > 0 ? 'info' : ''), text: pct(b.progress * 100, 1)}),
      el('span', {'class': 'name', title: b.name, text: b.name}), el('span', {'class': 'faint small', text: bytes(b.size)})]);
  }));
}
// Disk I/O and transfer tiles: home page only.
function renderIoTiles() {
  var io = $('a-io'), tr = $('a-transfer');
  if (!L) {
    [io, tr].forEach(function (b) { kpi(b, b === io ? 'qBittorrent disk I/O' : 'Transfer', b === io ? 'disk' : 'activity', '—', '', LIVE ? 'Loading…' : 'Live data needs seedbox run'); });
    return;
  }
  var wait = L.io.average_time_queue_ms;
  kpi(io, 'qBittorrent disk I/O', 'disk', L.io.queued_io_jobs, 'requests waiting', 'Block reads and writes queued inside qBittorrent, not torrents · ' +
    wait + ' ms average wait' + (wait >= 1000 ? ': disk saturated' : ''));
  io.querySelector('.value').style.color = wait >= 1000 ? C.ko : L.io.queued_io_jobs ? C.warn : C.ok;
  var ioCls = {move: 'warn', recheck: 'info', download: 'ok', upload: ''};
  io.appendChild(dropList(L.io_sources || [], function (s) {
    return el('div', {'class': 'item'}, [el('span', {'class': 'badge ' + ioCls[s.why], text: s.why}),
      el('span', {'class': 'name', title: s.name, text: s.name}),
      el('span', {'class': 'faint small', text: s.why === 'recheck' ? pct(s.progress * 100, 1) : s.why === 'move' ? bytes(s.size)
        : '↑ ' + rate(s.up) + ' · ↓ ' + rate(s.dl)})]);
  }, 'Torrents using the disk'));
  kpi(tr, 'Transfer', 'activity', rate(L.io.up_speed), 'up', 'Down ' + rate(L.io.dl_speed) + ' · ' + L.io.peers + ' peers · qBittorrent ' + L.version);
}
function renderActivity() {
  var busy = $('a-busy');
  if ($('a-io')) renderIoTiles();
  if (!L) return;
  clear(busy);
  if (!L.busy.length) busy.appendChild(el('p', {'class': 'empty', text: 'Nothing moving, checking or in error.'}));
  else {
    var tb = el('tbody');
    L.busy.forEach(function (b) {
      tb.appendChild(el('tr', {}, [el('td', {}, [stateBadge(b.state)]), el('td', {'class': 'name'}, [el('div', {'class': 't', title: b.name, text: b.name})]),
        el('td', {'class': 'opt muted', text: b.category}), el('td', {'class': 'num', text: bytes(b.size)}), el('td', {'class': 'num', text: pct(b.progress * 100, 1)})]));
    });
    busy.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {'class': 'dense'}, [el('thead', {}, [el('tr', {}, [
      el('th', {text: 'State'}), el('th', {text: 'Torrent'}), el('th', {'class': 'opt', text: 'Category'}), el('th', {'class': 'num', text: 'Size'}), el('th', {'class': 'num', text: 'Progress'})])]), tb])]));
  }
  renderJobs();
}
function renderJobs() {
  var jobs = $('a-jobs');
  clear(jobs);
  var list = (L.jobs || []).slice().reverse();
  if (!list.length) jobs.appendChild(el('p', {'class': 'empty', text: 'No job sent from the dashboard yet.'}));
  else {
    var jb = el('tbody'), view = paged('jobs', list, 5, 30, Infinity, renderJobs);
    view.rows.forEach(function (j) {
      jb.appendChild(el('tr', {}, [el('td', {}, [jobBadge(j.status)]), el('td', {text: ACTION_TEXT[j.action] || j.action}),
        el('td', {'class': 'name'}, [el('div', {'class': 't', title: j.name, text: j.name}),
          j.note ? el('div', {'class': 'faint small', text: j.note}) : null, createdButtons(j)]),
        el('td', {'class': 'opt path', text: j.target || ''}), el('td', {'class': 'num muted', text: ago(j.submitted * 1000)})]));
    });
    jobs.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {'class': 'dense'}, [el('thead', {}, [el('tr', {}, [
      el('th', {text: 'Status'}), el('th', {text: 'Action'}), el('th', {text: 'Torrent'}), el('th', {'class': 'opt', text: 'Target'}), el('th', {'class': 'num', text: 'Sent'})])]), jb])]));
    jobs.appendChild(view.controls);
  }
  var stored = storedTorrents().length, max = (L && L.created_max) || 0;
  if (stored && max) jobs.appendChild(el('p', {'class': 'small', style: stored >= max ? 'color:var(--warn)' : '',
    text: stored + ' / ' + max + ' created .torrent files stored' + (stored >= max ? ': the next one replaces the oldest, delete the ones already uploaded' : '')}));
}
// Log levels shown: any combination, warnings and errors by default.
var LOG_LEVELS = {info: false, warn: true, ko: true};
function renderLogs() {
  var box = $('logs'); clear(box);
  var bar = clear($('log-levels'));
  [['info', 'Info'], ['warn', 'Warning'], ['ko', 'Error']].forEach(function (lv) {
    var n = L ? L.events.filter(function (e) { return e.level === lv[0]; }).length : 0;
    bar.appendChild(el('button', {'class': 'chip', type: 'button', 'aria-pressed': LOG_LEVELS[lv[0]] ? 'true' : 'false', onclick: function () {
      LOG_LEVELS[lv[0]] = !LOG_LEVELS[lv[0]]; firstPage('logs'); renderLogs();
    }}, [icon('check', 'check'), lv[1], el('span', {'class': 'n', text: String(n)})]));
  });
  if (!L) { box.appendChild(el('p', {'class': 'empty', text: LIVE ? 'Loading…' : 'Live data needs seedbox run.'})); return; }
  var rows = L.events.filter(function (e) { return LOG_LEVELS[e.level]; }).reverse();
  if (!rows.length) {
    box.appendChild(el('p', {'class': 'empty', text: L.events.length ? 'Nothing at the selected levels.' : 'No move, removal or error in the log.'}));
    return;
  }
  var view = paged('logs', rows, 10, 20, Infinity, renderLogs);
  box.appendChild(logTable(view.rows));
  box.appendChild(view.controls);
}
function logTable(rows) {
  var tb = el('tbody');
  rows.forEach(function (e) {
    var cls = {info: 'info', warn: 'warn', ko: 'ko'}[e.level];
    tb.appendChild(el('tr', {}, [el('td', {'class': 'when', text: new Date(e.time).toLocaleString()}),
      el('td', {}, [el('span', {'class': 'badge ' + cls, text: e.level === 'ko' ? 'error' : e.level === 'warn' ? 'warning' : 'info'})]),
      el('td', {text: e.message})]));
  });
  return el('div', {'class': 'table-wrap'}, [el('table', {'class': 'log'}, [tb])]);
}
function renderErrors() {
  var box = $('a-errors'); clear(box);
  $('errors-clear').hidden = true;
  if (!L) { box.appendChild(el('p', {'class': 'empty', text: LIVE ? 'Loading…' : 'Live data needs seedbox run.'})); return; }
  var rows = (L.errors || []).slice().reverse();
  $('errors-clear').hidden = !rows.length || !LIVE;
  if (!rows.length) {
    box.appendChild(el('p', {'class': 'empty', text: L.errors_cleared
      ? 'No warning or error since the list was cleared, ' + new Date(L.errors_cleared).toLocaleString() + '.'
      : 'No warning or error in the qBittorrent log.'}));
    return;
  }
  var view = paged('errors', rows, 5, 15, 2, renderErrors);
  box.appendChild(logTable(view.rows));
  box.appendChild(view.controls);
}
// Short list first (few rows), "Show more" opens pages of `step` rows, at most
// `pages` of them. State survives live refreshes.
var PAGED = {};
function firstPage(key) { if (PAGED[key]) PAGED[key].page = 0; }
function paged(key, rows, first, step, pages, rerender) {
  var st = PAGED[key] || (PAGED[key] = {open: false, page: 0});
  var n = Math.min(pages, Math.ceil(rows.length / step)), bar = el('div', {'class': 'more'});
  st.page = Math.min(st.page, n - 1);
  function go(open, page) { st.open = open; st.page = page; rerender(); }
  if (!st.open) {
    if (rows.length > first) bar.appendChild(el('button', {'class': 'btn sm', type: 'button', onclick: function () { go(true, 0); }},
      ['Show ' + Math.min(rows.length, step) + (pages === Infinity ? ' per page, ' + rows.length + ' in all' : ' of ' + Math.min(rows.length, step * pages))]));
    return {rows: rows.slice(0, first), controls: bar};
  }
  bar.appendChild(el('button', {'class': 'btn sm', type: 'button', onclick: function () { go(false, 0); }}, ['Show ' + first]));
  function pageChip(p, text, label) {
    bar.appendChild(el('button', {'class': 'chip', type: 'button', 'aria-pressed': text === undefined && p === st.page ? 'true' : 'false',
      'aria-label': label || 'Page ' + (p + 1), disabled: p < 0 || p >= n ? true : null, onclick: go.bind(null, true, p)}, [text || String(p + 1)]));
  }
  if (n > 1) {
    // Many pages: first, last and two around the current one.
    if (n > 9) pageChip(st.page - 1, '‹', 'Previous page');
    for (var p = 0, gap = false; p < n; p++) {
      if (n <= 9 || p === 0 || p === n - 1 || Math.abs(p - st.page) <= 2) { pageChip(p); gap = false; }
      else if (!gap) { bar.appendChild(el('span', {'class': 'faint', text: '…'})); gap = true; }
    }
    if (n > 9) pageChip(st.page + 1, '›', 'Next page');
  }
  bar.appendChild(el('span', {'class': 'faint small', text: (st.page * step + 1) + '–' + Math.min((st.page + 1) * step, rows.length) + ' of ' + rows.length}));
  return {rows: rows.slice(st.page * step, (st.page + 1) * step), controls: bar};
}
// The server runs another version than this page (upgrade since it was opened): offer a reload.
function checkVersion(running) {
  var tag = $('page-version'), mine = tag.textContent.replace(/^v/, '');
  if (!running || running === mine) return;
  tag.classList.add('stale');
  tag.textContent = 'v' + mine + ' → v' + running + ': reload';
  tag.title = 'seedbox ' + running + ' is running; this page was built by ' + mine + '. Click to reload.';
  tag.onclick = function () { location.reload(); };
}
function refreshLive() {
  if (!LIVE) { renderLive(); return Promise.resolve(); }
  $('live-refresh').disabled = true;
  document.querySelectorAll('[data-live]').forEach(function (n) { n.classList.add('stale'); });
  return api('api/status').then(function (st) { L = st; checkVersion(st.seedbox); }).catch(function (e) {
    toast('qBittorrent status failed: ' + e.message);
  }).then(function () {
    document.querySelectorAll('[data-live]').forEach(function (n) { n.classList.remove('stale'); });
    $('live-refresh').disabled = false;
    $('live-time').textContent = L ? 'Updated ' + new Date().toLocaleTimeString() : '';
    renderLive();
  });
}
// Live parts present on this page (Activity is on both pages, the rest on the home page).
function renderLive() {
  if ($('k-queue')) renderQueueTiles();
  renderActivity(); renderErrors();
  if ($('logs')) renderLogs();
}
function setAuto(on) {
  clearInterval(autoTimer);
  $('auto').setAttribute('aria-pressed', on ? 'true' : 'false');
  var period = Number($('auto').dataset.period) * 1000;
  if (on && LIVE) autoTimer = setInterval(function () { if (!document.hidden) { refreshLive(); refreshMetrics(); } }, period);
}

// ---------- system metrics
var M = null, hours = 24;
function renderSystem() {
  var ids = ['m-cpu', 'm-mem', 'm-disk', 'm-net'];
  if (!M) { ids.forEach(function (id) { clear($(id)).appendChild(el('p', {'class': 'nodata', text: LIVE ? 'Loading…' : 'Live data needs seedbox run.'})); }); return; }
  var rows = M.series, xs = rows.map(function (r) { return r.t * 1000; });
  function col(k) { return rows.map(function (r) { return r[k] === undefined ? null : r[k]; }); }
  var tf = function (x) { var d = new Date(x); return hours > 48 ? d.toLocaleDateString(undefined, {day: 'numeric', month: 'short'}) : d.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'}); };
  var tt = function (x) { return new Date(x).toLocaleString(); };
  var empty = 'Samples appear every ' + Math.round((M.interval || 300) / 60) + ' min once seedbox run is up.';
  lineChart($('m-cpu'), {xs: xs, series: [{name: 'CPU', color: SLOTS[0], area: true, values: col('cpu_pct')}, {name: 'IO wait', color: SLOTS[1], values: col('iowait_pct')}],
    yMax: 100, yFmt: function (v) { return pct(v); }, xFmt: tf, tipFmt: tt, empty: empty, label: 'CPU and IO wait'});
  lineChart($('m-mem'), {xs: xs, series: [{name: 'Memory used', color: SLOTS[2], area: true, values: col('mem_used_pct')}],
    yMax: 100, yFmt: function (v) { return pct(v); }, xFmt: tf, tipFmt: tt, empty: empty, label: 'Memory used'});
  lineChart($('m-disk'), {xs: xs, series: [{name: 'Busiest disk', color: SLOTS[1], area: true, values: col('disk_busy_pct')}],
    yMax: 100, yFmt: function (v) { return pct(v); }, xFmt: tf, tipFmt: tt, empty: empty, label: 'Disk busy'});
  lineChart($('m-net'), {xs: xs, series: [{name: 'Upload', color: SLOTS[0], area: true, values: col('up_bps')}, {name: 'Download', color: SLOTS[2], area: true, values: col('dl_bps')}],
    yFmt: function (v) { return rate(v); }, xFmt: tf, tipFmt: tt, empty: empty, label: 'qBittorrent transfer'});
  var vol = $('k-volume'); clear(vol);
  vol.appendChild(el('div', {'class': 'label'}, [icon('disk', 'sm'), 'Volume usage']));
  (M.volumes || []).slice(0, 2).forEach(function (v) {
    var p = v.total ? v.used / v.total * 100 : 0, g = el('div');
    gauge(g, p, 'Volume', volumeColor(p));
    vol.appendChild(el('div', {'class': 'gauge-wrap'}, [g, el('div', {}, [el('div', {'class': 'value', text: pct(p)}),
      el('div', {'class': 'foot', text: bytes(v.free) + ' free of ' + bytes(v.total)})])]));
  });
  if (!(M.volumes || []).length) vol.appendChild(el('div', {'class': 'foot', text: 'No volume readable.'}));
}
function refreshMetrics() {
  if (!$('m-cpu')) return;
  if (!LIVE) { renderSystem(); clear($('k-volume')).appendChild(el('div', {'class': 'foot', text: 'Live data needs seedbox run.'})); return; }
  api('api/metrics?hours=' + hours).then(function (m) { M = m; renderSystem(); }).catch(function (e) { toast('Metrics failed: ' + e.message); });
}

// ---------- duplicates
var dupShown = 10;
function renderDuplicates() {
  var box = $('dups'); clear(box);
  var groups = D.duplicates, kinds = {same_tracker: 0, versions: 0, episodes: 0}, extras = [];
  groups.forEach(function (g) { kinds[g.kind]++; if (g.kind === 'same_tracker') extras = extras.concat(g.remove); });
  kpi($('d-same'), 'Same file, same tracker', 'duplicates', kinds.same_tracker, 'groups', extras.length + ' redundant torrents to remove');
  if (extras.length) {
    $('d-same').appendChild(el('div', {}, [el('button', {'class': 'btn sm', type: 'button', onclick: function () {
      confirmAct('remove', extras, 'Remove ' + extras.length + ' redundant torrents?',
        'In each group, the most seeded complete torrent is kept. Library files are never touched.');
    }}, [icon('remove', 'sm'), 'Remove all extras'])]));
  }
  kpi($('d-versions'), 'Several versions', 'library', kinds.versions, 'works', 'Same title and year, different files');
  kpi($('d-episodes'), 'Episodes twice', 'logs', kinds.episodes, 'seasons', 'Same episode number in one season folder');
  if (!groups.length) { box.appendChild(el('p', {'class': 'empty', text: 'No duplicate found.'})); return; }
  var tb = el('tbody');
  groups.slice(0, dupShown).forEach(function (g) {
    var detail, action = null;
    if (g.kind === 'same_tracker') {
      detail = el('div', {'class': 'muted'}, [g.torrents.length + ' torrents on ' + (TNAME[g.tracker] || g.tracker) + ', keeps ',
        el('b', {style: 'color:var(--t1);font-weight:500', text: short((BYHASH[g.keep] || {}).name || '', 60)})]);
      action = fixButton('remove_extra', {keep: g.keep, remove: g.remove});
    } else if (g.kind === 'versions') {
      detail = el('div', {}, g.entries.map(function (i) {
        var e = D.entries[i];
        return el('div', {'class': 'cell-flex', style: 'margin:4px 0'}, [statusBadge(e), i === g.best ? el('span', {'class': 'badge ok', text: 'best'}) : null,
          el('span', {'class': 'badge', text: e.resolution || '?'}), el('span', {'class': 'muted', text: e.folder + ' · ' + bytes(e.size)}),
          el('span', {'class': 't', title: e.name, text: short(e.name.split('/').pop(), 70)})]);
      }));
    } else {
      detail = el('div', {}, Object.keys(g.episodes).map(function (ep) {
        return el('div', {style: 'margin:4px 0'}, [el('span', {'class': 'badge', text: ep}),
          el('div', {'class': 'path'}, g.episodes[ep].map(function (f) { return el('div', {text: f}); }))]);
      }));
    }
    var kindBadge = {same_tracker: ['ko', 'Same tracker'], versions: ['warn', 'Versions'], episodes: ['warn', 'Episodes']}[g.kind];
    tb.appendChild(el('tr', {}, [el('td', {}, [el('span', {'class': 'badge ' + kindBadge[0], text: kindBadge[1]})]),
      el('td', {'class': 'name'}, [el('div', {'class': 't', title: g.title, text: g.title}), detail]), el('td', {'class': 'num'}, [action])]));
  });
  box.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {}, [el('thead', {}, [el('tr', {}, [
    el('th', {text: 'Kind'}), el('th', {text: 'Content'}), el('th', {'class': 'num', text: 'Fix'})])]), tb])]));
  if (groups.length > dupShown) {
    box.appendChild(el('div', {'class': 'more'}, [el('button', {'class': 'btn', type: 'button', text: 'Show all ' + groups.length + ' groups',
      onclick: function () { dupShown = groups.length; renderDuplicates(); }})]));
  }
}

// ---------- library
var folder = '', missing = '', query = '', sortKey = 'size', desc = true, openRow = null, libShown = [];
var selected = {};
D.entries.forEach(function (e, i) {
  e._i = i;
  e._problems = e.issues.filter(isProblem).length;
  e._dup = e.issues.some(isDup);
  e._k = (e.name + ' ' + e.folder + ' ' + e.trackers.map(function (k) { return TNAME[k] || k; }).join(' ')).toLowerCase();
  e._main = e.torrents.filter(function (h) { return BYHASH[h] && !BYHASH[h].link; });
  e._lang = langGroup(e.name);
});
// Language from the release name (MediaInfo tells more at check time).
function langGroup(name) {
  if (/(^|[^a-z0-9])(multi|vf2)([^a-z0-9]|$)/i.test(name)) return 'MULTI';
  if (/(^|[^a-z0-9])(vostfr|subfrench)([^a-z0-9]|$)/i.test(name)) return 'VOSTFR';
  if (/(^|[^a-z0-9])(truefrench|french|vff|vfq|vfi|vf)([^a-z0-9]|$)/i.test(name)) return 'FRENCH';
  return 'VO';
}
var MAIN_RES = ['2160p', '1080p', '720p'];
// Check results by entry and tracker (from /api/checks), and the trackers a check can search.
var CHK = {}, CHECKABLE = [];
function verdicts(e) { var c = CHK[e._i] || {}; return Object.keys(c).map(function (k) { return c[k].verdict; }); }
// Groups of filter chips: OR inside a group, AND between groups.
var GROUPS = [
  {id: 'seed', label: 'Seeding', chips: [
    ['everywhere', 'Seeded everywhere', function (e) { return e.coverage === 'everywhere' && e.status === 'seeded'; }],
    ['partial', 'Partially seeded', function (e) { return e.coverage === 'partial' && e.status !== 'incomplete'; }],
    ['none', 'On disk, not seeded', function (e) { return e.coverage === 'none'; }],
    ['incomplete', 'Downloading', function (e) { return e.status === 'incomplete'; }]
  ]},
  {id: 'state', label: 'Situation', chips: [
    ['problems', 'Problems', function (e) { return e._problems > 0; }],
    ['duplicates', 'Duplicates', function (e) { return e._dup; }],
    ['opportunity', 'Absent: upload it', function (e) { return e.search_state === 'opportunity'; }],
    ['other_release', 'Other release present', function (e) { return e.search_state === 'other_release'; }],
    ['unsearched', 'Not searched yet', function (e) { return e.search_state === 'unsearched' || e.search_state === 'not_indexed'; }]
  ]},
  {id: 'res', label: 'Resolution', chips: MAIN_RES.map(function (r) { return [r, r, function (e) { return e.resolution === r; }]; })
    .concat([['res_other', 'Other', function (e) { return MAIN_RES.indexOf(e.resolution) < 0; }]])},
  {id: 'lang', label: 'Language', chips: [['MULTI', 'MULTI'], ['FRENCH', 'FRENCH'], ['VOSTFR', 'VOSTFR'], ['VO', 'No French marker']]
    .map(function (l) { return [l[0], l[1], function (e) { return e._lang === l[0]; }]; })},
  {id: 'check', label: 'Check', chips: [
    ['unchecked', 'Not checked', function (e) { return !verdicts(e).length; }],
    ['v_clear', 'Clear', function (e) { return verdicts(e).indexOf('clear') >= 0; }],
    ['v_warn', 'Check it', function (e) { return verdicts(e).indexOf('warn') >= 0; }],
    ['v_blocked', 'Already there', function (e) { return verdicts(e).indexOf('blocked') >= 0; }],
    ['v_incomplete', 'Search failed', function (e) { return verdicts(e).indexOf('incomplete') >= 0; }]
  ]}
];
function noFilter() { var a = {}; GROUPS.forEach(function (g) { a[g.id] = {}; }); return a; }
var active = noFilter();
var CHIPFN = {};
GROUPS.forEach(function (g) { g.chips.forEach(function (c) { CHIPFN[c[0]] = {group: g.id, fn: c[2]}; }); });
function inGroups(e, skip) { return GROUPS.every(function (g) { return g.id === skip || inGroup(e, g.id); }); }
function inGroup(e, gid) {
  var keys = Object.keys(active[gid]);
  return !keys.length || keys.some(function (k) { return CHIPFN[k].fn(e); });
}
function baseMatch(e) {
  return (!folder || e.folder === folder) && (!missing || e.trackers.indexOf(missing) < 0) && (!query || e._k.indexOf(query) >= 0);
}
// From the home page's tiles: the library page, filtered.
function goLibrary(f) {
  if ($('lib-body')) { setFilter(f); location.hash = '#library'; return; }
  location.href = 'library.html?filter=' + encodeURIComponent(f) + '#library';
}
// From a tile: show only this chip.
function setFilter(f) {
  active = noFilter();
  if (CHIPFN[f]) active[CHIPFN[f].group][f] = true;
  firstPage('library'); renderLibrary();
}
function toggleChip(f) {
  var g = active[CHIPFN[f].group];
  if (g[f]) delete g[f]; else g[f] = true;
  firstPage('library'); renderLibrary();
}
function updateChips() {
  var none = GROUPS.every(function (g) { return !Object.keys(active[g.id]).length; });
  document.querySelectorAll('#lib-chips .chip').forEach(function (c) {
    var f = c.dataset.f;
    if (f === 'all') {
      c.setAttribute('aria-pressed', none ? 'true' : 'false');
      c.querySelector('.n').textContent = String(D.entries.filter(baseMatch).length);
      return;
    }
    var gid = CHIPFN[f].group;
    c.setAttribute('aria-pressed', active[gid][f] ? 'true' : 'false');
    // Count under the other groups' selection, so combinations read directly.
    c.querySelector('.n').textContent = String(D.entries.filter(function (e) {
      return baseMatch(e) && inGroups(e, gid) && CHIPFN[f].fn(e);
    }).length);
  });
}
function buildLibraryControls() {
  var chips = $('lib-chips');
  function chip(f, label, onclick) {
    return el('button', {'class': 'chip', type: 'button', 'data-f': f, 'aria-pressed': 'false', onclick: onclick},
      [icon('check', 'check'), label, el('span', {'class': 'n'})]);
  }
  chips.appendChild(el('div', {'class': 'chips'}, [chip('all', 'All', function () { setFilter('all'); })]));
  GROUPS.forEach(function (g) {
    chips.appendChild(el('div', {'class': 'chips', style: 'align-items:center'}, [el('span', {'class': 'muted small', style: 'width:80px', text: g.label})]
      .concat(g.chips.map(function (c) { return chip(c[0], c[1], function () { toggleChip(c[0]); }); }))));
  });
  var fs = $('lib-folder');
  var names = {};
  D.entries.forEach(function (e) { names[e.folder] = (names[e.folder] || 0) + 1; });
  Object.keys(names).sort().forEach(function (f) { fs.appendChild(el('option', {value: f, text: f + ' (' + names[f] + ')'})); });
  fs.onchange = function () { folder = fs.value; firstPage('library'); renderLibrary(); };
  var ms = $('lib-missing');
  TRACKERS.forEach(function (t) { ms.appendChild(el('option', {value: t.key, text: 'Missing on ' + t.name})); });
  ms.onchange = function () { missing = ms.value; firstPage('library'); renderLibrary(); };
  document.querySelectorAll('#lib-table th[data-sort]').forEach(function (th) {
    th.onclick = function () { var k = th.dataset.sort; desc = k === sortKey ? !desc : k !== 'name' && k !== 'folder'; sortKey = k; renderLibrary(); };
  });
  var dest = $('batch-dest');
  FOLDERS.forEach(function (f) { dest.appendChild(el('option', {value: f.path, text: f.label})); });
  $('batch-move').onclick = function () {
    var entries = Object.keys(selected).map(function (i) { return D.entries[i]; });
    var hashes = [], skipped = 0;
    entries.forEach(function (e) { if (e._main.length) hashes = hashes.concat(e._main); else skipped++; });
    if (!hashes.length) { toast('None of the selected entries has a library torrent to move.'); return; }
    var target = FOLDERS.filter(function (f) { return f.path === dest.value; })[0];
    confirmDialog('Move ' + hashes.length + ' torrent(s) to ' + target.label + '?',
      'qBittorrent moves them one at a time; cross-seed links stay valid (hardlinks).' + (skipped ? ' ' + skipped + ' selected entries have no library torrent and are skipped.' : ''), 'Move')
      .then(function () { return act('move', hashes, {location: target.path}); }, function () {});
  };
  $('batch-recheck').onclick = function () { batchAct('recheck'); };
  $('batch-start').onclick = function () { batchAct('start'); };
  $('batch-clear').onclick = function () { selected = {}; renderLibrary(); };
  loadChecks();
  $('lib-all').onchange = function () {
    var rows = libShown;
    if ($('lib-all').checked) rows.forEach(function (e) { selected[e._i] = true; }); else selected = {};
    renderLibrary();
  };
}
function batchAct(action) {
  var hashes = [];
  Object.keys(selected).forEach(function (i) { hashes = hashes.concat(D.entries[i].torrents); });
  if (!hashes.length) { toast('No torrent in the selection.'); return; }
  act(action, hashes);
}
function visibleEntries() {
  var rows = D.entries.filter(function (e) { return baseMatch(e) && inGroups(e); });
  rows.sort(function (a, b) {
    var x, y;
    if (sortKey === 'trackers') { x = a.trackers.length; y = b.trackers.length; }
    else if (sortKey === 'issues') { x = a.issues.length; y = b.issues.length; }
    else { x = a[sortKey]; y = b[sortKey]; }
    if (typeof x === 'string') { x = x.toLowerCase(); y = y.toLowerCase(); }
    return x < y ? (desc ? 1 : -1) : x > y ? (desc ? -1 : 1) : 0;
  });
  return rows;
}
function entryDetail(e) {
  var box = el('div', {'class': 'detail-grid'});
  box.appendChild(el('div', {'class': 'muted'}, [e.folder + ' · ' + e.files + ' file(s) · ' + bytes(e.size) + ' · uploaded ' + bytes(e.uploaded)]));
  if (e.issues.length) {
    var list = el('div');
    e.issues.forEach(function (i) {
      var kind = isProblem(i) ? (i.code === 'same_tracker' ? 'warn' : 'ko') : 'warn';
      var t = i.torrent && BYHASH[i.torrent];
      list.appendChild(el('div', {'class': 'issue'}, [el('span', {'class': 'badge ' + kind}, [icon(kind === 'ko' ? 'warn' : 'info', 'sm'), i.code.replace('_', ' ')]),
        el('div', {'class': 'txt'}, [i.text, t ? el('div', {'class': 'faint small', text: (TNAME[t.tracker] || t.tracker) + ' · ' + t.name}) : null,
          i.files ? el('div', {'class': 'path'}, Object.keys(i.files).map(function (ep) { return el('div', {text: ep + ': ' + i.files[ep].join(' | ')}); })) : null]),
        el('div', {'class': 'chips'}, (i.fixes || []).map(function (f) { return fixButton(f, i, e); }))]));
    });
    box.appendChild(list);
  }
  if (e.torrents.length) {
    var tb = el('tbody');
    e.torrents.forEach(function (h) {
      var t = BYHASH[h];
      if (!t) return;
      tb.appendChild(el('tr', {}, [el('td', {}, [trackerChip(t.tracker)]), el('td', {}, [stateBadge(t.state)]),
        el('td', {'class': 'name'}, [el('div', {'class': 't', title: t.name, text: t.name}), el('div', {'class': 'path', text: t.content_path})]),
        el('td', {}, [el('span', {'class': 'badge ' + (t.link ? '' : 'info'), text: t.link ? 'cross-seed link' : 'library'})]),
        el('td', {'class': 'num', text: t.seeds + ' / ' + t.leechs}), el('td', {'class': 'num', text: fix(t.ratio, 2)}),
        el('td', {'class': 'num'}, [el('div', {'class': 'chips', style: 'justify-content:flex-end;flex-wrap:nowrap'}, [
          el('button', {'class': 'btn sm', type: 'button', title: 'Recheck', 'aria-label': 'Recheck', onclick: function (ev) { ev.stopPropagation(); act('recheck', [h]); }}, [icon('recheck', 'sm')]),
          /^(stopped|paused)/.test(t.state) ? el('button', {'class': 'btn sm', type: 'button', title: 'Start', 'aria-label': 'Start', onclick: function (ev) { ev.stopPropagation(); act('start', [h]); }}, [icon('start', 'sm')]) : null,
          el('button', {'class': 'btn sm danger', type: 'button', title: 'Remove', 'aria-label': 'Remove', onclick: function (ev) {
            ev.stopPropagation(); confirmAct('remove', [h], 'Remove this torrent?', t.name + ' on ' + (TNAME[t.tracker] || t.tracker) + '. The library file is never touched.');
          }}, [icon('remove', 'sm')])])])]));
    });
    box.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {'class': 'subtable'}, [el('thead', {}, [el('tr', {}, [
      el('th', {text: 'Tracker'}), el('th', {text: 'State'}), el('th', {text: 'Torrent'}), el('th', {text: 'Where'}),
      el('th', {'class': 'num', text: 'Seeds / leechers'}), el('th', {'class': 'num', text: 'Ratio'}), el('th', {'class': 'num', text: 'Actions'})])]), tb])]));
  }
  if (e.search && Object.keys(e.search).length) {
    var srch = el('div', {'class': 'chips'});
    Object.keys(e.search).forEach(function (k) {
      var v = e.search[k], meta = SEARCH[v.verdict] || ['', v.verdict];
      srch.appendChild(el('span', {'class': 'badge ' + meta[0], title: v.searched ? 'Searched ' + new Date(v.searched).toLocaleString() : 'Never searched'},
        [(TNAME[k] || k) + ': ' + meta[1] + (v.searched ? ' · ' + ago(v.searched) : '')]));
    });
    box.appendChild(el('div', {}, [el('div', {'class': 'muted small', style: 'margin-bottom:8px', text: 'cross-seed on the trackers it is missing on'}), srch]));
  } else if (e.search_state === 'not_indexed') {
    box.appendChild(el('div', {'class': 'muted small', text: 'Not in cross-seed data folders: never searched.'}));
  }
  if (LIVE && D.actions && S.prowlarr) box.appendChild(matchPanel(e));
  if (LIVE && D.actions) { var ck = checkPanel(e); if (ck) box.appendChild(ck); }
  if (LIVE && D.actions) { var cp = createPanel(e); if (cp) box.appendChild(cp); }
  if (e._main.length) {
    var sel = el('select', {'class': 'select', 'aria-label': 'Destination folder'});
    FOLDERS.forEach(function (f) { if (f.label !== e.folder) sel.appendChild(el('option', {value: f.path, text: f.label})); });
    box.appendChild(el('div', {'class': 'cell-flex', style: 'flex-wrap:wrap'}, [el('span', {'class': 'muted', text: 'Move with qBittorrent to'}), sel,
      el('button', {'class': 'btn sm filled', type: 'button', onclick: function (ev) {
        ev.stopPropagation();
        var target = FOLDERS.filter(function (f) { return f.path === sel.value; })[0];
        confirmDialog('Move to ' + target.label + '?', e.name + ' (' + bytes(e.size) + '). Cross-seed links stay valid.', 'Move')
          .then(function () { return act('move', e._main, {location: target.path}); }, function () {});
      }}, [icon('move', 'sm'), 'Move'])]));
  }
  return box;
}
// ---------- checks against the trackers: is the film already there, does its name say what it holds
var CVERDICT = {clear: ['ok', 'clear'], warn: ['warn', 'check it'], blocked: ['ko', 'already there'], incomplete: ['warn', 'search failed']};
var CKIND = {same_size: ['ko', 'same size'], same_release: ['ko', 'same group + res.'], same_resolution: ['warn', 'same resolution'], other: ['', 'other release']};
var PROOF = {};
function loadChecks() {
  if (!LIVE) return;
  api('api/checks').then(function (r) {
    CHECKABLE = r.trackers || []; CHK = {};
    (r.checks || []).forEach(function (c) { (CHK[c.index] = CHK[c.index] || {})[c.tracker] = c; });
    renderCheckBatch(); renderLibrary();
  }, function () {});
}
function checkCell(e) {
  var c = CHK[e._i] || {};
  return el('td', {'class': 'opt'}, [el('div', {'class': 'chips'}, Object.keys(c).map(function (k) {
    var v = CVERDICT[c[k].verdict] || ['', c[k].verdict];
    return el('span', {'class': 'badge ' + v[0], title: (TNAME[k] || k) + ': ' + v[1] + ', ' + ago(c[k].at * 1000)}, [(TNAME[k] || k).split('.')[0] + ' · ' + v[1]]);
  }))]);
}
// Trackers this entry can be checked on: missing there, with a Prowlarr indexer.
function checkTargets(e) { return CHECKABLE.filter(function (k) { return e.trackers.indexOf(k) < 0; }); }
function runCheck(e, k) {
  return api('api/check', {op: 'check', entry: e._i, tracker: k}).then(function (r) {
    (CHK[e._i] = CHK[e._i] || {})[k] = r;
  }, function (err) {
    (CHK[e._i] = CHK[e._i] || {})[k] = {verdict: 'incomplete', reasons: [err.message], matches: [], searched: [], at: Date.now() / 1000, tracker: k};
  });
}
function renderCheckBatch() {
  var box = $('batch-check'); clear(box);
  if (!D.actions) return;
  CHECKABLE.forEach(function (k) {
    box.appendChild(el('button', {'class': 'btn sm', type: 'button', title: 'Search ' + (TNAME[k] || k) + ' for each selected film (one at a time)', onclick: function () {
      var todo = Object.keys(selected).map(function (i) { return D.entries[i]; }).filter(function (e) { return e.trackers.indexOf(k) < 0; });
      if (!todo.length) { toast('The selected entries are all seeded on ' + (TNAME[k] || k) + '.'); return; }
      var n = 0, total = todo.length;
      (function next() {
        if (!todo.length) { toast('Checked ' + total + ' entr' + (total > 1 ? 'ies' : 'y') + ' on ' + (TNAME[k] || k) + '.'); renderLibrary(); return; }
        var e = todo.shift();
        toast('Checking ' + (++n) + ' / ' + total + ' on ' + (TNAME[k] || k) + '…');
        runCheck(e, k).then(function () { renderLibrary(); next(); });
      })();
    }}, ['Check on ', trackerChip(k)]));
  });
}
function proofButtons(m) {
  var p = PROOF[m.candidate], box = el('div', {'class': 'chips', onclick: function (ev) { ev.stopPropagation(); }});
  if (p === 'busy') return el('span', {'class': 'faint small', text: 'working…'});
  if (!p) {
    box.appendChild(el('button', {'class': 'btn sm', type: 'button', title: 'Fetch its .torrent and hash pieces of the local file against it', onclick: function () {
      PROOF[m.candidate] = 'busy'; renderLibrary();
      api('api/match', {op: 'verify', candidate: m.candidate}).then(function (r) { PROOF[m.candidate] = r; },
        function (err) { PROOF[m.candidate] = {verified: false, reason: err.message}; }).then(renderLibrary);
    }}, ['Verify']));
  } else if (p.kept) {
    box.appendChild(el('span', {'class': 'badge', text: 'kept for review'}));
  } else if (p.verified) {
    box.appendChild(el('span', {'class': 'badge ok', text: 'same bytes'}));
    if (p.in_qbt) box.appendChild(el('span', {'class': 'badge', text: 'already in qBittorrent'}));
    else box.appendChild(el('button', {'class': 'btn sm filled', type: 'button', title: 'Add it to qBittorrent on the library file, started once the recheck says 100 %', onclick: function () {
      api('api/match', {op: 'apply', infohash: p.infohash, mode: 'inject'}).then(function () {
        p.in_qbt = true; toast('Inject started: follow it in Activity, jobs.'); refreshLive(); renderLibrary();
      }, function (err) { toast('Failed: ' + err.message); });
    }}, ['Inject']));
  } else {
    box.appendChild(el('span', {'class': 'badge warn', text: p.reason || 'not the same bytes'}));
  }
  if (!(p && (p.verified || p.kept))) {
    box.appendChild(el('button', {'class': 'btn sm', type: 'button', title: 'Save the tracker\'s .torrent and what is known of the local file, to be matched by hand', onclick: function () {
      api('api/check', {op: 'review', candidate: m.candidate, reason: p && p.reason ? 'verify: ' + p.reason : 'not verified'}).then(function () {
        PROOF[m.candidate] = {kept: true}; toast('Kept in review/: ask for it to be matched.'); renderLibrary();
      }, function (err) { toast('Failed: ' + err.message); });
    }}, ['Keep for review']));
  }
  return box;
}
function checkResult(e, k, c) {
  var v = CVERDICT[c.verdict] || ['', c.verdict];
  var box = el('div', {'class': 'small', style: 'display:flex;flex-direction:column;gap:8px'});
  box.appendChild(el('div', {'class': 'cell-flex', style: 'flex-wrap:wrap'}, [el('span', {'class': 'badge ' + v[0], text: v[1]}),
    el('span', {'class': 'faint', text: 'checked ' + ago(c.at * 1000)}),
    el('button', {'class': 'btn sm', type: 'button', onclick: function (ev) { ev.stopPropagation(); runCheck(e, k).then(renderLibrary); }}, ['Check again'])]));
  (c.reasons || []).forEach(function (r) { box.appendChild(el('div', {text: '• ' + r})); });
  if (c.languages && c.languages.audio) box.appendChild(el('div', {text: 'MediaInfo: ' + c.languages.group + ' · audio ' + (c.languages.audio.join(', ') || '?') +
    ' · subtitles ' + ((c.languages.subtitles || []).join(', ') || 'none')}));
  if ((c.tmdb || []).length) box.appendChild(el('div', {}, ['TMDB: '].concat(c.tmdb.map(function (t, i) {
    return el('span', {}, [i ? ' · ' : '', el('a', {href: t.url, target: '_blank', rel: 'noopener', text: t.title + ' (' + (t.year || '?') + ')'})]);
  }))));
  if ((c.errors || []).length) box.appendChild(el('div', {style: 'color:var(--warn)', text: 'Failed: ' + c.errors.join(' | ')}));
  if ((c.matches || []).length) {
    var tb = el('tbody');
    c.matches.forEach(function (m) {
      var kd = CKIND[m.kind] || ['', m.kind];
      tb.appendChild(el('tr', {}, [el('td', {}, [el('span', {'class': 'badge ' + kd[0], text: kd[1]})]),
        el('td', {}, [m.info_url ? el('a', {href: m.info_url, target: '_blank', rel: 'noopener', text: m.title}) : m.title]),
        el('td', {'class': 'num', text: bytes(m.size)}), el('td', {'class': 'num', text: String(m.seeders)}),
        el('td', {}, [m.candidate ? proofButtons(m) : (m.kind === 'same_size' || m.kind === 'same_release' ? el('span', {'class': 'faint small', text: 'check again to verify'}) : null)])]));
    });
    box.appendChild(el('table', {'class': 'dense subtable'}, [tb]));
  } else if (c.verdict !== 'incomplete') box.appendChild(el('div', {text: 'Nothing of this film found there.'}));
  return box;
}
function checkPanel(e) {
  var targets = checkTargets(e);
  if (!targets.length) return null;
  var box = el('div', {style: 'display:flex;flex-direction:column;gap:16px'});
  targets.forEach(function (k) {
    var c = (CHK[e._i] || {})[k];
    box.appendChild(el('div', {'class': 'cell-flex', style: 'flex-wrap:wrap'}, [el('b', {text: 'Check on'}), trackerChip(k),
      c ? null : el('button', {'class': 'btn sm', type: 'button', title: 'Search the tracker (targeted), TMDB, and the name against MediaInfo', onclick: function (ev) {
        ev.stopPropagation(); toast('Checking on ' + (TNAME[k] || k) + '…'); runCheck(e, k).then(renderLibrary);
      }}, ['Check']),
      c ? null : el('span', {'class': 'muted small', text: 'Is it already there, does its name say what the file holds'})]));
    if (c) box.appendChild(checkResult(e, k, c));
  });
  return box;
}

// ---------- .torrent creation for the trackers an entry is missing on
function createPanel(e) {
  var missingOn = (S.target_trackers || []).filter(function (k) { return e.trackers.indexOf(k) < 0; });
  if (!missingOn.length) return null;
  return el('div', {'class': 'cell-flex', style: 'flex-wrap:wrap'}, [el('b', {text: 'Create a .torrent for'}),
    el('span', {'class': 'muted small', text: 'with its .nfo (name + MediaInfo, checked before), hashed on the server, private, announce and source from that tracker\'s torrents; upload both, then Seed from Activity, jobs'})]
    .concat(missingOn.map(function (k) {
      var blocked = ((CHK[e._i] || {})[k] || {}).verdict === 'blocked';
      return el('button', {'class': 'btn sm', type: 'button', disabled: blocked ? true : null,
        title: blocked ? 'The check found this release already there: seed that one instead (Verify, Inject)' : null, onclick: function (ev) {
        ev.stopPropagation();
        toast('Reading the file with MediaInfo…');
        api('api/create', {op: 'describe', entry: e._i}).then(function (desc) { releaseForm(e, k, desc); },
          function (err) { toast('Failed: ' + err.message); });
      }}, [trackerChip(k)]);
    })));
}
// Release fields for the tracker's form and the .nfo: prefilled, mandatory ones required.
function releaseForm(e, k, desc) {
  var d = $('dialog'); clear(d);
  var inputs = {}, create = el('button', {'class': 'btn filled', text: 'Create', type: 'button'});
  function check() {
    var missing = desc.mandatory.filter(function (f) { return !inputs[f].value.trim(); });
    desc.mandatory.forEach(function (f) { inputs[f].classList.toggle('missing', !inputs[f].value.trim()); });
    create.disabled = missing.length ? true : null;
    create.title = missing.length ? 'Missing: ' + missing.map(function (f) { return desc.labels[f]; }).join(', ') : '';
  }
  var grid = el('div', {'class': 'form-grid'});
  Object.keys(desc.labels).forEach(function (f) {
    var mandatory = desc.mandatory.indexOf(f) >= 0;
    inputs[f] = el('input', {'class': 'select', type: 'text', value: desc.fields[f] || '', 'aria-label': desc.labels[f]});
    inputs[f].oninput = check;
    grid.appendChild(el('label', {}, [el('span', {text: desc.labels[f] + (mandatory ? ' *' : '')}), inputs[f]]));
  });
  var det = desc.details, tracks = (det.audio || []).map(function (a) {
    return [a.language.toUpperCase(), a.codec, a.channels].filter(Boolean).join(' ') + (a.title ? ' (' + a.title + ')' : '');
  });
  var subs = (det.subtitles || []).map(function (x) { return (x.language.toUpperCase() || '?') + (x.forced ? ' forced' : ''); });
  var stored = storedTorrents(), max = (L && L.created_max) || 0;
  d.appendChild(el('h3', {text: 'Upload to ' + (TNAME[k] || k)}));
  d.appendChild(el('div', {'class': 'body'}, [
    el('div', {'class': 'small', text: desc.name + ' · ' + bytes(e.size) + (det.files > 1 ? ' · ' + det.files + ' files' : '')}),
    el('div', {'class': 'faint small', text: 'Audio: ' + (tracks.join(' · ') || 'none') + (subs.length ? ' — subtitles: ' + subs.join(', ') : '')}),
    grid,
    el('div', {'class': 'faint small', text: '* mandatory. Checked from the name and MediaInfo; the .nfo also carries the full MediaInfo report. ' +
      'Creating reads the ' + bytes(e.size) + ' once from disk, in the background; the .torrent and the .nfo download when ready, keep this page open.'}),
    max && stored.length >= max ? el('div', {'class': 'small', style: 'color:var(--warn)', text: 'Limit of ' + max + ' stored .torrent files reached: the oldest (' +
      stored[0].name + ' for ' + (TNAME[stored[0].target] || stored[0].target) + ') is replaced.'}) : null]));
  var cancel = el('button', {'class': 'btn', text: 'Cancel', type: 'button'});
  d.appendChild(el('div', {'class': 'actions'}, [cancel, create]));
  cancel.onclick = function () { d.close(); };
  create.onclick = function () {
    var fields = {};
    Object.keys(inputs).forEach(function (f) { fields[f] = inputs[f].value.trim(); });
    create.disabled = true;
    api('api/create', {op: 'create', entry: e._i, tracker: k, fields: fields}).then(function (r) {
      d.close(); toast('Hashing in the background: the downloads start when it is ready.'); refreshLive(); awaitCreated(r.job.id);
    }, function (err) { create.disabled = null; toast('Failed: ' + err.message); });
  };
  check();
  d.showModal();
}
// Stored created files, oldest first (the one replaced when the limit is reached).
function storedTorrents() {
  return ((L && L.jobs) || []).filter(function (j) { return j.action === 'create' && j.stored; })
    .sort(function (a, b) { return (a.finished || 0) - (b.finished || 0); });
}
function downloadCreated(id, file) {
  var a = el('a', {href: 'api/created?job=' + encodeURIComponent(id) + (file ? '&file=' + file : ''), download: ''});
  document.body.appendChild(a); a.click(); a.remove();
}
// Polls the job, then downloads the .torrent and the .nfo once. One request at
// a time: /api/status can take longer than the poll period, and overlapping
// answers would each start the downloads.
var AWAITING = {};
function awaitCreated(id) {
  if (AWAITING[id]) return;
  AWAITING[id] = true;
  function poll() {
    api('api/status').then(function (st) {
      L = st;
      var j = (st.jobs || []).filter(function (x) { return x.id === id; })[0];
      if (!j || j.status === 'running' || j.status === 'pending') { setTimeout(poll, 5000); return; }
      delete AWAITING[id]; refreshLive();
      if (j.status === 'done') { downloadCreated(id); setTimeout(function () { downloadCreated(id, 'nfo'); }, 800); toast(j.name + ': .torrent and .nfo ready, upload them to ' + (TNAME[j.target] || j.target) + ', then Seed it from Activity, jobs.'); }
      else if (j.status === 'cancelled') toast(j.name + ': creation cancelled.');
      else toast('Creation failed: ' + (j.note || 'unknown error'));
    }, function () { setTimeout(poll, 5000); });
  }
  setTimeout(poll, 5000);
}
function createdButtons(j) {
  if (j.action === 'create' && j.status === 'running' && /^queued/.test(j.note || '') && D.actions) {
    return el('div', {'class': 'chips'}, [el('button', {'class': 'btn sm danger', type: 'button', title: 'Take it out of the queue, before hashing starts', onclick: function (ev) {
      ev.stopPropagation();
      api('api/create', {op: 'cancel', job: j.id}).then(function () { toast('Cancelled.'); refreshLive(); }, function (err) { toast('Failed: ' + err.message); });
    }}, [icon('remove', 'sm'), 'Cancel'])]);
  }
  if (j.action !== 'create' || j.status !== 'done' || !j.stored || !D.actions) return null;
  return el('div', {'class': 'chips'}, [
    el('button', {'class': 'btn sm', type: 'button', title: 'Download it again', onclick: function (ev) { ev.stopPropagation(); downloadCreated(j.id); }}, [icon('collect', 'sm'), '.torrent']),
    el('button', {'class': 'btn sm', type: 'button', title: 'Download the .nfo again', onclick: function (ev) { ev.stopPropagation(); downloadCreated(j.id, 'nfo'); }}, [icon('collect', 'sm'), '.nfo']),
    el('button', {'class': 'btn sm', type: 'button', title: 'Add it to qBittorrent, hash check skipped', onclick: function (ev) {
      ev.stopPropagation();
      confirmDialog('Seed ' + j.name + '?', 'Once uploaded to ' + (TNAME[j.target] || j.target) + ': adds this torrent to qBittorrent on the library files, ' +
        'hash check skipped. If the tracker gave you another .torrent (dupe, rewritten), add that one instead.', 'Seed')
        .then(function () {
          return api('api/create', {op: 'seed', job: j.id}).then(function () { toast('Added to qBittorrent.'); refreshLive(); });
        }, function () {}).catch(function (err) { toast('Failed: ' + err.message); });
    }}, [icon('start', 'sm'), 'Seed']),
    el('button', {'class': 'btn sm danger', type: 'button', title: 'Delete the stored .torrent', 'aria-label': 'Delete the stored .torrent', onclick: function (ev) {
      ev.stopPropagation();
      api('api/create', {op: 'delete', job: j.id}).then(function () { toast('Deleted.'); refreshLive(); }, function (err) { toast('Failed: ' + err.message); });
    }}, [icon('remove', 'sm')])]);
}

// ---------- release matching: find an entry on the trackers under its release name
var MATCH = {};  // entry index -> {busy, error, res, verify: {candidate id -> result}, choice}
var VERDICT = {exact: ['ok', 'exact size'], extras: ['info', 'size + extras'], other: ['', 'other release']};
function matchPanel(e) {
  var st = MATCH[e._i] || (MATCH[e._i] = {verify: {}});
  var box = el('div', {'class': 'match'});
  function redo() { box.replaceWith(matchPanel(e)); }
  var tmdbIn = el('input', {'class': 'select', type: 'text', inputmode: 'numeric', placeholder: 'TMDB id (optional)',
    'aria-label': 'TMDB id', style: 'width:160px', value: st.tmdb || ''});
  box.appendChild(el('div', {'class': 'cell-flex', style: 'flex-wrap:wrap'}, [
    el('b', {text: 'Find on trackers'}),
    el('span', {'class': 'muted small', text: 'renamed file or title in another language: search every title of the film, match by exact size, prove by piece hashes'}),
    tmdbIn,
    el('button', {'class': 'btn sm', type: 'button', disabled: st.busy ? true : null, onclick: function (ev) {
      ev.stopPropagation();
      st.tmdb = tmdbIn.value.trim(); st.busy = true; st.error = null; st.res = null; st.verify = {}; redo();
      api('api/match', {op: 'search', entry: e._i, tmdb: st.tmdb}).then(function (r) { st.res = r; }, function (err) { st.error = err.message; })
        .then(function () { st.busy = false; redo(); });
    }}, [icon(st.busy ? 'refresh' : 'search', st.busy ? 'sm spin' : 'sm'), st.busy ? 'Searching…' : 'Search']) ]));
  if (st.error) box.appendChild(el('p', {'class': 'small', style: 'color:var(--ko)', text: st.error}));
  var r = st.res;
  if (!r) return box;
  var id = r.identity;
  box.appendChild(el('p', {'class': 'muted small', text: 'Searched as ' + id.titles.join(' / ') + ' ' + id.year +
    (id.id ? ' · TMDB ' + id.id : '') + (id.imdb ? ' · ' + id.imdb : '') + ' on ' + r.trackers.join(', ') +
    ' · local file ' + r.entry.file + ' (' + r.entry.size.toLocaleString() + ' bytes)'}));
  if (id.alternatives && id.alternatives.length > 1) {
    box.appendChild(el('p', {'class': 'faint small', text: 'Not this film? Other TMDB matches: ' +
      id.alternatives.slice(1).map(function (a) { return a.title + ' (' + a.year + ') #' + a.id; }).join(' · ')}));
  }
  r.errors.forEach(function (msg) { box.appendChild(el('p', {'class': 'small', style: 'color:var(--warn)', text: msg})); });
  if (!r.candidates.length) { box.appendChild(el('p', {'class': 'empty', text: 'Nothing found on the trackers.'})); return box; }
  var tb = el('tbody');
  r.candidates.forEach(function (c) {
    var v = VERDICT[c.verdict], proof = st.verify[c.id];
    var flags = [c.seeded_there ? 'already seeded there' : '', c.in_qbt ? 'in qBittorrent' : ''].filter(Boolean).join(' · ');
    tb.appendChild(el('tr', {}, [el('td', {}, [el('span', {'class': 'badge ' + v[0], text: v[1]})]), el('td', {}, [trackerChip(c.tracker)]),
      el('td', {'class': 'name'}, [el('div', {'class': 't', title: c.title, text: c.title}), flags ? el('div', {'class': 'faint small', text: flags}) : null]),
      el('td', {'class': 'num', text: c.size.toLocaleString()}), el('td', {'class': 'num', text: String(c.seeders)}),
      el('td', {'class': 'num'}, [c.verdict === 'other' ? null : el('button', {'class': 'btn sm', type: 'button', disabled: proof === 'busy' ? true : null,
        title: 'Fetch the .torrent and hash its pieces from the local file', onclick: function (ev) {
          ev.stopPropagation();
          st.verify[c.id] = 'busy'; redo();
          api('api/match', {op: 'verify', candidate: c.id}).then(function (res) { st.verify[c.id] = res; },
            function (err) { st.verify[c.id] = {verified: false, reason: err.message}; }).then(redo);
        }}, [icon(proof === 'busy' ? 'refresh' : 'recheck', proof === 'busy' ? 'sm spin' : 'sm'), 'Verify'])])]));
    if (proof && proof !== 'busy') tb.appendChild(el('tr', {'class': 'detail'}, [el('td', {colspan: 6}, [proofPanel(e, st, c, proof, redo)])]));
  });
  box.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {'class': 'subtable'}, [el('thead', {}, [el('tr', {}, [
    el('th', {text: 'Match'}), el('th', {text: 'Tracker'}), el('th', {text: 'Release'}), el('th', {'class': 'num', text: 'Size (bytes)'}),
    el('th', {'class': 'num', text: 'Seeders'}), el('th', {'class': 'num', text: ''})])]), tb])]));
  if (r.others > r.candidates.filter(function (c) { return c.verdict === 'other'; }).length) {
    box.appendChild(el('p', {'class': 'faint small', text: r.others + ' other releases in all: uploading this file there may be refused as a dupe.'}));
  }
  return box;
}
function proofPanel(e, st, c, p, redo) {
  var box = el('div', {'class': 'detail-grid'});
  if (!p.verified) {
    box.appendChild(el('p', {style: 'color:var(--ko)', text: '✗ Not the same file: ' + (p.reason || (p.failed + ' of ' + p.checked + ' pieces differ'))}));
    return box;
  }
  box.appendChild(el('p', {style: 'color:var(--ok)', text: '✓ Same bytes: ' + p.checked + ' of ' + p.checked + ' pieces match (' + p.file + ', ' + p.files + ' file(s) in the torrent)'}));
  var choice = st.choice && st.choice[c.id] !== undefined ? st.choice[c.id] : (p.names[0] || '');
  var opts = el('div', {'class': 'chips', style: 'flex-direction:column;align-items:flex-start'});
  p.names.concat(['']).forEach(function (n) {
    var input = el('input', {type: 'radio', name: 'name-' + c.id, value: n});
    if (n === choice) input.checked = true;
    input.onchange = function () { st.choice = st.choice || {}; st.choice[c.id] = n; redo(); };
    opts.appendChild(el('label', {'class': 'cell-flex small'}, [input, n ? el('code', {text: n}) : el('span', {text: 'keep the current name: ' + p.current})]));
  });
  box.appendChild(el('div', {}, [el('div', {'class': 'muted small', style: 'margin-bottom:8px', text: 'Library file name'}), opts]));
  if (choice && p.sidecars.length) {
    var oldStem = p.current.replace(/\.[^.]+$/, ''), newStem = choice.replace(/\.[^.]+$/, '');
    var script = p.sidecars.map(function (s) {
      return 'mv -n -- "' + e.folder + '/' + s + '" "' + e.folder + '/' + newStem + s.slice(oldStem.length) + '"';
    }).join('\n');
    var code = el('code', {text: script});
    box.appendChild(el('div', {}, [el('div', {'class': 'cell-flex', style: 'justify-content:space-between;flex-wrap:wrap'}, [
      el('span', {'class': 'muted small', text: 'Sidecars are not in the torrent (seedbox mounts the media read-only): rename them on the NAS, from the media share root'}),
      el('button', {'class': 'btn sm', type: 'button', onclick: function (ev) { ev.stopPropagation(); copyText(script, code); }}, [icon('copy', 'sm'), 'Copy'])]),
      el('div', {'class': 'cmd'}, [code])]));
  }
  function apply(mode, title, body) {
    return function (ev) {
      ev.stopPropagation();
      confirmDialog(title, body, 'Go').then(function () {
        return api('api/match', {op: 'apply', infohash: p.infohash, mode: mode, name: choice}).then(function () {
          toast('Started in the background: follow it in Activity, jobs.'); refreshLive();
        });
      }, function () {}).catch(function (err) { toast('Failed: ' + err.message); });
    };
  }
  var buttons = [];
  if (!p.in_qbt) {
    buttons.push(el('button', {'class': 'btn sm filled', type: 'button', onclick: apply('inject', 'Seed this release?',
      'Adds the torrent to qBittorrent, stopped, pointing at the library file; starts it only if the recheck confirms 100 %. Nothing is downloaded into the library.')},
      [icon('start', 'sm'), 'Inject']));
    if (choice) buttons.push(el('button', {'class': 'btn sm', type: 'button', onclick: apply('inject_rename', 'Seed and rename?',
      'Injects the release, then renames ' + p.current + ' to ' + choice + ' through qBittorrent; the other torrents on this file follow.')},
      [icon('move', 'sm'), 'Inject + rename']));
  }
  if (choice) buttons.push(el('button', {'class': 'btn sm', type: 'button', onclick: apply('rename', 'Rename the library file?',
    p.current + ' becomes ' + choice + ', through qBittorrent (needs a torrent on this file).')}, [icon('move', 'sm'), 'Rename only']));
  box.appendChild(el('div', {'class': 'chips'}, buttons));
  return box;
}

function renderLibrary() {
  var rows = visibleEntries(), body = $('lib-body'); clear(body);
  $('lib-count').textContent = rows.length + ' of ' + D.entries.length;
  updateChips();
  document.querySelectorAll('#lib-table th[data-sort]').forEach(function (th) { th.classList.toggle('sorted', th.dataset.sort === sortKey); });
  var frag = document.createDocumentFragment(), view = paged('library', rows, 10, 20, Infinity, renderLibrary);
  libShown = view.rows;
  view.rows.forEach(function (e) {
    var cb = el('input', {type: 'checkbox', 'aria-label': 'Select', onclick: function (ev) { ev.stopPropagation(); }});
    cb.checked = !!selected[e._i];
    cb.onchange = function () { if (cb.checked) selected[e._i] = true; else delete selected[e._i]; updateBatch(); };
    var issues = el('td', {'class': 'num'});
    if (e._problems) issues.appendChild(el('span', {'class': 'badge ko', text: String(e._problems)}));
    else if (e._dup) issues.appendChild(el('span', {'class': 'badge warn', text: 'dup'}));
    var tr = el('tr', {'class': 'row' + (openRow === e._i ? ' open' : '')}, [el('td', {}, [cb]), el('td', {}, [statusBadge(e)]),
      el('td', {'class': 'name'}, [el('div', {'class': 't', title: e.name, text: label(e.name)}), el('div', {'class': 'faint small', text: e.folder})]),
      el('td', {'class': 'opt'}, [el('div', {'class': 'tracks'}, e.trackers.map(trackerChip))]),
      el('td', {'class': 'num', text: bytes(e.size)}), el('td', {'class': 'num opt', text: bytes(e.uploaded)}), issues, checkCell(e)]);
    tr.onclick = function () { openRow = openRow === e._i ? null : e._i; renderLibrary(); };
    frag.appendChild(tr);
    if (openRow === e._i) frag.appendChild(el('tr', {'class': 'detail'}, [el('td', {colspan: 8}, [entryDetail(e)])]));
  });
  body.appendChild(frag);
  $('lib-empty').hidden = rows.length > 0;
  var more = $('lib-more'); clear(more);
  more.appendChild(view.controls);
  updateBatch();
}
function updateBatch() {
  var n = Object.keys(selected).length;
  $('batch').classList.toggle('show', n > 0);
  $('batch-count').textContent = n + ' selected';
}

// ---------- outside library and warnings
var REASONS = {
  link_only: ['warn', 'Only cross-seed links', 'The library copy was removed; the torrent seeds from its link folder.'],
  outside: ['info', 'Outside the library', 'Stored outside the library roots (another share, download folder).'],
  downloading: ['info', 'Downloading', 'Not on disk yet.'],
  missing: ['ko', 'Files missing', 'qBittorrent points to files that are not on disk.'],
  unrecognised: ['warn', 'Not recognised', 'Inside a library root but matched to no entry.']
};
function renderOutside() {
  var box = $('outside'); clear(box);
  // Unfinished downloads in the transient folder are the download queue, not a problem.
  var list = D.unmatched.filter(function (u) { return u.reason !== 'transient'; });
  var queued = D.unmatched.length - list.length;
  if (queued) {
    box.appendChild(el('p', {'class': 'muted small', text: queued + ' unfinished download(s) in the transient folder, not counted here (live in Activity).'}));
  }
  if (!list.length) { box.appendChild(el('p', {'class': 'empty', text: 'Every torrent matches a library entry.'})); return; }
  var tb = el('tbody');
  list.forEach(function (u) {
    var r = REASONS[u.reason] || ['', u.reason, ''];
    tb.appendChild(el('tr', {}, [el('td', {}, [el('span', {'class': 'badge ' + r[0], title: r[2], text: r[1]})]),
      el('td', {'class': 'name'}, [el('div', {'class': 't', title: u.name, text: u.name}), el('div', {'class': 'path', text: u.path})]),
      el('td', {}, [el('div', {'class': 'tracks'}, u.trackers.map(trackerChip))]), el('td', {}, [stateBadge(u.state)]),
      el('td', {'class': 'num'}, [D.actions ? el('button', {'class': 'btn sm danger', type: 'button', title: 'Remove', 'aria-label': 'Remove',
        onclick: function () {
          confirmAct('remove', [u.hash], 'Remove this torrent?', u.name + '. ' + (u.reason === 'link_only'
            ? 'The library copy is already gone: with its link files, the space is freed (unless another torrent uses them).'
            : 'Its files stay on disk unless they are cross-seed links or transient downloads.'), null, u.reason === 'link_only');
        }}, [icon('remove', 'sm')]) : null])]));
  });
  box.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {}, [el('thead', {}, [el('tr', {}, [
    el('th', {text: 'Why'}), el('th', {text: 'Torrent'}), el('th', {text: 'Tracker'}), el('th', {text: 'State'}),
    el('th', {'class': 'num', text: ''})])]), tb])]));
  var legend = el('div', {'class': 'legend'});
  Object.keys(REASONS).forEach(function (k) { legend.appendChild(el('span', {}, [el('b', {style: 'color:var(--t1);font-weight:500', text: REASONS[k][1] + ':'}), REASONS[k][2]])); });
  box.appendChild(legend);
}
function renderOrphans() {
  var box = $('orphans'); clear(box);
  var o = D.orphan_links;
  if (!o || !o.count) { box.appendChild(el('p', {'class': 'empty', text: 'No leftover link file: every file in the cross-seed folders belongs to a torrent.'})); return; }
  box.appendChild(el('p', {'class': 'muted', text: o.count + ' file(s) no torrent uses (torrents removed without their files): ' +
    bytes(o.bytes) + ' freed by deleting them; the others are extra names of data kept elsewhere.'}));
  var code = el('code', {text: o.script});
  box.appendChild(el('div', {'class': 'cell-flex', style: 'justify-content:space-between;flex-wrap:wrap;margin:8px 0'}, [
    el('span', {'class': 'muted small', text: 'Run on the NAS from the media share root (the folder holding the cross-seed folder). seedbox mounts the media read-only.'}),
    el('button', {'class': 'btn sm', type: 'button', onclick: function () { copyText(o.script, code); }}, [icon('copy', 'sm'), 'Copy script'])]));
  box.appendChild(el('div', {'class': 'cmd', style: 'max-height:160px;overflow:auto;align-items:flex-start'}, [code]));
  box.appendChild(dropList(o.files.slice().sort(function (a, b) { return b.size - a.size; }), function (f) {
    return el('div', {'class': 'item'}, [el('span', {'class': 'badge ' + (f.links === 1 ? 'warn' : ''), text: f.links === 1 ? 'frees ' + bytes(f.size) : f.links + ' links'}),
      el('span', {'class': 'name', title: f.path, text: f.path})]);
  }, 'Show files'));
}
function renderWarnings() {
  var box = $('warnings'); clear(box);
  if (!D.warnings.length) { box.appendChild(el('p', {'class': 'empty', text: 'No warning.'})); return; }
  D.warnings.forEach(function (w) { box.appendChild(el('div', {'class': 'issue'}, [el('span', {'class': 'badge warn', text: 'warning'}), el('div', {'class': 'txt', text: w})])); });
}

// ---------- navigation, collect, wiring
function wireNav() {
  var links = document.querySelectorAll('.rail a[href^="#"]');
  var crumb = $('crumb');
  var obs = new IntersectionObserver(function (items) {
    items.forEach(function (it) {
      if (!it.isIntersecting) return;
      links.forEach(function (a) {
        var on = a.getAttribute('href') === '#' + it.target.id;
        a.classList.toggle('active', on);
        if (on) crumb.textContent = a.textContent.trim();
      });
    });
  }, {rootMargin: '-80px 0px -60% 0px'});
  document.querySelectorAll('main section[id]').forEach(function (s) { obs.observe(s); });
}
function collectNow() {
  if (!LIVE || !D.actions) { toast(LIVE ? 'Actions are disabled: set [service] actions = true.' : 'Needs seedbox run.'); return; }
  var b = $('collect'); b.disabled = true;
  api('api/collect', {}).then(function () {
    toast('Collection started, the page reloads when it is done.');
    var poll = setInterval(function () {
      api('api/collect').then(function (st) {
        if (!st.running) { clearInterval(poll); if (st.error) { toast('Collection failed: ' + st.error); b.disabled = false; } else location.reload(); }
      });
    }, 4000);
  }).catch(function (e) { toast('Failed: ' + e.message); b.disabled = false; });
}

function init() {
  var home = !!$('overview'), lib = !!$('lib-body');
  if (home) {
    renderHero(); renderOverview(); renderOutside(); renderWarnings(); renderOrphans();
  }
  if (lib) {
    renderLibraryHero(); renderDuplicates(); buildLibraryControls();
    var f = new URLSearchParams(location.search).get('filter');
    if (f && CHIPFN[f]) setFilter(f); else renderLibrary();
    $('search').addEventListener('input', function (e) {
      query = e.target.value.toLowerCase(); firstPage('library'); renderLibrary();
    });
  }
  renderLive();
  wireNav();
  $('live-refresh').onclick = function () { refreshLive(); refreshMetrics(); };
  $('errors-clear').onclick = function () {
    api('api/errors/clear', {}).then(refreshLive, function (e) { toast('Failed: ' + e.message); });
  };
  $('auto').onclick = function () { setAuto($('auto').getAttribute('aria-pressed') !== 'true'); };
  $('collect').onclick = collectNow;
  if (!D.actions) $('collect').hidden = true;
  document.querySelectorAll('#m-range .chip').forEach(function (c) {
    c.onclick = function () {
      hours = Number(c.dataset.h);
      document.querySelectorAll('#m-range .chip').forEach(function (x) { x.setAttribute('aria-pressed', x === c ? 'true' : 'false'); });
      refreshMetrics();
    };
  });
  var resize;
  if (home) window.addEventListener('resize', function () { clearTimeout(resize); resize = setTimeout(function () { renderOverview(); renderSystem(); }, 200); });
  refreshLive(); refreshMetrics();
  setAuto(LIVE);
}
init();
