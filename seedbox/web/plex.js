'use strict';
// Plex page: libraries, playback against the disk load, films never watched,
// transcoding risks per player, recently added, server state. Data from
// GET /api/plex (seedbox reads Plex's API), disk samples from /api/metrics.
// Uses the helpers of app.js (el, kpi, hbars, lineChart, paged, badges).

var PX = null, PXM = null, pxLib = '', pxDevice = 'ps5';
var PX_DEVICES = {ps5: 'PS5 (Plex app)', appletv: 'Apple TV 4K (Plex app)'};
var MBIT = 1000000;

function pxTime(sec) { return sec ? ago(sec * 1000) : '—'; }
function pxTable(head, rows) {
  return el('div', {'class': 'table-wrap'}, [el('table', {'class': 'dense'}, [
    el('thead', {}, [el('tr', {}, head.map(function (h) { return el('th', {'class': h[1] || '', text: h[0]}); }))]),
    el('tbody', {}, rows)])]);
}
function pxEmpty(box, text) { clear(box).appendChild(el('p', {'class': 'empty', text: text})); }
function pxTitle(f) { return f.title + (f.year ? ' (' + f.year + ')' : ''); }
function pxSeed(seed) {
  if (!seed) return el('span', {'class': 'badge', text: 'Not in the library'});
  var box = el('div', {'class': 'chips', style: 'flex-wrap:nowrap'}, [statusBadge(seed)]);
  (seed.trackers || []).forEach(function (k) { box.appendChild(trackerChip(k)); });
  return box;
}

function renderPlexMessage(text) {
  ['px-total', 'px-films', 'px-episodes', 'px-watched', 'px-streams', 'px-disk', 'px-seeding'].forEach(function (id) {
    kpi($(id), id.slice(3), 'info', '—', '', text);
  });
  ['px-libs', 'px-progress', 'px-sessions', 'px-unwatched', 'px-transcode', 'px-recent', 'px-server', 'px-butler', 'px-activities'].forEach(function (id) {
    pxEmpty($(id), text);
  });
}

