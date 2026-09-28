"""Collect the three sources (disk, qBittorrent, Prowlarr) and correlate them.

Torrents are matched to library entries by inode: cross-seed hardlinks point
at the same inodes as the organised files, whatever their name or path. The
torrent file list comes from the API (not a directory walk), so a torrent
never claims files it does not contain. Files qBittorrent has not finished
(or is rechecking) carry a ".!qB" suffix on disk: both names are tried.

On top of the matching, the snapshot carries a diagnosis: per-torrent and
per-entry issues with the fix the dashboard can apply, duplicate groups, why a
torrent sits outside the library, and a coverage timeline rebuilt from the
torrents' add dates.
"""

import os
import time
from datetime import UTC, date, datetime, timedelta

from seedbox import library, prowlarr, titles
from seedbox import trackers as trk
from seedbox.api import ApiError
from seedbox.config import fingerprint, map_path, unmap_path
from seedbox.qbittorrent import QbtClient

INCOMPLETE_SUFFIX = ".!qB"
STOPPED = ("stoppedDL", "stoppedUP", "pausedDL", "pausedUP")
DOWNLOADING = ("downloading", "stalledDL", "metaDL", "forcedDL", "queuedDL", "forcedMetaDL")
TIMELINE_DAYS = 120


def _torrent_trackers(client, torrent, aliases):
    # The current tracker is in the list already; ask for all only when none works.
    key = trk.key_for_url(torrent.get("tracker", ""), aliases)
    if key:
        return [key]
    keys = []
    try:
        items = client.trackers(torrent["hash"])
    except ApiError:
        items = []
    for item in items:
        key = trk.key_for_url(item.get("url", ""), aliases)
        if key and key not in keys:
            keys.append(key)
    return keys


def _stat_any(path):
    for candidate in (path, path + INCOMPLETE_SUFFIX):
        try:
            return os.stat(candidate)
        except OSError:
            continue
    return None


def _in_link_dir(cfg, path):
    parts = path.split("/")
    return any(d in parts for d in cfg.link_dirs)


def _under_roots(cfg, path):
    return any(path == r or path.startswith(r + "/") for r in cfg.roots)


def _torrent_files(cfg, client, torrent):
    """[(local path, file dict)] from the API file list, with both base paths tried."""
    try:
        files = client.files(torrent["hash"])
    except ApiError:
        files = []
    bases = [torrent.get("save_path") or ""]
    if torrent.get("download_path") and torrent["download_path"] not in bases:
        bases.append(torrent["download_path"])
    if not files:
        content = torrent.get("content_path")
        return (
            [(map_path(cfg, content), {"name": content, "size": torrent.get("size", 0), "progress": 1})]
            if content
            else []
        )
    out = []
    for index, f in enumerate(files):
        f.setdefault("index", index)
        chosen = None
        for base in bases:
            local = map_path(cfg, os.path.join(base, f.get("name", "")))
            if chosen is None:
                chosen = local
            if _stat_any(local):
                chosen = local
                break
        out.append((chosen, f))
    return out


def _is_media_name(cfg, name):
    name = name[: -len(INCOMPLETE_SUFFIX)] if name.endswith(INCOMPLETE_SUFFIX) else name
    return os.path.splitext(name)[1].lower() in cfg.media_ext


