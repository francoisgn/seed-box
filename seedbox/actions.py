"""Dashboard write actions, executed by qBittorrent, and the jobs they create.

Every action goes through qBittorrent (seedbox mounts the media read-only):
move (setLocation), recheck, start, skip missing extras (file priority 0),
strip the trackers Prowlarr does not know from a torrent that has one it
knows, remove a torrent. Requests are validated here, whatever the page sent:

- move: the destination must be an existing folder under a library root,
  outside the cross-seed link folders;
- strip trackers: the declared trackers are read from Prowlarr at that time,
  and a torrent must keep at least one;
- remove with files: only for cross-seed link torrents whose content no other
  torrent uses, so a library file is never deleted from the dashboard.

One action writes on disk itself: remove_orphans deletes leftover files in the
cross-seed link folders (the only part of the media mounted read-write). The
list is recomputed from qBittorrent at that time and only paths of the request
that are still orphans go; then the empty folders below each link folder.

Each torrent touched gets a job in <output>/jobs.json. Its status is derived
from qBittorrent's live state when read (a move is done when the save path is
the target, a removal when the torrent is gone), so the list shows what is
pending, running, done. The list keeps every open job and the most recent
finished ones, `KEEP_JOBS` in all (more only while more are open).
"""

import contextlib
import json
import os
import re
import threading
import time
import uuid

from seedbox import prowlarr
from seedbox import trackers as trk
from seedbox.api import ApiError
from seedbox.config import map_path, unmap_path

ACTIONS = ("move", "recheck", "start", "skip_extras", "strip_trackers", "remove", "set_category", "apply_category")
MAX_PATHS = 5000
HASH = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
MAX_HASHES = 500
KEEP_DONE_S = 7 * 86400
KEEP_JOBS = 60
FINISHED = ("done", "failed", "cancelled")
# Jobs run by a background worker (release matching, torrent creation), which writes their status.
WORKER_ACTIONS = ("inject", "rename", "create", "seed", "upload")
WORKER_MAX_S = 13 * 3600
CHECKING = ("checkingDL", "checkingUP", "checkingResumeData")
STOPPED = ("stoppedDL", "stoppedUP", "pausedDL", "pausedUP")

_lock = threading.Lock()


class ActionError(Exception):
    pass


def _jobs_path(cfg):
    return os.path.join(cfg.output_dir, "jobs.json")


