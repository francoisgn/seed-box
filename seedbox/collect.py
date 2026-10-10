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
import re
import time
from datetime import UTC, date, datetime, timedelta

from seedbox import crossseed, library, prowlarr, titles
from seedbox import trackers as trk
from seedbox.api import ApiError
from seedbox.config import fingerprint, map_path, unmap_path
from seedbox.qbittorrent import QbtClient

INCOMPLETE_SUFFIX = ".!qB"
STOPPED = ("stoppedDL", "stoppedUP", "pausedDL", "pausedUP")
DOWNLOADING = ("downloading", "stalledDL", "metaDL", "forcedDL", "queuedDL", "forcedMetaDL")
CHECKING = ("checkingDL", "checkingUP", "checkingResumeData", "moving")
TIMELINE_DAYS = 120


# Messages trackers send for a torrent they deleted (dupe, trumped, nuked).
UNREGISTERED = re.compile(
    r"(?i)unregistered|not registered|torrent (?:not found|does not exist|not exist|(?:has been )?(?:deleted|removed))"
    r"|info_?hash not found|trumped|\bdupe\b|nuked"
)
# HTTP 404 on the announce: the torrent is gone, unless the whole tracker answers
# that way (announce URL or passkey changed); correlate() tells the two apart.
# Not "Host not found": that is DNS.
NOT_FOUND = re.compile(r"(?i)\b404\b|^not found\.?$")
TRACKER_NOT_WORKING = 4


def _torrent_trackers(client, torrent, aliases):
    """(tracker keys, messages of trackers not working, every tracker key). The
    working tracker is in the torrent already; the list is asked for only when
    none works or the torrent has several trackers (extras to strip?)."""
    key = trk.key_for_url(torrent.get("tracker", ""), aliases)
    if key and (torrent.get("trackers_count") or 1) <= 1:
        return [key], [], [key]
    keys, errors = [], []
    try:
        items = client.trackers(torrent["hash"])
    except ApiError:
        items = []
    for item in items:
        other = trk.key_for_url(item.get("url", ""), aliases)
        if not other:
            continue
        if other not in keys:
            keys.append(other)
        if item.get("status") == TRACKER_NOT_WORKING:
            errors.append({"tracker": other, "msg": (item.get("msg") or "").strip()})
    if key:
        return [key], [], [key] + [k for k in keys if k != key]
    return keys, errors, keys


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


def _in_transient(cfg, path):
    return bool(cfg.transient_dir) and (path == cfg.transient_dir or path.startswith(cfg.transient_dir + "/"))


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


def _overlap(a, b):
    ra, rb = a.get("piece_range") or (), b.get("piece_range") or ()
    return len(ra) == 2 and len(rb) == 2 and ra[0] <= rb[1] and rb[0] <= ra[1]


def _piece_gaps(cfg, files, pieces):
    """What the missing pieces mean, from qBittorrent's piece states.

    Returns (stuck, inner). stuck: indexes of missing extras (.nfo, .jpg, sample)
    that share a piece with a media file: skipping them does not finish the
    torrent, that piece stays wanted for the media. inner: [(local path, file,
    count)] of media files missing pieces that no missing extra explains, data
    really absent or differing. Both empty when the piece states are unknown."""
    if not pieces:
        return [], []
    missing = [f for _, f in files if (f.get("progress") or 0) < 1 and (f.get("priority", 1) or 0) > 0]
    extras = [f for f in missing if not _is_media_name(cfg, f.get("name", ""))]
    media = [(path, f) for path, f in files if _is_media_name(cfg, f.get("name", ""))]
    stuck = [f["index"] for f in extras if any(_overlap(f, m) for _, m in media)]
    covered = set()
    for f in extras:
        r = f.get("piece_range") or ()
        if len(r) == 2:
            covered.update(range(r[0], r[1] + 1))
    inner = []
    for path, f in media:
        r = f.get("piece_range") or ()
        if (f.get("progress") or 0) >= 1 or len(r) != 2:
            continue
        count = sum(1 for p in range(r[0], min(r[1] + 1, len(pieces))) if pieces[p] != 2 and p not in covered)
        if count:
            inner.append((path, f, count))
    return stuck, inner