def _torrent_record(cfg, torrent, keys, files):
    state = torrent.get("state", "")
    progress = torrent.get("progress") or 0
    left = torrent.get("amount_left") or 0
    # Files still missing that are not media: .nfo, .jpg, samples nobody seeds.
    missing = [f for _, f in files if (f.get("progress") or 0) < 1 and (f.get("priority", 1) or 0) > 0]
    extras = [f["index"] for f in missing if not _is_media_name(cfg, f.get("name", ""))]
    media_missing = any(_is_media_name(cfg, f.get("name", "")) for f in missing)
    local_content = map_path(cfg, torrent.get("content_path") or "")
    issues = []
    link = torrent.get("category") == "cross-seed-link" or _in_link_dir(cfg, local_content)
    if state in STOPPED and progress < 1:
        if link and progress == 0:
            issues.append(
                {
                    "code": "failed_match",
                    "text": "cross-seed match stopped at 0 %: the data did not verify",
                    "fixes": ["recheck", "remove"],
                }
            )
        else:
            issues.append(
                {"code": "stopped", "text": f"stopped at {progress * 100:.1f} %", "fixes": ["start", "recheck"]}
            )
    elif state in STOPPED:
        issues.append({"code": "stopped", "text": "stopped while complete: not seeding", "fixes": ["start"]})
    if state in ("error", "missingFiles"):
        issues.append({"code": state, "text": f"qBittorrent reports {state}", "fixes": ["recheck"]})
    if state in DOWNLOADING and extras and not media_missing:
        issues.append(
            {
                "code": "extras",
                "text": f"waiting for {len(extras)} extra file(s) nobody seeds ({left / 1048576:.1f} MiB)",
                "fixes": ["skip_extras"],
            }
        )
    return {
        "hash": torrent.get("hash", ""),
        "name": torrent.get("name", ""),
        "state": state,
        "progress": progress,
        "left": left,
        "category": torrent.get("category", ""),
        "tracker": keys[0] if keys else "",
        "save_path": torrent.get("save_path", ""),
        "content_path": torrent.get("content_path", ""),
        "size": torrent.get("size") or 0,
        "uploaded": torrent.get("uploaded") or 0,
        "ratio": round(torrent.get("ratio") or 0, 2),
        "seeds": torrent.get("num_complete", 0),
        "leechs": torrent.get("num_incomplete", 0),
        "added_on": torrent.get("added_on", 0),
        "link": link,
        "extras": extras,
        "issues": issues,
        "entries": [],
    }


def correlate(cfg, client, entries, inode_index, progress=lambda msg: None):
    """Attach torrents to entries. Returns (torrent records, unmatched list)."""
    records, unmatched = [], []
    torrents = client.torrents()
    for position, torrent in enumerate(torrents, 1):
        progress(f"Correlating torrents {position}/{len(torrents)}")
        keys = _torrent_trackers(client, torrent, cfg.tracker_aliases)
        files = _torrent_files(cfg, client, torrent)
        record = _torrent_record(cfg, torrent, keys, files)
        records.append(record)

        targets, found = set(), 0
        for path, _ in files:
            st = _stat_any(path)
            if st is None:
                continue
            found += 1
            targets.update(inode_index.get((st.st_dev, st.st_ino), []))
        if not targets:
            # Fallback on paths: files not on disk yet (downloading) or unreadable.
            for path, _ in files:
                for i, entry in enumerate(entries):
                    if path == entry.path or path.startswith(entry.path + "/"):
                        targets.add(i)
                        break

        if not targets:
            content = map_path(cfg, torrent.get("content_path") or "")
            if not found:
                reason = "downloading" if record["progress"] < 1 else "missing"
            elif _in_link_dir(cfg, content):
                reason = "link_only"
            elif _under_roots(cfg, content):
                reason = "unrecognised"
            else:
                reason = "outside"
            unmatched.append(
                {
                    "hash": record["hash"],
                    "name": record["name"],
                    "path": content,
                    "trackers": keys,
                    "progress": record["progress"],
                    "state": record["state"],
                    "reason": reason,
                }
            )
            continue

        record["entries"] = sorted(targets)
        # A torrent spanning several entries (multi-season pack) is counted on
        # each, but its upload is split so totals stay exact.
        share = record["uploaded"] / len(targets)
        for i in targets:
            entry = entries[i]
            entry.torrents.append(len(records) - 1)
            entry.uploaded += share
            if record["progress"] >= 1:
                entry.complete = True
            for key in keys:
                if key not in entry.trackers:
                    entry.trackers.append(key)
    return records, unmatched


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


def _keep_best(records):
    """The torrent to keep among redundant ones: complete, most seeded, most uploaded."""
    return max(records, key=lambda r: (r["progress"] >= 1, not r["issues"], r["seeds"], r["uploaded"], -r["added_on"]))


