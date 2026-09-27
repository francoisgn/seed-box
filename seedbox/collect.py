"""Collect the three sources (disk, qBittorrent, Prowlarr) and correlate them.

Torrents are matched to library entries by inode: cross-seed hardlinks point
at the same inodes as the organised files, whatever their name or path. The
torrent file list comes from the API (not a directory walk), so a torrent
never claims files it does not contain.
"""

import os
import time
from datetime import UTC, datetime

from seedbox import library, prowlarr
from seedbox import trackers as trk
from seedbox.api import ApiError
from seedbox.config import map_path
from seedbox.qbittorrent import QbtClient


def _torrent_trackers(client, torrent, aliases):
    keys = []
    try:
        items = client.trackers(torrent["hash"])
    except ApiError:
        items = []
    for item in items:
        key = trk.key_for_url(item.get("url", ""), aliases)
        if key and key not in keys:
            keys.append(key)
    if not keys:
        key = trk.key_for_url(torrent.get("tracker", ""), aliases)
        if key:
            keys.append(key)
    return keys


def _torrent_paths(cfg, client, torrent):
    """Local paths of the torrent's files, from its file list."""
    base = torrent.get("save_path") or ""
    if (torrent.get("progress") or 0) < 1 and torrent.get("download_path"):
        base = torrent["download_path"]
    try:
        files = client.files(torrent["hash"])
    except ApiError:
        files = []
    if not files:
        content = torrent.get("content_path")
        return [map_path(cfg, content)] if content else []
    return [map_path(cfg, os.path.join(base, f.get("name", ""))) for f in files]


def correlate(cfg, client, entries, inode_index, progress=lambda msg: None):
    """Attach torrents to entries. Returns the torrents matching no entry."""
    unmatched = []
    torrents = client.torrents()
    for position, torrent in enumerate(torrents, 1):
        progress(f"Correlating torrents {position}/{len(torrents)}")
        keys = _torrent_trackers(client, torrent, cfg.tracker_aliases)
        paths = _torrent_paths(cfg, client, torrent)

        targets = set()
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            hit = inode_index.get((st.st_dev, st.st_ino))
            if hit is not None:
                targets.add(hit)

        if not targets:
            # Fallback on paths: files not on disk yet (incomplete) or not readable.
            for path in paths:
                for i, entry in enumerate(entries):
                    if path == entry.path or path.startswith(entry.path + "/"):
                        targets.add(i)
                        break

        if not targets:
            unmatched.append(
                {
                    "name": torrent.get("name", ""),
                    "path": map_path(cfg, torrent.get("content_path") or ""),
                    "trackers": keys,
                    "progress": torrent.get("progress", 0),
                }
            )
            continue

        # A torrent spanning several entries (multi-season pack) is counted on
        # each, but its upload is split so totals stay exact.
        share = (torrent.get("uploaded") or 0) / len(targets)
        for i in targets:
            entry = entries[i]
            entry.torrents.append(torrent.get("name", ""))
            entry.uploaded += share
            if (torrent.get("progress") or 0) >= 1:
                entry.complete = True
            for key in keys:
                if key not in entry.trackers:
                    entry.trackers.append(key)
    return unmatched, len(torrents)


def tracker_table(entries, indexers):
    """One row per tracker: the union of Prowlarr indexers and seen trackers."""
    table = {}
    for key, idx in indexers.items():
        table[key] = {
            "key": key,
            "name": idx["name"],
            "in_prowlarr": True,
            **idx,
            "entries": 0,
            "size": 0,
            "uploaded": 0,
        }
    for entry in entries:
        for key in entry.trackers:
            row = table.setdefault(
                key, {"key": key, "name": key, "in_prowlarr": False, "entries": 0, "size": 0, "uploaded": 0}
            )
            row["entries"] += 1
            row["size"] += entry.size
            row["uploaded"] += entry.uploaded
    return sorted(table.values(), key=lambda r: (-r["entries"], r["name"].lower()))


def run(cfg, log, progress=lambda msg: None):
    """Full collection. Returns the snapshot dict written by the report module."""
    started = time.time()
    warnings = []

    def warn(message):
        warnings.append(message)
        log.warn(message)

    progress("Scanning library")
    entries, inode_index = library.build(cfg, warn)
    if not entries:
        raise ApiError("no entry found in " + ", ".join(cfg.roots))

    progress("Connecting to qBittorrent")
    client = QbtClient(cfg.qbt_url, cfg.qbt_username, cfg.qbt_password)
    unmatched, torrent_count = correlate(cfg, client, entries, inode_index, progress)

    indexers = {}
    if cfg.prowlarr_enabled:
        progress("Reading Prowlarr indexers")
        try:
            indexers = prowlarr.indexers(
                prowlarr.ProwlarrClient(cfg.prowlarr_url, cfg.prowlarr_api_key), cfg.tracker_aliases
            )
        except ApiError as exc:
            warn(f"Prowlarr skipped: {exc}")

    rows = tracker_table(entries, indexers)
    for row in rows:
        if row["in_prowlarr"] and row["enabled"] and not row["entries"]:
            warn(f"tracker {row['name']} is in Prowlarr but seeds nothing from the library")
        if not row["in_prowlarr"] and indexers:
            warn(f"tracker {row['name']} seeds {row['entries']} library entries but is not in Prowlarr")
        if row["in_prowlarr"] and row.get("failing"):
            warn(f"tracker {row['name']} is failing in Prowlarr")

    counts = {s: sum(1 for e in entries if e.status == s) for s in ("seeded", "incomplete", "orphan")}
    total = len(entries)
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(time.time() - started, 1),
        "summary": {
            "entries": total,
            **counts,
            "coverage_pct": round((counts["seeded"] + counts["incomplete"]) / max(total, 1) * 100, 2),
            "size": sum(e.size for e in entries),
            "uploaded": int(sum(e.uploaded for e in entries)),
            "torrents": torrent_count,
            "unmatched_torrents": len(unmatched),
            "prowlarr": bool(indexers),
        },
        "trackers": rows,
        "entries": [
            {
                "category": e.category,
                "name": e.name,
                "path": e.path,
                "status": e.status,
                "trackers": e.trackers,
                "torrents": len(e.torrents),
                "size": e.size,
                "files": e.files,
                "uploaded": int(e.uploaded),
            }
            for e in entries
        ],
        "unmatched": unmatched,
        "warnings": warnings,
    }
