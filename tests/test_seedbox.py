import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import seedbox
from seedbox import collect, config, dashboard, library, qbittorrent, report, schedule, status, trackers, ui
from seedbox.api import ApiError


def touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)


class FakeQbt:
    """Stands in for QbtClient: torrents keyed by hash."""

    def __init__(self, torrents, trackers, files):
        self._torrents, self._trackers, self._files = torrents, trackers, files

    def torrents(self):
        return self._torrents

    def trackers(self, h):
        return [{"url": u} for u in self._trackers.get(h, [])]

    def files(self, h):
        return [{"name": n} for n in self._files.get(h, [])]


class TrackerKeys(unittest.TestCase):
    def test_domain(self):
        self.assertEqual(trackers.domain("tracker.example.org"), "example.org")
        self.assertEqual(trackers.domain("a.b.example.co.uk"), "example.co.uk")
        self.assertEqual(trackers.domain("example.org"), "example.org")
        self.assertEqual(trackers.domain("10.0.0.1"), "10.0.0.1")

    def test_pseudo_trackers_ignored(self):
        self.assertIsNone(trackers.key_for_url("** [DHT] **"))
        self.assertIsNone(trackers.key_for_url(""))

    def test_aliases(self):
        aliases = {"announce.other.net": "Tracker A", "example.org": "Tracker B"}
        self.assertEqual(trackers.key_for_url("https://announce.other.net/x/announce", aliases), "Tracker A")
        self.assertEqual(trackers.key_for_url("https://t.example.org/announce", aliases), "Tracker B")


class ConfigLoading(unittest.TestCase):
    def test_env_overrides_file_and_secret_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "seedbox.toml")
            with open(conf, "w") as handle:
                handle.write(
                    '[library]\nroots = ["/a"]\n[qbittorrent]\nurl = "http://q:1/"\n'
                    '[qbittorrent.path_map]\n"/downloads" = "/media/dl"\n"/downloads/x" = "/other"\n'
                )
            secret = os.path.join(tmp, "pw")
            with open(secret, "w") as handle:
                handle.write("s3cret\n")
            env = {"SEEDBOX_ROOTS": "/m1,/m2/", "SEEDBOX_QBT_PASSWORD_FILE": secret}
            with mock.patch.dict(os.environ, env, clear=True):
                cfg = config.load(conf)
        self.assertEqual(cfg.roots, ["/m1", "/m2"])
        self.assertEqual(cfg.qbt_url, "http://q:1")
        self.assertEqual(cfg.qbt_password, "s3cret")
        self.assertEqual(config.map_path(cfg, "/downloads/x/f.mkv"), "/other/f.mkv")
        self.assertEqual(config.map_path(cfg, "/downloads/y"), "/media/dl/y")
        self.assertEqual(config.map_path(cfg, "/downloadsz"), "/downloadsz")

    def test_empty_secret_file_keeps_file_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "seedbox.toml")
            with open(conf, "w") as handle:
                handle.write('[library]\nroots = ["/a"]\n[qbittorrent]\npassword = "from-file"\n')
            empty = os.path.join(tmp, "empty")
            open(empty, "w").close()
            with mock.patch.dict(os.environ, {"SEEDBOX_QBT_PASSWORD_FILE": empty}, clear=True):
                self.assertEqual(config.load(conf).qbt_password, "from-file")

    def test_missing_roots(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(config, "DEFAULT_PATHS", ()),
            self.assertRaises(config.ConfigError),
        ):
            config.load()

    def test_prowlarr_needs_key(self):
        env = {"SEEDBOX_ROOTS": "/a", "SEEDBOX_PROWLARR_URL": "http://p"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(config, "DEFAULT_PATHS", ()),
            self.assertRaises(config.ConfigError),
        ):
            config.load()


