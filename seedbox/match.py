"""Release matching: find a library file on the trackers under its release name.

A file renamed on disk (or a release named in another language) is invisible to
cross-seed, which searches by name. Here the search goes the other way:

1. search: every title TMDB knows the film under (French, English, original)
   plus the year, and the IMDb id where the indexer supports it, on each
   Prowlarr indexer. A result of exactly the file's size is a candidate.
2. verify: fetch the candidate's .torrent and hash a sample of its pieces from
   the local file: same bytes, same release.
3. apply, in the background (a job):
   - inject: add the torrent to qBittorrent, stopped, pointing at the library
     file (its file renamed to the local name, other files skipped), recheck,
     start only when the check confirms 100 %; nothing is ever downloaded
     into the library;
   - rename: rename the library file to the release name through qBittorrent
     (seedbox mounts the media read-only), then the other torrents using the
     same file follow.

Candidates keep Prowlarr's download links (with its API key) on the server;
the page only gets opaque ids.
"""

import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from seedbox import actions, titles, tmdb
from seedbox import torrentfile as tf
from seedbox.api import ApiError
from seedbox.config import map_path, unmap_path
from seedbox.prowlarr import ProwlarrClient, search_indexers

# A result this much bigger than the file: the same release with small extras (.nfo).
EXTRAS_SLACK = 5 * 1048576
KEEP = 200
CHECK_POLL_S = 30
CHECK_MAX_S = 12 * 3600
NAME = re.compile(r"^[^/\\\x00]{1,240}$")

_candidates = {}  # id -> candidate, with its download link
_torrents = {}  # infohash -> verified torrent
_lock = threading.Lock()


class MatchError(Exception):
    pass


def _remember(store, key, value):
    with _lock:
        store[key] = value
        while len(store) > KEEP:
            store.pop(next(iter(store)))


# ---------- entry and its main file
_snap = {"mtime": None, "data": None}


def load_snapshot(cfg):
    """The last collection (entries with their paths), re-read when it changes."""
    import json

    path = os.path.join(cfg.output_dir, "snapshot.json")
    try:
        mtime = os.path.getmtime(path)
        if _snap["mtime"] != mtime:
            with open(path, encoding="utf-8") as handle:
                _snap.update(mtime=mtime, data=json.load(handle))
    except (OSError, ValueError) as exc:
        raise MatchError("no collection yet: collect first") from exc
    return _snap["data"]


def handle(cfg, qbt_factory, body):
    """HTTP helper for POST /api/match: (status code, response dict).

    {"op": "search", "entry": i, "tmdb": id?} | {"op": "verify", "candidate": id}
    | {"op": "apply", "infohash": h, "mode": "inject"|"rename"|"inject_rename", "name": n}"""
    import json

    try:
        request = json.loads(body or b"{}")
        if not isinstance(request, dict):
            raise ValueError
    except ValueError:
        return 400, {"error": "invalid JSON body"}
    if not cfg.actions:
        # Searching and fetching .torrent files act on the trackers: same switch as the other actions.
        return 403, {"error": "actions are disabled ([service] actions = true to enable)"}
    op = request.get("op")
    try:
        snapshot = load_snapshot(cfg)
        if op == "search":
            tmdb_id = request.get("tmdb")
            if tmdb_id not in (None, ""):
                try:
                    tmdb_id = int(tmdb_id)
                except (TypeError, ValueError) as exc:
                    raise MatchError("TMDB id: a number") from exc
            return 200, search(cfg, snapshot, request.get("entry"), qbt_factory(), tmdb_id or None)
        if op == "verify":
            return 200, verify(cfg, snapshot, str(request.get("candidate") or ""), qbt_factory())
        if op == "apply":
            p = plan(
                cfg, snapshot, str(request.get("infohash") or ""), request.get("mode"), str(request.get("name") or "")
            )
            return 200, {"job": start(cfg, qbt_factory, p)}
    except MatchError as exc:
        return 400, {"error": str(exc)}
    except ApiError as exc:
        return 502, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": f"file access: {exc}"}
    return 400, {"error": f"unknown op: {op!r}"}


def _entry(snapshot, index):
    try:
        entry = snapshot["entries"][int(index)]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MatchError("unknown library entry, collect again") from exc
    return entry


def main_file(cfg, entry):
    """The media file of an entry: itself, or the biggest media file of its folder."""
    path = entry["path"]
    if not os.path.exists(path):
        raise MatchError("this entry moved or was renamed since the last collection: collect again")
    if entry.get("kind") == "file":
        return path
    best, size = None, -1
    for root, _, files in os.walk(path):
        for name in files:
            if os.path.splitext(name)[1].lower() in cfg.media_ext:
                full = os.path.join(root, name)
                s = os.path.getsize(full)
                if s > size:
                    best, size = full, s
    if not best:
        raise MatchError("no media file in this entry")
    return best


