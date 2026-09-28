"""Release matching: .torrent parsing, piece proof, names, search and the apply worker."""

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from seedbox import actions, config, match
from seedbox import torrentfile as tf

PL = 16  # piece length of the test torrents


def benc(v):
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, str):
        v = v.encode()
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, list):
        return b"l" + b"".join(benc(x) for x in v) + b"e"
    return b"d" + b"".join(benc(k) + benc(v[k]) for k in sorted(v)) + b"e"


def torrent(name, files):
    """.torrent bytes for files [(relative path or None for single file, content)]."""
    blob = b"".join(c for _, c in files)
    pieces = b"".join(hashlib.sha1(blob[k : k + PL]).digest() for k in range(0, len(blob), PL))
    info = {"name": name, "piece length": PL, "pieces": pieces}
    if files[0][0] is None:
        info["length"] = len(blob)
    else:
        info["files"] = [{"length": len(c), "path": p.split("/")} for p, c in files]
    return benc({"announce": "http://t.example/a", "info": info})


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


MOVIE = bytes(range(256)) * 3 + b"end"  # 771 bytes: the last piece is short


class TorrentFile(unittest.TestCase):
    def test_parse_and_verify(self):
        data = torrent("Release.2013.1080p.mkv", [(None, MOVIE)])
        meta = tf.parse(data)
        self.assertEqual(meta["files"], [{"path": "Release.2013.1080p.mkv", "length": 771, "offset": 0}])
        start = data.index(b"4:info") + 6
        self.assertEqual(meta["infohash"], hashlib.sha1(data[start:-1]).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "Renamed.mkv")
            write(local, MOVIE)
            self.assertEqual(tf.verify(meta, meta["files"][0], local), {"checked": 10, "failed": 0, "verified": True})
            write(local, MOVIE[:-3] + b"END")  # same size, last piece differs
            proof = tf.verify(meta, meta["files"][0], local, count=100)
            self.assertEqual((proof["verified"], proof["failed"]), (False, 1))

    def test_file_sharing_pieces_with_an_nfo(self):
        nfo = b"release notes" * 3  # 39 bytes, not piece-aligned
        meta = tf.parse(torrent("Release", [("Release.nfo", nfo), ("Release.mkv", MOVIE)]))
        mkv = meta["files"][1]
        self.assertEqual((mkv["path"], mkv["offset"]), ("Release/Release.mkv", 39))
        inside = tf.pieces_inside(meta, mkv)
        # Piece 2 starts at 32 < 39: shared with the .nfo, left out; the short last piece is in.
        self.assertEqual((inside[0], inside[-1]), (3, len(meta["pieces"]) - 1))
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "Other.Name.mkv")
            write(local, MOVIE)
            self.assertTrue(tf.verify(meta, mkv, local)["verified"])

    def test_not_a_torrent(self):
        with self.assertRaises(tf.TorrentError):
            tf.parse(b"<html>login</html>")


class Names(unittest.TestCase):
    def test_suggestions(self):
        self.assertEqual(match._release_name("X.2013.x264-ULSHD.mkv.FRENCH", ".mkv"), "X.2013.x264-ULSHD.mkv")
        self.assertEqual(match._release_name("X.2013.x264-ULSHD", ".mkv"), "X.2013.x264-ULSHD.mkv")
        names = match.suggestions(
            "Miyazaki.The.Wind.Rises.2013.mkv",
            "Le.Vent.Se.Leve.2013.x264-ULSHD.mkv",
            ["Le.Vent.Se.Leve.2013.x264-ULSHD.mkv.FRENCH", "le.vent.se.leve.2013.x264-ulshd"],
        )
        # The torrent's own file name first, then the other spellings.
        self.assertEqual(names, ["Le.Vent.Se.Leve.2013.x264-ULSHD.mkv", "le.vent.se.leve.2013.x264-ulshd.mkv"])
        self.assertEqual(match.suggestions("Same.mkv", "Same.mkv", []), [])


class FakeProwlarr:
    results = {}  # indexer id -> results
    torrents = {}  # download url -> bytes

    def __init__(self, *_):
        self.queries = []

    def get(self, path):
        return [
            {"id": 1, "name": "Alpha", "protocol": "torrent", "enable": True, "indexerUrls": ["https://alpha.example/"],
             "capabilities": {"movieSearchParams": ["q", "imdbId"]}},
            {"id": 2, "name": "Beta", "protocol": "torrent", "enable": True, "indexerUrls": ["https://beta.example/"],
             "capabilities": {"movieSearchParams": ["q"]}},
        ]  # fmt: skip

    def search(self, query, indexer_id):
        FakeProwlarr.log.append((indexer_id, query))
        return FakeProwlarr.results.get(indexer_id, [])

    def download(self, url):
        return FakeProwlarr.torrents[url]