def diagnose(entries, records, target_keys):
    """Per-entry issues, coverage level and duplicate groups."""
    duplicates = []
    for i, entry in enumerate(entries):
        entry.issues = []
        entry.coverage = "none"
        if entry.torrents:
            covered = set(entry.trackers) & target_keys if target_keys else set(entry.trackers)
            entry.coverage = "everywhere" if target_keys and target_keys <= covered else "partial"
        for t in entry.torrents:
            for issue in records[t]["issues"]:
                entry.issues.append({**issue, "torrent": records[t]["hash"]})
        # Several torrents of the same tracker on the same content.
        by_tracker = {}
        for t in entry.torrents:
            if records[t]["tracker"] and len(records[t]["entries"]) == 1:
                by_tracker.setdefault(records[t]["tracker"], []).append(records[t])
        for key, group in sorted(by_tracker.items()):
            if len(group) < 2:
                continue
            keep = _keep_best(group)
            extra = [r["hash"] for r in group if r is not keep]
            entry.issues.append(
                {
                    "code": "same_tracker",
                    "text": f"{len(group)} torrents on {key} for the same files",
                    "fixes": ["remove_extra"],
                    "keep": keep["hash"],
                    "remove": extra,
                }
            )
            duplicates.append(
                {
                    "kind": "same_tracker",
                    "title": entry.name,
                    "tracker": key,
                    "entries": [i],
                    "torrents": [r["hash"] for r in group],
                    "keep": keep["hash"],
                    "remove": extra,
                }
            )
        if entry.lone:
            entry.issues.append(
                {
                    "code": "lone_film",
                    "text": f"only film in the grouping folder {entry.folder}: move it next to the others, "
                    "or it is a leftover of a move",
                    "fixes": ["move"],
                }
            )
        if entry.duplicate_episodes:
            entry.issues.append(
                {
                    "code": "episodes",
                    "text": "episodes present in several versions: " + ", ".join(entry.duplicate_episodes),
                    "fixes": [],
                }
            )
            duplicates.append(
                {"kind": "episodes", "title": entry.name, "entries": [i], "episodes": entry.duplicate_episodes}
            )

    # Several entries (different files) of the same work: versions.
    groups = {}
    for i, entry in enumerate(entries):
        info = titles.parse(os.path.basename(entry.name))
        if entry.kind == "dir" and not info["year"] and not info["episode"]:
            continue  # show folders, collections: no reliable identity
        if len(info["title"]) < 2:
            continue
        entry.title_key = info["key"]
        entry.resolution = info["resolution"]
        groups.setdefault(info["key"], []).append(i)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        for i in members:
            others = [entries[j].name for j in members if j != i]
            entries[i].issues.append(
                {"code": "versions", "text": "other version(s): " + "; ".join(others), "fixes": [], "group": key}
            )
        best = max(members, key=lambda j: (titles.rank(entries[j].name), bool(entries[j].torrents), entries[j].size))
        duplicates.append(
            {
                "kind": "versions",
                "title": key.split("|")[0] + (f" ({key.split('|')[1]})" if key.split("|")[1] else ""),
                "entries": members,
                "best": best,
            }
        )
    return duplicates


def timeline(entries, records, rows):
    """Coverage per day, rebuilt from the add date of each entry's first torrent.

    Approximation: removed torrents are unknown, so it shows when content got
    seeded, not when it stopped being seeded.
    """
    first = {}
    per_tracker = {}
    for i, entry in enumerate(entries):
        stamps = [records[t]["added_on"] for t in entry.torrents if records[t]["added_on"]]
        if not stamps:
            continue
        first[i] = min(stamps)
        for t in entry.torrents:
            key = records[t]["tracker"]
            if key and records[t]["added_on"]:
                cur = per_tracker.setdefault(key, {})
                cur[i] = min(cur.get(i, records[t]["added_on"]), records[t]["added_on"])
    if not first:
        return []
    today = date.today()
    start = max(datetime.fromtimestamp(min(first.values()), UTC).date(), today - timedelta(days=TIMELINE_DAYS))
    names = {r["key"]: r["name"] for r in rows}
    out = []
    day = start
    while day <= today:
        end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC).timestamp()
        point = {"date": day.isoformat(), "seeded": sum(1 for v in first.values() if v <= end), "trackers": {}}
        for key, stamps in per_tracker.items():
            point["trackers"][names.get(key, key)] = sum(1 for v in stamps.values() if v <= end)
        out.append(point)
        day += timedelta(days=1)
    return out