def _hardlinked(path):
    st = _stat_any(path)
    return bool(st) and st.st_nlink > 1


def _torrent_record(cfg, torrent, keys, files, tracker_errors=(), pieces=None, now=None):
    state = torrent.get("state", "")
    progress = torrent.get("progress") or 0
    left = torrent.get("amount_left") or 0
    # Files still missing that are not media: .nfo, .jpg, samples nobody seeds.
    missing = [f for _, f in files if (f.get("progress") or 0) < 1 and (f.get("priority", 1) or 0) > 0]
    extras = [f["index"] for f in missing if not _is_media_name(cfg, f.get("name", ""))]
    media_missing = any(_is_media_name(cfg, f.get("name", "")) for f in missing)
    stuck, inner = _piece_gaps(cfg, files, pieces)
    if pieces:
        # A media file a few bytes short only because an extra shares its last piece is not missing data.
        media_missing = bool(inner)
    local_content = map_path(cfg, torrent.get("content_path") or "")
    issues = []
    link = torrent.get("category") == cfg.link_category or _in_link_dir(cfg, local_content)
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
    for err in tracker_errors:
        not_found = bool(NOT_FOUND.search(err["msg"]))
        if not_found or UNREGISTERED.search(err["msg"]):
            issues.append(
                {
                    "code": "unregistered",
                    "text": f"{err['tracker']} deleted this torrent ({err['msg']}): remove it; the content is "
                    "searched again later, or re-add it by hand",
                    "fixes": ["remove"],
                    "tracker": err["tracker"],
                    "not_found": not_found,
                }
            )
        else:
            issues.append(
                {"code": "tracker_error", "text": f"{err['tracker']}: {err['msg'] or 'not working'}", "fixes": []}
            )
    if state in DOWNLOADING and extras and not media_missing and not stuck:
        issues.append(
            {
                "code": "extras",
                "text": f"waiting for {len(extras)} extra file(s) nobody seeds ({left / 1048576:.1f} MiB)",
                "fixes": ["skip_extras"],
            }
        )
    # Cross-seed matches that can only end badly: the link data differs from the
    # release, or the bytes it lacks can come from no one.
    differing = [(path, f, n) for path, f, n in inner if (f.get("progress") or 0) > 0 and _hardlinked(path)]
    seeders = torrent.get("num_complete") or 0
    availability = torrent.get("availability", -1)
    added = torrent.get("added_on") or 0
    days = ((now or time.time()) - added) / 86400 if added else 0
    blocked = None
    if link and progress < 1 and differing:
        path, f, n = differing[0]
        blocked = {
            "code": "differing_media",
            "text": f"{n} piece(s) of {os.path.basename(path)} differ from this release: finishing it would rewrite "
            "the library file through its hardlink. Remove it and block its infohash in cross-seed",
            "fixes": ["remove"],
        }
    elif (
        link
        and progress < 1
        and cfg.dead_partial_days > 0
        and (state in DOWNLOADING or state in STOPPED)
        and (stuck or inner)
        and not seeders
        and (availability is None or availability < 1)
        and days >= cfg.dead_partial_days
    ):
        what = (
            f"{sum(n for _, _, n in inner)} piece(s) inside the video"
            if inner
            else f"{len(stuck)} extra file(s) sharing a piece with the video"
        )
        blocked = {
            "code": "dead_partial",
            "text": f"never finishes: {what} missing, no seeder after {int(days)} days. It seeds nothing: "
            "remove it and block its infohash in cross-seed",
            "fixes": ["remove"],
        }
    if blocked:
        # Start, recheck or skipping extras would not get it anywhere.
        issues = [i for i in issues if i["code"] not in ("stopped", "extras")]
        issues.append(blocked)
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
        "downloaded": torrent.get("downloaded") or 0,
        "ratio": round(torrent.get("ratio") or 0, 2),
        "seeding_time": torrent.get("seeding_time") or 0,
        "auto_tmm": bool(torrent.get("auto_tmm")),
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
        keys, tracker_errors, every = _torrent_trackers(client, torrent, cfg.tracker_aliases)
        files = _torrent_files(cfg, client, torrent)
        pieces = None
        if (torrent.get("progress") or 0) < 1 and torrent.get("state") not in CHECKING:
            try:
                pieces = client.piece_states(torrent["hash"])
            except ApiError:
                pieces = None
        record = _torrent_record(cfg, torrent, keys, files, tracker_errors, pieces)
        record["all_trackers"] = every
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
            if record["progress"] < 1 and _in_transient(cfg, content):
                # The download queue, kept apart from the library on purpose: not a problem.
                reason = "transient"
            elif not found:
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
    _whole_tracker_404(records)
    return records, unmatched


