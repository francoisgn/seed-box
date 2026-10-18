#!/usr/bin/env python3
"""Chapters of a Blu-ray title, from its playlist (.mpls), as an FFMETADATA file.

A rip made from the main .m2ts alone loses the disc chapters, which live in
BDMV/PLAYLIST/*.mpls. This reads the playlist that plays the given clip (the longest one
when several do) and prints the chapters in ffmpeg's metadata format, ready for
`reencode.sh --chapters FILE` or `ffmpeg -i video -i FILE -map_metadata 1 -map_chapters 1`.

Usage: mpls-chapters.py [--clip 00002] [--list] DISC_FOLDER
  --clip   clip number of the main stream (default: the clip of the longest playlist)
  --list   list the playlists (clips, duration, chapter count) instead of printing chapters
"""

import argparse
import glob
import os
import struct
import sys

TICKS = 45000.0  # playlist time stamps are in 45 kHz units


def parse(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"MPLS":
        raise ValueError("not an MPLS file")
    list_start, mark_start = struct.unpack(">II", data[8:16])
    pos = list_start + 4 + 2  # length, reserved
    n_items, _n_sub = struct.unpack(">HH", data[pos : pos + 4])
    pos += 4
    items = []
    for _ in range(n_items):
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        body = data[pos + 2 : pos + 2 + length]
        clip = body[0:5].decode("ascii", "replace")
        t_in, t_out = struct.unpack(">II", body[12:20])
        items.append((clip, t_in, t_out))
        pos += 2 + length
    marks = []
    pos = mark_start + 4
    n_marks = struct.unpack(">H", data[pos : pos + 2])[0]
    pos += 2
    for _ in range(n_marks):
        _res, mtype, item_ref, stamp = struct.unpack(">BBHI", data[pos : pos + 8])
        if mtype == 1:  # entry mark = chapter
            marks.append((item_ref, stamp))
        pos += 14
    return items, marks


def chapters(items, marks):
    starts, cum = [], 0.0
    for _clip, t_in, t_out in items:
        starts.append((cum, t_in))
        cum += (t_out - t_in) / TICKS
    out = []
    for item_ref, stamp in marks:
        if item_ref >= len(items):
            continue
        base, t_in = starts[item_ref]
        out.append(base + max(stamp - t_in, 0) / TICKS)
    return sorted(out), cum


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("disc")
    ap.add_argument("--clip")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    found = []
    for path in sorted(glob.glob(os.path.join(args.disc, "BDMV", "PLAYLIST", "*.mpls"))):
        try:
            items, marks = parse(path)
        except Exception:
            continue
        ch, total = chapters(items, marks)
        found.append((os.path.basename(path), items, ch, total))
    if not found:
        sys.exit("ko no playlist found")
    if args.list:
        for name, items, ch, total in found:
            clips = ",".join(sorted({i[0] for i in items}))[:22]
            print(f"{name}  {total / 60:6.1f} min  {len(items):2d} clip(s) {clips:<22} {len(ch):2d} chapter(s)")
        return
    cand = [f for f in found if args.clip is None or any(i[0] == args.clip for i in f[1])]
    pure = [f for f in cand if args.clip is not None and all(i[0] == args.clip for i in f[1])]
    cand = pure or cand  # prefer a playlist that plays only this clip: its chapter times match the stream
    if not cand:
        sys.exit(f"ko no playlist plays clip {args.clip}")
    name, items, ch, total = max(cand, key=lambda f: f[3])
    sys.stderr.write(f"playlist {name}: {total / 60:.1f} min, {len(ch)} chapter(s)\n")
    print(";FFMETADATA1")
    for i, start in enumerate(ch):
        end = ch[i + 1] if i + 1 < len(ch) else total
        start_ms, end_ms = int(start * 1000), int(max(end, start) * 1000)
        print(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}\nEND={end_ms}\ntitle=Chapter {i + 1:02d}")


if __name__ == "__main__":
    main()
