// Upload section of the library page: the tracker's upload API (access, settings)
// and the tracker .torrent files kept for review. Checks live in the library.
// Every text from the data is inserted with textContent. Its own scope: app.js
// runs on the same page (el, $, clear, api, toast, trackerChip come from it).
(function () {
'use strict';

var DATA = null;

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

function load() {
  return api('api/upload').then(function (r) {
    DATA = r;
    $('upload').hidden = false;
    if ($('nav-upload')) $('nav-upload').hidden = false;
    renderStatus(); renderReviews();
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

$('live-refresh').addEventListener('click', load);
load();
})();