def _check_library_path(cfg, path):
    real = os.path.realpath(path)
    if not any(actions._under(real, os.path.realpath(r)) for r in cfg.roots):
        raise MatchError("file outside the library roots")
    return path


# ---------- search
def identity(cfg, name, tmdb_id=None):
    """{'id', 'titles', 'year', 'imdb', 'alternatives'}: from TMDB when a key is
    set, else the title parsed from the file name."""
    parsed = titles.parse(name)
    if not cfg.tmdb_api_key:
        if tmdb_id:
            raise MatchError("a TMDB id needs a TMDB API key ([tmdb] api_key)")
        title = re.sub(r"[._]+", " ", os.path.splitext(os.path.basename(name))[0])
        title = title[: title.find(parsed["year"])].strip() if parsed["year"] and parsed["year"] in title else title
        return {"id": None, "titles": [title], "year": parsed["year"], "imdb": "", "alternatives": []}
    alternatives = []
    if not tmdb_id:
        alternatives = tmdb.search(cfg.tmdb_api_key, parsed["title"], parsed["year"])
        if not alternatives:
            raise MatchError(
                f"TMDB knows no film named {parsed['title']!r} ({parsed['year'] or 'no year'}): give its id"
            )
        tmdb_id = alternatives[0]["id"]
    ident = tmdb.movie(cfg.tmdb_api_key, tmdb_id)
    ident["alternatives"] = alternatives
    return ident


def _queries(ident, indexer):
    out = [f"{t} {ident['year']}".strip() for t in ident["titles"]]
    if indexer["imdb"] and ident.get("imdb"):
        out.append(f"{{ImdbId:{ident['imdb']}}}")
    return out


def _search_indexer(client, indexer, queries):
    results, errors = [], []
    for q in queries:
        try:
            results.extend((indexer, r) for r in client.search(q, indexer["id"]))
        except ApiError as exc:
            errors.append(f"{indexer['name']}: {q}: {exc}")
    return results, errors


def search(cfg, snapshot, index, qbt=None, tmdb_id=None):
    """Candidates for one library entry, best first."""
    if not cfg.match_enabled:
        raise MatchError("release matching needs Prowlarr ([prowlarr] url and api key)")
    entry = _entry(snapshot, index)
    local = _check_library_path(cfg, main_file(cfg, entry))
    size = os.path.getsize(local)
    ident = identity(cfg, os.path.basename(local), tmdb_id)
    client = ProwlarrClient(cfg.prowlarr_url, cfg.prowlarr_api_key)
    indexers = search_indexers(client, cfg.tracker_aliases)
    with ThreadPoolExecutor(max_workers=max(len(indexers), 1)) as pool:
        done = list(pool.map(lambda ix: _search_indexer(client, ix, _queries(ident, ix)), indexers))
    live = {t["hash"] for t in qbt.torrents()} if qbt else set()

    seen, found, errors = set(), [], [e for _, errs in done for e in errs]
    for results, _ in done:
        for indexer, r in results:
            rsize = int(r.get("size") or 0)
            key = (r.get("infoHash") or "").lower() or (indexer["id"], r.get("title"), rsize)
            if key in seen:
                continue
            seen.add(key)
            verdict = "exact" if rsize == size else "extras" if size < rsize <= size + EXTRAS_SLACK else "other"
            cid = uuid.uuid4().hex[:12]
            infohash = (r.get("infoHash") or "").lower()
            _remember(
                _candidates,
                cid,
                {
                    "entry": int(index),
                    "url": r.get("downloadUrl") or "",
                    "title": r.get("title", ""),
                    "tracker": indexer["key"],
                },
            )
            found.append(
                {
                    "id": cid,
                    "tracker": indexer["key"],
                    "indexer": indexer["name"],
                    "title": r.get("title", ""),
                    "size": rsize,
                    "verdict": verdict,
                    "seeders": int(r.get("seeders") or 0),
                    "in_qbt": bool(infohash and infohash in live),
                    "seeded_there": indexer["key"] in (entry.get("trackers") or []),
                    "info_url": r.get("infoUrl") or "",
                }
            )
    rank = {"exact": 0, "extras": 1, "other": 2}
    found.sort(key=lambda c: (rank[c["verdict"]], -c["seeders"]))
    close = [c for c in found if c["verdict"] != "other"]
    return {
        "entry": {"index": int(index), "file": os.path.basename(local), "size": size},
        "identity": ident,
        "trackers": [ix["name"] for ix in indexers],
        "candidates": close + [c for c in found if c["verdict"] == "other"][:10],
        "others": sum(1 for c in found if c["verdict"] == "other"),
        "errors": errors,
    }