def _whole_tracker_404(records):
    """A 404 means a deleted torrent only if the tracker still works for others."""
    working = {
        r["tracker"] for r in records if not any(i["code"] in ("unregistered", "tracker_error") for i in r["issues"])
    }
    for r in records:
        for i in r["issues"]:
            if i.get("not_found") and i["tracker"] not in working:
                i.update(
                    code="tracker_error",
                    text=f"{i['tracker']} answers 404 for every torrent: announce URL or passkey changed?",
                    fixes=[],
                )


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
                    "text": "several files for the same episode: " + ", ".join(entry.duplicate_episodes),
                    "fixes": [],
                    "files": entry.duplicate_episodes,
                }
            )
            duplicates.append(
                {"kind": "episodes", "title": entry.name, "entries": [i], "episodes": entry.duplicate_episodes}
            )

    # Several entries (different files) of the same work: versions.
    groups = {}
    for i, entry in enumerate(entries):
        info = titles.parse(os.path.basename(entry.name))
        if entry.kind == "dir" and not info["year"] and not info["episode"] and not info["season"]:
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


def search_status(cfg, entries, xs, target):
    """Per entry, for each target tracker it is missing on: has cross-seed searched there?

    States: complete (on every target tracker), opportunity (searched everywhere
    it is missing, absent from at least one: upload it), other_release (searched,
    only other releases of the title there: an upload may be a dupe), found (a
    match exists that is not in qBittorrent), unsearched, not_indexed (outside
    cross-seed's dataDirs).
    """
    counts = {"complete": 0, "opportunity": 0, "other_release": 0, "found": 0, "unsearched": 0, "not_indexed": 0}
    for entry in entries:
        missing = sorted(set(target) - set(entry.trackers))
        if not missing:
            entry.search_state = "complete"
        else:
            title = xs["titles"].get(unmap_path(cfg, entry.path))
            if title is None:
                entry.search_state = "not_indexed"
            else:
                slots = xs["searchees"].get(title, {})
                for key in missing:
                    slot = slots.get(key)
                    entry.search[key] = {
                        "verdict": crossseed.verdict(slot),
                        "searched": (slot or {}).get("searched", 0),
                    }
                verdicts = {v["verdict"] for v in entry.search.values()}
                if "unsearched" in verdicts:
                    entry.search_state = "unsearched"
                elif "found" in verdicts:
                    entry.search_state = "found"
                elif "absent" in verdicts:
                    entry.search_state = "opportunity"
                else:
                    # Only other releases of the title there: an upload may be refused as a dupe.
                    entry.search_state = "other_release"
        counts[entry.search_state] += 1
    return counts