class QbtLogin(unittest.TestCase):
    """Login answers of old ("200 Ok." / "200 Fails.") and recent (204 / 401) qBittorrent."""

    def login(self, status, body, cookie="SID=abc; HttpOnly; path=/"):
        headers = mock.Mock()
        headers.get_all.return_value = [cookie]
        with mock.patch.object(qbittorrent, "request", return_value=(status, body, headers)):
            return qbittorrent.QbtClient("http://q", "admin", "pw")

    def test_success(self):
        self.assertEqual(self.login(200, "Ok.").cookie, "SID=abc")
        self.assertEqual(self.login(204, "").cookie, "SID=abc")

    def test_cookie_name_since_5(self):
        client = self.login(204, "", "QBT_SID_8090=abc; HttpOnly; SameSite=Strict; path=/")
        self.assertEqual(client.cookie, "QBT_SID_8090=abc")

    def test_failure(self):
        for code, body in ((200, "Fails."), (401, "Unauthorized"), (403, "")):
            with self.assertRaises(ApiError):
                self.login(code, body)


class StartupCollection(unittest.TestCase):
    """`seedbox run` collects at start only when it is actually needed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with (
            mock.patch.dict(os.environ, {"SEEDBOX_ROOTS": "/a", "SEEDBOX_OUTPUT_DIR": self.tmp.name}, clear=True),
            mock.patch.object(config, "DEFAULT_PATHS", ()),
        ):
            self.cfg = config.load()
        self.cfg.schedule = "sun 04:00"

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, when, fingerprint=None):
        generated = when.astimezone().isoformat()  # local naive -> aware, like a real run
        with open(os.path.join(self.tmp.name, "snapshot.json"), "w") as handle:
            json.dump({"generated": generated, "config": fingerprint or config.fingerprint(self.cfg)}, handle)

    def decide(self, now):
        from seedbox import cli

        return cli.startup_collection(self.cfg, now)[0]

    def test_rules(self):
        wednesday = datetime(2026, 9, 30, 10, 0)
        self.assertTrue(self.decide(wednesday))  # no snapshot
        self.snapshot(datetime(2026, 9, 27, 4, 1))  # Sunday's run happened
        self.assertFalse(self.decide(wednesday))
        self.assertTrue(self.decide(datetime(2026, 10, 4, 5, 0)))  # next Sunday's run was missed
        self.snapshot(datetime(2026, 9, 26, 12, 0))  # before Sunday: missed
        self.assertTrue(self.decide(wednesday))
        self.snapshot(datetime(2026, 9, 29, 12, 0), fingerprint="other")
        self.assertTrue(self.decide(wednesday))  # config changed

    def test_interval_mode(self):
        self.cfg.schedule = ""
        self.cfg.interval_hours = 24
        self.snapshot(datetime(2026, 9, 30, 0, 0))
        self.assertFalse(self.decide(datetime(2026, 9, 30, 10, 0)))
        self.assertTrue(self.decide(datetime(2026, 10, 1, 0, 0)))


class SecretsFile(unittest.TestCase):
    def _load(self, mode, body):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "seedbox.toml")
            with open(conf, "w") as handle:
                handle.write('[library]\nroots = ["/a"]\n' + body)
            os.chmod(conf, mode)
            with mock.patch.dict(os.environ, {}, clear=True):
                return config.load(conf)

    def test_readable_secrets_warn(self):
        self.assertTrue(self._load(0o644, '[qbittorrent]\npassword = "x"\n').warnings)
        self.assertFalse(self._load(0o600, '[qbittorrent]\npassword = "x"\n').warnings)
        self.assertFalse(self._load(0o644, "").warnings)

    def test_bad_schedule(self):
        with self.assertRaises(config.ConfigError):
            self._load(0o600, '[service]\nschedule = "sunday 4h"\n')


class Schedule(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(schedule.parse("04:00"), (set(range(7)), 4, 0))
        self.assertEqual(schedule.parse("Sun 4:30"), ({6}, 4, 30))
        self.assertEqual(schedule.parse("mon,thu 23:59")[0], {0, 3})
        for bad in ("24:00", "sun", "xyz 04:00", "04:00 sun", "4h"):
            with self.assertRaises(ValueError):
                schedule.parse(bad)

    def test_prev_run(self):
        wednesday = datetime(2026, 9, 30, 10, 0)
        self.assertEqual(schedule.prev_run(schedule.parse("sun 04:00"), wednesday), datetime(2026, 9, 27, 4, 0))
        self.assertEqual(schedule.prev_run(schedule.parse("10:00"), wednesday), wednesday)
        self.assertEqual(schedule.prev_run(schedule.parse("wed 11:00"), wednesday), datetime(2026, 9, 23, 11, 0))

    def test_next_run(self):
        wednesday = datetime(2026, 9, 30, 10, 0)
        self.assertEqual(schedule.next_run(schedule.parse("11:00"), wednesday), datetime(2026, 9, 30, 11, 0))
        self.assertEqual(schedule.next_run(schedule.parse("10:00"), wednesday), datetime(2026, 10, 1, 10, 0))
        self.assertEqual(schedule.next_run(schedule.parse("sun 04:00"), wednesday), datetime(2026, 10, 4, 4, 0))
        self.assertEqual(schedule.next_run(schedule.parse("wed 09:00"), wednesday), datetime(2026, 10, 7, 9, 0))
        self.assertEqual(schedule.describe(schedule.parse("sun 04:00")), "sun at 04:00")


class Pipeline(unittest.TestCase):
    """Library on disk + cross-seed hardlinks + fake qBittorrent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.movies, self.shows = os.path.join(t, "movies"), os.path.join(t, "shows")
        touch(os.path.join(self.movies, "collection", "Movie.A", "a.mkv"), 100)
        touch(os.path.join(self.movies, "collection", "Movie.A", "Subs", "a.srt"), 1)
        touch(os.path.join(self.movies, "Movie.B", "b.mkv"), 200)
        touch(os.path.join(self.movies, "loose.mp4"), 50)
        touch(os.path.join(self.shows, "Show.X", "S01", "e1.mkv"), 30)
        touch(os.path.join(self.shows, "Show.X", "S02", "e1.mkv"), 30)
        touch(os.path.join(self.movies, ".cross-seed", "ignored.mkv"))
        # Cross-seed copy of Movie.A under another name, seen by qBittorrent as /dl.
        self.dl = os.path.join(t, "dl")
        os.makedirs(os.path.join(self.dl, "Other.Name"))
        os.link(
            os.path.join(self.movies, "collection", "Movie.A", "a.mkv"), os.path.join(self.dl, "Other.Name", "x.mkv")
        )
        with (
            mock.patch.dict(
                os.environ,
                {"SEEDBOX_ROOTS": f"{self.movies},{self.shows}", "SEEDBOX_OUTPUT_DIR": os.path.join(t, "out")},
                clear=True,
            ),
            mock.patch.object(config, "DEFAULT_PATHS", ()),
        ):
            self.cfg = config.load()
        self.cfg.path_map = {"/dl": self.dl}

    def tearDown(self):
        self.tmp.cleanup()

    def test_library_entries(self):
        entries, index = library.build(self.cfg)
        names = sorted(e.name for e in entries)
        self.assertEqual(names, ["Movie.B", "Show.X/S01", "Show.X/S02", "collection/Movie.A", "loose.mp4"])
        movie_a = next(e for e in entries if e.name == "collection/Movie.A")
        self.assertEqual((movie_a.size, movie_a.files), (101, 2))
        self.assertEqual(len(index), 6)

    def test_correlate_by_inode_and_path(self):
        entries, index = library.build(self.cfg)
        qbt = FakeQbt(
            torrents=[
                {"hash": "a", "name": "Other.Name", "save_path": "/dl", "progress": 1, "uploaded": 1000},
                {
                    "hash": "b",
                    "name": "Movie.B",
                    "save_path": self.movies,
                    "progress": 0.99,
                    "uploaded": 0,
                    "content_path": os.path.join(self.movies, "Movie.B"),
                },
                {"hash": "p", "name": "Pack", "save_path": self.shows, "progress": 1, "uploaded": 600},
                {
                    "hash": "z",
                    "name": "Elsewhere",
                    "save_path": "/nowhere",
                    "progress": 1,
                    "uploaded": 0,
                    "content_path": "/nowhere/Elsewhere",
                },
            ],
            trackers={
                "a": ["** [DHT] **", "https://t.alpha.example/announce", "https://t2.beta.example/a"],
                "b": ["https://alpha.example/announce"],
                "p": ["https://t.alpha.example/announce"],
            },
            files={
                "a": ["Other.Name/x.mkv"],
                "b": ["Movie.B/missing-part.mkv"],
                "p": ["Show.X/S01/e1.mkv", "Show.X/S02/e1.mkv"],
            },
        )
        records, unmatched = collect.correlate(self.cfg, qbt, entries, index)
        by = {e.name: e for e in entries}
        self.assertEqual(len(records), 4)
        self.assertEqual(by["collection/Movie.A"].status, "seeded")
        self.assertEqual(by["collection/Movie.A"].trackers, ["alpha.example", "beta.example"])
        self.assertEqual(by["Movie.B"].status, "incomplete")  # matched through the path fallback
        self.assertEqual(by["loose.mp4"].status, "orphan")
        self.assertEqual(by["Show.X/S01"].uploaded + by["Show.X/S02"].uploaded, 600)
        self.assertEqual([(u["name"], u["reason"]) for u in unmatched], [("Elsewhere", "missing")])

        indexers = {
            "alpha.example": {"name": "Alpha", "enabled": True, "failing": False, "grabs": 3},
            "gamma.example": {"name": "Gamma", "enabled": True, "failing": False, "grabs": 0},
        }
        rows = {r["key"]: r for r in collect.tracker_table(entries, indexers)}
        self.assertEqual(rows["alpha.example"]["entries"], 4)
        self.assertEqual(rows["gamma.example"]["entries"], 0)
        self.assertFalse(rows["beta.example"]["in_prowlarr"])

    def test_report_files(self):
        snap = {
            "generated": "2026-01-01T00:00:00+00:00",
            "duration_s": 1,
            "summary": {
                "entries": 1,
                "seeded": 0,
                "incomplete": 0,
                "orphan": 1,
                "coverage_pct": 0.0,
                "size": 1,
                "uploaded": 0,
                "torrents": 0,
                "unmatched_torrents": 0,
                "prowlarr": False,
            },
            "trackers": [],
            "unmatched": [],
            "warnings": [],
            "entries": [
                {
                    "category": "m",
                    "coverage": "none",
                    "issues": [],
                    "name": "</script><b>x",
                    "path": "/secret/path",
                    "status": "orphan",
                    "trackers": [],
                    "torrents": [],
                    "size": 1,
                    "files": 1,
                    "uploaded": 0,
                }
            ],
        }
        report.write(self.cfg, snap)
        report.write(self.cfg, snap)
        out = self.cfg.output_dir
        self.assertEqual(len(report.read_history(os.path.join(out, "history.csv"), ",")), 2)
        with open(os.path.join(out, "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("</script><b>", page)
        # Every placeholder filled, the rest of the page intact.
        placeholders = r"@(title|page|crumb|favicon|css|flag|nav|check|logo|range|version|data|history|js|upjs)@"
        self.assertEqual(re.findall(placeholders, page), [])
        self.assertIn('<nav class="rail"', page)
        # Two pages: the home page, and the library page (Activity on both).
        with open(os.path.join(out, "library.html"), encoding="utf-8") as handle:
            library = handle.read()
        self.assertEqual(re.findall(placeholders, library), [])
        for section in ("attention", "overview", "system", "logs-sec"):
            self.assertIn(f'<section id="{section}">', page)
            self.assertNotIn(f'<section id="{section}">', library)
        for section in ("library", "duplicates"):
            self.assertIn(f'<section id="{section}">', library)
            self.assertNotIn(f'<section id="{section}">', page)
        self.assertIn('<section id="activity">', page)
        self.assertIn('<section id="activity">', library)
        self.assertIn('<section id="upload" hidden>', library)
        self.assertIn('href="library.html"', page)
        # Rail: the page's sections, a separator, then the other page; each page its own header.
        self.assertLess(page.index('href="#logs-sec"'), page.index('class="rail-sep"'))
        self.assertLess(page.index('class="rail-sep"'), page.index('href="library.html"'))
        self.assertIn(">Back Home</a>", library)
        self.assertIn("<h1>Library Management plane</h1>", library)
        self.assertIn("<h1>Seedbox control plane</h1>", page)
        # Disk I/O and transfer tiles on the home page only.
        self.assertIn('id="a-io"', page)
        self.assertNotIn('id="a-io"', library)
        self.assertNotIn('id="a-transfer"', library)
        self.assertIn("family=Inconsolata:wght@400", page)
        self.assertNotIn("/secret/path", page)
        with open(os.path.join(out, "snapshot.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["entries"][0]["path"], "/secret/path")
        # Rebuilt without collecting (after an upgrade): same history, current version in the header.
        os.remove(os.path.join(out, "index.html"))
        self.assertTrue(report.rerender(self.cfg))
        with open(os.path.join(out, "index.html"), encoding="utf-8") as handle:
            self.assertIn(f'id="page-version" title="Version of this page">v{seedbox.__version__}<', handle.read())
        self.assertEqual(len(report.read_history(os.path.join(out, "history.csv"), ",")), 2)

    def test_dashboard_embed_roundtrip(self):
        value = {"x": "</script><!--"}
        self.assertEqual(json.loads(dashboard._embed(value)), value)


class FakeQbtStatus:
    def version(self):
        return "v5.2.3"

    def torrents(self):
        return [
            {"state": "stalledUP", "name": "seeding", "size": 10, "progress": 1},
            {"state": "uploading", "name": "upload", "size": 10, "progress": 1, "upspeed": 2048},
            {"state": "checkingDL", "name": "queued check", "size": 100, "progress": 0, "added_on": 2},
            {"state": "checkingDL", "name": "running check", "size": 100, "progress": 0.75, "added_on": 1},
            {"state": "moving", "name": "move", "size": 5, "progress": 1},
            {"state": "error", "name": "broken", "category": "cross-seed-link", "size": 7, "progress": 0},
        ]

    def maindata(self):
        return {"server_state": {"queued_io_jobs": 14, "average_time_queue": 1136, "up_info_speed": 1024}}

    def preferences(self):
        return {"max_active_uploads": 20, "disk_io_type": 2, "web_ui_password": "never shown"}

    def log(self):
        return [
            {"timestamp": 1, "type": 1, "message": "Torrent added"},
            {"timestamp": 2, "type": 2, "message": "Moved torrent successfully. Torrent: x"},
            {"timestamp": 3, "type": 4, "message": "File error alert. Torrent: y"},
        ]


class Ratios(unittest.TestCase):
    def test_ratio_table(self):
        records = [
            {"tracker": "a.example", "uploaded": 30, "downloaded": 10},
            {"tracker": "a.example", "uploaded": 10, "downloaded": 0},
            {"tracker": "public.example", "uploaded": 5, "downloaded": 5},
            {"tracker": "", "uploaded": 1, "downloaded": 0},
        ]
        rows = collect.ratio_table(records, {"a.example", "b.example"}, {"a.example": "Alpha", "b.example": "Beta"})
        self.assertEqual([(r["name"], r["up"], r["down"], r["torrents"]) for r in rows],
                         [("Alpha", 40, 10, 2), ("Beta", 0, 0, 0), ("Other trackers", 6, 5, 2)])  # fmt: skip

    def test_upload_windows(self):
        day = report.DAY
        hist = [
            {"date": "2026-09-01T00:00:00+00:00", "key": "a", "uploaded": "100"},
            {"date": "2026-09-20T00:00:00+00:00", "key": "a", "uploaded": "400"},
            {"date": "2026-09-27T00:00:00+00:00", "key": "a", "uploaded": "700"},
        ]
        now = datetime.fromisoformat("2026-09-29T00:00:00+00:00").timestamp()
        self.assertEqual(report.upload_since(hist, "a", 1000, now, 7)[0], 600)  # from 09-20
        up, since = report.upload_since(hist, "a", 1000, now, 60)  # history shorter: from the oldest row
        self.assertEqual((up, since[:10]), (900, "2026-09-01"))
        self.assertEqual(report.upload_since(hist, "a", 50, now, 7)[0], 0)  # torrents removed: never negative
        self.assertEqual(report.upload_since(hist, "b", 5, now, 7), (None, None))
        self.assertEqual(day, 86400)


class Unreadable(unittest.TestCase):
    def test_mode_000_once_per_inode(self):
        from seedbox import cli

        with tempfile.TemporaryDirectory() as tmp:
            lib, links = os.path.join(tmp, "films"), os.path.join(tmp, ".cross-seed")
            os.makedirs(lib)
            os.makedirs(links)
            for name in ("Locked.mkv", "Fine.mkv"):
                with open(os.path.join(lib, name), "w") as f:
                    f.write("x")
            os.link(os.path.join(lib, "Locked.mkv"), os.path.join(links, "Locked.mkv"))
            os.chmod(os.path.join(lib, "Locked.mkv"), 0)
            try:
                # A hardlink is the same file: reported once.
                self.assertEqual(cli.unreadable_files([lib, links]), [os.path.join(lib, "Locked.mkv")])
            finally:
                os.chmod(os.path.join(lib, "Locked.mkv"), 0o644)


class Status(unittest.TestCase):
    def test_gather(self):
        st = status.gather(FakeQbtStatus())
        self.assertEqual(st["torrents"], 6)
        self.assertEqual(st["states"]["checkingDL"], 2)
        # Moves first, then checks (running one first), then errors; plain seeding left out.
        self.assertEqual([b["name"] for b in st["busy"]], ["move", "running check", "queued check", "broken"])
        self.assertEqual(st["checking"], {"count": 2, "bytes": 125, "running": 1})
        # Who hits the disk: a check waiting its turn reads nothing yet.
        self.assertEqual(
            [(s["why"], s["name"]) for s in st["io_sources"]],
            [("move", "move"), ("recheck", "running check"), ("upload", "upload")],
        )
        # Errors kept apart from moves and removals.
        self.assertEqual([e["message"] for e in st["errors"]], ["File error alert. Torrent: y"])
        self.assertEqual(st["io"]["queued_io_jobs"], 14)
        self.assertEqual(st["settings"], {"max_active_uploads": 20, "disk_io_type": 2})
        self.assertEqual([e["level"] for e in st["events"]], ["info", "warn"])

    def test_errors_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"SEEDBOX_ROOTS": tmp, "SEEDBOX_OUTPUT_DIR": tmp}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config, "DEFAULT_PATHS", ()):
                cfg = config.load()
            self.enterContext(mock.patch.object(status.actions, "refresh", return_value=[]))
            self.assertEqual(len(status.gather(FakeQbtStatus(), cfg=cfg)["errors"]), 1)
            status.clear_errors(cfg, now=3)
            st = status.gather(FakeQbtStatus(), cfg=cfg)
            # Logged at or before the clear: hidden; the log itself is not touched.
            self.assertEqual((st["errors"], st["errors_cleared"][:4]), ([], "1970"))

    def test_show(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status.show(status.gather(FakeQbtStatus()), ui)
        text = out.getvalue()
        self.assertIn("running check", text)
        self.assertIn("1136 ms", text)
        self.assertIn("max_active_uploads=20", text)


if __name__ == "__main__":
    unittest.main()