function renderPlexLibraries() {
  var libs = PX.libraries.slice().sort(function (a, b) { return b.bytes - a.bytes; });
  var total = libs.reduce(function (s, l) { return s + l.bytes; }, 0);
  var films = libs.filter(function (l) { return l.type === 'movie'; }), shows = libs.filter(function (l) { return l.type === 'show'; });
  var nFilms = films.reduce(function (s, l) { return s + l.items; }, 0), wFilms = films.reduce(function (s, l) { return s + l.watched; }, 0);
  var nEps = shows.reduce(function (s, l) { return s + l.episodes; }, 0), wEps = shows.reduce(function (s, l) { return s + l.watched; }, 0);
  var nShows = shows.reduce(function (s, l) { return s + l.items; }, 0);
  kpi($('px-total'), 'Plex on disk', 'disk', bytes(total), '', libs.length + ' libraries · ' + PX.server.name + ' · Plex ' + PX.server.version);
  kpi($('px-films'), 'Films', 'library', nFilms, '', wFilms + ' watched · ' + (nFilms - wFilms) + ' never',
    function () { location.hash = '#plex-unwatched'; });
  kpi($('px-episodes'), 'Episodes', 'library', nEps, '', 'in ' + nShows + ' series · ' + wEps + ' watched');
  kpi($('px-watched'), 'Watched', 'start', nFilms ? pct(wFilms / nFilms * 100) : '—', 'of the films',
    (nEps ? pct(wEps / nEps * 100) + ' of the episodes · ' : '') + libs.reduce(function (s, l) { return s + l.in_progress; }, 0) + ' in progress');
  $('px-sub').textContent = 'what Plex holds, read from its API · ' + bytes(total) + ' in ' + libs.length + ' libraries';

  var rows = libs.map(function (l) {
    var share = total ? l.bytes / total * 100 : 0, count = l.type === 'show' ? l.episodes : l.items;
    return el('tr', {}, [
      el('td', {}, [el('b', {style: 'font-weight:500', text: l.title}), l.refreshing ? el('span', {'class': 'badge info', style: 'margin-left:8px', text: 'scanning'}) : null]),
      el('td', {'class': 'opt muted', text: l.type === 'show' ? 'series' : 'films'}),
      el('td', {'class': 'num', text: l.type === 'show' ? l.items + ' · ' + l.episodes + ' ep.' : String(l.items)}),
      el('td', {'class': 'num', text: bytes(l.bytes)}),
      el('td', {'class': 'num', text: pct(share, 1)}),
      el('td', {'class': 'num', text: count ? pct(l.watched / count * 100) : '—'}),
      el('td', {'class': 'num opt', text: pxTime(l.scanned_at)}),
      el('td', {'class': 'num'}, [l.unmatched ? el('span', {'class': 'badge warn', text: String(l.unmatched)}) : el('span', {'class': 'faint', text: '0'})]),
      el('td', {'class': 'num'}, [l.missing ? el('span', {'class': 'badge ko', text: String(l.missing)}) : el('span', {'class': 'faint', text: '0'})])]);
  });
  rows.push(el('tr', {}, [el('td', {}, [el('b', {text: 'All'})]), el('td', {'class': 'opt'}),
    el('td', {'class': 'num', text: nFilms + ' films · ' + nEps + ' ep.'}), el('td', {'class': 'num'}, [el('b', {text: bytes(total)})]),
    el('td', {'class': 'num', text: '100 %'}), el('td', {}), el('td', {'class': 'opt'}), el('td', {}), el('td', {})]));
  clear($('px-libs')).appendChild(pxTable([['Library'], ['Type', 'opt'], ['Items', 'num'], ['Size', 'num'], ['Share', 'num'], ['Watched', 'num'],
    ['Last scan', 'num opt'], ['Unmatched', 'num'], ['Missing files', 'num']], rows));

  hbars($('px-space'), libs.map(function (l, i) {
    return {label: l.title, value: l.bytes, color: SLOTS[i % SLOTS.length], tip: [{value: bytes(l.bytes), label: 'on disk'},
      {value: String(l.type === 'show' ? l.episodes : l.items), label: l.type === 'show' ? 'episodes' : 'films'}]};
  }), total);

  var prog = PX.progress.slice().sort(function (a, b) { return b.at - a.at; });
  if (!prog.length) pxEmpty($('px-progress'), 'Nothing started and left unfinished.');
  else clear($('px-progress')).appendChild(pxTable([['Title'], ['Library', 'opt'], ['Watched', 'num'], ['Last played', 'num opt']],
    prog.slice(0, 12).map(function (p) {
      return el('tr', {}, [el('td', {'class': 'name'}, [el('div', {'class': 't', title: p.title, text: p.title})]), el('td', {'class': 'opt muted', text: p.library}),
        el('td', {'class': 'num', text: pct(p.pct)}), el('td', {'class': 'num opt', text: pxTime(p.at)})]);
    })));
}