def category_check(cfg, records, categories):
    """Is each torrent in the category that matches where its files are?

    Library torrents: a category whose save path is their folder. Cross-seed
    link torrents: the link category, inside a link folder. Transient downloads
    (partial, one-off): anywhere under transient_dir, until finished.
    """
    paths = {name: (c.get("savePath") or "").rstrip("/") for name, c in categories.items()}
    by_path = {}
    for name, path in paths.items():
        if path and name != cfg.link_category:
            by_path.setdefault(path, name)
    transient = unmap_path(cfg, cfg.transient_dir) if cfg.transient_dir else ""
    rows = []
    for r in records:
        save = (r["save_path"] or "").rstrip("/")
        cat = r["category"]
        status, text, fix, suggest = "ok", "", "", ""
        if r["link"]:
            if cat != cfg.link_category:
                status, text, fix, suggest = (
                    "ko",
                    f"cross-seed link without the {cfg.link_category} category",
                    "set_category",
                    cfg.link_category,
                )
        elif cat == cfg.link_category:
            status, text = "ko", "library torrent in the cross-seed link category"
            suggest = by_path.get(save, "")
            fix = "set_category" if suggest else ""
        elif transient and (save == transient or save.startswith(transient + "/")):
            if r["progress"] >= 1:
                status, text = "warn", "finished but still in the transient folder: give it a category, or clean it"
        elif not cat:
            suggest = by_path.get(save, "")
            status, text = (
                "ko",
                "no category" + (f": {suggest} matches its folder" if suggest else ", and no category has this folder"),
            )
            fix = "set_category" if suggest else ""
        elif paths.get(cat, "") != save:
            status = "warn"
            target = paths.get(cat) or "the default folder"
            text = f"category {cat} points to {target}: moved there once auto management is on"
            fix = "apply_category"
        if status != "ok":
            rows.append({"hash": r["hash"], "status": status, "text": text, "fix": fix, "suggest": suggest})
    counts = {"ok": len(records) - len(rows)}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"counts": counts, "issues": rows, "categories": paths}


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


def ratio_table(records, target, names):
    """Upload and download of the torrents in qBittorrent, per declared tracker
    (Prowlarr / cross-seed). Other trackers are left out: the "Undeclared
    trackers" tile lists their torrents, to clean them."""
    groups = {k: {"key": k, "name": names.get(k, k), "up": 0, "down": 0, "torrents": 0} for k in target}
    for r in records:
        g = groups.get(r.get("tracker"))
        if g:
            g["up"] += r.get("uploaded") or 0
            g["down"] += r.get("downloaded") or 0
            g["torrents"] += 1
    return sorted(groups.values(), key=lambda g: g["name"].lower())


def link_folders(cfg):
    """Cross-seed link folders on disk: each link_dirs name next to a library root."""
    seen = []
    for root in cfg.roots:
        for name in cfg.link_dirs:
            path = os.path.join(os.path.dirname(root), name)
            if os.path.isdir(path) and path not in seen:
                seen.append(path)
    return seen


