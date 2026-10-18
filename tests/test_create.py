""".torrent creation: bencode, pieces, content of an entry, tracker setup, jobs, seeding, HTTP."""

import hashlib
import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from seedbox import actions, cli, config, create, nfo
from seedbox import torrentfile as tf

KEY = "tracker-a.example"
ANNOUNCE = "https://announce.tracker-a.example/SECRETPASSKEY/announce"


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


def load_cfg(roots, out):
    env = {"SEEDBOX_ROOTS": ",".join(roots), "SEEDBOX_OUTPUT_DIR": out}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config, "DEFAULT_PATHS", ()):
        cfg = config.load()
    cfg.actions = True
    return cfg


def existing_torrent(source):
    info = {"name": "Other", "piece length": 16, "pieces": b"x" * 20, "length": 10, "private": 1}
    if source:
        info["source"] = source
    return tf.encode({"announce": ANNOUNCE, "info": info})


class FakeQbt:
    def __init__(self, source="SRC"):
        self.live, self.added, self.source = [], [], source

    def torrents(self):
        return self.live

    def trackers(self, h):
        return [{"url": "** [DHT] **"}, {"url": ANNOUNCE}]

    def export(self, h):
        return existing_torrent(self.source)

    def categories(self):
        return {}

    def add_torrent(self, content, save_path, category="", stopped=True, layout="NoSubfolder", skip_checking=False):
        self.added.append((tf.parse(content)["infohash"], save_path, stopped, layout, skip_checking))


def wait(cfg, job_id):
    for _ in range(200):
        job = next(j for j in actions.load_jobs(cfg) if j["id"] == job_id)
        if job["status"] in actions.FINISHED:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


