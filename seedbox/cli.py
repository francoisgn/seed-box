"""Command line: collect once, check connectivity, or run as a service."""

import argparse
import functools
import http.server
import json
import os
import signal
import stat
import sys
import threading
from datetime import datetime

from seedbox import __version__, actions, collect, match, metrics, prowlarr, report, status, ui
from seedbox import schedule as sched
from seedbox.api import ApiError
from seedbox.config import ConfigError, fingerprint, load
from seedbox.qbittorrent import QbtClient


def cmd_collect(cfg):
    with ui.Spinner("Collecting") as spinner:
        snap = collect.run(cfg, ui, spinner.update)
        index = report.write(cfg, snap)
    s = snap["summary"]
    ui.ok(
        f"{s['entries']} entries | {s['seeded']} seeded | {s['incomplete']} incomplete | "
        f"{s['orphan']} orphan | coverage {s['coverage_pct']:.1f}% | {snap['duration_s']}s"
    )
    for t in snap["trackers"]:
        ui.info(f"{t['name']:<28} {t['entries']:5d} entries")
    if s["unmatched_torrents"]:
        ui.warn(f"{s['unmatched_torrents']} torrent(s) match no library entry")
    ui.ok(f"dashboard: {index}")
    return 0


SKIP_DIRS = ("@eaDir", "#recycle", "#snapshot")


def unreadable_files(dirs):
    """Files with no read bit at all (mode 000 and the like), one path per inode.

    The mode, not os.access: seedbox and qBittorrent may run as different users."""
    seen, out = set(), []
    for top in dirs:
        for root, subdirs, files in os.walk(top):
            subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS)
            for name in sorted(files):
                path = os.path.join(root, name)
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) in seen or not stat.S_ISREG(st.st_mode):
                    continue
                seen.add((st.st_dev, st.st_ino))
                if not st.st_mode & 0o444:
                    out.append(path)
    return out


def cmd_check(cfg):
    """Check each source separately, to validate a deployment."""
    rc = 0
    ui.info(f"config: {cfg.source or 'environment only'}")
    ui.info(f"qBittorrent password: {'set' if cfg.qbt_password else 'not set'}")
    if cfg.prowlarr_enabled:
        ui.info(f"Prowlarr API key: {'set' if cfg.prowlarr_api_key else 'not set'}")
    ui.info(f"collection: {_schedule_text(cfg)}")
    for root in cfg.roots:
        if os.path.isdir(root) and os.access(root, os.R_OK | os.X_OK):
            ui.ok(f"library root readable: {root}")
        else:
            ui.ko(f"library root missing or unreadable: {root}")
            rc = 1
    for prefix, target in cfg.path_map.items():
        (ui.ok if os.path.isdir(target) else ui.warn)(f"path map {prefix} -> {target}")
    with ui.Spinner("Looking for unreadable files"):
        bad = unreadable_files(list(cfg.roots) + list(cfg.path_map.values()))
    if bad:
        # qBittorrent cannot seed them: torrents look fine (stalledUP) until a peer asks.
        ui.ko(f"{len(bad)} file(s) nobody can read (chmod a+r to fix):")
        for path in bad[:20]:
            ui.ko(f"  {path}")
        if len(bad) > 20:
            ui.ko(f"  ... and {len(bad) - 20} more")
        rc = 1
    else:
        ui.ok("media files readable")
    try:
        client = QbtClient(cfg.qbt_url, cfg.qbt_username, cfg.qbt_password)
        ui.ok(f"qBittorrent {client.version()} at {cfg.qbt_url}, {len(client.torrents())} torrents")
    except ApiError as exc:
        ui.ko(str(exc))
        rc = 1
    if cfg.prowlarr_enabled:
        try:
            client = prowlarr.ProwlarrClient(cfg.prowlarr_url, cfg.prowlarr_api_key)
            found = prowlarr.indexers(client, cfg.tracker_aliases)
            ui.ok(f"Prowlarr {client.version()} at {cfg.prowlarr_url}, {len(found)} torrent indexers")
        except ApiError as exc:
            ui.ko(str(exc))
            rc = 1
    else:
        ui.warn("Prowlarr not configured (optional)")
    try:
        os.makedirs(cfg.output_dir, exist_ok=True)
        (ui.ok if os.access(cfg.output_dir, os.W_OK) else ui.ko)(f"output dir: {cfg.output_dir}")
    except OSError as exc:
        ui.ko(f"output dir {cfg.output_dir}: {exc}")
        rc = 1
    return rc


def cmd_status(cfg):
    """What qBittorrent is busy with: rechecks, moves, errors, disk queue."""
    with ui.Spinner("Asking qBittorrent"):
        st = status.gather(QbtClient(cfg.qbt_url, cfg.qbt_username, cfg.qbt_password), cfg=cfg)
    status.show(st, ui)
    return 0


