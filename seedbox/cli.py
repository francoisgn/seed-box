"""Command line: collect once, check connectivity, or run as a service."""

import argparse
import functools
import http.server
import json
import os
import threading
import time

from seedbox import __version__, collect, prowlarr, report, status, ui
from seedbox import schedule as sched
from seedbox.api import ApiError
from seedbox.config import ConfigError, load
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


def cmd_run(cfg):
    """Serve the output directory, collect at start then on schedule."""
    os.makedirs(cfg.output_dir, exist_ok=True)
    _Handler.cfg = cfg
    handler = functools.partial(_Handler, directory=cfg.output_dir)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", cfg.port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ui.ok(f"serving {cfg.output_dir} on port {cfg.port}, collection {_schedule_text(cfg)}")
    while True:
        try:
            cmd_collect(cfg)
        except (ApiError, OSError) as exc:
            ui.ko(f"collection failed: {exc}")
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
