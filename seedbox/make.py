"""`seedbox make`: a .torrent and a .nfo for any file or folder, outside the library.

The dashboard's creation (create.py) only works on library entries, with the
announce URL borrowed from the client. This command takes plain paths, so a
folder the service must never see (not in [library] roots, not in qBittorrent's
categories) can still get a release-grade .nfo and a private .torrent. It reads
no config file and talks to nothing: the announce URL and the source come from
the command line (or $SEEDBOX_ANNOUNCE / $SEEDBOX_SOURCE), never from the repo.

One path = one torrent. `--each` makes one per child of each given folder.
"""

import argparse
import os
import sys

from seedbox import create, nfo, ui
from seedbox import torrentfile as tf
from seedbox.config import DEFAULT_MEDIA_EXT, DEFAULT_SKIP_DIRS

SIDECAR_EXT = (".nfo", ".torrent")
JUNK = (".DS_Store", "Thumbs.db")


class MakeError(Exception):
    pass


def spec_for(path):
    """{'name', 'files': [(path on disk, [path in the torrent], length)], 'total'} for a file or folder."""
    path = os.path.abspath(path)
    name = os.path.basename(path)
    if os.path.isfile(path):
        size = os.path.getsize(path)
        return {"name": name, "files": [(path, [name], size)], "total": size}
    if not os.path.isdir(path):
        raise MakeError(f"{path}: no such file or folder")
    files = []
    for root, dirs, names in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in DEFAULT_SKIP_DIRS)
        for n in sorted(names):
            if n in JUNK or n.lower().endswith(SIDECAR_EXT):
                continue
            full = os.path.join(root, n)
            files.append((full, os.path.relpath(full, path).split(os.sep), os.path.getsize(full)))
    if not files:
        raise MakeError(f"{name}: no file to put in a torrent")
    return {"name": name, "files": files, "total": sum(f[2] for f in files)}


def main_file(spec):
    """The biggest media file of the content (the one MediaInfo describes)."""
    media = [f for f in spec["files"] if os.path.splitext(f[0])[1].lower() in DEFAULT_MEDIA_EXT]
    if not media:
        raise MakeError(f"{spec['name']}: no media file for the .nfo")
    return max(media, key=lambda f: f[2])[0]


def parse_fields(pairs):
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or key not in nfo.FIELDS:
            raise MakeError(f"--set {pair!r}: expected KEY=VALUE, KEY in {', '.join(nfo.FIELDS)}")
        out[key] = value
    return out


def write_private(path, data):
    """Owner-only: a .torrent holds the passkey."""
    create._write(path, data)


def make_one(path, trackers, out_dir, overrides, spinner=None):
    """One .nfo and one .torrent per tracker, the files hashed once.

    trackers: [(label, announce, source)]; a label gives <name>.<label>.torrent."""
    spec = spec_for(path)
    found = nfo.describe(spec["name"], main_file(spec), len(spec["files"]))
    fields = {**found["fields"], **overrides}
    missing = [nfo.LABELS[k] for k in nfo.MANDATORY if not fields.get(k)]
    if missing:
        ui.warn(f"{spec['name']}: fields to fill by hand in the .nfo or with --set: {', '.join(missing)}")
    text = nfo.render(spec["name"], fields, found["details"], found["report"], spec["total"])

    def progress(done):
        if spinner:
            spinner.update(f"hashing {spec['name']}: {done / spec['total'] * 100:.0f} %")

    pieces, pl = create.hash_pieces(spec, progress)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, spec["name"])
    write_private(base + ".nfo", text.encode())
    for label, announce, source in trackers:
        data = create.assemble(spec, pieces, pl, announce, source)
        write_private(f"{base}.{label}.torrent" if label else base + ".torrent", data)
        ui.ok(f"{spec['name']} {label}: {len(pieces) // 20} pieces of {pl // 1024} KiB, {tf.parse(data)['infohash']}")
    return base


def read_trackers(path):
    """[(label, announce, source)] from a tab-separated file: label, announce URL, source (optional)."""
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip() and not line.startswith("#"):
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 2 or not cols[0] or not cols[1]:
                    raise MakeError(f"{path}: expected label<TAB>announce[<TAB>source]")
                out.append((cols[0], cols[1], cols[2] if len(cols) > 2 else ""))
    if not out:
        raise MakeError(f"{path}: no tracker")
    return out


def targets(paths, each):
    if not each:
        return list(paths)
    out = []
    for top in paths:
        if not os.path.isdir(top):
            raise MakeError(f"--each: {top} is not a folder")
        out += [
            os.path.join(top, n)
            for n in sorted(os.listdir(top))
            if not n.startswith(".") and n not in DEFAULT_SKIP_DIRS and not n.lower().endswith(SIDECAR_EXT)
        ]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seedbox make", description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="file or folder (one torrent each)")
    parser.add_argument("--each", action="store_true", help="make one torrent per child of each given folder")
    parser.add_argument(
        "--announce",
        default=os.environ.get("SEEDBOX_ANNOUNCE", ""),
        help="announce URL, passkey included ($SEEDBOX_ANNOUNCE)",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("SEEDBOX_SOURCE", ""),
        help="'source' field the tracker requires ($SEEDBOX_SOURCE)",
    )
    parser.add_argument(
        "--trackers",
        metavar="FILE",
        help="several trackers, hashed once: lines 'label<TAB>announce<TAB>source' (a private file, 600)",
    )
    parser.add_argument("--out", default=".", help="folder for the .torrent and .nfo files (default: current)")
    parser.add_argument(
        "--set", action="append", metavar="KEY=VALUE", help=f"override a .nfo field ({', '.join(nfo.FIELDS)})"
    )
    parser.add_argument(
        "--skip-existing", action="store_true", help="leave alone a release whose .torrent already exists in --out"
    )
    args = parser.parse_args(argv)
    if not args.announce and not args.trackers:
        ui.ko("--announce (or $SEEDBOX_ANNOUNCE) or --trackers FILE is required")
        return 2
    rc = 0
    try:
        overrides = parse_fields(args.set)
        trackers = read_trackers(args.trackers) if args.trackers else [("", args.announce, args.source)]
        todo = targets(args.paths, args.each)
        for path in todo:
            first = f".{trackers[0][0]}" if trackers[0][0] else ""
            if args.skip_existing and os.path.exists(
                os.path.join(args.out, os.path.basename(path) + first + ".torrent")
            ):
                ui.info(f"{os.path.basename(path)}: already made, skipped")
                continue
            try:
                with ui.Spinner(f"hashing {os.path.basename(path)}") as spinner:
                    make_one(path, trackers, args.out, overrides, spinner)
            except (MakeError, nfo.NfoError, create.CreateError, OSError) as exc:
                ui.ko(str(exc))
                rc = 1
    except MakeError as exc:
        ui.ko(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130
    return rc


if __name__ == "__main__":
    sys.exit(main())