def cmd_match(cfg, name, tmdb_id=None, verify=False):
    """Find a library entry on the trackers under its release name (read-only:
    applying the result is done from the dashboard)."""
    if not name:
        ui.ko("match: give part of the library entry name")
        return 1
    try:
        snap = match.load_snapshot(cfg)
        hits = [i for i, e in enumerate(snap["entries"]) if name.lower() in e["name"].lower()]
        if len(hits) != 1:
            (ui.ko if not hits else ui.warn)(f"{len(hits)} entries match {name!r}, be more precise")
            for i in hits[:15]:
                ui.info(f"  {snap['entries'][i]['name']}")
            return 1
        client = QbtClient(cfg.qbt_url, cfg.qbt_username, cfg.qbt_password)
        with ui.Spinner("Searching the trackers"):
            res = match.search(cfg, snap, hits[0], client, tmdb_id)
        ident = res["identity"]
        ui.info(f"{res['entry']['file']} ({res['entry']['size']} bytes)")
        ui.info(
            f"searched as {' / '.join(ident['titles'])} {ident['year']}"
            + (f", TMDB {ident['id']}" if ident["id"] else "")
            + (f", {ident['imdb']}" if ident["imdb"] else "")
        )
        for err in res["errors"]:
            ui.warn(err)
        close = [c for c in res["candidates"] if c["verdict"] != "other"]
        if not close:
            ui.warn(f"no release of this exact size ({res['others']} other releases found)")
            return 0
        for c in close:
            where = " (already seeded there)" if c["seeded_there"] else " (in qBittorrent)" if c["in_qbt"] else ""
            ui.ok(f"{c['verdict']:<6} {c['indexer']:<20} {c['title']}{where}")
            if verify:
                with ui.Spinner(f"Verifying on {c['indexer']}"):
                    v = match.verify(cfg, snap, c["id"], client)
                if v.get("verified"):
                    rename = v["names"][0] if v["names"] else "(same name)"
                    ui.ok(f"       verified: {v['checked']}/{v['checked']} pieces match; rename to: {rename}")
                else:
                    ui.ko(f"       not the same file: {v.get('reason') or str(v.get('failed')) + ' piece(s) differ'}")
        return 0
    except (match.MatchError, OSError) as exc:
        ui.ko(str(exc))
        return 1


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the output directory and the dashboard API.

    GET  /api/status   what qBittorrent is busy with, plus seedbox jobs
    GET  /api/metrics  host and qBittorrent samples (?hours=48)
    GET  /api/collect  state of the collection
    POST /api/collect  collect now
    POST /api/action   move, recheck, start, skip extras, remove ([service] actions)
    POST /api/match    release matching: search, verify, apply ([service] actions)

    POSTs need the X-Seedbox header and a JSON body: a page from another site
    cannot send that without a CORS preflight, which is never granted here.
    """

    cfg = None
    service = None

    def _client(self):
        return QbtClient(self.cfg.qbt_url, self.cfg.qbt_username, self.cfg.qbt_password)

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/api/status":
            try:
                return self._send(200, status.gather(self._client(), cfg=self.cfg))
            except ApiError as exc:
                return self._send(502, {"error": str(exc)})
        if path == "/api/metrics":
            try:
                hours = min(max(float(params.get("hours", 48)), 1), 24 * self.cfg.metrics_days)
            except ValueError:
                hours = 48
            roots = [os.path.dirname(r) or r for r in self.cfg.roots] + [self.cfg.output_dir]
            return self._send(
                200,
                {
                    "series": metrics.series(self.cfg, hours),
                    "volumes": metrics.volumes(roots),
                    "interval": self.cfg.metrics_interval,
                },
            )
        if path == "/api/collect":
            return self._send(200, self.service.collect_state())
        return super().do_GET()

    def do_POST(self):
        path = self.path.partition("?")[0]
        if self.headers.get("X-Seedbox") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._send(403, {"error": "missing X-Seedbox header or JSON content type"})
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != self.headers.get("Host"):
            return self._send(403, {"error": "cross-origin request refused"})
        length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
        body = self.rfile.read(length)
        if path == "/api/action":
            code, result = actions.handle(self.cfg, self._client, body)
            return self._send(code, result)
        if path == "/api/match":
            code, result = match.handle(self.cfg, self._client, body)
            return self._send(code, result)
        if path == "/api/collect":
            if not self.cfg.actions:
                return self._send(403, {"error": "actions are disabled ([service] actions = true to enable)"})
            return self._send(202, self.service.trigger())
        return self._send(404, {"error": "unknown endpoint"})

    def log_message(self, *args):
        pass


class _Service:
    """Collection loop of `seedbox run`: scheduled, or triggered from the dashboard."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.state = {"running": False, "last": None, "error": None, "reason": None}

    def collect_state(self):
        return dict(self.state)

    def trigger(self):
        if not self.state["running"]:
            # Running from now on: a poll right after must not see "done".
            self.state.update(running=True, reason="requested from the dashboard", error=None)
            self.wake.set()
        return self.collect_state()

    def collect(self, reason):
        with self.lock:
            self.state.update(running=True, reason=reason, error=None)
            try:
                cmd_collect(self.cfg)
            except (ApiError, OSError) as exc:
                self.state["error"] = str(exc)
                ui.ko(f"collection failed: {exc}")
            finally:
                self.state.update(running=False, last=datetime.now().astimezone().isoformat(timespec="seconds"))

    def loop(self, needed, why):
        while True:
            if needed:
                self.collect(why)
            needed, why = True, "schedule"
            if self.wake.wait(_wait(self.cfg)):
                self.wake.clear()
                why = self.state.get("reason") or "requested"