def added_per_day(records, days=60):
    today = date.today()
    start = today - timedelta(days=days - 1)
    counts = {(start + timedelta(days=n)).isoformat(): {"cross_seed": 0, "other": 0} for n in range(days)}
    for r in records:
        if not r["added_on"]:
            continue
        day = datetime.fromtimestamp(r["added_on"], UTC).date().isoformat()
        if day in counts:
            counts[day]["cross_seed" if r["link"] else "other"] += 1
    return [{"date": d, **c} for d, c in counts.items()]


def folders(cfg, entries):
    """Library folders holding entries, as move destinations (local and qBittorrent paths)."""
    seen = {}
    for e in entries:
        local = os.path.dirname(e.path)
        if _under_roots(cfg, local):
            seen[local] = seen.get(local, 0) + 1
    return [
        {"path": p, "qbt": unmap_path(cfg, p), "label": _label(cfg, p), "entries": n}
        for p, n in sorted(seen.items(), key=lambda kv: kv[0].lower())
    ]


def _label(cfg, path):
    for root in cfg.roots:
        if path == root or path.startswith(root + "/"):
            parent = os.path.dirname(root)
            return os.path.relpath(path, parent)
    return path


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
    records, unmatched = correlate(cfg, client, entries, inode_index, progress)

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

    # "Everywhere" = on every enabled Prowlarr tracker (or every tracker seen).
    target = {k for k, v in indexers.items() if v.get("enabled")} or {r["key"] for r in rows}
    duplicates = diagnose(entries, records, target)

    counts = {s: sum(1 for e in entries if e.status == s) for s in ("seeded", "incomplete", "orphan")}
    coverage = {c: sum(1 for e in entries if e.coverage == c) for c in ("everywhere", "partial", "none")}
    problems = sum(1 for e in entries if any(i["code"] not in ("versions", "episodes") for i in e.issues))
    dup_entries = {i for d in duplicates for i in d["entries"]}
    states = {}
    for r in records:
        states[r["state"]] = states.get(r["state"], 0) + 1
    total = len(entries)
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": fingerprint(cfg),
        "duration_s": round(time.time() - started, 1),
        "summary": {
            "entries": total,
            **counts,
            **coverage,
            "problems": problems,
            "duplicates": len(dup_entries),
            "coverage_pct": round((counts["seeded"] + counts["incomplete"]) / max(total, 1) * 100, 2),
            "size": sum(e.size for e in entries),
            "uploaded": int(sum(e.uploaded for e in entries)),
            "torrents": len(records),
            "cross_seed_torrents": sum(1 for r in records if r["link"]),
            "unmatched_torrents": len(unmatched),
            "prowlarr": bool(indexers),
            "target_trackers": sorted(target),
        },
        "trackers": rows,
        "entries": [
            {
                "category": e.category,
                "name": e.name,
                "folder": e.folder,
                "kind": e.kind,
                "path": e.path,
                "status": e.status,
                "coverage": e.coverage,
                "trackers": e.trackers,
                "torrents": [records[t]["hash"] for t in e.torrents],
                "size": e.size,
                "files": e.files,
                "uploaded": int(e.uploaded),
                "issues": e.issues,
                "resolution": e.resolution,
            }
            for e in entries
        ],
        "torrents": records,
        "duplicates": duplicates,
        "unmatched": unmatched,
        "folders": folders(cfg, entries),
        "timeline": timeline(entries, records, rows),
        "added": added_per_day(records),
        "states": dict(sorted(states.items(), key=lambda kv: -kv[1])),
        "actions": cfg.actions,
        "warnings": warnings,
    }
