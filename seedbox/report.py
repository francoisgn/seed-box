"""Write the snapshot: JSON, CSV inventory, CSV history, HTML dashboard."""

import csv
import io
import json
import os
from datetime import datetime

from seedbox import dashboard

GIB = 1024**3
TIB = 1024**4

HISTORY_HEADER = ["date", "entries", "seeded", "incomplete", "orphan", "coverage_pct", "size_tib", "uploaded_tib"]
TRACKER_HISTORY_HEADER = ["date", "tracker", "entries", "coverage_pct", "uploaded_tib"]
RATIO_HISTORY_HEADER = ["date", "key", "uploaded", "downloaded", "torrents"]
DAY = 86400


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _csv(rows, delimiter):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerows(rows)
    return buffer.getvalue()


def _append(path, header, rows, delimiter):
    exists = os.path.isfile(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        if not exists:
            writer.writerow(header)
        writer.writerows(rows)


def read_history(path, delimiter):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def upload_since(history, key, up_now, now, days):
    """(bytes uploaded over the last `days`, start of that window as ISO).

    From the per-collection ratio history: the latest row at least `days` old,
    else the oldest one (the window is then shorter, its start says so). A
    torrent removed takes its counter with it: never below 0."""
    rows = [(datetime.fromisoformat(r["date"]).timestamp(), r) for r in history if r.get("key") == key]
    if not rows:
        return None, None
    old = [x for x in rows if x[0] <= now - days * DAY]
    ts, base = max(old, key=lambda x: x[0]) if old else min(rows, key=lambda x: x[0])
    return max(up_now - int(base["uploaded"]), 0), datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


def write(cfg, snap):
    out = cfg.output_dir
    os.makedirs(out, exist_ok=True)
    delim = cfg.csv_delimiter
    s = snap["summary"]
    date = snap["generated"]

    _append(
        os.path.join(out, "history.csv"),
        HISTORY_HEADER,
        [
            [
                date,
                s["entries"],
                s["seeded"],
                s["incomplete"],
                s["orphan"],
                f"{s['coverage_pct']:.2f}",
                f"{s['size'] / TIB:.3f}",
                f"{s['uploaded'] / TIB:.3f}",
            ]
        ],
        delim,
    )
    _append(
        os.path.join(out, "history-trackers.csv"),
        TRACKER_HISTORY_HEADER,
        [
            [
                date,
                t["name"],
                t["entries"],
                f"{t['entries'] / max(s['entries'], 1) * 100:.2f}",
                f"{t['uploaded'] / TIB:.3f}",
            ]
            for t in snap["trackers"]
        ],
        delim,
    )

    # Ratios: windows computed from the history before this run is appended.
    ratio_path = os.path.join(out, "history-ratios.csv")
    ratio_history = read_history(ratio_path, delim)
    now = datetime.fromisoformat(date).timestamp()
    for r in snap.get("ratios", []):
        for days in (7, 30):
            r[f"up_{days}d"], r[f"up_{days}d_since"] = upload_since(ratio_history, r["key"], r["up"], now, days)
    _append(
        ratio_path,
        RATIO_HISTORY_HEADER,
        [[date, r["key"], r["up"], r["down"], r["torrents"]] for r in snap.get("ratios", [])],
        delim,
    )

    inventory = [
        [
            "category",
            "name",
            "path",
            "status",
            "coverage",
            "issues",
            "trackers",
            "tracker_count",
            "torrent_count",
            "size_gib",
            "files",
            "uploaded_gib",
        ]
    ]
    for e in snap["entries"]:
        inventory.append(
            [
                e["category"],
                e["name"],
                e["path"],
                e["status"],
                e["coverage"],
                "|".join(i["code"] for i in e["issues"]),
                "|".join(e["trackers"]),
                len(e["trackers"]),
                len(e["torrents"]),
                f"{e['size'] / GIB:.2f}",
                e["files"],
                f"{e['uploaded'] / GIB:.2f}",
            ]
        )
    _atomic_write(os.path.join(out, "inventory.csv"), _csv(inventory, delim))

    _atomic_write(os.path.join(out, "snapshot.json"), json.dumps(snap, ensure_ascii=False, indent=1))

    history = read_history(os.path.join(out, "history.csv"), delim)
    return dashboard.write_pages(out, snap, history, _atomic_write)


def rerender(cfg):
    """Rebuild the dashboard pages from the last snapshot, without collecting: a new
    version's page right after a deploy. Returns False without a snapshot."""
    out = cfg.output_dir
    try:
        with open(os.path.join(out, "snapshot.json"), encoding="utf-8") as handle:
            snap = json.load(handle)
    except (OSError, ValueError):
        return False
    history = read_history(os.path.join(out, "history.csv"), cfg.csv_delimiter)
    dashboard.write_pages(out, snap, history, _atomic_write)
    return True
