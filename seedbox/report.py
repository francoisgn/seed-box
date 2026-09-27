"""Write the snapshot: JSON, CSV inventory, CSV history, HTML dashboard."""

import csv
import io
import json
import os

from seedbox import dashboard

GIB = 1024**3
TIB = 1024**4

HISTORY_HEADER = ["date", "entries", "seeded", "incomplete", "orphan", "coverage_pct", "size_tib", "uploaded_tib"]
TRACKER_HISTORY_HEADER = ["date", "tracker", "entries", "coverage_pct", "uploaded_tib"]


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

    inventory = [
        [
            "category",
            "name",
            "path",
            "status",
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
                "|".join(e["trackers"]),
                len(e["trackers"]),
                e["torrents"],
                f"{e['size'] / GIB:.2f}",
                e["files"],
                f"{e['uploaded'] / GIB:.2f}",
            ]
        )
    _atomic_write(os.path.join(out, "inventory.csv"), _csv(inventory, delim))

    _atomic_write(os.path.join(out, "snapshot.json"), json.dumps(snap, ensure_ascii=False, indent=1))

    history = read_history(os.path.join(out, "history.csv"), delim)
    _atomic_write(os.path.join(out, "index.html"), dashboard.render(snap, history))
    return os.path.join(out, "index.html")
