// Upload section of the library page: films of the library missing on the tracker of [upload], from
// /api/upload. Check asks the server to search the tracker (Prowlarr), TMDB
// and MediaInfo; Send queues uploads of checked films. Every text from the
// data is inserted with textContent. Its own scope: app.js runs on the same page.
(function () {
'use strict';

var DATA = null, CHECKS = {}, OPEN = {}, SELECTED = {}, PROOF = {}, PAGE = 0, STEP = 20;
var SORT = {key: 'seeds', dir: -1}, F = {q: '', on: {res: {}, lang: {}, seeded: {}, check: {}}};
var GIB = Math.pow(1024, 3);

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
function $(id) { return document.getElementById(id); }
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
function size(n) { n = Number(n || 0); return n >= GIB ? (n / GIB).toFixed(1) + ' GiB' : (n / 1048576).toFixed(0) + ' MiB'; }
var toastTimer = null;
function toast(msg) {
  var t = $('toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.style.display = 'none'; }, 6000);
}
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
var VERDICT = {clear: ['ok', 'clear'], warn: ['warn', 'check it'], blocked: ['ko', 'already there'], incomplete: ['warn', 'search failed']};
var KIND = {same_size: ['ko', 'same size'], same_release: ['ko', 'same group + res.'], same_resolution: ['warn', 'same resolution'], other: ['', 'other release']};
function verdictOf(f) { var c = CHECKS[f.index] || f.check; return c ? c.verdict : ''; }

// ---------- status
function renderStatus() {
  var st = DATA.status || {}, box = clear($('up-status'));
  clear($('up-title')).append('Upload to ', st.tracker ? trackerChip(st.tracker) : '?');
  var access = st.approved === true ? ['ok', 'API access granted'] : st.approved === false ? ['ko', 'API access refused'] : ['warn', 'API access unknown'];
  var fields = Object.keys(st.fields || {}).map(function (k) { return k + ' = ' + st.fields[k]; }).join(' · ');
  box.appendChild(el('div', {'class': 'chips'}, [
    el('span', {'class': 'badge ' + access[0], text: access[1]}),
    el('span', {'class': 'badge ' + (st.send ? 'info' : 'warn'), text: st.send ? 'Sending on' : 'Sending off ([upload] send = false)'}),
    el('span', {'class': 'badge', text: '.nfo: ' + (st.nfo || '?')}),
    st.rate ? el('span', {'class': 'badge', text: st.rate.used + ' / ' + st.rate.limit + ' tries this hour' + (st.rate.wait_s ? ', next in ' + Math.ceil(st.rate.wait_s / 60) + ' min' : '')}) : null,
    DATA.actions ? null : el('span', {'class': 'badge warn', text: 'Actions disabled: checks and sending need [service] actions = true'})
  ]));
  box.appendChild(el('p', {'class': 'small muted', text: (st.base || '') + (fields ? ' · fields sent: ' + fields : ' · no extra field configured')}));
  if (st.access) box.appendChild(el('p', {'class': 'small', style: 'color:var(--warn)', text: st.access}));
  if (st.answer) {
    // The API's own answer (categories, options...): its fields name what [upload.api.submit.fields] can send.
    box.appendChild(el('details', {}, [el('summary', {'class': 'small', text: 'What the API answered'}),
      el('pre', {'class': 'small', style: 'white-space:pre-wrap;max-height:320px;overflow:auto', text: JSON.stringify(st.answer, null, 2).slice(0, 20000)})]));
  }
}

// ---------- filters and list
// Filter chips, as in the library: any chip of a group, every group.
var MAIN_RES = ['2160p', '1080p', '720p'];
function groups() {
  var trackers = {};
  (DATA.films || []).forEach(function (f) { f.trackers.forEach(function (t) { trackers[t] = true; }); });
  return [
    {id: 'res', label: 'Resolution', chips: MAIN_RES.map(function (r) { return [r, r, function (f) { return f.resolution === r; }]; })
      .concat([['other', 'Other', function (f) { return MAIN_RES.indexOf(f.resolution) < 0; }]])},
    {id: 'lang', label: 'Language', chips: [['MULTI', 'MULTI'], ['FRENCH', 'FRENCH'], ['VOSTFR', 'VOSTFR'], ['VO', 'No French marker']]
      .map(function (l) { return [l[0], l[1], function (f) { return f.language === l[0]; }]; })},
    {id: 'seeded', label: 'Seeded on', chips: [['none', 'Nowhere', function (f) { return !f.trackers.length; }]]
      .concat(Object.keys(trackers).sort().map(function (t) { return [t, t, function (f) { return f.trackers.indexOf(t) >= 0; }, true]; }))},
    {id: 'check', label: 'Check', chips: [['none', 'Not checked', ''], ['clear', 'Clear'], ['warn', 'Check it'], ['blocked', 'Already there'], ['incomplete', 'Search failed']]
      .map(function (c) { var v = c[0] === 'none' ? '' : c[0]; return [c[0], c[1], function (f) { return verdictOf(f) === v; }]; })}
  ];
}
function passes(f, skip) {
  return groups().every(function (g) {
    var keys = Object.keys(F.on[g.id]);
    return g.id === skip || !keys.length || g.chips.some(function (c) { return F.on[g.id][c[0]] && c[2](f); });
  });
}
function renderFilters() {
  var box = clear($('up-filters')), any = Object.keys(F.on).some(function (g) { return Object.keys(F.on[g]).length; });
  box.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-bottom:16px';
  function chip(pressed, kids, onclick) {
    return el('button', {'class': 'chip', type: 'button', 'aria-pressed': pressed ? 'true' : 'false', onclick: onclick}, [icon('check', 'check')].concat(kids));
  }
  box.appendChild(el('div', {'class': 'chips'}, [chip(!any, ['All'], function () { F.on = {res: {}, lang: {}, seeded: {}, check: {}}; PAGE = 0; renderFilters(); renderRows(); })]));
  groups().forEach(function (g) {
    var row = el('div', {'class': 'chips', style: 'align-items:center'}, [el('span', {'class': 'muted small', style: 'width:80px', text: g.label})]);
    g.chips.forEach(function (c) {
      // Count: the films this chip would show, the other groups' filters applied.
      var n = (DATA.films || []).filter(function (f) { return matchesQuery(f) && passes(f, g.id) && c[2](f); }).length;
      row.appendChild(chip(F.on[g.id][c[0]], [c[3] ? trackerChip(c[0]) : c[1], el('span', {'class': 'n', text: String(n)})], function () {
        if (F.on[g.id][c[0]]) delete F.on[g.id][c[0]]; else F.on[g.id][c[0]] = true;
        PAGE = 0; renderFilters(); renderRows();
      }));
    });
    box.appendChild(row);
  });
}
function matchesQuery(f) {
  var q = F.q.toLowerCase();
  return !q || (f.name + ' ' + f.folder).toLowerCase().indexOf(q) >= 0;
}
function shown() {
  var rows = (DATA.films || []).filter(function (f) { return matchesQuery(f) && passes(f); });
  var k = SORT.key;
  rows.sort(function (a, b) {
    var x = k === 'trackers' ? a.trackers.length : k === 'check' ? verdictOf(a) : a[k];
    var y = k === 'trackers' ? b.trackers.length : k === 'check' ? verdictOf(b) : b[k];
    return (x < y ? -1 : x > y ? 1 : 0) * SORT.dir;
  });
  return rows;
}
// Create the .torrent and .nfo by hand (the library's dialog), for an upload through the
// tracker's site while sending through its API is off. Not for a release it already has.
function createButton(f) {
  var c = CHECKS[f.index], blocked = c && c.verdict === 'blocked', entry = D.entries[f.index];
  return el('div', {'class': 'cell-flex', style: 'flex-wrap:wrap'}, [
    el('button', {'class': 'btn sm', type: 'button', disabled: blocked || !DATA.actions || !entry ? true : null,
      title: blocked ? 'The tracker already has this release: seed it instead (Verify, Inject)' : 'Hashed on the server, the .torrent and .nfo download when ready',
      onclick: function (ev) {
        ev.stopPropagation();
        toast('Reading the file with MediaInfo…');
        api('api/create', {op: 'describe', entry: f.index}).then(function (desc) { releaseForm(entry, DATA.status.tracker, desc); },
          function (err) { toast('Failed: ' + err.message); });
      }}, [icon('collect', 'sm'), 'Create .torrent + .nfo']),
    el('span', {'class': 'muted small', text: c ? '' : 'Check it first: the tracker may already have it.'})]);
}
function detail(f) {
  var c = CHECKS[f.index];
  if (!c) return el('div', {'class': 'small', style: 'display:flex;flex-direction:column;gap:8px'}, [createButton(f),
    el('p', {'class': 'small muted', text: 'Not checked in this session: select it, then Check.'})]);
  var box = el('div', {'class': 'small', style: 'display:flex;flex-direction:column;gap:8px'});
  box.appendChild(createButton(f));
  (c.reasons || []).forEach(function (r) { box.appendChild(el('div', {text: '• ' + r})); });
  if (c.languages && c.languages.audio) {
    box.appendChild(el('div', {text: 'MediaInfo: ' + c.languages.group + ' · audio ' + (c.languages.audio.join(', ') || '?') +
      ' · subtitles ' + (c.languages.subtitles.join(', ') || 'none')}));
  }
  if ((c.tmdb || []).length) {
    box.appendChild(el('div', {}, ['TMDB: '].concat(c.tmdb.map(function (t, i) {
      return el('span', {}, [i ? ' · ' : '', el('a', {href: t.url, target: '_blank', rel: 'noopener', text: t.title + ' (' + (t.year || '?') + ')'})]);
    }))));
  }
  box.appendChild(el('div', {'class': 'muted', text: 'Searched: ' + (c.searched || []).join(' | ')}));
  if ((c.errors || []).length) box.appendChild(el('div', {style: 'color:var(--warn)', text: 'Failed: ' + c.errors.join(' | ')}));
  if ((c.matches || []).length) {
    var tb = el('tbody');
    c.matches.forEach(function (m) {
      var k = KIND[m.kind];
      tb.appendChild(el('tr', {}, [el('td', {}, [el('span', {'class': 'badge ' + k[0], text: k[1]})]),
        el('td', {}, [m.info_url ? el('a', {href: m.info_url, target: '_blank', rel: 'noopener', text: m.title}) : m.title]),
        el('td', {'class': 'num', text: size(m.size)}), el('td', {'class': 'num', text: String(m.seeders)}),
        el('td', {}, [m.candidate && DATA.actions ? proofButtons(f, m) : null])]));
    });
    box.appendChild(el('table', {'class': 'dense subtable'}, [tb]));
  } else box.appendChild(el('div', {text: 'Nothing of this film found on the tracker.'}));
  return box;
}
function renderRows() {
  var rows = shown(), body = clear($('up-rows')), n = Math.max(Math.ceil(rows.length / STEP), 1);
  PAGE = Math.min(PAGE, n - 1);
  $('up-count').textContent = rows.length + ' shown of ' + (DATA.films || []).length;
  rows.slice(PAGE * STEP, (PAGE + 1) * STEP).forEach(function (f) {
    var v = verdictOf(f), badge = VERDICT[v];
    var box = el('input', {type: 'checkbox', 'aria-label': 'Select', onclick: function (ev) { ev.stopPropagation(); }, onchange: function () {
      if (box.checked) SELECTED[f.index] = true; else delete SELECTED[f.index];
      renderBatch();
    }});
    box.checked = !!SELECTED[f.index];
    body.appendChild(el('tr', {style: 'cursor:pointer', onclick: function () { OPEN[f.index] = !OPEN[f.index]; renderRows(); }}, [
      el('td', {}, [box]),
      el('td', {'class': 'name'}, [el('div', {'class': 't', title: f.name, text: f.name}), el('div', {'class': 'faint small', text: f.folder})]),
      el('td', {text: f.resolution || '?'}), el('td', {text: f.language}),
      el('td', {'class': 'num', text: size(f.size)}), el('td', {'class': 'opt'}, f.trackers.length ? [el('div', {'class': 'chips'}, f.trackers.map(trackerChip))] : [el('span', {'class': 'faint small', text: 'none'})]),
      el('td', {'class': 'num', text: String(f.seeds)}), el('td', {'class': 'num opt', text: size(f.uploaded)}),
      el('td', {}, [badge ? el('span', {'class': 'badge ' + badge[0], text: badge[1]}) : el('span', {'class': 'faint small', text: '—'})])
    ]));
    if (OPEN[f.index]) body.appendChild(el('tr', {}, [el('td', {}), el('td', {colspan: '8'}, [detail(f)])]));
  });
  var more = clear($('up-more'));
  if (n > 1) {
    more.appendChild(el('button', {'class': 'chip', type: 'button', disabled: PAGE === 0 ? true : null, onclick: function () { PAGE--; renderRows(); }}, ['‹']));
    more.appendChild(el('span', {'class': 'faint small', text: 'Page ' + (PAGE + 1) + ' of ' + n}));
    more.appendChild(el('button', {'class': 'chip', type: 'button', disabled: PAGE >= n - 1 ? true : null, onclick: function () { PAGE++; renderRows(); }}, ['›']));
  }
  renderBatch();
}
function selection() { return Object.keys(SELECTED).map(Number); }
function renderBatch() {
  var sel = selection(), st = DATA.status || {};
  $('up-batch').classList.toggle('show', sel.length > 0);
  $('up-batch-count').textContent = sel.length + ' selected';
  var ready = st.send && st.approved !== false && DATA.actions;
  $('up-send').disabled = !ready;
  $('up-send').title = ready ? 'Upload the checked films, one at a time' : 'Sending needs API access and [upload] send = true';
  $('up-check').disabled = !DATA.actions;
}

// ---------- the tracker already has it: seed its torrent instead (verify + inject), or keep it for review
function proofButtons(f, m) {
  var p = PROOF[m.candidate], box = el('div', {'class': 'chips', onclick: function (ev) { ev.stopPropagation(); }});
  function redo() { renderRows(); }
  if (p === 'busy') return el('span', {'class': 'faint small', text: 'working…'});
  if (!p) {
    box.appendChild(el('button', {'class': 'btn sm', type: 'button', title: 'Fetch its .torrent and hash pieces of the local file against it',
      onclick: function () {
        PROOF[m.candidate] = 'busy'; redo();
        api('api/match', {op: 'verify', candidate: m.candidate}).then(function (r) { PROOF[m.candidate] = r; },
          function (e) { PROOF[m.candidate] = {verified: false, reason: e.message}; }).then(redo);
      }}, ['Verify']));
  } else if (p.kept) {
    box.appendChild(el('span', {'class': 'badge', text: 'kept for review'}));
  } else if (p.verified) {
    box.appendChild(el('span', {'class': 'badge ok', text: 'same bytes'}));
    if (p.in_qbt) box.appendChild(el('span', {'class': 'badge', text: 'already in qBittorrent'}));
    else box.appendChild(el('button', {'class': 'btn sm filled', type: 'button', title: 'Add it to qBittorrent on the library file, started once the recheck says 100 %',
      onclick: function () {
        api('api/match', {op: 'apply', infohash: p.infohash, mode: 'inject'}).then(function () {
          p.in_qbt = true; toast('Inject started: follow it in the dashboard, Activity, jobs.'); redo();
        }, function (e) { toast('Failed: ' + e.message); });
      }}, ['Inject']));
  } else {
    box.appendChild(el('span', {'class': 'badge warn', text: p.reason || 'not the same bytes'}));
  }
  if (p !== 'busy' && !(p && (p.verified || p.kept))) {
    box.appendChild(el('button', {'class': 'btn sm', type: 'button', title: 'Save the tracker\'s .torrent and what is known of the local file, to be matched by hand',
      onclick: function () {
        var reason = p && p.reason ? 'verify: ' + p.reason : 'not verified';
        api('api/upload', {op: 'review', candidate: m.candidate, reason: reason}).then(function () {
          PROOF[m.candidate] = {kept: true}; toast('Kept in review/: ask for it to be matched.'); load();
        }, function (e) { toast('Failed: ' + e.message); });
      }}, ['Keep for review']));
  }
  return box;
}
function renderReviews() {
  var list = DATA.reviews || [], box = $('up-reviews');
  clear(box);
  if (!list.length) { box.appendChild(el('p', {'class': 'empty', text: 'Nothing waiting.'})); return; }
  var tb = el('tbody');
  list.forEach(function (r) {
    tb.appendChild(el('tr', {}, [el('td', {'class': 'name'}, [el('div', {'class': 't', title: r.release, text: r.release})]),
      el('td', {text: r.tracker}), el('td', {'class': 'small muted', text: r.reason || ''})]));
  });
  box.appendChild(el('div', {'class': 'table-wrap'}, [el('table', {'class': 'dense'}, [tb])]));
}

// ---------- check and send
var busy = false;
function checkSelected() {
  if (busy) return;
  var todo = selection();
  busy = true;
  var done = 0;
  function next() {
    if (!todo.length) { busy = false; toast('Checked ' + done + ' film(s).'); renderFilters(); renderRows(); return; }
    var i = todo.shift();
    toast('Checking ' + (done + 1) + ' / ' + (done + todo.length + 1) + ': searching the tracker, TMDB, MediaInfo…');
    api('api/upload', {op: 'check', entry: i}).then(function (r) { CHECKS[i] = r; OPEN[i] = true; }, function (e) {
      CHECKS[i] = {verdict: 'incomplete', reasons: [e.message], matches: [], searched: []};
    }).then(function () { done++; renderRows(); next(); });
  }
  next();
}
function sendSelected() {
  var sel = selection();
  var clearOnes = sel.filter(function (i) { return (CHECKS[i] || {}).verdict === 'clear'; });
  var warned = sel.filter(function (i) { return (CHECKS[i] || {}).verdict === 'warn'; });
  var skipped = sel.length - clearOnes.length - warned.length;
  var d = $('dialog'); clear(d);
  var incl = el('input', {type: 'checkbox', id: 'incl'});
  d.appendChild(el('h3', {text: 'Send ' + clearOnes.length + ' film(s) to ' + DATA.status.tracker + '?'}));
  d.appendChild(el('p', {'class': 'small', text: 'Each one: .torrent and .nfo created, sent, then seeded from the library file. ' +
    'Duplicates and unidentified films stop for manual review, never retried.' + (skipped ? ' ' + skipped + ' not checked or already there: left out.' : '')}));
  if (warned.length) d.appendChild(el('label', {'class': 'small'}, [incl, ' Also send the ' + warned.length + ' marked "check it" (I read their details)']));
  var cancel = el('button', {'class': 'btn', type: 'button', text: 'Cancel', onclick: function () { d.close(); }});
  var ok = el('button', {'class': 'btn filled', type: 'button', text: 'Send', onclick: function () {
    var list = clearOnes.concat(incl.checked ? warned : []);
    d.close();
    if (!list.length) { toast('Nothing to send.'); return; }
    api('api/upload', {op: 'send', entries: list}).then(function (r) {
      toast(r.jobs.length + ' upload(s) queued: follow them in the dashboard, Activity, jobs.');
      SELECTED = {}; load();
    }, function (e) { toast('Failed: ' + e.message); });
  }});
  d.appendChild(el('div', {'class': 'actions'}, [cancel, ok]));
  d.showModal();
}

function load() {
  return api('api/upload').then(function (r) {
    DATA = r;
    $('upload').hidden = false;
    if ($('nav-upload')) $('nav-upload').hidden = false;
    (r.films || []).forEach(function (f) { if (f.check && !CHECKS[f.index]) CHECKS[f.index] = f.check; });
    renderStatus(); renderFilters(); renderRows(); renderReviews();
  }, function (e) {
    // No upload API configured (404, config changed since the page was written): hide the section.
    if (/no upload API/.test(e.message)) {
      $('upload').hidden = true;
      if ($('nav-upload')) $('nav-upload').hidden = true;
      return;
    }
    $('upload').hidden = false; clear($('up-status')).appendChild(el('p', {'class': 'empty', text: 'Unavailable: ' + e.message}));
  });
}

$('up-q').oninput = function (e) { F.q = e.target.value; PAGE = 0; renderFilters(); renderRows(); };
$('up-all').onchange = function (e) {
  shown().slice(PAGE * STEP, (PAGE + 1) * STEP).forEach(function (f) { if (e.target.checked) SELECTED[f.index] = true; else delete SELECTED[f.index]; });
  renderRows();
};
document.querySelectorAll('th[data-up-sort]').forEach(function (th) {
  th.onclick = function () {
    var k = th.getAttribute('data-up-sort');
    SORT = {key: k, dir: SORT.key === k ? -SORT.dir : (k === 'name' || k === 'language' ? 1 : -1)};
    renderRows();
  };
});
$('up-check').onclick = checkSelected;
$('up-send').onclick = sendSelected;
$('up-clear').onclick = function () { SELECTED = {}; renderRows(); };
$('live-refresh').addEventListener('click', load);
load();
})();