class FakeQbt:
    def __init__(self, cfg):
        self.cfg, self.live, self.names, self.calls = cfg, [], {}, []
        self.progress_after_check = 1

    def torrents(self):
        return self.live

    def categories(self):
        return {"films": {"savePath": "/video/films/Miyazaki"}}

    def files(self, h):
        return [{"name": n, "size": s} for n, s in self.names.get(h, [])]

    def add_torrent(self, content, save_path, category, stopped):
        meta = tf.parse(content)
        self.calls.append(("add", save_path, category, stopped))
        # NoSubfolder: names without the torrent root folder.
        multi = len(meta["files"]) > 1
        self.names[meta["infohash"]] = [
            (f["path"].split("/", 1)[-1] if multi else f["path"], f["length"]) for f in meta["files"]
        ]
        self.live.append({"hash": meta["infohash"], "state": "stoppedDL", "progress": 0, "name": meta["name"],
                          "save_path": save_path, "content_path": save_path})  # fmt: skip

    def file_priority(self, h, ids, prio):
        self.calls.append(("prio", ids, prio))

    def rename_file(self, h, old, new):
        self.calls.append(("rename", h, old, new))
        names = self.names[h]
        self.names[h] = [(new if n == old else n, s) for n, s in names]
        # qBittorrent renames on disk when the old name exists.
        src = os.path.join(self.cfg.roots[0], "Miyazaki", old)
        if os.path.exists(src):
            os.rename(src, os.path.join(self.cfg.roots[0], "Miyazaki", new))

    def recheck(self, hashes):
        self.calls.append(("recheck", hashes))
        for t in self.live:
            if t["hash"] in hashes:
                t.update(state="stoppedUP", progress=self.progress_after_check)

    def start(self, hashes):
        self.calls.append(("start", hashes))


