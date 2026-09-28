"""Read a .torrent file (bencode) and prove a local file is part of it.

The proof hashes a sample of the torrent's pieces straight from the local file
(SHA-1, as in the .torrent): same bytes, same release, whatever the file name.
Only pieces lying entirely inside the file are used, so a file that shares its
first or last piece with a sidecar (.nfo) is still checked on the rest.
"""

import hashlib
import os


class TorrentError(Exception):
    pass


def _decode(data, i):
    """(value, next index) for the bencoded value at data[i]."""
    c = data[i : i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1 : end]), end + 1
    if c == b"l":
        i, out = i + 1, []
        while data[i : i + 1] != b"e":
            v, i = _decode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i, out = i + 1, {}
        while data[i : i + 1] != b"e":
            k, i = _decode(data, i)
            start = i
            v, i = _decode(data, i)
            out[k] = v
            if k == b"info":
                out[b"__info_span__"] = (start, i)
        return out, i + 1
    if c.isdigit():
        colon = data.index(b":", i)
        n = int(data[i:colon])
        return data[colon + 1 : colon + 1 + n], colon + 1 + n
    raise TorrentError(f"invalid bencode at byte {i}")


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def parse(data):
    """{'infohash', 'name', 'piece_length', 'pieces', 'files': [{'path', 'length', 'offset'}], 'total'}.

    files[].path is relative to the torrent root ("Name/file.mkv"); a
    single-file torrent has one file whose path is its name."""
    try:
        meta, _ = _decode(data, 0)
        info = meta[b"info"]
        start, end = meta[b"__info_span__"]
        name = _text(info[b"name"])
        piece_length = int(info[b"piece length"])
        blob = info[b"pieces"]
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        raise TorrentError(f"not a torrent file: {exc}") from exc
    if len(blob) % 20:
        raise TorrentError("not a torrent file: pieces field is not a list of SHA-1")
    files, offset = [], 0
    if b"files" in info:
        for f in info[b"files"]:
            length = int(f[b"length"])
            # Padding files (BEP 47) take room in the pieces but are not real files.
            if b"p" not in f.get(b"attr", b""):
                files.append(
                    {"path": "/".join([name] + [_text(p) for p in f[b"path"]]), "length": length, "offset": offset}
                )
            offset += length
    else:
        offset = int(info[b"length"])
        files.append({"path": name, "length": offset, "offset": 0})
    return {
        "infohash": hashlib.sha1(data[start:end]).hexdigest(),
        "name": name,
        "piece_length": piece_length,
        "pieces": [blob[k : k + 20] for k in range(0, len(blob), 20)],
        "files": files,
        "total": offset,
    }


def pieces_inside(meta, f):
    """Indexes of the pieces lying entirely inside file f."""
    pl, start, end = meta["piece_length"], f["offset"], f["offset"] + f["length"]
    first = -(-start // pl)  # ceil
    # The last piece of the torrent may be short: it ends with the torrent.
    last = len(meta["pieces"]) - 1 if end >= meta["total"] else end // pl - 1
    return list(range(first, last + 1))


def sample(indexes, count):
    """Up to count indexes spread over the list: first, last and evenly between."""
    if len(indexes) <= count:
        return list(indexes)
    step = (len(indexes) - 1) / (count - 1)
    return sorted({indexes[round(k * step)] for k in range(count)})


def verify(meta, f, local_path, count=10):
    """Hash a sample of f's pieces from local_path. {'checked', 'failed', 'verified'}."""
    if os.path.getsize(local_path) != f["length"]:
        return {"checked": 0, "failed": 0, "verified": False, "reason": "size differs"}
    pl, total = meta["piece_length"], meta["total"]
    chosen = sample(pieces_inside(meta, f), count)
    if not chosen:
        return {"checked": 0, "failed": 0, "verified": False, "reason": "file smaller than a piece"}
    failed = 0
    with open(local_path, "rb") as handle:
        for k in chosen:
            begin, end = k * pl, min((k + 1) * pl, total)
            handle.seek(begin - f["offset"])
            if hashlib.sha1(handle.read(end - begin)).digest() != meta["pieces"][k]:
                failed += 1
    return {"checked": len(chosen), "failed": failed, "verified": failed == 0}
