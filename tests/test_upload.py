"""Upload API: checks against the tracker, the API client, the upload jobs."""

import email.parser
import email.policy
import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from seedbox import actions, cli, config, match, nfo, upload
from seedbox import torrentfile as tf
from tests.test_create import ANNOUNCE, KEY, FakeQbt, load_cfg, write

PASSKEY = "SECRETPASSKEY"
FILM = "Some.Film.2019.MULTi.1080p.BluRay.x264-GRP.mkv"
SIZE = 50_000
DESCRIBED = {
    "fields": {"title": "Some Film", "year": "2019", "language": "MULTI", "source": "BluRay", "group": "GRP",
               "resolution": "1080p", "video_codec": "x264", "audio_codec": "AC3", "channels": "", "bit_depth": "",
               "hdr": ""},
    "missing": [], "details": {"audio": [], "subtitles": [], "files": 1}, "report": "General\nComplete name : x\n",
}  # fmt: skip


class FakeApi(http.server.BaseHTTPRequestHandler):
    """The upload API as documented: categories, uploads (JSON answers)."""

    answer = None  # (status, body) for the next upload; None: accept it
    seen = []

    def _json(self, status, body, headers=()):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {PASSKEY}":
            return self._json(401, {"ok": False, "code": "INVALID_API_KEY", "message": "Passkey invalide"})
        if FakeApi.approved is False:
            return self._json(403, {"ok": False, "code": "NOT_APPROVED", "message": "Compte non approuvé"})
        cats = [{"id": 2000, "name": "Movies", "categories": [
            {"id": 2100, "name": "Film", "presentation_types": ["html_auto", "manual"], "media_type": "movie"},
            {"id": 2200, "name": "Series", "presentation_types": ["html_auto"], "media_type": "tv"}]}]  # fmt: skip
        return self._json(200, {"ok": True, "code": "CATEGORIES_RETURNED", "categories": cats})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw
        )
        parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
        FakeApi.seen.append(
            {k: p.get_payload(decode=True) for k, p in parts.items()} | {"auth": self.headers["Authorization"]}
        )
        if FakeApi.answer:
            status, body = FakeApi.answer
            return self._json(status, body, [("Retry-After", "120")] if status == 429 else ())
        infohash = tf.parse(parts["torrent_file"].get_payload(decode=True))["infohash"]
        return self._json(201, {"ok": True, "code": "ACCEPTED", "torrent_id": 7, "info_hash": infohash})

    def log_message(self, *args):
        pass


FakeApi.approved = True

IDENT = {"id": 5, "titles": ["Some Film"], "year": "2019", "imdb": ""}

# The documented contract, written as a user would in [upload.api].
PROFILE = {
    "base": "https://www.tracker-a.example",
    "headers": {"Authorization": "Bearer {passkey}"},
    "probe": {"path": "/api/v1/categories"},
    "submit": {"path": "/api/v1/uploads", "torrent_field": "torrent_file", "nfo_field": "nfo_file",
               "fields": {"category_id": 2100, "presentation_type": "html_auto"}},
    "answer": {"success": ["ACCEPTED", "ALREADY_ACCEPTED"], "review": ["DUPLICATE", "TMDB_NOT_MATCHED"],
               "id": "torrent_id", "infohash": "info_hash", "candidates": "candidates"},
    "limits": {"per_hour": 30},
}  # fmt: skip


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "movies")
        self.cfg = load_cfg([self.root], os.path.join(self.tmp.name, "out"))
        self.film = os.path.join(self.root, FILM)
        write(self.film, os.urandom(SIZE))
        self.snapshot = {
            "summary": {"target_trackers": [KEY, "tracker-b.example"]},
            "entries": [
                {"name": FILM, "path": self.film, "kind": "file", "size": SIZE, "trackers": ["tracker-b.example"],
                 "torrents": ["b" * 40], "folder": "movies", "resolution": "1080p", "search": {}},
                {"name": "Show.S01E02.1080p.mkv", "path": self.film, "kind": "file", "size": SIZE, "trackers": []},
                {"name": "Other.2018.1080p.mkv", "path": self.film, "kind": "file", "size": SIZE, "trackers": [KEY]},
            ],
            "torrents": [{"hash": "a" * 40, "tracker": KEY},
                         {"hash": "b" * 40, "tracker": "tracker-b.example", "seeds": 12}],
        }  # fmt: skip
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cfg.upload_tracker = KEY
        self.cfg.upload_api = config.upload_profile(PROFILE)
        self.cfg.upload_api["base"] = f"http://127.0.0.1:{self.server.server_port}"  # https in real configs
        FakeApi.answer, FakeApi.seen, FakeApi.approved = None, [], True
        upload._probe.update(at=0, value=None)
        upload._passkeys.clear()
        upload._checks.clear()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()