class Bencode(unittest.TestCase):
    def test_round_trip_and_piece_length(self):
        data = tf.encode({"info": {"name": "N", "piece length": 16, "pieces": b"p" * 20, "length": 3}, "a": [1, "x"]})
        self.assertEqual(data[:4], b"d1:a")  # keys sorted
        self.assertEqual(tf.parse(data)["name"], "N")
        self.assertEqual(create.piece_length(10), create.MIN_PIECE)
        self.assertEqual(create.piece_length(10 * 10**9), 8 * 1024 * 1024)
        self.assertEqual(create.piece_length(10**13), create.MAX_PIECE)


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "movies")
        self.cfg = load_cfg([self.root], os.path.join(self.tmp.name, "out"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_file_ignores_sidecars(self):
        film = os.path.join(self.root, "Some.Entry.2020.mkv")
        write(film, os.urandom(300_000))
        write(os.path.join(self.root, "Some.Entry.2020.nfo"), b"notes")
        spec = create.content(self.cfg, {"path": film, "kind": "file", "size": 300_005})
        meta = tf.parse(create.build(spec, ANNOUNCE, "SRC"))
        self.assertEqual((meta["name"], meta["source"], len(meta["files"])), ("Some.Entry.2020.mkv", "SRC", 1))
        self.assertTrue(tf.verify(meta, meta["files"][0], film, count=100)["verified"])

    def test_parts_refused(self):
        film = os.path.join(self.root, "Some.Entry.CD1.avi")
        write(film, b"a" * 100)
        write(os.path.join(self.root, "Some.Entry.CD2.avi"), b"b" * 100)
        with self.assertRaises(create.CreateError):
            create.content(self.cfg, {"path": film, "kind": "file", "size": 200})

    def test_folder_skips_metadata(self):
        folder = os.path.join(self.root, "Some.Show.S01")
        episode = os.urandom(70_000)
        write(os.path.join(folder, "E01.mkv"), episode)
        write(os.path.join(folder, "Subs", "E01.srt"), b"subtitles")
        write(os.path.join(folder, "@eaDir", "thumb.jpg"), b"x")
        write(os.path.join(folder, ".DS_Store"), b"x")
        spec = create.content(self.cfg, {"path": folder, "kind": "dir"})
        self.assertEqual([p for _, p, _ in spec["files"]], [["E01.mkv"], ["Subs", "E01.srt"]])
        meta = tf.parse(create.build(spec, ANNOUNCE, ""))
        self.assertEqual([f["path"] for f in meta["files"]], ["Some.Show.S01/E01.mkv", "Some.Show.S01/Subs/E01.srt"])
        self.assertEqual(meta["source"], "")
        # Smaller than a piece: one piece over both files, in order.
        self.assertEqual(meta["pieces"], [hashlib.sha1(episode + b"subtitles").digest()])


FIELDS = {"title": "Some Entry", "year": "2020", "language": "MULTI", "resolution": "1080p",
          "video_codec": "x264", "audio_codec": "AC3"}  # fmt: skip
DESCRIBED = {"fields": {}, "missing": [], "details": {"audio": [], "subtitles": [], "files": 1}, "report": "General\n"}


@mock.patch.object(nfo, "describe", lambda *a: DESCRIBED)
class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "movies")
        self.cfg = load_cfg([self.root], os.path.join(self.tmp.name, "out"))
        self.film = os.path.join(self.root, "Some.Entry.2020.mkv")
        write(self.film, os.urandom(50_000))
        self.snapshot = {
            "summary": {"target_trackers": [KEY, "tracker-b.example"]},
            "entries": [{"path": self.film, "kind": "file", "size": 50_000, "trackers": ["tracker-b.example"]}],
            "torrents": [{"hash": "a" * 40, "tracker": KEY}],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_then_seed(self):
        qbt = FakeQbt()
        with self.assertRaises(create.CreateError):
            create.start(self.cfg, qbt, self.snapshot, 0, "tracker-b.example", FIELDS)  # already seeded there
        job = wait(self.cfg, create.start(self.cfg, qbt, self.snapshot, 0, KEY, FIELDS)["id"])
        self.assertEqual(job["status"], "done", job.get("note"))
        path = create.path_for(self.cfg, job)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertIn(b"SECRETPASSKEY", data)
        self.assertEqual(tf.parse(data)["infohash"], job["hash"])
        self.assertNotIn("SECRETPASSKEY", json.dumps(actions.load_jobs(self.cfg)))
        with open(create.path_for(self.cfg, job, "nfo"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("  TITLE      Some Entry\n", text)
        self.assertIn(" MEDIAINFO\n", text)
        with self.assertRaisesRegex(nfo.NfoError, "Year"):
            create.start(self.cfg, qbt, self.snapshot, 0, KEY, dict(FIELDS, year=" "))

        seeded = create.seed(self.cfg, qbt, job["id"])
        self.assertEqual(qbt.added, [(job["hash"], self.root, False, "Original", True)])
        self.assertEqual((seeded["action"], seeded["status"]), ("seed", "done"))
        qbt.live = [{"hash": job["hash"]}]
        with self.assertRaises(create.CreateError):
            create.seed(self.cfg, qbt, job["id"])

    def test_limit_replaces_the_oldest_and_delete(self):
        self.cfg.created_max = 2
        qbt, done = FakeQbt(), []
        for _ in range(3):
            job = wait(self.cfg, create.start(self.cfg, qbt, self.snapshot, 0, KEY, FIELDS)["id"])
            done.append(job["id"])
            time.sleep(0.02)  # distinct mtimes
        jobs = {j["id"]: j for j in actions.load_jobs(self.cfg)}
        self.assertEqual([jobs[i].get("stored") for i in done], [False, True, True])
        self.assertIn("limit reached", jobs[done[0]]["note"])
        with self.assertRaises(create.CreateError):
            create.seed(self.cfg, qbt, done[0])
        create.delete(self.cfg, done[1])
        self.assertFalse(os.path.exists(create.path_for(self.cfg, {"id": done[1]})))
        self.assertEqual(sorted(os.listdir(os.path.join(self.cfg.output_dir, "created"))),
                         [f"{done[2]}.nfo", f"{done[2]}.torrent"])  # fmt: skip
        with self.assertRaises(create.CreateError):
            create.delete(self.cfg, done[1])

    def test_cancel_while_queued(self):
        qbt = FakeQbt()
        with create._one_at_a_time:  # another hashing holds the queue
            queued = create.start(self.cfg, qbt, self.snapshot, 0, KEY, FIELDS)
            self.assertIn("queued", queued["note"])
            self.assertEqual(create.cancel(self.cfg, queued["id"])["status"], "cancelled")
            with self.assertRaises(create.CreateError):
                create.cancel(self.cfg, queued["id"])
        job = wait(self.cfg, queued["id"])
        time.sleep(0.1)  # the worker had its turn and left it cancelled
        job = next(j for j in actions.load_jobs(self.cfg) if j["id"] == queued["id"])
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse(os.path.exists(create.path_for(self.cfg, job)))
        done = wait(self.cfg, create.start(self.cfg, qbt, self.snapshot, 0, KEY, FIELDS)["id"])
        self.assertEqual(done["status"], "done")
        with self.assertRaises(create.CreateError):
            create.cancel(self.cfg, done["id"])

    def test_restart_fails_open_worker_jobs(self):
        running = actions.add_job(self.cfg, {"action": "create", "hash": "", "name": "x", "status": "running"})
        actions.add_job(self.cfg, {"action": "move", "hash": "a" * 40, "name": "y"})
        self.assertEqual(actions.interrupted(self.cfg), 1)
        by = {j["id"]: j for j in actions.load_jobs(self.cfg)}
        self.assertEqual(by[running["id"]]["status"], "failed")
        self.assertIn("restart", by[running["id"]]["note"])

    def test_file_changed_before_seeding(self):
        job = wait(self.cfg, create.start(self.cfg, FakeQbt(source=""), self.snapshot, 0, KEY, FIELDS)["id"])
        write(self.film, b"shorter")
        with self.assertRaises(create.CreateError):
            create.seed(self.cfg, FakeQbt(), job["id"])

    def test_unknown_announce(self):
        self.snapshot["torrents"] = []
        with self.assertRaises(create.CreateError):
            create.start(self.cfg, FakeQbt(), self.snapshot, 0, KEY, FIELDS)


class AddTorrent(unittest.TestCase):
    def test_files_stay_in_the_save_path(self):
        from seedbox import qbittorrent

        client = qbittorrent.QbtClient("http://qbt.example")
        with mock.patch.object(qbittorrent, "request", return_value=(200, "", {})) as req:
            client.add_torrent(b"d4:infod4:name1:xee", "/video/films", "films", stopped=True)
        body = req.call_args.args[1]
        self.assertIn(b'name="useDownloadPath"\r\n\r\nfalse', body)
        self.assertIn(b'name="savepath"\r\n\r\n/video/films', body)


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_cfg([self.tmp.name], self.tmp.name)
        write(os.path.join(self.tmp.name, "created", "abc.torrent"), b"d8:announce6:secrete")
        job = actions.add_job(self.cfg, {"action": "create", "hash": "", "name": "Some.Entry", "target": KEY})
        actions.update_job(self.cfg, job["id"], status="done")
        os.rename(os.path.join(self.tmp.name, "created", "abc.torrent"), create.path_for(self.cfg, job))
        self.job = job
        handler = type("H", (cli._Handler,), {"cfg": self.cfg, "service": cli._Service(self.cfg)})
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: handler(*a, directory=self.tmp.name))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Disposition") or "", resp.read()

    def test_created_only_through_the_api(self):
        name = f"{self.job['id']}.torrent"
        for path in (f"/created/{name}", f"/./created/{name}", f"/%63reated/{name}", "/created/"):
            self.assertEqual(self.get(path)[0], 404, path)
        status, disposition, body = self.get(f"/api/created?job={self.job['id']}")
        self.assertEqual((status, body), (200, b"d8:announce6:secrete"))
        self.assertIn(f"Some.Entry.{KEY}.torrent", disposition)
        self.assertEqual(self.get("/api/created?job=nope")[0], 404)
        write(create.path_for(self.cfg, self.job, "nfo"), b"Title : Some Entry")
        status, disposition, body = self.get(f"/api/created?job={self.job['id']}&file=nfo")
        self.assertEqual((status, body), (200, b"Title : Some Entry"))
        self.assertIn("Some.Entry.nfo", disposition)
        self.assertEqual(self.get(f"/api/created?job={self.job['id']}&file=../x")[0], 400)
        self.cfg.actions = False
        self.assertEqual(self.get(f"/api/created?job={self.job['id']}")[0], 403)


if __name__ == "__main__":
    unittest.main()