def orphan_links(cfg, records, limit=500):
    """Files in the link folders that no torrent uses: leftovers of torrents
    removed without their files. {'count', 'bytes', 'files', 'script', 'writable'}.

    bytes: space freed by deleting them (files without another hardlink). The
    script runs from the media share root (the folder holding the link folder).
    writable: the link folders are mounted read-write, seedbox can delete them."""
    used = {map_path(cfg, r.get("content_path") or "").rstrip("/") for r in records}
    used.discard("")
    found, freed = [], 0
    for folder in link_folders(cfg):
        base = os.path.dirname(folder)
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d not in cfg.skip_dirs and not d.startswith("@"))
            # A torrent folder in use: everything under it belongs to that torrent.
            if root.rstrip("/") in used:
                dirs[:] = []
                continue
            for name in sorted(files):
                path = os.path.join(root, name)
                if path in used:
                    continue
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                if st.st_nlink == 1:
                    freed += st.st_size
                found.append({"path": os.path.relpath(path, base), "size": st.st_size, "links": st.st_nlink})
    lines = [f'rm -f -- "{f["path"]}"' for f in found]
    lines += [f'find "{os.path.relpath(folder, os.path.dirname(folder))}" -mindepth 2 -type d -empty -delete'
              for folder in link_folders(cfg)] if found else []  # fmt: skip
    writable = bool(link_folders(cfg)) and all(os.access(f, os.W_OK) for f in link_folders(cfg))
    return {
        "count": len(found),
        "bytes": freed,
        "files": found[:limit],
        "script": "\n".join(lines),
        "writable": writable,
    }


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
        if row["key"] in cfg.tracker_colors:
            row["color"] = cfg.tracker_colors[row["key"]]
        if row["in_prowlarr"] and row["enabled"] and not row["entries"]:
            warn(f"tracker {row['name']} is in Prowlarr but seeds nothing from the library")
        if not row["in_prowlarr"] and indexers:
            warn(f"tracker {row['name']} seeds {row['entries']} library entries but is not in Prowlarr")
        if row["in_prowlarr"] and row.get("failing"):
            warn(f"tracker {row['name']} is failing in Prowlarr")

    # Torrents on trackers Prowlarr does not know: public or one-off sharing.
    # Extra trackers on a declared torrent (public announces a .torrent ships
    # with): they leak the torrent and the IP outside the private tracker.
    for r in records:
        r["declared"] = not indexers or not r["tracker"] or r["tracker"] in indexers
        every = r.pop("all_trackers", [])
        r["extra_trackers"] = (
            [k for k in every if k not in indexers] if indexers and any(k in indexers for k in every) else []
        )
    try:
        categories = client.categories()
    except ApiError as exc:
        categories = {}
        warn(f"qBittorrent categories unavailable: {exc}")

    # "Everywhere" = on every enabled Prowlarr tracker (or every tracker seen).
    target = {k for k, v in indexers.items() if v.get("enabled")} or {r["key"] for r in rows}
    duplicates = diagnose(entries, records, target)
    names = {r["key"]: r["name"] for r in rows}
    names.update({k: v["name"] for k, v in indexers.items()})
    ratios = ratio_table(records, target, names)

    progress("Reading cross-seed history")
    indexer_keys = {v["name"].lower(): k for k, v in indexers.items()}
    xs = crossseed.read(cfg.cross_seed_db, cfg.tracker_aliases, indexer_keys)
    search = search_status(cfg, entries, xs, target) if xs else None
    if xs:
        now_ms = time.time() * 1000
        for idx in xs["indexers"]:
            # A limit whose retry time is past is over: cross-seed just has not queried the indexer since.
            if idx["status"] and idx["status"] != "OK" and idx.get("retry_after", 0) > now_ms:
                until = datetime.fromtimestamp(idx["retry_after"] / 1000).astimezone().strftime("%H:%M")
                warn(f"cross-seed: indexer {idx['name']} is {idx['status']} until {until}, its searches are on hold")

    counts = {s: sum(1 for e in entries if e.status == s) for s in ("seeded", "incomplete", "orphan")}
    coverage = {c: sum(1 for e in entries if e.coverage == c) for c in ("everywhere", "partial", "none")}
    problems = sum(1 for e in entries if any(i["code"] not in ("versions", "episodes") for i in e.issues))
    errors = [
        r["hash"]
        for r in records
        if any(
            i["code"]
            in (
                "unregistered",
                "tracker_error",
                "error",
                "missingFiles",
                "failed_match",
                "dead_partial",
                "differing_media",
            )
            for i in r["issues"]
        )
    ]
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
            "error_torrents": errors,
            "duplicates": len(dup_entries),
            "coverage_pct": round((counts["seeded"] + counts["incomplete"]) / max(total, 1) * 100, 2),
            "size": sum(e.size for e in entries),
            "uploaded": int(sum(e.uploaded for e in entries)),
            "torrents": len(records),
            "cross_seed_torrents": sum(1 for r in records if r["link"]),
            "unmatched_torrents": sum(1 for u in unmatched if u["reason"] != "transient"),
            "transient_downloads": sum(1 for u in unmatched if u["reason"] == "transient"),
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
                "search": e.search,
                "search_state": e.search_state,
            }
            for e in entries
        ],
        "search": search,
        "cross_seed": {"indexers": xs["indexers"]} if xs else None,
        "categories": category_check(cfg, records, categories),
        "undeclared": [r["hash"] for r in records if not r["declared"]],
        "extra_trackers": [r["hash"] for r in records if r["extra_trackers"]],
        "transient_qbt": unmap_path(cfg, cfg.transient_dir) if cfg.transient_dir else "",
        "torrents": records,
        "duplicates": duplicates,
        "unmatched": unmatched,
        "orphan_links": orphan_links(cfg, records),
        "ratios": ratios,
        "folders": folders(cfg, entries),
        "timeline": timeline(entries, records, rows),
        "added": added_per_day(records),
        "states": dict(sorted(states.items(), key=lambda kv: -kv[1])),
        "actions": cfg.actions,
        "warnings": warnings,
    }