# ---------- verify
def _release_name(title, ext):
    """A search result title as a file name: "X.mkv.FRENCH" -> "X.mkv", "X" -> "X.mkv"."""
    m = re.search(r"(?i)\.(mkv|mp4|avi|m4v|ts|iso)\b", title)
    if m:
        return title[: m.end()]
    return title + ext


def suggestions(current, torrent_name, other_titles):
    """Names to rename the file to: the torrent's file name first, then the
    titles of the other exact matches, most frequent first; never the current name."""
    ext = os.path.splitext(current)[1]
    counts = {}
    for t in [torrent_name] + list(other_titles):
        n = _release_name(t, ext)
        counts[n] = counts.get(n, 0) + (100 if t == torrent_name else 1)
    names = sorted(counts, key=lambda n: -counts[n])
    return [n for n in names if n != current]


def verify(cfg, snapshot, cid, qbt=None):
    """Fetch the candidate's .torrent and hash a sample of its pieces from the local file."""
    with _lock:
        cand = _candidates.get(cid)
        siblings = [c["title"] for k, c in _candidates.items() if k != cid and c["entry"] == (cand or {}).get("entry")]
    if not cand:
        raise MatchError("candidate expired, search again")
    entry = _entry(snapshot, cand["entry"])
    local = _check_library_path(cfg, main_file(cfg, entry))
    size = os.path.getsize(local)
    data = ProwlarrClient(cfg.prowlarr_url, cfg.prowlarr_api_key).download(cand["url"])
    try:
        meta = tf.parse(data)
    except tf.TorrentError as exc:
        raise MatchError(str(exc)) from exc
    same = [f for f in meta["files"] if f["length"] == size]
    if not same:
        return {"verified": False, "reason": "no file of this size in the torrent", "infohash": meta["infohash"]}
    f = same[0]
    proof = tf.verify(meta, f, local)
    in_qbt = bool(qbt and any(t["hash"] == meta["infohash"] for t in qbt.torrents()))
    if proof["verified"]:
        _remember(
            _torrents,
            meta["infohash"],
            {"data": data, "meta": meta, "file": f, "entry": cand["entry"], "tracker": cand["tracker"]},
        )
    current = os.path.basename(local)
    return {
        "infohash": meta["infohash"],
        "torrent": meta["name"],
        "file": f["path"],
        "files": len(meta["files"]),
        "in_qbt": in_qbt,
        "current": current,
        # Other results of this search naming the same release (other trackers, other spellings).
        "names": suggestions(
            current, os.path.basename(f["path"]), [t for t in siblings if _same_release(t, f["path"])]
        ),
        "sidecars": _sidecars(local),
        **proof,
    }


