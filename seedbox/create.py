"""Create a .torrent from a library entry, for a tracker it is missing on.

Uploading a release to a tracker only sends its .torrent (a few KB): the data
leaves by seeding. Creating it here hashes the files where they are, on the
server, instead of reading them over the network from another machine.

- announce URL: taken from a torrent of that tracker already in qBittorrent
  (the passkey is already there), never typed into seedbox;
- source field: copied from that torrent's .torrent when it has one (some
  trackers require it, and it is part of the infohash);
- private flag set, piece size from the total size;
- content: a film file on its own (its sidecars stay out), or a folder (a
  season, a film with its extras) with every file the entry counts;
- a .nfo beside it (not in the torrent): the fields the tracker's form asks
  for, from the name and MediaInfo, checked and completed in the dashboard.

Hashing reads every byte once, sequentially, one job at a time: the disks are
shared with qBittorrent. The page downloads the .torrent as soon as it is
ready. A copy stays in <output>/created/ for the "seed" button, at most
`created_max` of them (the oldest is replaced, or delete them by hand), served
only through the API with actions enabled: it holds the passkey.

After the upload, "seed" adds the created torrent to qBittorrent with the hash
check skipped (its files were just hashed). If the tracker hands back another
.torrent (a dupe, or a rewritten one), add that one by hand instead.
"""

import contextlib
import glob
import hashlib
import json
import os
import threading
import time
from collections import Counter

from seedbox import __version__, actions, library, match, nfo, trackers
from seedbox import torrentfile as tf
from seedbox.api import ApiError
from seedbox.config import unmap_path

MIN_PIECE = 256 * 1024
MAX_PIECE = 16 * 1024 * 1024
TARGET_PIECES = 1500
PROGRESS_S = 20
SAMPLE_TORRENTS = 3

_one_at_a_time = threading.Lock()


class CreateError(Exception):
    pass


def piece_length(total):
    """Power of two from 256 KiB to 16 MiB, for about TARGET_PIECES pieces."""
    pl = MIN_PIECE
    while pl < MAX_PIECE and total > pl * TARGET_PIECES:
        pl *= 2
    return pl


def content(cfg, entry):
    """{'name', 'save', 'files': [(path on disk, [path in the torrent], length)], 'total'}.

    save: the folder qBittorrent seeds from (the torrent's name sits in it)."""
    path = entry["path"]
    if not os.path.exists(path):
        raise CreateError("this entry moved or was renamed since the last collection: collect again")
    real = os.path.realpath(path)
    if not any(actions._under(real, os.path.realpath(r)) for r in cfg.roots):
        raise CreateError("entry outside the library roots")
    name = os.path.basename(path)
    if entry.get("kind") == "file":
        with_sidecars = sum(os.path.getsize(f) for f in library._sidecars(path))
        if (entry.get("size") or 0) > with_sidecars:
            # CD1/CD2 parts side by side with other films: no folder to make a torrent of.
            raise CreateError("a film in several parts: put them in a folder of their own first")
        files = [(path, [name], os.path.getsize(path))]
    else:
        found = sorted(library._dir_files(cfg, path))
        if not found:
            raise CreateError("no file in this entry")
        files = [(f, os.path.relpath(f, path).split(os.sep), os.path.getsize(f)) for f in found]
    return {"name": name, "save": os.path.dirname(path), "files": files, "total": sum(f[2] for f in files)}


def tracker_setup(cfg, qbt, snapshot, key):
    """(announce URL, source) from the torrents of that tracker in qBittorrent."""
    hashes = [t["hash"] for t in snapshot.get("torrents", []) if t.get("tracker") == key]
    if not hashes:
        raise CreateError(f"no torrent of {key} in qBittorrent: its announce URL is unknown")
    announce, sources = "", Counter()
    for h in hashes[:SAMPLE_TORRENTS]:
        try:
            if not announce:
                announce = next(
                    (
                        t["url"]
                        for t in qbt.trackers(h)
                        if trackers.key_for_url(t.get("url"), cfg.tracker_aliases) == key
                    ),
                    "",
                )
            sources[tf.parse(qbt.export(h))["source"]] += 1
        except (ApiError, tf.TorrentError):
            continue
    if not announce:
        raise CreateError(f"no announce URL of {key} found in qBittorrent")
    return announce, sources.most_common(1)[0][0] if sources else ""


