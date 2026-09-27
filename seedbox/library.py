"""Disk inventory: turn library roots into entries, indexed by inode.

An entry is the first directory, walking down from a root, that directly
holds a media file: grouping folders (collections, show folders) are walked
through, not counted, and everything below an entry (subtitles, extras) is
part of it. A season folder is an entry, which matches the granularity of
season packs. A media file sitting directly in a root is an entry of its own.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Entry:
    path: str
    category: str
    name: str
    size: int = 0
    files: int = 0
    torrents: list = field(default_factory=list)
    trackers: list = field(default_factory=list)
    uploaded: int = 0
    complete: bool = False

    @property
    def status(self):
        if not self.torrents:
            return "orphan"
        if not self.complete:
            return "incomplete"
        return "seeded"


def _is_media(cfg, name):
    return os.path.splitext(name)[1].lower() in cfg.media_ext


def _has_media(cfg, path):
    try:
        with os.scandir(path) as it:
            return any(not e.name.startswith(".") and _is_media(cfg, e.name) and e.is_file() for e in it)
    except OSError:
        return False


def _collect(cfg, path, depth):
    if depth > cfg.max_depth:
        return []
    if _has_media(cfg, path):
        return [path]
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    found = []
    for name in names:
        if name in cfg.skip_dirs or name.startswith("."):
            continue
        child = os.path.join(path, name)
        if os.path.isdir(child) and not os.path.islink(child):
            found.extend(_collect(cfg, child, depth + 1))
    return found


def _scan(cfg, path):
    """(total size, file count, inode set) of an entry."""
    total = count = 0
    inodes = set()
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in cfg.skip_dirs and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            try:
                st = os.stat(os.path.join(dirpath, name), follow_symlinks=False)
            except OSError:
                continue
            total += st.st_size
            count += 1
            inodes.add((st.st_dev, st.st_ino))
    return total, count, inodes


def build(cfg, warn=lambda msg: None):
    """Return (entries, inode_index) where inode_index maps (dev, ino) -> entry index."""
    entries = []
    index = {}
    for root in cfg.roots:
        if not os.path.isdir(root):
            warn(f"library root missing, skipped: {root}")
            continue
        category = os.path.basename(root) or root
        candidates = _collect(cfg, root, 0)
        # Loose media files directly in the root: one entry per file.
        if candidates == [root]:
            candidates = []
            for name in sorted(os.listdir(root)):
                full = os.path.join(root, name)
                if not name.startswith(".") and _is_media(cfg, name) and os.path.isfile(full):
                    candidates.append(full)
                elif os.path.isdir(full) and name not in cfg.skip_dirs and not name.startswith("."):
                    candidates.extend(_collect(cfg, full, 1))
        for path in candidates:
            if os.path.isfile(path):
                st = os.stat(path)
                size, files, inodes = st.st_size, 1, {(st.st_dev, st.st_ino)}
            else:
                size, files, inodes = _scan(cfg, path)
            if not files:
                continue
            position = len(entries)
            entries.append(
                Entry(path=path, category=category, name=os.path.relpath(path, root), size=size, files=files)
            )
            for key in inodes:
                index.setdefault(key, position)
    return entries, index