function renderPlexPlayback() {
  var s = PX.sessions;
  var kbps = s.reduce(function (a, x) { return a + x.kbps; }, 0);
  var trans = s.filter(function (x) { return x.decision === 'transcode'; }).length;
  kpi($('px-streams'), 'Playing now', 'start', s.length, s.length === 1 ? 'stream' : 'streams',
    s.length ? fix(kbps / 1000, 1) + ' Mbit/s · ' + (trans ? trans + ' transcoding' : 'no video transcode') : 'Nothing playing');
  if (trans) $('px-streams').querySelector('.value').style.color = C.ko;
  var last = PXM && PXM.series.length ? PXM.series[PXM.series.length - 1] : null;
  kpi($('px-disk'), 'Busiest disk', 'disk', last && last.disk_busy_pct !== undefined ? pct(last.disk_busy_pct) : '—', 'busy',
    last ? 'IO wait ' + pct(last.iowait_pct || 0) + ' · sampled ' + ago(last.t * 1000) : 'No sample yet');
  if (last && last.disk_busy_pct !== undefined) $('px-disk').querySelector('.value').style.color = level(last.disk_busy_pct, [[50, C.ok], [80, C.warn]], C.ko);
  kpi($('px-seeding'), 'Seeding meanwhile', 'up', L ? rate(L.io.up_speed) : '—', 'up',
    L ? L.io.queued_io_jobs + ' disk requests waiting in qBittorrent · ' + L.io.average_time_queue_ms + ' ms average wait' : 'qBittorrent status loading');

  if (!s.length) pxEmpty($('px-sessions'), 'Nothing playing. Start a film: this card, the disk load and the charts below show whether the disks keep up.');
  else {
    var dec = {'direct play': 'ok', 'direct stream': 'info', 'audio transcode': 'warn', transcode: 'ko'};
    clear($('px-sessions')).appendChild(pxTable([['Title'], ['Player'], ['Mode'], ['Quality', 'opt'], ['Bitrate', 'num'], ['Progress', 'num']],
      s.map(function (x) {
        return el('tr', {}, [el('td', {'class': 'name'}, [el('div', {'class': 't', title: x.title, text: x.title})]),
          el('td', {text: x.player + (x.state === 'paused' ? ' · paused' : '')}),
          el('td', {}, [el('span', {'class': 'badge ' + (dec[x.decision] || ''), text: x.decision})]),
          el('td', {'class': 'opt muted', text: x.resolution}), el('td', {'class': 'num', text: fix(x.kbps / 1000, 1) + ' Mbit/s'}),
          el('td', {'class': 'num', text: pct(x.progress)})]);
      })));
  }
  renderPlexLoad();
}

function renderPlexLoad() {
  if (!PXM) { pxEmpty($('px-load'), LIVE ? 'Loading…' : 'Live data needs seedbox run.'); pxEmpty($('px-rate'), ''); return; }
  var rows = PXM.series, xs = rows.map(function (r) { return r.t * 1000; });
  function col(k, f) { return rows.map(function (r) { return r[k] === undefined ? null : f ? f(r[k]) : r[k]; }); }
  var tf = function (x) { return new Date(x).toLocaleDateString(undefined, {day: 'numeric', month: 'short', hour: '2-digit'}); };
  var tt = function (x) { return new Date(x).toLocaleString(); };
  lineChart($('px-load'), {xs: xs, series: [{name: 'Busiest disk', color: SLOTS[1], area: true, values: col('disk_busy_pct')},
    {name: 'IO wait', color: SLOTS[3], values: col('iowait_pct')}], yMax: 100, yFmt: function (v) { return pct(v); }, xFmt: tf, tipFmt: tt,
    empty: 'Samples appear every few minutes once seedbox run is up.', label: 'Busiest disk'});
  lineChart($('px-rate'), {xs: xs, series: [{name: 'Plex streams', color: '#e5a00d', area: true, values: col('plex_kbps', function (v) { return v / 1000; })},
    {name: 'qBittorrent upload', color: SLOTS[0], values: col('up_bps', function (v) { return v * 8 / MBIT; })}],
    yFmt: function (v) { return fix(v, 0) + ' Mbit/s'; }, xFmt: tf, tipFmt: tt,
    empty: 'Plex playback is sampled with the system metrics, from this version on.', label: 'Plex playback'});
}