def build(spec, announce, source, progress=lambda done: None):
    """The .torrent bytes; progress(bytes hashed) is called along the way."""
    pl = piece_length(spec["total"])
    pieces, buf, done, last = [], bytearray(), 0, time.monotonic()
    for path, _, length in spec["files"]:
        read = 0
        with open(path, "rb") as handle:
            while chunk := handle.read(pl - len(buf)):
                buf += chunk
                read += len(chunk)
                if len(buf) == pl:
                    pieces.append(hashlib.sha1(buf).digest())
                    buf.clear()
                if time.monotonic() - last > PROGRESS_S:
                    progress(done + read)
                    last = time.monotonic()
        if read != length:
            raise CreateError(f"{os.path.basename(path)} changed while being hashed")
        done += read
    if buf:
        pieces.append(hashlib.sha1(buf).digest())
    info = {"name": spec["name"], "piece length": pl, "pieces": b"".join(pieces), "private": 1}
    if len(spec["files"]) == 1 and len(spec["files"][0][1]) == 1:
        info["length"] = spec["total"]
    else:
        info["files"] = [{"length": length, "path": parts} for _, parts, length in spec["files"]]
    if source:
        info["source"] = source
    meta = {
        "announce": announce,
        "created by": f"seedbox {__version__}",
        "creation date": int(time.time()),
        "info": info,
    }
    return tf.encode(meta)


# ---------- jobs
def _dir(cfg):
    return os.path.join(cfg.output_dir, "created")


def path_for(cfg, job, ext="torrent"):
    return os.path.join(_dir(cfg), f"{job['id']}.{ext}")


def _make_room(cfg):
    """Delete the oldest created files so a new one fits in created_max."""
    stored = sorted(glob.glob(os.path.join(_dir(cfg), "*.torrent")), key=os.path.getmtime)
    for old in stored[: max(len(stored) - cfg.created_max + 1, 0)]:
        job_id = os.path.basename(old).removesuffix(".torrent")
        try:
            os.remove(old)
        except OSError:
            continue
        _remove_nfo(cfg, job_id)
        actions.update_job(cfg, job_id, stored=False, note="replaced by a newer .torrent (limit reached)")


def _remove_nfo(cfg, job_id):
    with contextlib.suppress(OSError):
        os.remove(path_for(cfg, {"id": job_id}, "nfo"))


def describe(cfg, snapshot, index):
    """What the .nfo will say, for the page to check.

    {'name', 'fields', 'missing', 'details', 'labels', 'mandatory'}"""
    entry = match._entry(snapshot, index)
    spec = content(cfg, entry)
    found = nfo.describe(spec["name"], match.main_file(cfg, entry), len(spec["files"]))
    return {
        "name": spec["name"],
        "fields": found["fields"],
        "missing": found["missing"],
        "details": found["details"],
        "labels": nfo.LABELS,
        "mandatory": list(nfo.MANDATORY),
    }


def delete(cfg, job_id):
    """Delete a created file by hand. Returns the job."""
    job = created_job(cfg, job_id)
    os.remove(path_for(cfg, job))
    _remove_nfo(cfg, job_id)
    actions.update_job(cfg, job_id, stored=False, note="deleted")
    return {**job, "stored": False}


def start(cfg, qbt, snapshot, index, key, fields):
    """Validate, write the .nfo text, then hash in the background. Returns the job."""
    if key not in (snapshot.get("summary", {}).get("target_trackers") or []):
        raise CreateError(f"unknown tracker: {key}")
    entry = match._entry(snapshot, index)
    if key in (entry.get("trackers") or []):
        raise CreateError(f"already seeded on {key}")
    spec = content(cfg, entry)
    fields = nfo.clean_fields(fields)
    announce, source = tracker_setup(cfg, qbt, snapshot, key)
    found = nfo.describe(spec["name"], match.main_file(cfg, entry), len(spec["files"]))
    text = nfo.render(spec["name"], fields, found["details"], found["report"], spec["total"])
    job = actions.add_job(
        cfg,
        {
            "action": "create",
            "hash": "",
            "name": spec["name"],
            "from": entry["path"],
            "target": key,
            "status": "running",
            "note": "queued behind another hashing" if _one_at_a_time.locked() else "queued",
            "save": spec["save"],
            "source": source,
        },
    )
    threading.Thread(target=_run, args=(cfg, spec, announce, source, job, text), daemon=True).start()
    return job


