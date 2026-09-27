import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from seedbox import collect, config, dashboard, library, report, schedule, trackers


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
        unmatched, count = collect.correlate(self.cfg, qbt, entries, index)
        by = {e.name: e for e in entries}
        self.assertEqual(count, 4)
        self.assertEqual(by["collection/Movie.A"].status, "seeded")
        self.assertEqual(by["collection/Movie.A"].trackers, ["alpha.example", "beta.example"])
        self.assertEqual(by["Movie.B"].status, "incomplete")  # matched through the path fallback
        self.assertEqual(by["loose.mp4"].status, "orphan")
        self.assertEqual(by["Show.X/S01"].uploaded + by["Show.X/S02"].uploaded, 600)
        self.assertEqual([u["name"] for u in unmatched], ["Elsewhere"])

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
                    "name": "</script><b>x",
                    "path": "/secret/path",
                    "status": "orphan",
                    "trackers": [],
                    "torrents": 0,
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
        self.assertNotIn("/secret/path", page)
        with open(os.path.join(out, "snapshot.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["entries"][0]["path"], "/secret/path")

    def test_dashboard_embed_roundtrip(self):
        value = {"x": "</script><!--"}
        self.assertEqual(json.loads(dashboard._embed(value)), value)


if __name__ == "__main__":
    unittest.main()