class Checks(unittest.TestCase):
    LOCAL = {"titles": ["somefilm"], "year": "2019", "group": "GRP", "resolution": "1080p", "size": 10_000_000_000}

    def kind(self, title, size=1):
        return upload.classify({"title": title, "size": size}, self.LOCAL)

    def test_classify(self):
        self.assertEqual(self.kind("Some.Film.2019.MULTi.1080p.BluRay.x264-OTHER.FRENCH", 10_050_000_000), "same_size")
        self.assertEqual(self.kind("Some Film 2019 MULTi 1080p BluRay x264-GRP.FRENCH", 12_000_000_000), "same_release")
        self.assertEqual(self.kind("Some.Film.2019.1080p.WEB.H264-ABC", 4_000_000_000), "same_resolution")
        self.assertEqual(self.kind("Some.Film.2019.2160p.UHD.x265-ABC", 30_000_000_000), "other")
        self.assertIsNone(self.kind("Some.Film.2005.1080p.x264-GRP", 10_000_000_000))  # another year
        self.assertIsNone(self.kind("Some.Film.S01E01.1080p.x264-GRP"))  # a series
        self.assertIsNone(self.kind("Free.Solo.2019.1080p.x264-GRP"))  # another title

    def naming(self, name, audio=("fr", "en"), subs=(), res="1080p", codec="x264"):
        tech = {"resolution": res, "video_codec": codec,
                "audio": [{"language": a} for a in audio], "subtitles": [{"language": x} for x in subs]}  # fmt: skip
        return upload.naming(name, nfo.from_name(name), tech)

    def test_naming_rules(self):
        self.assertEqual(self.naming("Some.Film.2019.MULTi.1080p.BluRay.x264-GRP.mkv"), ([], []))
        blocking, _ = self.naming("Some.Film.2019.MULTi.1080p.BluRay.x264-GRP.mkv", audio=("fr",))
        self.assertIn("one audio language", blocking[0])
        self.assertIn(
            "2160p", self.naming("Some.Film.2019.FRENCH.2160p.x265-GRP.mkv", audio=("fr",), codec="x265")[0][0]
        )
        self.assertIn("HEVC", self.naming("Some.Film.2019.FRENCH.1080p.x265-GRP.mkv", audio=("fr",))[0][0])
        self.assertIn("no French", self.naming("Some.Film.2019.VFF.1080p.x264-GRP.mkv", audio=("en",))[0][0])
        self.assertEqual(self.naming("Some.Film.2019.VOSTFR.1080p.x264-GRP.mkv", audio=("en",), subs=("fr",)), ([], []))
        self.assertTrue(self.naming("Some.Film.2019.VOSTFR.1080p.x264-GRP.mkv", audio=("fr",), subs=("fr",))[0])
        _, warnings = self.naming("Some Film 2019 1080p x264-GRP.mkv", audio=("fr",))
        self.assertTrue(any("spaces" in w for w in warnings))
        self.assertTrue(any("no language" in w for w in warnings))
        self.assertEqual(
            upload.naming("Some.Film.2019.1080p.mkv", nfo.from_name("Some.Film.2019.1080p.mkv"), None), ([], [])
        )

    def test_queries_most_precise_first(self):
        ident = {"titles": ["Some Film", "Un Film"], "year": "2019", "imdb": "tt1"}
        qs = match.queries(ident, {"imdb": True}, {"group": "GRP", "resolution": "1080p"})
        self.assertEqual(qs, ["Some Film 2019 GRP", "Un Film 2019 GRP", "Some Film 2019 1080p", "Un Film 2019 1080p",
                              "Some Film 2019", "Un Film 2019", "{ImdbId:tt1}"])  # fmt: skip
        self.assertEqual(match.queries(ident, {"imdb": False}), ["Some Film 2019", "Un Film 2019"])