function renderPlexUnwatched() {
  var films = PX.films.filter(function (f) { return !f.watched; });
  var libs = {};
  films.forEach(function (f) { libs[f.library] = (libs[f.library] || 0) + f.bytes; });
  var chips = clear($('px-unw-chips'));
  [''].concat(Object.keys(libs).sort()).forEach(function (l) {
    var n = films.filter(function (f) { return !l || f.library === l; });
    chips.appendChild(el('button', {'class': 'chip', type: 'button', 'aria-pressed': pxLib === l ? 'true' : 'false', onclick: function () {
      pxLib = l; firstPage('px-unwatched'); renderPlexUnwatched();
    }}, [icon('check', 'check'), l || 'All libraries', el('span', {'class': 'n', text: n.length + ' · ' + bytes(n.reduce(function (s, f) { return s + f.bytes; }, 0))})]));
  });
  var rows = films.filter(function (f) { return !pxLib || f.library === pxLib; }).sort(function (a, b) { return b.bytes - a.bytes; });
  if (!rows.length) { pxEmpty($('px-unwatched'), 'Every film here has been watched.'); return; }
  var pg = paged('px-unwatched', rows, 15, 30, Infinity, renderPlexUnwatched);
  clear($('px-unwatched')).appendChild(pxTable([['Film'], ['Library', 'opt'], ['Quality', 'opt'], ['Size', 'num'], ['Seeding'], ['Added', 'num opt']],
    pg.rows.map(function (f) {
      return el('tr', {}, [el('td', {'class': 'name'}, [el('div', {'class': 't', title: f.file, text: pxTitle(f)})]),
        el('td', {'class': 'opt muted', text: f.library}), el('td', {'class': 'opt muted', text: f.resolution + ' · ' + (f.video || '?')}),
        el('td', {'class': 'num', text: bytes(f.bytes)}), el('td', {}, [pxSeed(f.seed)]), el('td', {'class': 'num opt', text: pxTime(f.added_at)})]);
    })));
  $('px-unwatched').appendChild(pg.controls);
}

function renderPlexTranscode() {
  var chips = clear($('px-tc-chips'));
  Object.keys(PX_DEVICES).forEach(function (d) {
    var n = PX.films.filter(function (f) { return f.risks[d].length; }).length;
    chips.appendChild(el('button', {'class': 'chip', type: 'button', 'aria-pressed': pxDevice === d ? 'true' : 'false', onclick: function () {
      pxDevice = d; firstPage('px-transcode'); renderPlexTranscode();
    }}, [icon('check', 'check'), PX_DEVICES[d], el('span', {'class': 'n', text: String(n)})]));
  });
  if (PX.analysis_pending) chips.appendChild(el('span', {'class': 'muted small', style: 'align-self:center',
    text: PX.analysis_pending + ' films not analysed yet (audio and subtitle tracks are read a few dozen at a time)'}));
  var rows = PX.films.filter(function (f) { return f.risks[pxDevice].length; }).sort(function (a, b) { return b.bytes - a.bytes; });
  if (!rows.length) { pxEmpty($('px-transcode'), 'Nothing leaves Direct Play on this player.'); return; }
  var other = pxDevice === 'ps5' ? 'appletv' : 'ps5';
  var pg = paged('px-transcode', rows, 15, 30, Infinity, renderPlexTranscode);
  clear($('px-transcode')).appendChild(pxTable([['Film'], ['Library', 'opt'], ['Why'], ['On the other player', 'opt']],
    pg.rows.map(function (f) {
      return el('tr', {}, [el('td', {'class': 'name'}, [el('div', {'class': 't', title: f.file, text: pxTitle(f)})]),
        el('td', {'class': 'opt muted', text: f.library}),
        el('td', {}, f.risks[pxDevice].map(function (r) { return el('div', {'class': 'small', text: r}); })),
        el('td', {'class': 'opt'}, [f.risks[other].length ? el('span', {'class': 'badge warn', text: f.risks[other].length + ' issue(s)'})
          : el('span', {'class': 'badge ok', text: 'Direct Play'})])]);
    })));
  $('px-transcode').appendChild(pg.controls);
}

function renderPlexRecent() {
  if (!PX.recent.length) { pxEmpty($('px-recent'), 'Nothing added yet.'); return; }
  var kind = {movie: 'film', season: 'season', episode: 'episode', show: 'series'};
  clear($('px-recent')).appendChild(pxTable([['Title'], ['Type', 'opt'], ['Library'], ['Added', 'num']], PX.recent.map(function (r) {
    return el('tr', {}, [el('td', {'class': 'name'}, [el('div', {'class': 't', title: r.title, text: r.title + (r.year ? ' (' + r.year + ')' : '')})]),
      el('td', {'class': 'opt muted', text: kind[r.type] || r.type}), el('td', {'class': 'muted', text: r.library || ''}),
      el('td', {'class': 'num', text: pxTime(r.added_at)})]);
  })));
}

