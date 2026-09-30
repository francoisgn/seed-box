"""What qBittorrent is busy with right now: rechecks, moves, errors, disk I/O queue.

Read-only snapshot for `seedbox status` and the dashboard's live panel. Deletions
have no queue in libtorrent: they only show up afterwards, in the log.
"""

import json
import os
import re
from datetime import UTC, datetime

from seedbox import __version__, actions

# States that mean work (or trouble) rather than plain seeding/downloading.
BUSY = (
    "moving",
    "checkingDL",
    "checkingUP",
    "checkingResumeData",
    "error",
    "missingFiles",
    "metaDL",
    "forcedMetaDL",
    "queuedDL",
    "queuedUP",
)
CHECKING = ("checkingDL", "checkingUP", "checkingResumeData")
# Log lines worth showing: file moves, removals, rechecks, errors.
EVENT = re.compile(r"\b(?:move|moving|moved|deleted|removed|recheck|error|failed)\b", re.I)
# Log types (bit flags): 1 normal, 2 info, 4 warning, 8 critical.
LEVEL = {1: "info", 2: "info", 4: "warn", 8: "ko"}
# Preferences that decide how much runs at once (queueing, disk).
SETTINGS = (
    "queueing_enabled",
    "max_active_downloads",
    "max_active_uploads",
    "max_active_torrents",
    "dont_count_slow_torrents",
    "max_active_checking_torrents",
    "max_connec",
    "max_uploads",
    "hashing_threads",
    "async_io_threads",
    "disk_io_type",
    "up_limit",
    "dl_limit",
)


# Dashboard state kept by seedbox (qBittorrent's log itself cannot be cleared).
STATE_FILE = "ui-state.json"


def _state_path(cfg):
    return os.path.join(cfg.output_dir, STATE_FILE)


def errors_cleared(cfg):
    """Time the errors list was last cleared from the dashboard (epoch), or None."""
    try:
        with open(_state_path(cfg), encoding="utf-8") as handle:
            return float(json.load(handle).get("errors_cleared"))
    except (OSError, ValueError, TypeError):
        return None


def clear_errors(cfg, now=None):
    """Hide the warnings and errors logged so far; returns the time as ISO."""
    now = now or datetime.now(UTC).timestamp()
    try:
        with open(_state_path(cfg), encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    state["errors_cleared"] = now
    os.makedirs(cfg.output_dir, exist_ok=True)
    tmp = _state_path(cfg) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, _state_path(cfg))
    return _iso(now)


def _iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


MOVE_LOG = re.compile(
    r"^(?P<what>Enqueued torrent move|Start moving torrent|Moved torrent successfully|Failed to move torrent)"
    r'\. Torrent: "(?P<name>[^"]*)"'
)
REMOVE_LOG = re.compile(r'^Torrent (?P<what>removed|content removed)\. Torrent: "(?P<name>[^"]*)"')


def log_moves(log):
    """Moves seen in the log (any origin: seedbox, WebUI, *arr), latest state per torrent."""
    moves = {}
    for m in log:
        found = MOVE_LOG.match(m.get("message", ""))
        if not found:
            continue
        what = found.group("what")
        state = {"Enqueued torrent move": "pending", "Start moving torrent": "running"}.get(
            what, "done" if what.startswith("Moved") else "failed"
        )
        moves[found.group("name")] = {"name": found.group("name"), "status": state, "time": _iso(m["timestamp"])}
    return list(moves.values())


def log_removals(log, limit=50):
    out = []
    for m in log:
        found = REMOVE_LOG.match(m.get("message", ""))
        if found:
            out.append(
                {
                    "name": found.group("name"),
                    "files": found.group("what") == "content removed",
                    "time": _iso(m.get("timestamp", 0)),
                }
            )
    return out[-limit:]


