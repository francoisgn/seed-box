"""Disk inventory: turn library roots into entries, indexed by inode.

An entry is one work as it is stored and shared:

- a folder whose media files are episodes (a season, a numbered collection
  like "DBZ - 001…") or a single film with its extras: the folder;
- a folder holding several unrelated media files (films dropped side by side
  in "incoming", "archives", "disney"…): one entry per file, with its
  same-name sidecars (.nfo, .srt);
- a season-pack folder made of one folder per episode: the season folder.

Grouping folders (collections, show folders) are walked through, not counted.
"""

import os
from dataclasses import dataclass, field

from seedbox import titles

SAMPLE = ("sample", "trailer")


@dataclass
class Entry:
    path: str
    category: str
    name: str
    kind: str = "dir"  # "dir", or "file" (a media file, or its parts CD1/CD2, with sidecars)
    size: int = 0
    files: int = 0
    media_inodes: set = field(default_factory=set)
    duplicate_episodes: list = field(default_factory=list)
    # Only film of a grouping folder ("archives" holding one file): fine while
    # moving or filling a folder, a problem if it lasts.
    lone: bool = False
    torrents: list = field(default_factory=list)
    trackers: list = field(default_factory=list)
    uploaded: int = 0
    complete: bool = False
    # Filled by collect.diagnose().
    issues: list = field(default_factory=list)
    coverage: str = "none"
    title_key: str = ""
    resolution: str = ""

    @property
    def folder(self):
        """Folder holding the entry, relative to its root's parent ("films/incoming")."""
        parent = os.path.dirname(self.name)
        return f"{self.category}/{parent}" if parent else self.category

    @property
    def status(self):
        if not self.torrents:
            return "orphan"
        if not self.complete:
            return "incomplete"
        return "seeded"


def _is_media(cfg, name):
    if os.path.splitext(name)[1].lower() not in cfg.media_ext:
        return False
    lower = name.lower()
    return not any(word in lower for word in SAMPLE)


def _listing(cfg, path):
    """(media files, sub-directories) directly in path."""
    media, dirs = [], []
    try:
        with os.scandir(path) as it:
            for e in sorted(it, key=lambda e: e.name):
                if e.name.startswith(".") or e.name in cfg.skip_dirs:
                    continue
                if e.is_dir(follow_symlinks=False):
                    dirs.append(e.path)
                elif e.is_file(follow_symlinks=False) and _is_media(cfg, e.name):
                    media.append(e.path)
    except OSError:
        pass
    return media, dirs


def _episodic(paths):
    names = [os.path.basename(p) for p in paths]
    return sum(titles.is_episode(n) for n in names) >= max(2, len(names) * 0.6)


def _collect(cfg, path, depth):
    """Candidate entries under path: list of (kind, path)."""
    if depth > cfg.max_depth:
        return []
    media, dirs = _listing(cfg, path)
    if depth > 0 and media and _episodic(media):
        return [("dir", path)]
    children = [c for d in dirs for c in _collect(cfg, d, depth + 1)]
    if depth > 0 and len(media) == 1 and not children:
        # One film, maybe with extras folders: the folder is the entry, unless
        # it is a grouping folder ("archives") that happens to hold one film.
        grouping = titles.looks_like_release(os.path.basename(media[0])) and not titles.looks_like_release(
            os.path.basename(path)
        )
        if not grouping:
            return [("dir", path)]
        return [("lone", media[0])]
    # Season pack stored as one folder per episode: the season is the entry.
    if (
        depth > 0
        and not media
        and len(children) >= 2
        and titles.is_season_folder(os.path.basename(path))
        and all(os.path.dirname(p) == path and titles.is_episode(os.path.basename(p)) for _, p in children)
    ):
        return [("dir", path)]
    return [(kind, m) for kind, m in _group_parts(media)] + children


def _group_parts(media):
    """One candidate per film: CD1/CD2 (part 1/part 2) files are one entry."""
    groups, order = {}, []
    for path in media:
        key = titles.part_key(os.path.basename(path))
        key = (os.path.dirname(path), key) if key else path
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(path)
    return [("parts", tuple(groups[k])) if len(groups[k]) > 1 else ("file", groups[k][0]) for k in order]


def _stat_files(cfg, paths):
    """(total size, file count, all inodes, media inodes) for a list of files."""
    total = count = 0
    inodes, media = set(), set()
    for full in paths:
        try:
            st = os.stat(full, follow_symlinks=False)
        except OSError:
            continue
        total += st.st_size
        count += 1
        key = (st.st_dev, st.st_ino)
        inodes.add(key)
        if _is_media(cfg, os.path.basename(full)):
            media.add(key)
    return total, count, inodes, media


def _dir_files(cfg, path):
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in cfg.skip_dirs and not d.startswith(".")]
        out.extend(os.path.join(dirpath, n) for n in filenames if not n.startswith("."))
    return out


def _sidecars(path):
    """The media file and its same-stem siblings (subtitles, nfo)."""
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    try:
        siblings = [n for n in os.listdir(folder) if n != name and n.startswith(stem + ".")]
    except OSError:
        siblings = []
    return [path] + [os.path.join(folder, n) for n in sorted(siblings)]


def _duplicate_episodes(names):
    seen = {}
    for name in names:
        ep = titles.parse(name)["episode"]
        if ep:
            seen.setdefault(ep, []).append(name)
    return sorted(ep for ep, found in seen.items() if len(found) > 1)


def build(cfg, warn=lambda msg: None):
    """Return (entries, inode_index): inode_index maps (dev, ino) -> list of entry positions."""
    entries = []
    index = {}
    for root in cfg.roots:
        if not os.path.isdir(root):
            warn(f"library root missing, skipped: {root}")
            continue
        category = os.path.basename(root) or root
        for kind, found in _collect(cfg, root, 0):
            lone = kind == "lone"
            if kind == "parts":
                path, files = found[0], [f for part in found for f in _sidecars(part)]
                kind = "file"
            elif lone:
                path, files, kind = found, _sidecars(found), "file"
            else:
                path = found
                files = _sidecars(path) if kind == "file" else _dir_files(cfg, path)
            size, count, inodes, media = _stat_files(cfg, files)
            if not count:
                continue
            entry = Entry(
                path=path,
                category=category,
                name=os.path.relpath(path, root),
                kind=kind,
                size=size,
                files=count,
                media_inodes=media,
                lone=lone,
            )
            if kind == "dir":
                names = [os.path.basename(f) for f in files if _is_media(cfg, os.path.basename(f))]
                entry.duplicate_episodes = _duplicate_episodes(names)
            position = len(entries)
            entries.append(entry)
            for key in inodes:
                index.setdefault(key, []).append(position)
    return entries, index