def _write(path, data):
    """Owner-only: a .torrent holds the passkey."""
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(path + ".tmp", path)


def _run(cfg, spec, announce, source, job, nfo_text):
    total = spec["total"]

    def progress(done):
        actions.update_job(cfg, job["id"], note=f"hashing {done / total * 100:.0f} % of {total / 1e9:.1f} GB")

    try:
        with _one_at_a_time:
            progress(0)
            data = build(spec, announce, source, progress)
        meta = tf.parse(data)
        os.makedirs(_dir(cfg), exist_ok=True)
        _make_room(cfg)
        _write(path_for(cfg, job, "nfo"), nfo_text.encode())
        _write(path_for(cfg, job), data)
        note = f"ready: {len(meta['pieces'])} pieces of {meta['piece_length'] // 1024} KiB"
        actions.update_job(
            cfg,
            job["id"],
            hash=meta["infohash"],
            note=note + (f", source {source}" if source else ""),
            status="done",
            stored=True,
            finished=time.time(),
        )
    except (OSError, CreateError, tf.TorrentError) as exc:
        actions.update_job(cfg, job["id"], note=str(exc), status="failed", finished=time.time())


def created_job(cfg, job_id):
    job = next((j for j in actions.load_jobs(cfg) if j["id"] == job_id and j["action"] == "create"), None)
    if not job or job["status"] != "done" or not os.path.isfile(path_for(cfg, job)):
        raise CreateError("this .torrent is no longer stored (replaced or deleted), create it again")
    return job


def seed(cfg, qbt, job_id):
    """Add a created torrent to qBittorrent, hash check skipped. Returns the job."""
    job = created_job(cfg, job_id)
    with open(path_for(cfg, job), "rb") as handle:
        data = handle.read()
    meta = tf.parse(data)
    if any(t["hash"] == meta["infohash"] for t in qbt.torrents()):
        raise CreateError("this torrent is already in qBittorrent")
    # The files must still be the ones hashed.
    for f in meta["files"]:
        local = os.path.join(job["save"], f["path"])
        if not os.path.isfile(local) or os.path.getsize(local) != f["length"]:
            raise CreateError(f"{f['path']} moved or changed since the torrent was created")
    folder = unmap_path(cfg, job["save"])
    category = next(
        (n for n, c in qbt.categories().items() if (c.get("savePath") or "").rstrip("/") == folder.rstrip("/")), ""
    )
    qbt.add_torrent(data, folder, category, stopped=False, layout="Original", skip_checking=True)
    return actions.add_job(
        cfg,
        {
            "action": "seed",
            "hash": meta["infohash"],
            "name": meta["name"],
            "from": job["save"],
            "target": job["target"],
            "status": "done",
            "finished": time.time(),
        },
    )


def handle(cfg, qbt_factory, body):
    """HTTP helper for POST /api/create: (status code, response dict).

    {"op": "describe", "entry": i} | {"op": "create", "entry": i, "tracker": key, "fields": {…}}
    | {"op": "seed" | "delete", "job": id}"""
    try:
        request = json.loads(body or b"{}")
        if not isinstance(request, dict):
            raise ValueError
    except ValueError:
        return 400, {"error": "invalid JSON body"}
    if not cfg.actions:
        return 403, {"error": "actions are disabled ([service] actions = true to enable)"}
    op = request.get("op")
    try:
        if op == "describe":
            return 200, describe(cfg, match.load_snapshot(cfg), request.get("entry"))
        if op == "create":
            snapshot = match.load_snapshot(cfg)
            job = start(
                cfg, qbt_factory(), snapshot, request.get("entry"), str(request.get("tracker")), request.get("fields")
            )
            return 200, {"job": job}
        if op == "seed":
            return 200, {"job": seed(cfg, qbt_factory(), str(request.get("job") or ""))}
        if op == "delete":
            return 200, {"job": delete(cfg, str(request.get("job") or ""))}
    except (CreateError, match.MatchError, tf.TorrentError, nfo.NfoError) as exc:
        return 400, {"error": str(exc)}
    except ApiError as exc:
        return 502, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": f"file access: {exc}"}
    return 400, {"error": f"unknown op: {op!r}"}