function renderPlexServer() {
  var sv = PX.server, rows = [
    ['Server', sv.name + ' · ' + sv.platform],
    ['Version', sv.version],
    ['Update', sv.update ? sv.update.version + ' available' : 'up to date (checked ' + pxTime(sv.update_checked) + ')']];
  if (sv.data) {
    var d = sv.data.sizes;
    rows.push(['Database', bytes(d.database)], ['Metadata (posters, art)', bytes(d.metadata)], ['Media (thumbnails, analysis)', bytes(d.media)],
      ['Cache', bytes(d.cache)], ['Measured', pxTime(sv.data.at)]);
  } else rows.push(['Data folder', 'not mounted in seedbox (PLEX_CONFIG_DIR in deploy.conf): sizes unknown']);
  clear($('px-server')).appendChild(pxTable([['', ''], ['', '']], rows.map(function (r) {
    return el('tr', {}, [el('td', {'class': 'muted', text: r[0]}), el('td', {text: r[1]})]);
  })));
  if (sv.update) $('px-server').querySelector('tbody tr:nth-child(3) td:last-child').style.color = C.warn;

  $('px-window').textContent = 'run between ' + String(sv.window[0]).padStart(2, '0') + ':00 and ' + String(sv.window[1]).padStart(2, '0') + ':00';
  var on = sv.butler.filter(function (t) { return t.enabled; }), off = sv.butler.length - on.length;
  clear($('px-butler')).appendChild(pxTable([['Task'], ['Every', 'num']], on.map(function (t) {
    return el('tr', {}, [el('td', {text: t.title}), el('td', {'class': 'num', text: t.interval === 1 ? 'day' : t.interval + ' days'})]);
  })));
  $('px-butler').appendChild(el('p', {'class': 'muted small', text: off + ' other tasks disabled (previews, markers, analyses: the light setup).'}));

  if (!PX.activities.length) pxEmpty($('px-activities'), 'Idle: no scan or metadata update running.');
  else clear($('px-activities')).appendChild(pxTable([['Activity'], ['Detail'], ['Progress', 'num']], PX.activities.map(function (a) {
    return el('tr', {}, [el('td', {text: a.title}), el('td', {'class': 'muted', text: a.subtitle || ''}), el('td', {'class': 'num', text: pct(a.progress)})]);
  })));
}

function renderPlex() {
  if (!LIVE) { renderPlexMessage('Live data needs seedbox run.'); return; }
  if (!PX) { renderPlexMessage('Loading…'); return; }
  if (!PX.configured) { renderPlexMessage('Plex is not configured: [plex] url in seedbox.toml.'); return; }
  if (PX.error) { renderPlexMessage('Plex: ' + PX.error); return; }
  renderPlexLibraries(); renderPlexPlayback(); renderPlexUnwatched(); renderPlexTranscode(); renderPlexRecent(); renderPlexServer();
}

function refreshPlex(force) {
  if (!LIVE) { renderPlex(); return; }
  Promise.all([
    api('api/plex' + (force ? '?force=1' : '')).then(function (p) { PX = p; }, function (e) { PX = {configured: true, error: e.message}; }),
    api('api/metrics?hours=72').then(function (m) { PXM = m; }, function () { PXM = null; })
  ]).then(renderPlex);
}

function initPlex() {
  $('hero-sub').textContent = 'Plex as seen by seedbox: libraries, playback against the disk load, films to purge, transcoding risks.';
  renderPlex();
  refreshPlex(false);
  $('live-refresh').addEventListener('click', function () { refreshPlex(true); });
  setInterval(function () { if (!document.hidden && $('auto').getAttribute('aria-pressed') === 'true') refreshPlex(false); },
    Number($('auto').dataset.period) * 1000);
}
initPlex();