class Matching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.films = os.path.join(t, "media", "films")
        self.local = os.path.join(self.films, "Miyazaki", "Miyazaki.The.Wind.Rises.2013.mkv")
        write(self.local, MOVIE)
        write(os.path.join(self.films, "Miyazaki", "Miyazaki.The.Wind.Rises.2013.fr.srt"), b"1")
        env = {"SEEDBOX_ROOTS": self.films, "SEEDBOX_OUTPUT_DIR": os.path.join(t, "out"),
               "SEEDBOX_PROWLARR_URL": "http://prowlarr:9696", "SEEDBOX_PROWLARR_API_KEY": "k"}  # fmt: skip
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config, "DEFAULT_PATHS", ()):
            self.cfg = config.load()
        self.cfg.path_map = {"/video": os.path.join(t, "media")}
        self.cfg.actions = True
        self.snap = {"entries": [{"name": "Miyazaki/Miyazaki.The.Wind.Rises.2013.mkv", "kind": "file",
                                  "path": self.local, "trackers": ["beta.example"]}]}  # fmt: skip
        FakeProwlarr.log = []
        good = torrent("Le.Vent.Se.Leve.2013.x264-ULSHD.mkv", [(None, MOVIE)])
        FakeProwlarr.torrents = {
            "http://prowlarr:9696/1/dl": good,
            "http://prowlarr:9696/2/dl": torrent("Fake.mkv", [(None, b"y" * 771)]),
        }
        FakeProwlarr.results = {
            1: [{"title": "Le.Vent.Se.Leve.2013.x264-ULSHD.mkv", "size": 771, "downloadUrl": "http://prowlarr:9696/1/dl",
                 "infoHash": tf.parse(good)["infohash"], "seeders": 7},
                {"title": "Le.Vent.Se.Leve.2013.2160p", "size": 90_000_000, "downloadUrl": "http://prowlarr:9696/1/x"}],
            2: [{"title": "Le.Vent.Se.Leve.2013.x264-ULSHD.mkv.FRENCH", "size": 771, "downloadUrl": "http://prowlarr:9696/2/dl"}],
        }  # fmt: skip
        patches = [
            mock.patch.object(match, "ProwlarrClient", FakeProwlarr),
            mock.patch.object(match, "CHECK_POLL_S", 0),
            mock.patch("seedbox.prowlarr.ProwlarrClient.get", FakeProwlarr.get),
            mock.patch.object(match.time, "sleep", lambda s: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cfg.tmdb_api_key = "key"
        tmdb_movie = {
            "id": 149870,
            "titles": ["Le Vent se lève", "The Wind Rises"],
            "year": "2013",
            "imdb": "tt2013293",
        }
        for name, value in (("movie", lambda k, i: dict(tmdb_movie)), ("search", lambda k, t, y="": [{"id": 149870}])):
            p = mock.patch.object(match.tmdb, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_verify_inject_and_rename(self):
        qbt = FakeQbt(self.cfg)
        res = match.search(self.cfg, self.snap, 0, qbt)
        # Every title with the year, plus the IMDb id where the indexer supports it.
        self.assertIn((1, "{ImdbId:tt2013293}"), FakeProwlarr.log)
        self.assertNotIn((2, "{ImdbId:tt2013293}"), FakeProwlarr.log)
        self.assertIn((2, "The Wind Rises 2013"), FakeProwlarr.log)
        verdicts = [(c["tracker"], c["verdict"], c["seeded_there"]) for c in res["candidates"]]
        self.assertEqual(
            verdicts,
            [("alpha.example", "exact", False), ("beta.example", "exact", True), ("alpha.example", "other", False)],
        )
        self.assertNotIn("downloadUrl", json.dumps(res))
        self.assertNotIn("prowlarr:9696", json.dumps(res))

        # Beta's torrent has the size but not the bytes.
        bad = match.verify(self.cfg, self.snap, res["candidates"][1]["id"], qbt)
        self.assertFalse(bad["verified"])
        good = match.verify(self.cfg, self.snap, res["candidates"][0]["id"], qbt)
        self.assertTrue(good["verified"])
        self.assertEqual(good["names"][0], "Le.Vent.Se.Leve.2013.x264-ULSHD.mkv")
        self.assertEqual(good["sidecars"], ["Miyazaki.The.Wind.Rises.2013.fr.srt"])

        with self.assertRaises(match.MatchError):
            match.plan(self.cfg, self.snap, good["infohash"], "inject_rename", "../evil.mkv")
        with self.assertRaises(match.MatchError):
            match.plan(self.cfg, self.snap, good["infohash"], "inject_rename", "Other.avi")
        p = match.plan(self.cfg, self.snap, good["infohash"], "inject_rename", good["names"][0])
        job = match.start(self.cfg, lambda: qbt, p)
        # Background worker; wait for it.
        for t in list(match.threading.enumerate()):
            if t is not match.threading.current_thread() and t.daemon:
                t.join(5)
        h = good["infohash"]
        self.assertEqual(qbt.calls[0], ("add", "/video/films/Miyazaki", "films", True))
        # Named after the local file first (nothing moves on disk), rechecked, started, then renamed on disk.
        kinds = [c[0] for c in qbt.calls]
        self.assertEqual(kinds, ["add", "rename", "recheck", "start", "rename"])
        self.assertEqual(qbt.calls[1][2:], ("Le.Vent.Se.Leve.2013.x264-ULSHD.mkv", "Miyazaki.The.Wind.Rises.2013.mkv"))
        self.assertTrue(os.path.exists(os.path.join(self.films, "Miyazaki", "Le.Vent.Se.Leve.2013.x264-ULSHD.mkv")))
        jobs = {j["id"]: j for j in actions.load_jobs(self.cfg)}
        self.assertEqual((jobs[job["id"]]["status"], jobs[job["id"]]["action"]), ("done", "inject"))
        self.assertEqual(h, qbt.live[0]["hash"])

    def test_never_starts_an_incomplete_torrent(self):
        qbt = FakeQbt(self.cfg)
        qbt.progress_after_check = 0.4
        res = match.search(self.cfg, self.snap, 0, qbt)
        good = match.verify(self.cfg, self.snap, res["candidates"][0]["id"], qbt)
        job = match.start(self.cfg, lambda: qbt, match.plan(self.cfg, self.snap, good["infohash"], "inject"))
        for t in list(match.threading.enumerate()):
            if t is not match.threading.current_thread() and t.daemon:
                t.join(5)
        self.assertNotIn("start", [c[0] for c in qbt.calls])
        stored = {j["id"]: j for j in actions.load_jobs(self.cfg)}[job["id"]]
        self.assertEqual(stored["status"], "failed")
        self.assertIn("left stopped", stored["note"])

    def test_actions_switch_and_expired_candidate(self):
        self.cfg.actions = False
        code, body = match.handle(self.cfg, lambda: FakeQbt(self.cfg), b'{"op": "search", "entry": 0}')
        self.assertEqual(code, 403)
        self.cfg.actions = True
        with self.assertRaises(match.MatchError):
            match.verify(self.cfg, self.snap, "nope", None)
        # Renamed since the collection: a clear error, not a crash.
        os.rename(self.local, self.local + ".old")
        code, body = match.handle(self.cfg, lambda: FakeQbt(self.cfg), b'{"op": "search", "entry": 0}')
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