class Api(Base):
    def test_probe_and_access(self):
        self.assertEqual(upload.passkey(self.cfg, FakeQbt(), self.snapshot), PASSKEY)
        self.assertTrue(upload.probe(self.cfg, PASSKEY)["ok"])
        FakeApi.approved = False
        with self.assertRaises(upload.UploadError) as caught:
            upload.probe(self.cfg, PASSKEY, fresh=True)
        self.assertEqual((caught.exception.code, caught.exception.status), ("NOT_APPROVED", 403))
        self.assertIs(upload.status(self.cfg, FakeQbt(), self.snapshot)["approved"], True)  # cached answer
        upload._probe.update(at=0, value=None)
        st = upload.status(self.cfg, FakeQbt(), self.snapshot)
        self.assertIs(st["approved"], False)
        self.assertIn("NOT_APPROVED", st["access"])
        self.assertNotIn(PASSKEY, json.dumps(st))

    def test_profile_checks(self):
        bad = [
            {**PROFILE, "base": "http://www.tracker-a.example"},
            {**PROFILE, "submit": {**PROFILE["submit"], "fields": {"key": "{passkey}"}}},  # passkey outside headers
            {**PROFILE, "submit": {**PROFILE["submit"], "fields": {"x": "{nope}"}}},
            {**PROFILE, "submit": {"path": "/u"}},
            {**PROFILE, "answer": {"success": "ACCEPTED"}},
        ]
        for raw in bad:
            with self.assertRaises(config.ConfigError):
                config.upload_profile(raw)
        other = config.upload_profile({"base": "https://t.example", "headers": {"X-Api-Key": "{passkey}"},
                                       "submit": {"path": "/upload.php", "torrent_field": "file", "nfo_field": "nfo",
                                                  "fields": {"tmdb": "{tmdb_id}", "type": "movie"}}})  # fmt: skip
        self.assertEqual(other["probe"], {})
        self.assertEqual(upload.fill(other["submit"]["fields"]["tmdb"], {"tmdb_id": 5}), "5")
        with self.assertRaisesRegex(upload.UploadError, "tmdb_id"):
            upload.fill("{tmdb_id}", {"tmdb_id": ""})

    def test_multipart_answer_with_a_torrent(self):
        boundary = "b0"
        raw = (
            f"--{boundary}\r\nContent-Type: application/json\r\n\r\n"
            '{"ok": true, "code": "ACCEPTED", "torrent_id": 3}\r\n'
            f"--{boundary}\r\nContent-Type: application/x-bittorrent\r\n"
            'Content-Disposition: attachment; filename="x.torrent"\r\n\r\n'
            "d4:infod4:name1:xee\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        body, torrent = upload.parse_answer(raw, f"multipart/mixed; boundary={boundary}")
        self.assertEqual((body["code"], torrent), ("ACCEPTED", b"d4:infod4:name1:xee"))

    def test_rate(self):
        self.cfg.upload_api["limits"]["per_hour"] = 2
        upload._attempt(self.cfg)
        self.assertEqual(upload.rate_state(self.cfg)["wait_s"], 0)
        upload._attempt(self.cfg)
        self.assertGreater(upload.rate_state(self.cfg)["wait_s"], 3500)
        self.assertEqual(upload.retry_after("120"), 120)
        self.assertEqual(upload.retry_after(None), 3600)


@mock.patch.object(nfo, "describe", lambda *a: DESCRIBED)
class Flow(Base):
    def candidates(self):
        return {f["index"]: f for f in upload.candidates(self.cfg, self.snapshot)}

    def fake_check(self, results, tmdb_found=({"id": 5, "title": "Some Film", "year": "2019"},)):
        client = mock.Mock()
        client.search.side_effect = lambda q, i: results
        tracks = [{"@type": "Audio", "Language": "fr"}, {"@type": "Audio", "Language": "en"}]
        self.cfg.tmdb_api_key = "k"
        with (
            mock.patch.object(upload, "_indexer", return_value=(client, {"id": 1, "imdb": False, "key": KEY})),
            mock.patch.object(upload.tmdb, "search", return_value=list(tmdb_found)),
            mock.patch.object(upload.tmdb, "movie", return_value=IDENT),
            mock.patch.object(nfo, "run", return_value=(tracks, "")),
        ):  # fmt: skip
            return upload.check(self.cfg, self.snapshot, 0), client

    def test_candidates_are_films_missing_there(self):
        found = self.candidates()
        self.assertEqual(list(found), [0])  # not the episode, not the one already there
        self.assertEqual((found[0]["language"], found[0]["group"], found[0]["seeds"]), ("MULTI", "GRP", 12))

    def test_check_blocks_a_release_already_there(self):
        result, client = self.fake_check([{"title": "Some.Film.2019.MULTi.1080p.BluRay.x264-GRP.FRENCH", "size": SIZE}])
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(client.search.call_args_list[0].args[0], "Some Film 2019 GRP")
        self.assertEqual(result["languages"]["group"], "MULTI")
        self.assertEqual(result["tmdb"][0]["url"], "https://www.themoviedb.org/movie/5")
        self.assertEqual(self.candidates()[0]["check"]["verdict"], "blocked")

    def test_check_warns_and_clears(self):
        other = {"title": "Some.Film.2019.1080p.WEB.H264-ABC", "size": SIZE * 3}
        self.assertEqual(self.fake_check([other])[0]["verdict"], "warn")
        self.assertEqual(
            self.fake_check([{"title": "Some.Film.2019.2160p.x265-ABC", "size": SIZE * 9}])[0]["verdict"], "clear"
        )
        self.assertEqual(self.fake_check([], tmdb_found=())[0]["verdict"], "warn")  # TMDB knows no such film

    def test_keep_the_trackers_torrent_for_review(self):
        same = {
            "title": "Some.Film.2019.MULTi.1080p.BluRay.x264-GRP.FRENCH",
            "size": SIZE,
            "downloadUrl": "http://p/dl/1",
        }
        result, _ = self.fake_check([same])
        cid = result["matches"][0]["candidate"]
        self.assertTrue(cid)
        files = [{"length": SIZE, "path": [FILM]}, {"length": 10, "path": ["film.nfo"]}]
        info = {"name": "Some.Film", "piece length": 16, "pieces": b"x" * 20, "files": files, "private": 1}
        data = tf.encode({"announce": ANNOUNCE, "info": info})
        with mock.patch.object(upload.ProwlarrClient, "download", return_value=data):
            kept = upload.keep(self.cfg, self.snapshot, cid, "verify: no file of this size")
        base = os.path.join(self.cfg.output_dir, "review", kept["infohash"])
        self.assertEqual(os.stat(base + ".torrent").st_mode & 0o777, 0o600)
        with open(base + ".json", encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["entry"]["files"], [{"path": FILM, "length": SIZE}])
        self.assertEqual(len(record["torrent"]["files"]), 2)
        self.assertNotIn(PASSKEY, json.dumps(record))
        self.assertEqual(upload.reviews(self.cfg)[0]["reason"], "verify: no file of this size")
        with self.assertRaises(upload.UploadError):
            upload.keep(self.cfg, self.snapshot, "expired", "")

    def test_sending_is_gated(self):
        with self.assertRaisesRegex(upload.UploadError, "send = false"):
            upload.start(self.cfg, FakeQbt, self.snapshot, [0])
        self.cfg.upload_send = True
        with self.assertRaisesRegex(upload.UploadError, "check it first"):
            upload.start(self.cfg, FakeQbt, self.snapshot, [0])
        self.fake_check([{"title": "Some.Film.2019.MULTi.1080p.BluRay.x264-GRP", "size": SIZE}])
        with self.assertRaisesRegex(upload.UploadError, "blocked"):
            upload.start(self.cfg, FakeQbt, self.snapshot, [0])

    def wait(self, job_id):
        for _ in range(300):
            job = next(j for j in actions.load_jobs(self.cfg) if j["id"] == job_id)
            if job["status"] in actions.FINISHED:
                return job
            time.sleep(0.02)
        raise AssertionError("upload did not finish")

    def test_upload_then_seed(self):
        self.cfg.upload_send = True
        self.fake_check([])
        qbt = FakeQbt()
        with mock.patch.object(upload, "WAIT_POLL_S", 0.02):
            job = self.wait(upload.start(self.cfg, lambda: qbt, self.snapshot, [0])[0]["id"])
        self.assertEqual(job["status"], "done", job["note"])
        sent = FakeApi.seen[-1]
        self.assertEqual(sent["auth"], f"Bearer {PASSKEY}")
        self.assertEqual((sent["category_id"], sent["presentation_type"]), (b"2100", b"html_auto"))
        sent_nfo = sent["nfo_file"].decode()
        self.assertIn("automated through seedbox", sent_nfo)
        self.assertTrue(sent_nfo.rstrip().endswith("Complete name : x"))  # MediaInfo report last
        self.assertEqual(tf.parse(sent["torrent_file"])["name"], FILM)
        self.assertEqual(tf._decode(sent["torrent_file"], 0)[0][b"announce"], ANNOUNCE.encode())
        self.assertEqual(qbt.added[0][0], job["hash"])  # the same torrent, seeded
        with open(os.path.join(self.cfg.output_dir, "uploads.jsonl"), encoding="utf-8") as handle:
            logged = handle.read()
        self.assertIn('"code": "ACCEPTED"', logged)
        for text in (logged, json.dumps(actions.load_jobs(self.cfg))):
            self.assertNotIn(PASSKEY, text)

    def test_duplicate_goes_to_review(self):
        self.cfg.upload_send = True
        self.fake_check([])
        FakeApi.answer = (
            409,
            {"ok": False, "code": "DUPLICATE", "message": "Doublon", "candidates": [{"name": "Some.Film.X"}]},
        )
        qbt = FakeQbt()
        with mock.patch.object(upload, "WAIT_POLL_S", 0.02):
            job = self.wait(upload.start(self.cfg, lambda: qbt, self.snapshot, [0])[0]["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("manual review", job["note"])
        self.assertIn("Some.Film.X", job["note"])
        self.assertEqual(qbt.added, [])
        self.assertEqual(len(FakeApi.seen), 1)  # never retried


class Page(Base):
    def setUp(self):
        super().setUp()
        handler = type("H", (cli._Handler,), {"cfg": self.cfg, "service": cli._Service(self.cfg)})
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        served = self.cfg.output_dir  # as `seedbox run` serves it
        self.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: handler(*a, directory=served))
        threading.Thread(target=self.web.serve_forever, daemon=True).start()

    def tearDown(self):
        self.web.shutdown()
        self.web.server_close()
        super().tearDown()

    def call(self, method, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.server_port)
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()

    def test_api_only_when_configured(self):
        self.assertEqual(self.call("GET", "/upload")[0], 302)  # now a section of library.html
        write(os.path.join(self.cfg.output_dir, "review", "x.torrent"), b"d8:announce6:secrete")
        for method in ("GET", "HEAD"):
            self.assertEqual(self.call(method, "/review/x.torrent")[0], 404)
        self.cfg.upload_api = {}
        self.assertEqual(self.call("GET", "/api/upload")[0], 404)


if __name__ == "__main__":
    unittest.main()
