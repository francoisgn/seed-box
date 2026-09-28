"""Self-contained HTML dashboard: data embedded as JSON, rendered client-side.

The page works offline (opened as a file, it shows the last collection); served
by `seedbox run`, it adds live qBittorrent activity, jobs, system metrics and
the actions. Styles, script and artwork live in seedbox/web/ and are inlined
here, so the page is still one file. Every value is inserted with textContent,
never as HTML.
"""

import base64
import json
import os

from seedbox import __version__

WEB = os.path.join(os.path.dirname(__file__), "web")

NAV = [
    ("overview", "Overview", "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"),
    ("activity", "Activity", "M7 4v16M7 4L3 8M7 4l4 4M17 20V4M17 20l-4-4M17 20l4-4"),
    ("duplicates", "Duplicates", "M8 8h12v12H8zM4 16V4h12"),
    ("library", "Library", "M3 7h14v13H3zM7 3h14v13M8 11v5l4-2.5z"),
    ("system", "System", "M3 12h4l2-5 4 10 2-5h6"),
    ("logs-sec", "Logs", "M5 5h14M5 9.5h14M5 14h9M5 18.5h9"),
]
CHECK = '<svg class="icon check" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'


def _asset(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as handle:
        return handle.read()


def _nav():
    links = []
    for key, label, path in NAV:
        active = ' class="active"' if key == "overview" else ""
        links.append(
            f'<a href="#{key}"{active}><span class="pill">'
            f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="{path}"/></svg></span>{label}</a>'
        )
    return "\n  ".join(links)


def _range_chips():
    options = [(6, "6 h"), (24, "24 h"), (72, "3 days"), (336, "14 days")]
    return "".join(
        f'<button class="chip" type="button" data-h="{h}" aria-pressed="{"true" if h == 24 else "false"}">{CHECK}{label}</button>'
        for h, label in options
    )


PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seedbox control plane</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,@favicon@">
<style>@css@</style></head><body>

<nav class="rail" aria-label="Sections">
  <div class="flag">@flag@</div>
  @nav@
</nav>

<header class="topbar">
  <div class="crumbs"><span>Seedbox</span><span aria-hidden="true">›</span><b id="crumb">Overview</b></div>
  <label class="search"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5L20 20"/></svg>
    <input id="search" type="search" placeholder="Search the library by name, folder or tracker" aria-label="Search the library"></label>
  <div class="top-actions">
    <span class="muted small opt" id="live-time"></span>
    <button class="chip opt" id="auto" type="button" aria-pressed="false">@check@Auto refresh</button>
    <button class="btn" id="live-refresh" type="button"><svg class="icon sm" viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></svg><span class="label">Refresh</span></button>
    <button class="btn filled" id="collect" type="button"><svg class="icon sm" viewBox="0 0 24 24"><path d="M12 4v10M8 10l4 4 4-4M5 18h14"/></svg><span class="label">Collect now</span></button>
  </div>
</header>

<main>
<div class="hero">
  @logo@
  <div><h1>Seedbox control plane</h1><p id="hero-sub"></p><div class="meta" id="hero-meta"></div></div>
</div>

<section id="overview">
  <div class="grid">
    <div class="card kpi c3" id="k-coverage"></div>
    <div class="card kpi c3" id="k-library"></div>
    <div class="card kpi c3" id="k-uploaded"></div>
    <div class="card kpi c3" id="k-problems"></div>
    <div class="card kpi c3" id="k-dups"></div>
    <div class="card kpi c3" id="k-queue" data-live></div>
    <div class="card kpi c3" id="k-rechecks" data-live></div>
    <div class="card kpi c3" id="k-volume" data-live></div>

    <div class="card c5"><div class="card-head"><h3>Library by seeding status</h3></div><div class="chart" id="c-status"></div></div>
    <div class="card c7"><div class="card-head"><h3>Coverage by tracker</h3><span class="sub muted small">share of the library each tracker seeds</span></div><div class="chart" id="c-trackers"></div></div>
    <div class="card c8"><div class="card-head"><h3>Seeded entries over time</h3><span class="sub muted small">rebuilt from torrent add dates</span></div><div class="chart" id="c-timeline"></div></div>
    <div class="card c4"><div class="card-head"><h3>Torrent states</h3></div><div class="chart" id="c-states"></div></div>
    <div class="card c8"><div class="card-head"><h3>Torrents added per day</h3><span class="sub muted small">last 60 days</span></div><div class="chart" id="c-added"></div></div>
    <div class="card c4"><div class="card-head"><h3>Coverage at each collection</h3></div><div class="chart" id="c-history"></div></div>
  </div>
</section>

<section id="activity">
  <div class="section-head"><h2>qBittorrent activity</h2><span class="muted">live: rechecks, moves, removals and errors</span></div>
  <div class="grid">
    <div class="card kpi c6" id="a-io" data-live></div>
    <div class="card kpi c6" id="a-transfer" data-live></div>
    <div class="card c12" data-live><div class="card-head"><h3>Busy torrents</h3><span class="sub muted small">moving, checking, queued, stopped or in error</span></div><div id="a-busy"></div></div>
    <div class="card c12" data-live><div class="card-head"><h3>Jobs sent from the dashboard</h3><span class="sub muted small">status read back from qBittorrent</span></div><div id="a-jobs"></div></div>
  </div>
</section>

<section id="duplicates">
  <div class="section-head"><h2>Duplicates</h2><span class="muted">same file on the same tracker, several versions of a work, episodes twice</span></div>
  <div class="grid">
    <div class="card kpi c4" id="d-same"></div>
    <div class="card kpi c4" id="d-versions"></div>
    <div class="card kpi c4" id="d-episodes"></div>
    <div class="card c12" id="dups"></div>
  </div>
</section>

<section id="library">
  <div class="section-head"><h2>Library</h2><span class="muted" id="lib-count"></span></div>
  <div class="card">
    <div class="chips" id="lib-chips" style="margin-bottom:16px"></div>
    <div class="chips" style="margin-bottom:8px">
      <select class="select" id="lib-folder" aria-label="Folder"><option value="">All folders</option></select>
      <select class="select" id="lib-missing" aria-label="Missing on tracker"><option value="">Any tracker</option></select>
    </div>
    <div class="table-wrap"><table id="lib-table">
      <thead><tr><th style="width:48px"><input type="checkbox" id="lib-all" aria-label="Select all shown"></th>
      <th data-sort="coverage">Status</th><th data-sort="name">Name</th><th class="opt" data-sort="trackers">Trackers</th>
      <th class="num" data-sort="size">Size</th><th class="num opt" data-sort="uploaded">Uploaded</th><th class="num" data-sort="issues">Issues</th></tr></thead>
      <tbody id="lib-body"></tbody></table></div>
    <p class="empty" id="lib-empty" hidden>No entry matches.</p>
    <div class="more" id="lib-more"></div>
  </div>
  <div class="batch" id="batch">
    <b id="batch-count"></b>
    <span class="muted">Move with qBittorrent to</span>
    <select class="select" id="batch-dest" aria-label="Destination folder"></select>
    <button class="btn filled sm" id="batch-move" type="button">Move</button>
    <button class="btn sm" id="batch-recheck" type="button">Recheck</button>
    <button class="btn sm" id="batch-start" type="button">Start</button>
    <button class="btn sm" id="batch-clear" type="button">Clear selection</button>
  </div>
</section>

<section id="outside-sec">
  <div class="section-head"><h2>Torrents outside the library</h2><span class="muted">matched to no library entry, with the reason</span></div>
  <div class="card" id="outside"></div>
</section>

<section id="system">
  <div class="section-head"><h2>System</h2><span class="muted">host and qBittorrent, sampled by seedbox</span>
    <div class="chips range" id="m-range">@range@</div></div>
  <div class="grid">
    <div class="card c6" data-live><div class="card-head"><h3>CPU and IO wait</h3></div><div class="chart" id="m-cpu"></div></div>
    <div class="card c6" data-live><div class="card-head"><h3>Busiest disk</h3><span class="sub muted small">time spent doing IO</span></div><div class="chart" id="m-disk"></div></div>
    <div class="card c6" data-live><div class="card-head"><h3>Memory used</h3></div><div class="chart" id="m-mem"></div></div>
    <div class="card c6" data-live><div class="card-head"><h3>qBittorrent transfer</h3></div><div class="chart" id="m-net"></div></div>
  </div>
</section>

<section id="logs-sec">
  <div class="section-head"><h2>Logs</h2><span class="muted">qBittorrent moves, removals and errors</span></div>
  <div class="grid">
    <div class="card c12" data-live id="logs"></div>
    <div class="card c12"><div class="card-head"><h3>Collection warnings</h3></div><div id="warnings"></div></div>
  </div>
  <p class="muted small" style="margin-top:32px">Matched by inode: content hardlinked by cross-seed counts as seeded wherever the
  torrent points. seedbox @version@</p>
</section>
</main>

<div class="tooltip" id="tooltip" role="tooltip"></div>
<div class="toast" id="toast" role="status"></div>
<dialog id="dialog"></dialog>
<script type="application/json" id="data">@data@</script>
<script type="application/json" id="history">@history@</script>
<script>@js@</script>
</body></html>
"""


def _embed(value):
    # "<" escaped so the JSON can never close the <script> element.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def render(snap, history):
    data = {k: v for k, v in snap.items() if k != "entries"}
    data["entries"] = [{k: v for k, v in e.items() if k != "path"} for e in snap["entries"]]
    flag = _asset("flag.svg").strip()
    parts = {
        "favicon": base64.b64encode(flag.encode()).decode(),
        "css": _asset("app.css"),
        "flag": flag,
        "nav": _nav(),
        "check": CHECK,
        "logo": _asset("logo.svg").strip(),
        "range": _range_chips(),
        "version": __version__,
        "data": _embed(data),
        "history": _embed(history),
        "js": _asset("app.js"),
    }
    # One pass over the template: inserted content is never scanned again.
    pieces = PAGE.split("@")
    out = []
    for i, piece in enumerate(pieces):
        out.append(parts[piece] if i % 2 and piece in parts else (("@" + piece) if i % 2 else piece))
    return "".join(out)