def _same_release(title, path):
    """Whether a result title names the same release as the torrent file (case and separators aside)."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", os.path.splitext(os.path.basename(s))[0].lower())  # noqa: E731
    return norm(_release_name(title, "")).startswith(norm(path)) or norm(path).startswith(
        norm(_release_name(title, ""))
    )


def _sidecars(local):
    """Files next to the media file with the same stem (.nfo, .srt, .fr.srt…)."""
    folder, name = os.path.split(local)
    stem = os.path.splitext(name)[0]
    try:
        return sorted(n for n in os.listdir(folder) if n != name and n.startswith(stem + "."))
    except OSError:
        return []


# ---------- apply (background job)
def _torrents_on(qbt, cfg, local):
    """[(hash, file index, file name in qBittorrent)] of the torrents whose file is this path."""
    out = []
    for t in qbt.torrents():
        content = map_path(cfg, t.get("content_path") or "")
        if content != local and not local.startswith(content.rstrip("/") + "/"):
            continue
        save = map_path(cfg, t.get("save_path") or "")
        for i, f in enumerate(qbt.files(t["hash"]) or []):
            if os.path.normpath(os.path.join(save, f.get("name", ""))) == local:
                out.append((t["hash"], i, f["name"]))
    return out


def plan(cfg, snapshot, infohash, mode, new_name=""):
    """Validate an apply request. Returns what the worker needs."""
    if not cfg.actions:
        raise MatchError("actions are disabled ([service] actions = true to enable)")
    if mode not in ("inject", "rename", "inject_rename"):
        raise MatchError(f"unknown mode: {mode!r}")
    with _lock:
        verified = _torrents.get(str(infohash))
    if not verified:
        raise MatchError("release not verified (or expired), verify it again")
    local = _check_library_path(cfg, main_file(cfg, _entry(snapshot, verified["entry"])))
    if "rename" in mode:
        if not NAME.match(new_name or "") or new_name.startswith(".") or new_name in (".", ".."):
            raise MatchError("invalid file name")
        if os.path.splitext(new_name)[1].lower() != os.path.splitext(local)[1].lower():
            raise MatchError("the new name must keep the file extension")
        if os.path.exists(os.path.join(os.path.dirname(local), new_name)):
            raise MatchError(f"{new_name} already exists in that folder")
    return {"local": local, "mode": mode, "name": new_name, **verified}


def start(cfg, qbt_factory, job_plan):
    """Create the job and run it in the background. Returns the job."""
    qbt = qbt_factory()
    if "inject" in job_plan["mode"] and any(t["hash"] == job_plan["meta"]["infohash"] for t in qbt.torrents()):
        raise MatchError("this release is already in qBittorrent")
    if job_plan["mode"] == "rename" and not _torrents_on(qbt, cfg, job_plan["local"]):
        raise MatchError("no torrent uses this file: inject the release first (seedbox cannot rename by itself)")
    job = actions.add_job(
        cfg,
        {
            "action": "inject" if "inject" in job_plan["mode"] else "rename",
            "hash": job_plan["meta"]["infohash"],
            "name": job_plan["meta"]["name"],
            "from": os.path.basename(job_plan["local"]),
            "target": job_plan["name"] or job_plan["tracker"],
            "status": "running",
            "note": "queued",
        },
    )
    threading.Thread(target=_run, args=(cfg, qbt_factory, job_plan, job["id"]), daemon=True).start()
    return job


def _note(cfg, job_id, note, status=None):
    fields = {"note": note}
    if status:
        fields.update(status=status, finished=time.time())
    actions.update_job(cfg, job_id, **fields)


def _run(cfg, qbt_factory, p, job_id):
    try:
        qbt = qbt_factory()
        if "inject" in p["mode"] and not _inject(cfg, qbt, p, job_id):
            return
        if "rename" in p["mode"]:
            _rename(cfg, qbt, p, job_id)
        _note(cfg, job_id, "done", "done")
    except (ApiError, MatchError, OSError) as exc:
        _note(cfg, job_id, str(exc), "failed")


def _inject(cfg, qbt, p, job_id):
    local, infohash = p["local"], p["meta"]["infohash"]
    folder = unmap_path(cfg, os.path.dirname(local))
    categories = qbt.categories()
    category = next((n for n, c in categories.items() if (c.get("savePath") or "").rstrip("/") == folder), "")
    _note(cfg, job_id, "adding the torrent, stopped")
    qbt.add_torrent(p["data"], folder, category, stopped=True)
    for _ in range(30):
        if any(t["hash"] == infohash for t in qbt.torrents()):
            break
        time.sleep(1)
    else:
        raise MatchError("qBittorrent did not add the torrent")
    # No root folder (NoSubfolder): qBittorrent's file names drop the torrent name.
    files = qbt.files(infohash) or []
    target = next((i for i, f in enumerate(files) if f.get("size") == p["file"]["length"]), None)
    if target is None:
        raise MatchError("matched file not found in qBittorrent's file list")
    skip = [i for i in range(len(files)) if i != target]
    if skip:
        qbt.file_priority(infohash, skip, 0)
    current = os.path.basename(local)
    if files[target]["name"] != current:
        # The release file does not exist on disk yet: only the name in the torrent changes.
        qbt.rename_file(infohash, files[target]["name"], current)
    _note(cfg, job_id, "rechecking against the library file")
    qbt.recheck([infohash])
    deadline, polls = time.time() + CHECK_MAX_S, 0
    while time.time() < deadline:
        time.sleep(CHECK_POLL_S)
        polls += 1
        t = next((t for t in qbt.torrents() if t["hash"] == infohash), None)
        if t is None:
            raise MatchError("the torrent was removed during its recheck")
        # A few polls of grace: qBittorrent may not have started the check yet.
        if t["state"] in ("checkingDL", "checkingUP", "checkingResumeData", "queuedDL", "queuedUP") or polls < 3:
            continue
        if (t.get("progress") or 0) >= 1:
            qbt.start([infohash])
            _note(cfg, job_id, "verified 100 %, seeding")
            return True
        # Never start a torrent that would download into the library.
        raise MatchError(f"recheck found {t.get('progress', 0) * 100:.1f} %, left stopped")
    raise MatchError("recheck did not finish in time, left stopped")


def _rename(cfg, qbt, p, job_id):
    local, new = p["local"], p["name"]
    users = _torrents_on(qbt, cfg, local)
    if not users:
        raise MatchError("no torrent uses this file any more")
    _note(cfg, job_id, f"renaming through qBittorrent ({len(users)} torrent(s))")
    for n, (h, _, name) in enumerate(users):
        # The first rename moves the file on disk; for the others the old name no
        # longer exists, so only the name in the torrent changes.
        qbt.rename_file(h, name, os.path.join(os.path.dirname(name), new) if "/" in name else new)
        if n == 0:
            for _ in range(30):
                if os.path.exists(os.path.join(os.path.dirname(local), new)):
                    break
                time.sleep(1)
            else:
                raise MatchError("qBittorrent did not rename the file")