def gather(client, events=30, cfg=None, errors_kept=30):
    """Snapshot from a QbtClient: states, busy torrents, disk queue, recent events, jobs."""
    torrents = client.torrents()
    server = (client.maindata() or {}).get("server_state", {})
    prefs = client.preferences() or {}
    log = client.log() or []

    states = {}
    for t in torrents:
        states[t.get("state", "unknown")] = states.get(t.get("state", "unknown"), 0) + 1
    busy = sorted(
        (
            {
                "state": t.get("state", ""),
                "name": t.get("name", ""),
                "category": t.get("category", ""),
                "size": t.get("size", 0),
                "progress": t.get("progress", 0),
                "added_on": t.get("added_on", 0),
            }
            for t in torrents
            if t.get("state") in BUSY
        ),
        key=lambda b: (BUSY.index(b["state"]), -b["progress"], b["added_on"]),
    )
    checking = [b for b in busy if b["state"] in CHECKING]
    # The disk queue counts block reads/writes, not torrents: list who issues them.
    io_sources = sorted(
        (
            {
                "name": t.get("name", ""),
                "state": t.get("state", ""),
                "why": "move"
                if t.get("state") == "moving"
                else "recheck"
                if t.get("state") in CHECKING
                else "download"
                if t.get("dlspeed", 0)
                else "upload",
                "up": t.get("upspeed", 0),
                "dl": t.get("dlspeed", 0),
                "size": t.get("size", 0),
                "progress": t.get("progress", 0),
            }
            for t in torrents
            if t.get("state") == "moving"
            # A check waiting its turn reads nothing yet.
            or (t.get("state") in CHECKING and t.get("progress", 0) > 0)
            or t.get("upspeed", 0)
            or t.get("dlspeed", 0)
        ),
        key=lambda s: (("move", "recheck", "download", "upload").index(s["why"]), -(s["up"] + s["dl"])),
    )
    recent = [
        {
            "time": _iso(m.get("timestamp", 0)),
            "level": LEVEL.get(m.get("type"), "info"),
            "message": m.get("message", ""),
        }
        for m in log
        # Match on the message, not on the torrent name ("Movie" is not a move).
        if EVENT.search(m.setdefault("message", "").split(". Torrent:")[0]) or (m.get("type") or 1) >= 4
    ][-events:]
    # Warnings and errors on their own: a burst of moves must not push them out.
    # Those logged before the last "clear" from the dashboard are hidden.
    cleared = errors_cleared(cfg) if cfg else None
    errors = [
        {"time": _iso(m.get("timestamp", 0)), "level": LEVEL.get(m.get("type"), "warn"), "message": m["message"]}
        for m in log
        if (m.get("type") or 1) >= 4 and (cleared is None or m.get("timestamp", 0) > cleared)
    ][-errors_kept:]
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "version": client.version(),
        "seedbox": __version__,
        "torrents": len(torrents),
        "states": dict(sorted(states.items(), key=lambda kv: -kv[1])),
        "busy": busy,
        "checking": {
            "count": len(checking),
            # Progress of a checking torrent is the check's own progress: size * rest = left to read.
            "bytes": sum(b["size"] * (1 - b["progress"]) for b in checking),
            # One check runs at a time (max_active_checking_torrents); the others wait at 0 %.
            "running": sum(1 for b in checking if b["progress"] > 0),
        },
        "io": {
            "queued_io_jobs": server.get("queued_io_jobs", 0),
            "average_time_queue_ms": server.get("average_time_queue", 0),
            "up_speed": server.get("up_info_speed", 0),
            "dl_speed": server.get("dl_info_speed", 0),
            "peers": server.get("total_peer_connections", 0),
        },
        "settings": {k: prefs[k] for k in SETTINGS if k in prefs},
        "io_sources": io_sources,
        "events": recent,
        "errors": errors,
        "errors_cleared": _iso(cleared) if cleared else None,
        "moves": log_moves(log),
        "removals": log_removals(log),
        "jobs": actions.refresh(cfg, torrents) if cfg else [],
        "created_max": cfg.created_max if cfg else 0,
    }


def _size(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024
    return ""


def show(st, ui):
    """Terminal rendering of a gather() snapshot."""
    states = ", ".join(f"{n} {s}" for s, n in st["states"].items())
    ui.info(f"qBittorrent {st['version']}: {st['torrents']} torrents ({states})")

    io = st["io"]
    level = ui.ko if io["average_time_queue_ms"] >= 1000 else ui.warn if io["queued_io_jobs"] else ui.ok
    level(
        f"qBittorrent disk I/O: {io['queued_io_jobs']} block requests waiting (not torrents), "
        f"{io['average_time_queue_ms']} ms average wait | "
        f"up {_size(io['up_speed'])}/s, down {_size(io['dl_speed'])}/s, {io['peers']} peers"
    )
    chk = st["checking"]
    if chk["count"]:
        ui.warn(
            f"rechecks: {chk['count']} torrent(s), {chk['running']} running, {chk['count'] - chk['running']} waiting, "
            f"about {_size(chk['bytes'])} left to read"
        )

    moving = [m for m in st.get("moves", []) if m["status"] in ("pending", "running")]
    if moving:
        ui.warn(f"moves: {len(moving)} pending or running (log)")
    open_jobs = [j for j in st.get("jobs", []) if j["status"] in ("pending", "running")]
    if open_jobs:
        counts = actions.summary(open_jobs)
        ui.warn("seedbox jobs: " + ", ".join(f"{n} {a}" for a, n in counts.items()))

    if st["busy"]:
        print()
        for b in st["busy"]:
            level = ui.ko if b["state"] in ("error", "missingFiles") else ui.warn
            cat = f" [{b['category']}]" if b["category"] else ""
            level(f"{b['state']:<18} {b['progress'] * 100:5.1f}% {_size(b['size']):>10}  {b['name']}{cat}")
    else:
        ui.ok("nothing moving, checking or in error")

    if st.get("io_sources"):
        print()
        ui.info("disk I/O comes from:")
        for s in st["io_sources"]:
            ui.info(f"{s['why']:<9} up {_size(s['up'])}/s, down {_size(s['dl'])}/s  {s['name']}")

    if st.get("errors"):
        print()
        ui.info("latest qBittorrent warnings and errors (log):")
        for e in st["errors"]:
            stamp = datetime.fromisoformat(e["time"]).astimezone().strftime("%m-%d %H:%M")
            getattr(ui, e["level"])(f"{stamp}  {e['message']}")

    if st["events"]:
        print()
        ui.info("recent moves, removals and errors (log):")
        for e in st["events"]:
            stamp = datetime.fromisoformat(e["time"]).astimezone().strftime("%m-%d %H:%M")
            getattr(ui, e["level"])(f"{stamp}  {e['message']}")

    print()
    ui.info("limits: " + ", ".join(f"{k}={v}" for k, v in st["settings"].items()))
