"""Command line: collect once, check connectivity, or run as a service."""

import argparse
import functools
import http.server
import os
import threading
import time

from seedbox import __version__, collect, prowlarr, report, ui
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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def cmd_run(cfg):
    """Serve the output directory and collect every interval_hours."""
    os.makedirs(cfg.output_dir, exist_ok=True)
    handler = functools.partial(_QuietHandler, directory=cfg.output_dir)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", cfg.port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ui.ok(f"serving {cfg.output_dir} on port {cfg.port}, collecting every {cfg.interval_hours:g}h")
    while True:
        try:
            cmd_collect(cfg)
        except (ApiError, OSError) as exc:
            ui.ko(f"collection failed: {exc}")
        time.sleep(max(cfg.interval_hours, 0.1) * 3600)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seedbox", description=__doc__)
    parser.add_argument("--version", action="version", version=f"seedbox {__version__}")
    parser.add_argument("-c", "--config", help="TOML config file (default: $SEEDBOX_CONFIG, ./seedbox.toml)")
    parser.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=["collect", "check", "run"],
        help="collect once (default), check the sources, or run as a service",
    )
    args = parser.parse_args(argv)
    try:
        cfg = load(args.config)
        return {"collect": cmd_collect, "check": cmd_check, "run": cmd_run}[args.command](cfg)
    except (ConfigError, ApiError) as exc:
        ui.ko(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130