def _schedule_text(cfg):
    if cfg.schedule:
        return sched.describe(sched.parse(cfg.schedule))
    return f"every {cfg.interval_hours:g}h"


def _wait(cfg):
    """Seconds until the next collection."""
    if cfg.schedule:
        now = sched.now()
        nxt = sched.next_run(sched.parse(cfg.schedule), now)
        ui.info(f"next collection: {nxt:%Y-%m-%d %H:%M}")
        return (nxt - now).total_seconds()
    return max(cfg.interval_hours, 0.1) * 3600


def startup_collection(cfg, now=None):
    """Whether `run` must collect right away, and why.

    A restart (deploy, reboot) must not add a history line for nothing: collect
    only without a previous snapshot, when the config changed since, or when a
    scheduled run (or the interval) was missed while the service was down.
    """
    now = now or sched.now()
    try:
        with open(os.path.join(cfg.output_dir, "snapshot.json"), encoding="utf-8") as handle:
            snap = json.load(handle)
        last = datetime.fromisoformat(snap["generated"]).astimezone().replace(tzinfo=None)
    except (OSError, ValueError, KeyError, TypeError):
        return True, "no previous collection"
    if snap.get("config") != fingerprint(cfg):
        return True, "config changed since the last collection"
    if cfg.schedule:
        missed = sched.prev_run(sched.parse(cfg.schedule), now)
        if last < missed:
            return True, f"scheduled run of {missed:%Y-%m-%d %H:%M} was missed"
    elif (now - last).total_seconds() >= max(cfg.interval_hours, 0.1) * 3600:
        return True, "interval elapsed since the last collection"
    return False, f"last collection {last:%Y-%m-%d %H:%M} is current"


def cmd_run(cfg):
    """Serve the dashboard and its API, collect if needed, then on schedule or on demand."""
    # PID 1 in a container ignores SIGTERM by default: `docker stop` would wait
    # its whole timeout, then kill (exit 137). Exit right away instead.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.makedirs(cfg.output_dir, exist_ok=True)
    service = _Service(cfg)
    _Handler.cfg, _Handler.service = cfg, service
    handler = functools.partial(_Handler, directory=cfg.output_dir)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", cfg.port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ui.ok(f"serving {cfg.output_dir} on port {cfg.port}, collection {_schedule_text(cfg)}")
    ui.info(f"dashboard actions: {'enabled' if cfg.actions else 'disabled'}")
    if cfg.metrics_interval > 0:
        client = functools.partial(QbtClient, cfg.qbt_url, cfg.qbt_username, cfg.qbt_password)
        threading.Thread(target=metrics.loop, args=(cfg, client, ui), daemon=True).start()
    needed, why = startup_collection(cfg)
    ui.info(f"startup collection: {'yes' if needed else 'skipped'} ({why})")
    service.loop(needed, why)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seedbox", description=__doc__)
    parser.add_argument("--version", action="version", version=f"seedbox {__version__}")
    parser.add_argument("-c", "--config", help="TOML config file (default: $SEEDBOX_CONFIG, ./seedbox.toml)")
    parser.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=["collect", "check", "status", "run", "match"],
        help="collect once (default), check the sources, show what qBittorrent is busy with, run as a service, "
        "or find a library file on the trackers (match NAME)",
    )
    parser.add_argument("name", nargs="*", help="match: part of the library entry name")
    parser.add_argument("--tmdb", type=int, help="match: TMDB id of the film (default: searched from the name)")
    parser.add_argument("--verify", action="store_true", help="match: fetch the exact matches and hash their pieces")
    args = parser.parse_args(argv)
    try:
        cfg = load(args.config)
        for message in cfg.warnings:
            ui.warn(message)
        if args.command == "match":
            return cmd_match(cfg, " ".join(args.name), args.tmdb, args.verify)
        return {"collect": cmd_collect, "check": cmd_check, "status": cmd_status, "run": cmd_run}[args.command](cfg)
    except (ConfigError, ApiError) as exc:
        ui.ko(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130