def load_jobs(cfg):
    try:
        with open(_jobs_path(cfg), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def _save_jobs(cfg, jobs):
    os.makedirs(cfg.output_dir, exist_ok=True)
    tmp = _jobs_path(cfg) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(jobs, handle, ensure_ascii=False)
    os.replace(tmp, _jobs_path(cfg))


def _in_link_dir(cfg, path):
    parts = path.split("/")
    return any(d in parts for d in cfg.link_dirs)


def _check_destination(cfg, local):
    local = os.path.normpath(local)
    # Resolved: neither ".." nor a symlink inside a root can lead outside it.
    real = os.path.realpath(local)
    if not any(_under(real, os.path.realpath(r)) for r in cfg.roots):
        raise ActionError("destination must be inside a library root")
    if _in_link_dir(cfg, real):
        raise ActionError("destination is a cross-seed link folder")
    # qBittorrent creates a missing destination; its parent must exist.
    if not os.path.isdir(real) and not os.path.isdir(os.path.dirname(real)):
        raise ActionError(f"destination folder and its parent do not exist: {local}")
    return unmap_path(cfg, local)


def _under(path, root):
    return os.path.commonpath([path, root]) == root


def _is_link(cfg, torrent):
    return torrent.get("category") == cfg.link_category or _in_link_dir(
        cfg, map_path(cfg, torrent.get("content_path") or "")
    )


def _is_transient(cfg, torrent):
    if not cfg.transient_dir:
        return False
    local = map_path(cfg, torrent.get("content_path") or torrent.get("save_path") or "")
    return local == cfg.transient_dir or local.startswith(cfg.transient_dir + "/")


def _declared(cfg):
    """Tracker keys Prowlarr knows."""
    if not cfg.prowlarr_enabled:
        raise ActionError("no Prowlarr configured: the declared trackers are unknown")
    found = prowlarr.indexers(prowlarr.ProwlarrClient(cfg.prowlarr_url, cfg.prowlarr_api_key), cfg.tracker_aliases)
    if not found:
        raise ActionError("Prowlarr lists no indexer: the declared trackers are unknown")
    return set(found)


def _undeclared_urls(cfg, client, torrent, declared):
    """Announce URLs of a torrent that no declared tracker owns."""
    urls, kept = [], 0
    for item in client.trackers(torrent["hash"]):
        key = trk.key_for_url(item.get("url", ""), cfg.tracker_aliases)
        if not key:
            continue
        if key in declared:
            kept += 1
        else:
            urls.append(item["url"])
    if urls and not kept:
        raise ActionError(f"{torrent['name']}: no declared tracker, remove the torrent instead")
    return urls


def run(cfg, client, request, declared=None):
    """Validate and execute one action request. Returns the jobs created.
    declared: tracker keys Prowlarr knows (read from it when needed and None)."""
    if not cfg.actions:
        raise ActionError("actions are disabled ([service] actions = true to enable)")
    action = request.get("action")
    if action == "remove_orphans":
        return _remove_orphans(cfg, client, request)
    if action not in ACTIONS:
        raise ActionError(f"unknown action: {action!r}")
    hashes = request.get("hashes") or []
    if not isinstance(hashes, list) or not hashes or len(hashes) > MAX_HASHES:
        raise ActionError(f"hashes: a list of 1 to {MAX_HASHES} torrent hashes")
    hashes = [str(h).lower() for h in hashes]
    if not all(HASH.match(h) for h in hashes):
        raise ActionError("invalid torrent hash")

    live = {t["hash"]: t for t in client.torrents()}
    missing = [h for h in hashes if h not in live]
    if missing:
        raise ActionError(f"{len(missing)} torrent(s) no longer in qBittorrent, refresh the page")

    target, stripped = "", {}
    if action == "move":
        target = _check_destination(cfg, str(request.get("location") or ""))
        client.set_location(hashes, target)
    elif action == "recheck":
        client.recheck(hashes)
    elif action == "start":
        client.start(hashes)
    elif action == "skip_extras":
        for h in hashes:
            ids = [
                i
                for i, f in enumerate(client.files(h) or [])
                if (f.get("progress") or 0) < 1
                and (f.get("priority", 1) or 0) > 0
                and os.path.splitext(f.get("name", "").removesuffix(".!qB"))[1].lower() not in cfg.media_ext
            ]
            if ids:
                client.file_priority(h, ids, 0)
    elif action == "strip_trackers":
        declared = _declared(cfg) if declared is None else declared
        plan = {h: _undeclared_urls(cfg, client, live[h], declared) for h in hashes}
        for h, urls in plan.items():
            if urls:
                client.remove_trackers(h, urls)
        stripped = {h: len(urls) for h, urls in plan.items()}
    elif action in ("set_category", "apply_category"):
        category = str(request.get("category") or "")
        if category and category not in client.categories():
            raise ActionError(f"unknown qBittorrent category: {category}")
        if action == "set_category" and not category:
            raise ActionError("set_category needs a category")
        if category:
            client.set_category(hashes, category)
        if action == "apply_category":
            # Auto management on: qBittorrent moves each torrent to its category folder.
            client.auto_management(hashes, True)
        target = category
    elif action == "remove":
        delete_files = bool(request.get("delete_files"))
        if delete_files:
            for h in hashes:
                torrent = live[h]
                if not (_is_link(cfg, torrent) or _is_transient(cfg, torrent)):
                    raise ActionError(
                        f"{torrent['name']}: files are deleted only for cross-seed links and transient downloads"
                    )
                shared = [
                    o["name"]
                    for oh, o in live.items()
                    if oh not in hashes and o.get("content_path") == torrent.get("content_path")
                ]
                if shared:
                    raise ActionError(f"{torrent['name']}: its files are used by another torrent ({shared[0]})")
        client.delete(hashes, delete_files)
        target = "with files" if delete_files else ""

    now = time.time()
    created = [
        {
            "id": uuid.uuid4().hex[:12],
            "action": action,
            "hash": h,
            "name": live[h].get("name", ""),
            "from": live[h].get("save_path", ""),
            "target": f"{stripped[h]} tracker(s)" if action == "strip_trackers" else target,
            "submitted": now,
            "status": "done" if action in ("skip_extras", "strip_trackers") else "pending",
        }
        for h in hashes
    ]
    with _lock:
        jobs = load_jobs(cfg) + created
        _save_jobs(cfg, jobs[-1000:])
    return created


def _remove_orphans(cfg, client, request):
    """Delete the requested link files that no torrent uses now, then the empty folders."""
    from seedbox import collect  # collect imports qBittorrent and Prowlarr clients: load it only here

    paths = request.get("paths") or []
    if not isinstance(paths, list) or not paths or len(paths) > MAX_PATHS:
        raise ActionError(f"paths: a list of 1 to {MAX_PATHS} link file paths")
    folders = collect.link_folders(cfg)
    if not folders:
        raise ActionError("no cross-seed link folder found")
    if not all(os.access(f, os.W_OK) for f in folders):
        raise ActionError("the cross-seed link folders are mounted read-only (see docs/deployment.md)")
    # Fresh list: a torrent added since the snapshot keeps its files.
    now_orphans = {f["path"] for f in collect.orphan_links(cfg, client.torrents(), limit=None)["files"]}
    wanted = {str(p) for p in paths} & now_orphans
    removed, freed = 0, 0
    for rel in sorted(wanted):
        for folder in folders:
            path = os.path.normpath(os.path.join(os.path.dirname(folder), rel))
            if not _under(os.path.realpath(os.path.dirname(path)), os.path.realpath(folder)):
                continue
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            os.remove(path)
            removed += 1
            freed += st.st_size if st.st_nlink == 1 else 0
            break
    for folder in folders:
        # Like the script's find -mindepth 2: the per-tracker folders stay.
        for root, _dirs, _files in os.walk(folder, topdown=False):
            if os.path.dirname(root) != folder and root != folder and not os.listdir(root):
                with contextlib.suppress(OSError):
                    os.rmdir(root)
    job = {
        "id": uuid.uuid4().hex[:12],
        "action": "remove_orphans",
        "hash": "",
        "name": f"{removed} orphan link file(s)",
        "from": "",
        "target": f"{removed} of {len(paths)} removed",
        "submitted": time.time(),
        "status": "done",
        "freed": freed,
    }
    job["finished"] = job["submitted"]
    with _lock:
        _save_jobs(cfg, (load_jobs(cfg) + [job])[-1000:])
    return [job]


def add_job(cfg, fields):
    """Record a job run by another module (release matching). Returns it."""
    job = {"id": uuid.uuid4().hex[:12], "submitted": time.time(), "status": "pending", **fields}
    with _lock:
        _save_jobs(cfg, (load_jobs(cfg) + [job])[-1000:])
    return job


def update_job(cfg, job_id, **fields):
    with _lock:
        jobs = load_jobs(cfg)
        for job in jobs:
            if job["id"] == job_id:
                job.update(fields)
        _save_jobs(cfg, jobs)


def refresh(cfg, torrents):
    """Jobs with their status from the live torrent list; prunes old finished jobs."""
    live = {t["hash"]: t for t in torrents}
    now = time.time()
    with _lock:
        jobs = load_jobs(cfg)
        changed = False
        for job in jobs:
            if job["status"] not in FINISHED:
                status = _status(job, live.get(job["hash"]), now)
                if status != job["status"]:
                    job["status"], changed = status, True
                    if status in FINISHED:
                        job["finished"] = now
        kept = _prune(jobs, now)
        if changed or len(kept) != len(jobs):
            _save_jobs(cfg, kept)
    return kept


def _prune(jobs, now):
    """Open jobs, stored created files (their Seed button), then the newest finished jobs."""
    room = KEEP_JOBS - sum(1 for j in jobs if j["status"] not in FINISHED or j.get("stored"))
    keep = set()
    for i in range(len(jobs) - 1, -1, -1):
        job = jobs[i]
        if job["status"] not in FINISHED or job.get("stored"):
            keep.add(i)
        elif room > 0 and now - job.get("finished", job["submitted"]) <= KEEP_DONE_S:
            keep.add(i)
            room -= 1
    return [j for i, j in enumerate(jobs) if i in keep]


def interrupted(cfg):
    """At startup: a worker job still open was stopped by the restart."""
    now = time.time()
    with _lock:
        jobs = load_jobs(cfg)
        stopped = [j for j in jobs if j["action"] in WORKER_ACTIONS and j["status"] not in FINISHED]
        for job in stopped:
            job.update(status="failed", note="interrupted by a seedbox restart", finished=now)
        if stopped:
            _save_jobs(cfg, jobs)
    return len(stopped)


def _status(job, torrent, now):
    age = now - job["submitted"]
    action = job["action"]
    if action in WORKER_ACTIONS:
        # Status kept by the worker; one still running after a restart was interrupted.
        return job["status"] if age < WORKER_MAX_S else "failed"
    if action == "remove":
        return "done" if torrent is None else ("pending" if age < 3600 else "failed")
    if torrent is None:
        return "failed"
    state = torrent.get("state", "")
    if action == "move":
        if torrent.get("save_path", "").rstrip("/") == job["target"].rstrip("/"):
            return "done"
        return "running" if state == "moving" else "pending"
    if action == "recheck":
        if state in CHECKING:
            return "running"
        return "done" if age > 20 else "pending"
    if action == "apply_category":
        if state == "moving":
            return "running"
        return "done" if torrent.get("auto_tmm") and age > 20 else "pending"
    if action == "start":
        return "pending" if state in STOPPED and age < 600 else ("failed" if state in STOPPED else "done")
    return "done"


def summary(jobs):
    counts = {}
    for job in jobs:
        if job["status"] in ("pending", "running"):
            counts[job["action"]] = counts.get(job["action"], 0) + 1
    return counts


def handle(cfg, client_factory, body):
    """HTTP helper: (status code, response dict)."""
    try:
        request = json.loads(body or b"{}")
        if not isinstance(request, dict):
            raise ValueError
    except ValueError:
        return 400, {"error": "invalid JSON body"}
    try:
        created = run(cfg, client_factory(), request)
    except ActionError as exc:
        return 400, {"error": str(exc)}
    except ApiError as exc:
        return 502, {"error": str(exc)}
    return 200, {"jobs": created}
