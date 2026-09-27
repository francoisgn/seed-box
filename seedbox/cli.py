"""Command line: collect once, check connectivity, or run as a service."""

import argparse
import functools
import http.server
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime

from seedbox import __version__, collect, prowlarr, report, status, ui
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
        st = status.gather(QbtClient(cfg.qbt_url, cfg.qbt_username, cfg.qbt_password))
    status.show(st, ui)
    return 0


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the output directory, plus GET /api/status (live, read-only)."""

    cfg = None

    def do_GET(self):
        if self.path.split("?")[0] != "/api/status":
            return super().do_GET()
        try:
            code, body = 200, status.gather(QbtClient(self.cfg.qbt_url, self.cfg.qbt_username, self.cfg.qbt_password))
        except ApiError as exc:
            code, body = 502, {"error": str(exc)}
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


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
    """Serve the output directory, collect if needed, then on schedule."""
    # PID 1 in a container ignores SIGTERM by default: `docker stop` would wait
    # its whole timeout, then kill (exit 137). Exit right away instead.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.makedirs(cfg.output_dir, exist_ok=True)
    _Handler.cfg = cfg
    handler = functools.partial(_Handler, directory=cfg.output_dir)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", cfg.port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ui.ok(f"serving {cfg.output_dir} on port {cfg.port}, collection {_schedule_text(cfg)}")
    needed, why = startup_collection(cfg)
    ui.info(f"startup collection: {'yes' if needed else 'skipped'} ({why})")
    while True:
        if needed:
            try:
                cmd_collect(cfg)
            except (ApiError, OSError) as exc:
                ui.ko(f"collection failed: {exc}")
        needed = True
        time.sleep(_wait(cfg))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seedbox", description=__doc__)
    parser.add_argument("--version", action="version", version=f"seedbox {__version__}")
    parser.add_argument("-c", "--config", help="TOML config file (default: $SEEDBOX_CONFIG, ./seedbox.toml)")
    parser.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=["collect", "check", "status", "run"],
        help="collect once (default), check the sources, show what qBittorrent is busy with, or run as a service",
    )
    args = parser.parse_args(argv)
    try:
        cfg = load(args.config)
        for message in cfg.warnings:
            ui.warn(message)
        return {"collect": cmd_collect, "check": cmd_check, "status": cmd_status, "run": cmd_run}[args.command](cfg)
    except (ConfigError, ApiError) as exc:
        ui.ko(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130
