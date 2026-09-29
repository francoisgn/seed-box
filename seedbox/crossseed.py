"""cross-seed database (read-only): what was searched where, and what was found.

For content not seeded on a tracker, it tells an upload opportunity (searched
there, nothing matching) from content cross-seed has not searched yet. The
database is copied before reading (with its WAL), so the live one is never
locked; decision URLs carry tracker API keys and are reduced to their domain
right away, never kept.
"""

import os
import shutil
import sqlite3
import tempfile

from seedbox import trackers as trk

FOUND = ("MATCH", "MATCH_PARTIAL", "MATCH_SIZE_ONLY", "INFO_HASH_ALREADY_EXISTS", "SAME_INFO_HASH")
MISMATCH = (
    "SIZE_MISMATCH",
    "PARTIAL_SIZE_MISMATCH",
    "RESOLUTION_MISMATCH",
    "SOURCE_MISMATCH",
    "RELEASE_GROUP_MISMATCH",
)


def read(path, aliases, indexer_keys):
    """{'indexers': [...], 'searchees': {name: {tracker key: {'searched': ms, 'decision': str}}}} or None.

    indexer_keys maps a Prowlarr indexer name to its tracker key (from Prowlarr).
    """
    if not path or not os.path.isfile(path):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal", "-shm"):
            if os.path.isfile(path + suffix):
                shutil.copy(path + suffix, os.path.join(tmp, "db" + suffix))
        db = sqlite3.connect(os.path.join(tmp, "db"))
        try:
            return _read(db, aliases, indexer_keys)
        except sqlite3.Error:
            return None
        finally:
            db.close()


def _read(db, aliases, indexer_keys):
    # cross-seed names its indexers as Prowlarr does: map them to tracker keys.
    indexers = {}
    try:
        rows = db.execute("SELECT id, name, active, status, retry_after FROM indexer").fetchall()
    except sqlite3.Error:  # older schema, no retry_after
        rows = [r + (None,) for r in db.execute("SELECT id, name, active, status FROM indexer")]
    for ident, name, active, status, retry_after in rows:
        name = name or ""
        indexers[ident] = {
            "name": name,
            "active": bool(active),
            "status": status or "",
            # Epoch ms: cross-seed keeps the status until it queries the indexer again,
            # so a limit whose retry time is past is over, whatever the status says.
            "retry_after": retry_after or 0,
            "key": indexer_keys.get(name.lower()) or aliases.get(name.lower()),
        }

    searchees = {}
    rows = db.execute(
        "SELECT s.name, t.indexer_id, t.last_searched FROM timestamp t JOIN searchee s ON s.id = t.searchee_id"
    )
    for name, indexer_id, last in rows:
        key = indexers.get(indexer_id, {}).get("key")
        if not key:
            continue
        slot = searchees.setdefault(name, {}).setdefault(key, {"searched": 0, "decision": ""})
        slot["searched"] = max(slot["searched"], last or 0)
    for name, guid, decision in db.execute(
        "SELECT s.name, d.guid, d.decision FROM decision d JOIN searchee s ON s.id = d.searchee_id"
    ):
        key = trk.key_for_url(guid or "", aliases)
        if not key:
            continue
        slot = searchees.setdefault(name, {}).setdefault(key, {"searched": 0, "decision": ""})
        # A match wins over a mismatch from another upload of the same content.
        if slot["decision"] not in FOUND:
            slot["decision"] = decision
    # Data-based searchees: library path (as qBittorrent and cross-seed see it) -> searchee name.
    titles = {}
    try:
        for path, title in db.execute("SELECT path, title FROM data"):
            titles[path] = title
    except sqlite3.Error:
        pass
    return {
        "titles": titles,
        "indexers": [
            {
                "name": i["name"],
                "key": i["key"],
                "active": i["active"],
                "status": i["status"],
                "retry_after": i["retry_after"],
            }
            for i in indexers.values()
        ],
        "searchees": searchees,
    }


def verdict(slot):
    """What cross-seed knows about one content on one tracker."""
    if not slot:
        return "unsearched"
    if slot["decision"] in FOUND:
        return "found"
    if slot["decision"] in MISMATCH:
        return "other_release"
    return "absent" if slot["searched"] else "unsearched"
