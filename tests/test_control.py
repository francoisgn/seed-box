"""0.9 control plane: entry granularity, diagnosis, duplicates, actions, metrics, HTTP API."""

import http.client
import http.server
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from seedbox import actions, cli, collect, config, crossseed, library, metrics, titles

A = "a" * 40
B = "b" * 40
C = "c" * 40


def touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)


def load_cfg(roots, out, **extra):
    env = {"SEEDBOX_ROOTS": ",".join(roots), "SEEDBOX_OUTPUT_DIR": out}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config, "DEFAULT_PATHS", ()):
        cfg = config.load()
    for key, value in extra.items():
        setattr(cfg, key, value)
    return cfg


class Titles(unittest.TestCase):
    def test_parse(self):
        a = titles.parse("Warcraft (2016) [BD UHDR10 hybriDV8 x265 MULTi VFF THD ATMOS].mkv")
        b = titles.parse("Warcraft.2016.MULTi.1080p.BluRay.x264.AC3-ShowFr.mkv")
        self.assertEqual(a["key"], b["key"])
        self.assertEqual((a["resolution"], b["resolution"]), ("2160p", "1080p"))
        self.assertEqual(titles.parse("1917.2019.MULTi.1080p.mkv")["title"], "1917")
        self.assertEqual(titles.parse("[site.net] Finding.Dory.2016.MULTI.1080p.mkv")["key"], "finding dory|2016|")
        self.assertEqual(titles.parse("Game.of.Thrones.S08E02.1080p.mkv")["episode"], "S08E02")

    def test_episodes_and_parts(self):
        self.assertTrue(titles.is_episode("DBZ - 001 Un mysterieux guerrier.avi"))
        self.assertTrue(titles.is_episode("The.Truth.About.The.Harry.Quebert.Affair.01.FR.720p.mkv"))
        self.assertFalse(titles.is_episode("Asterix - 1967 - Asterix Le Gaulois - 720p.mkv"))
        self.assertFalse(titles.is_episode("101.Dalmatians.1961.french.avi"))
        self.assertTrue(titles.is_season_folder("season-08"))
        self.assertEqual(titles.part_key("Film.2005.XviD-CD1.avi"), titles.part_key("Film.2005.XviD-CD2.avi"))
        self.assertIsNone(titles.part_key("Film.2005.mkv"))


class Granularity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.films = os.path.join(t, "films")
        touch(os.path.join(self.films, "incoming", "Alpha.2016.1080p.mkv"), 100)
        touch(os.path.join(self.films, "incoming", "Alpha.2016.1080p.srt"), 1)
        touch(os.path.join(self.films, "incoming", "Beta.2019.720p.mkv"), 50)
        touch(os.path.join(self.films, "incoming", "Gamma.2005.XviD-CD1.avi"), 20)
        touch(os.path.join(self.films, "incoming", "Gamma.2005.XviD-CD2.avi"), 20)
        touch(os.path.join(self.films, "incoming", "Gamma.2005.XviD-sample.avi"), 1)
        touch(os.path.join(self.films, "archives", "Alpha.2016.2160p.mkv"), 300)
        touch(os.path.join(self.films, "Show", "season-01", "Show.S01E01.mkv"))
        touch(os.path.join(self.films, "Show", "season-01", "Show.S01E02.mkv"))
        touch(os.path.join(self.films, "Show", "season-01", "Show.S01E02.MULTi.mkv"))
        touch(os.path.join(self.films, "Pack.S02", "Pack.S02E01", "e.mkv"))
        touch(os.path.join(self.films, "Pack.S02", "Pack.S02E02", "e.mkv"))
        touch(os.path.join(self.films, "DBZ", "DBZ - 001 Titre.avi"))
        touch(os.path.join(self.films, "DBZ", "DBZ - 002 Titre.avi"))
        self.cfg = load_cfg([self.films], os.path.join(t, "out"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_entries(self):
        entries, _ = library.build(self.cfg)
        by = {e.name: e for e in entries}
        self.assertEqual(
            sorted(by),
            [
                "DBZ",
                "Pack.S02",
                "Show/season-01",
                "archives/Alpha.2016.2160p.mkv",
                "incoming/Alpha.2016.1080p.mkv",
                "incoming/Beta.2019.720p.mkv",
                "incoming/Gamma.2005.XviD-CD1.avi",
            ],
        )
        alpha = by["incoming/Alpha.2016.1080p.mkv"]
        self.assertEqual((alpha.kind, alpha.files, alpha.size, alpha.folder), ("file", 2, 101, "films/incoming"))
        self.assertEqual(by["incoming/Gamma.2005.XviD-CD1.avi"].files, 2)  # both parts, not the sample
        self.assertEqual(list(by["Show/season-01"].duplicate_episodes), ["S01E02"])
        self.assertTrue(by["archives/Alpha.2016.2160p.mkv"].lone)
        self.assertFalse(alpha.lone)


class FakeQbt:
    def __init__(self, torrents, files, trackers=None):
        self._torrents, self._files, self._trackers = torrents, files, trackers or {}
        self.calls = []

    def torrents(self):
        return self._torrents

    def files(self, h):
        return [dict(f) for f in self._files.get(h, [])]

    def trackers(self, h):
        return [{"url": u} for u in self._trackers.get(h, [])]

    def set_location(self, hashes, location):
        self.calls.append(("move", hashes, location))

    def delete(self, hashes, delete_files=False):
        self.calls.append(("remove", hashes, delete_files))

    def recheck(self, hashes):
        self.calls.append(("recheck", hashes))

    def start(self, hashes):
        self.calls.append(("start", hashes))

    def file_priority(self, h, ids, priority):
        self.calls.append(("prio", h, ids, priority))


class Diagnosis(unittest.TestCase):
    """A library file, three cross-seed links of it, one still named .!qB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.media = os.path.join(t, "media")
        self.films = os.path.join(self.media, "films")
        film = os.path.join(self.films, "archives", "Zoo.2016.1080p.mkv")
        touch(film, 100)
        touch(os.path.join(self.films, "archives", "Other.2010.mkv"), 10)
        links = os.path.join(self.media, ".cross-seed", "T1")
        os.makedirs(links)
        os.link(film, os.path.join(links, "Zoo.2016.1080p.mkv"))
        os.link(film, os.path.join(links, "Zoo.2016.1080p.AC3.mkv.!qB"))
        os.makedirs(os.path.join(self.media, ".cross-seed", "T2"))
        os.link(film, os.path.join(self.media, ".cross-seed", "T2", "Zoo.mkv"))
        touch(os.path.join(self.media, ".cross-seed", "T2", "Gone.mkv"))
        self.cfg = load_cfg([self.films], os.path.join(t, "out"), path_map={"/video": self.media})
        tr = "https://t1.example/announce"
        base = {
            "progress": 1,
            "uploaded": 0,
            "category": "cross-seed-link",
            "num_complete": 1,
            "added_on": 1_700_000_000,
        }
        self.qbt = FakeQbt(
            torrents=[
                {**base, "hash": A, "name": "Zoo.2016.1080p.mkv", "save_path": "/video/.cross-seed/T1", "state": "stalledUP", "tracker": tr, "num_complete": 5},
                {**base, "hash": B, "name": "Zoo.2016.1080p.AC3.mkv", "save_path": "/video/.cross-seed/T1", "state": "stalledDL", "tracker": tr, "progress": 0.999, "amount_left": 1000},
                {**base, "hash": C, "name": "Zoo.mkv", "save_path": "/video/.cross-seed/T2", "state": "stoppedDL", "tracker": "https://t2.example/a", "progress": 0},
                {**base, "hash": "d" * 40, "name": "Gone.mkv", "save_path": "/video/.cross-seed/T2", "state": "stalledUP", "tracker": "https://t2.example/a",
                 "content_path": "/video/.cross-seed/T2/Gone.mkv"},
            ],
            files={
                A: [{"name": "Zoo.2016.1080p.mkv", "progress": 1, "priority": 1}],
                B: [{"name": "Zoo.2016.1080p.AC3.mkv", "progress": 1, "priority": 1}, {"name": "Zoo.nfo", "progress": 0, "priority": 1}],
                C: [{"name": "Zoo.mkv", "progress": 0, "priority": 1}],
                "d" * 40: [{"name": "Gone.mkv", "progress": 1, "priority": 1}],
            },
        )  # fmt: skip

    def tearDown(self):
        self.tmp.cleanup()

    def test_diagnose(self):
        entries, index = library.build(self.cfg)
        records, unmatched = collect.correlate(self.cfg, self.qbt, entries, index)
        duplicates = collect.diagnose(entries, records, {"t1.example", "t2.example"})
        zoo = next(e for e in entries if e.name.endswith("Zoo.2016.1080p.mkv"))
        self.assertEqual(len(zoo.torrents), 3)  # the .!qB link matched too
        self.assertEqual(zoo.coverage, "everywhere")
        codes = sorted(i["code"] for i in zoo.issues)
        self.assertEqual(codes, ["extras", "failed_match", "same_tracker"])
        same = next(i for i in zoo.issues if i["code"] == "same_tracker")
        self.assertEqual((same["keep"], same["remove"]), (A, [B]))
        self.assertEqual([d["kind"] for d in duplicates], ["same_tracker"])
        self.assertEqual([(u["name"], u["reason"]) for u in unmatched], [("Gone.mkv", "link_only")])
        extras = next(r for r in records if r["hash"] == B)["extras"]
        self.assertEqual(extras, [1])
        timeline = collect.timeline(entries, records, [{"key": "t1.example", "name": "T1"}])
        self.assertEqual(timeline[-1]["seeded"], 1)

    def test_versions(self):
        touch(os.path.join(self.films, "incoming", "Zoo.2016.2160p.mkv"), 5)
        touch(os.path.join(self.films, "incoming", "Else.2001.mkv"), 5)
        entries, index = library.build(self.cfg)
        records, _ = collect.correlate(self.cfg, self.qbt, entries, index)
        duplicates = collect.diagnose(entries, records, set())
        versions = [d for d in duplicates if d["kind"] == "versions"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(entries[versions[0]["best"]].name, "incoming/Zoo.2016.2160p.mkv")

    def test_actions(self):
        cfg = self.cfg
        with self.assertRaises(actions.ActionError):
            actions.run(cfg, self.qbt, {"action": "recheck", "hashes": [A]})  # disabled
        cfg.actions = True
        for bad in ({"action": "nuke", "hashes": [A]}, {"action": "recheck", "hashes": ["zz"]},
                    {"action": "recheck", "hashes": ["e" * 40]}, {"action": "move", "hashes": [A], "location": "/tmp"},
                    {"action": "move", "hashes": [A], "location": os.path.join(self.media, ".cross-seed")}):  # fmt: skip
            with self.assertRaises(actions.ActionError):
                actions.run(cfg, self.qbt, bad)
        dest = os.path.join(self.films, "archives")
        jobs = actions.run(cfg, self.qbt, {"action": "move", "hashes": [A], "location": dest})
        self.assertEqual(self.qbt.calls[-1], ("move", [A], "/video/films/archives"))
        # Files of a torrent sharing its content path with another one are kept.
        self.qbt._torrents[1]["content_path"] = self.qbt._torrents[0]["content_path"] = "/video/.cross-seed/T1/x"
        with self.assertRaises(actions.ActionError):
            actions.run(cfg, self.qbt, {"action": "remove", "hashes": [A], "delete_files": True})
        actions.run(cfg, self.qbt, {"action": "remove", "hashes": [C], "delete_files": True})
        self.assertEqual(self.qbt.calls[-1], ("remove", [C], True))
        actions.run(cfg, self.qbt, {"action": "skip_extras", "hashes": [B]})
        self.assertEqual(self.qbt.calls[-1], ("prio", B, [1], 0))

        # Status read back: move done once the save path is the target, removal once gone.
        live = [dict(t) for t in self.qbt._torrents if t["hash"] != C]
        live[0]["save_path"] = "/video/films/archives"
        by = {(j["action"], j["hash"]): j["status"] for j in actions.refresh(cfg, live)}
        self.assertEqual(by[("move", A)], "done")
        self.assertEqual(by[("remove", C)], "done")
        self.assertEqual(jobs[0]["target"], "/video/films/archives")

    def test_jobs_kept(self):
        now = time.time()
        jobs = [{"id": f"d{i}", "action": "move", "hash": A, "status": "done", "submitted": now - 100 + i, "finished": now - 100 + i}
                for i in range(80)]  # fmt: skip
        jobs[0]["stored"] = True  # a created .torrent keeps its job
        jobs[1]["finished"] = now - actions.KEEP_DONE_S - 1
        jobs += [
            {"id": f"o{i}", "action": "remove", "hash": A, "status": "pending", "submitted": now} for i in range(5)
        ]
        actions._save_jobs(self.cfg, jobs)
        kept = actions.refresh(self.cfg, [{"hash": A, "state": "uploading", "save_path": "/x"}])
        ids = [j["id"] for j in kept]
        self.assertEqual(len(kept), actions.KEEP_JOBS)
        self.assertEqual(ids[:1] + ids[-5:], ["d0"] + [f"o{i}" for i in range(5)])
        self.assertEqual(ids[1], "d26")  # the newest finished fill the rest
        # Status changes are saved, and more open jobs than the limit are all kept.
        self.assertEqual({j["status"] for j in actions.refresh(self.cfg, [])[-5:]}, {"done"})
        self.assertEqual(actions.load_jobs(self.cfg)[-1]["status"], "done")
        many = [{"id": f"p{i}", "action": "move", "hash": A, "target": "/y", "status": "pending", "submitted": now}
                for i in range(70)]  # fmt: skip
        actions._save_jobs(self.cfg, jobs + many)
        live = [{"hash": A, "state": "uploading", "save_path": "/x"}]
        self.assertEqual(len([j for j in actions.refresh(self.cfg, live) if j["status"] == "pending"]), 75)

    def test_actions_refuse_library_file_deletion(self):
        self.cfg.actions = True
        library_torrent = {"hash": "f" * 40, "name": "Other", "category": "", "save_path": "/video/films/archives",
                           "content_path": "/video/films/archives/Other.2010.mkv"}  # fmt: skip
        self.qbt._torrents.append(library_torrent)
        with self.assertRaises(actions.ActionError):
            actions.run(self.cfg, self.qbt, {"action": "remove", "hashes": ["f" * 40], "delete_files": True})


class CrossSeed(unittest.TestCase):
    """Search history: opportunity vs not searched yet; moves; tracker-deleted torrents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.media = os.path.join(t, "media")
        self.films = os.path.join(self.media, "films")
        for name in ("Split.2017.mkv", "Never.2020.mkv", "Seeded.2019.mkv", "Other.2018.mkv"):
            touch(os.path.join(self.films, "archives", name))
        touch(os.path.join(self.films, "incoming", "New.2024.mkv"))
        touch(os.path.join(self.films, "incoming", "New.2024.nfo"), 1)
        self.db = os.path.join(t, "cross-seed.db")
        db = sqlite3.connect(self.db)
        db.executescript(
            """
            CREATE TABLE indexer (id integer primary key, name text, url text, active boolean, status text);
            CREATE TABLE searchee (id integer primary key, name text, first_searched integer, last_searched integer);
            CREATE TABLE timestamp (searchee_id integer, indexer_id integer, first_searched integer, last_searched integer);
            CREATE TABLE decision (id integer primary key, searchee_id integer, guid text, info_hash text, decision text,
                first_seen integer, last_seen integer);
            CREATE TABLE data (path text primary key, title text);
            INSERT INTO indexer VALUES (1, 'Alpha', 'http://p/1/api', 1, 'OK'), (2, 'Beta', 'http://p/2/api', 1, 'RATE_LIMITED');
            INSERT INTO searchee VALUES (1, 'Split.2017.mkv', null, null), (2, 'Other.2018.mkv', null, null);
            INSERT INTO timestamp VALUES (1, 1, 1, 1000), (1, 2, 1, 2000), (2, 1, 1, 3000);
            INSERT INTO decision VALUES (1, 2, 'https://beta.example/api/torrents/x/download?apikey=SECRET', 'h', 'PARTIAL_SIZE_MISMATCH', 1, 1);
            INSERT INTO data VALUES ('/video/films/archives/Split.2017.mkv', 'Split.2017.mkv'),
                ('/video/films/archives/Never.2020.mkv', 'Never.2020.mkv'), ('/video/films/archives/Other.2018.mkv', 'Other.2018.mkv');
            """
        )
        db.commit()
        db.close()
        self.cfg = load_cfg([self.films], os.path.join(t, "out"), path_map={"/video": self.media})

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_status(self):
        xs = crossseed.read(self.db, {}, {"alpha": "alpha.example", "beta": "beta.example"})
        self.assertNotIn("SECRET", repr(xs))
        entries, _ = library.build(self.cfg)
        by = {os.path.basename(e.path): e for e in entries}
        by["Seeded.2019.mkv"].trackers = ["alpha.example", "beta.example"]
        counts = collect.search_status(self.cfg, entries, xs, {"alpha.example", "beta.example"})
        self.assertEqual(by["Split.2017.mkv"].search_state, "opportunity")
        self.assertEqual(by["Other.2018.mkv"].search["beta.example"]["verdict"], "other_release")
        self.assertEqual(by["Other.2018.mkv"].search_state, "opportunity")
        self.assertEqual(by["Never.2020.mkv"].search_state, "unsearched")
        self.assertEqual(by["New.2024.mkv"].search_state, "not_indexed")
        self.assertEqual(by["Seeded.2019.mkv"].search_state, "complete")
        self.assertEqual(counts["opportunity"], 2)
        # Only another release on the one tracker it misses: not a plain opportunity.
        other = by["Other.2018.mkv"]
        other.trackers, other.search = ["alpha.example"], {}
        collect.search_status(self.cfg, [other], xs, {"alpha.example", "beta.example"})
        self.assertEqual(other.search_state, "other_release")
        self.assertEqual([i["status"] for i in xs["indexers"]], ["OK", "RATE_LIMITED"])

        # The new folder does not exist yet: qBittorrent creates it.
        self.cfg.actions = True
        qbt = FakeQbt([{"hash": A, "name": "Split", "save_path": "/video/films/archives"}], {})
        actions.run(self.cfg, qbt, {"action": "move", "hashes": [A], "location": os.path.join(self.films, "films")})
        self.assertEqual(qbt.calls[-1], ("move", [A], "/video/films/films"))
        # Neither ".." nor a symlink inside the root leads out of it.
        os.symlink(self.tmp.name, os.path.join(self.films, "escape"))
        for location in (os.path.join(self.films, "..", "out"), os.path.join(self.films, "escape", "out")):
            with self.assertRaises(actions.ActionError):
                actions.run(self.cfg, qbt, {"action": "move", "hashes": [A], "location": location})

    def test_unregistered(self):
        torrent = {"hash": A, "name": "Dupe", "state": "stalledUP", "tracker": ""}
        qbt = FakeQbt([torrent], {})
        qbt.trackers = lambda h: [
            {"url": "** [DHT] **", "status": 2, "msg": ""},
            {"url": "https://t.alpha.example/announce", "status": 4, "msg": "Unregistered torrent"},
        ]
        keys, errors = collect._torrent_trackers(qbt, torrent, {})
        self.assertEqual(keys, ["alpha.example"])
        record = collect._torrent_record(self.cfg, torrent, keys, [], errors)
        self.assertEqual([i["code"] for i in record["issues"]], ["unregistered"])
        dns = collect._torrent_record(self.cfg, torrent, keys, [], [{"tracker": "x", "msg": "Host not found"}])
        self.assertEqual([i["code"] for i in dns["issues"]], ["tracker_error"])

        # 404: deleted while the tracker works for other torrents...
        gone = collect._torrent_record(self.cfg, torrent, keys, [], [{"tracker": "alpha.example", "msg": "Not Found"}])
        ok = collect._torrent_record(self.cfg, dict(torrent, hash=B), keys, [], [])
        collect._whole_tracker_404([gone, ok])
        self.assertEqual([i["code"] for i in gone["issues"]], ["unregistered"])
        # ...but a tracker answering 404 for all of them has moved: nothing to remove.
        gone = collect._torrent_record(self.cfg, torrent, keys, [], [{"tracker": "alpha.example", "msg": "HTTP 404"}])
        collect._whole_tracker_404([gone])
        self.assertEqual([(i["code"], i["fixes"]) for i in gone["issues"]], [("tracker_error", [])])


class OrphanLinks(unittest.TestCase):
    def test_leftover_link_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "media")
            films, links = os.path.join(media, "films"), os.path.join(media, ".cross-seed", "tracker-a")
            touch(os.path.join(films, "Kept.mkv"), 5)
            touch(os.path.join(links, "Used.mkv"), 3)
            touch(os.path.join(links, "Pack", "e01.mkv"), 3)
            touch(os.path.join(links, "Alone.mkv"), 7)
            os.link(os.path.join(films, "Kept.mkv"), os.path.join(links, "Other.Name.mkv"))
            cfg = load_cfg([films], os.path.join(tmp, "out"), path_map={"/video": media})
            records = [
                {"content_path": "/video/.cross-seed/tracker-a/Used.mkv"},
                {"content_path": "/video/.cross-seed/tracker-a/Pack"},
            ]
            o = collect.orphan_links(cfg, records)
            self.assertEqual(sorted(f["path"] for f in o["files"]),
                             [".cross-seed/tracker-a/Alone.mkv", ".cross-seed/tracker-a/Other.Name.mkv"])  # fmt: skip
            # Only the file without another hardlink frees space.
            self.assertEqual((o["count"], o["bytes"]), (2, 7))
            self.assertIn('rm -f -- ".cross-seed/tracker-a/Alone.mkv"', o["script"])
            self.assertIn('find ".cross-seed" -mindepth 2 -type d -empty -delete', o["script"])


class Categories(unittest.TestCase):
    def test_check_and_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            films = os.path.join(tmp, "media", "films")
            os.makedirs(os.path.join(tmp, "media", "tmp-inc"))
            cfg = load_cfg([films], tmp, path_map={"/video": os.path.join(tmp, "media")})
            cfg.transient_dir = os.path.join(tmp, "media", "tmp-inc")
            cats = {"films": {"savePath": "/video/films/films"}, "films-disney": {"savePath": "/video/films/disney"},
                    "cross-seed-link": {"savePath": ""}}  # fmt: skip
            base = {"progress": 1, "link": False, "category": ""}
            records = [
                {**base, "hash": "1", "save_path": "/video/films/disney", "category": "films-disney"},
                {**base, "hash": "2", "save_path": "/video/films/disney"},
                {**base, "hash": "3", "save_path": "/video/films/incoming", "category": "films"},
                {**base, "hash": "4", "save_path": "/video/.cross-seed/T", "link": True, "category": "cross-seed-link"},
                {**base, "hash": "5", "save_path": "/video/.cross-seed/T", "link": True},
                {**base, "hash": "6", "save_path": "/video/tmp-inc", "progress": 0.5},
                {**base, "hash": "7", "save_path": "/video/tmp-inc"},
            ]
            check = collect.category_check(cfg, records, cats)
            by = {i["hash"]: i for i in check["issues"]}
            self.assertEqual(sorted(by), ["2", "3", "5", "7"])
            self.assertEqual((by["2"]["fix"], by["2"]["suggest"]), ("set_category", "films-disney"))
            self.assertEqual((by["3"]["status"], by["3"]["fix"]), ("warn", "apply_category"))
            self.assertEqual(by["5"]["suggest"], "cross-seed-link")
            self.assertEqual(by["7"]["status"], "warn")
            self.assertEqual(check["counts"]["ok"], 3)

            cfg.actions = True
            live = [{"hash": A, "name": "a", "save_path": "/video/films/incoming", "category": "films"},
                    {"hash": B, "name": "b", "save_path": "/video/tmp-inc", "content_path": "/video/tmp-inc/b"}]  # fmt: skip
            qbt = FakeQbt(live, {})
            qbt.categories = lambda: cats
            qbt.set_category = lambda hashes, cat: qbt.calls.append(("category", hashes, cat))
            qbt.auto_management = lambda hashes, on: qbt.calls.append(("tmm", hashes, on))
            actions.run(cfg, qbt, {"action": "apply_category", "hashes": [A], "category": "films"})
            self.assertEqual(qbt.calls[-2:], [("category", [A], "films"), ("tmm", [A], True)])
            with self.assertRaises(actions.ActionError):
                actions.run(cfg, qbt, {"action": "set_category", "hashes": [A], "category": "nope"})
            actions.run(cfg, qbt, {"action": "remove", "hashes": [B], "delete_files": True})
            self.assertEqual(qbt.calls[-1], ("remove", [B], True))


class Metrics(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(metrics.parse_cpu("cpu  100 0 50 800 40 0 10 0 0 0\ncpu0 1 2"), (1000, 800, 40))
        total, avail = metrics.parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
        self.assertEqual((total, avail), (1024000, 256000))
        disks = metrics.parse_diskstats(
            "   8       0 sda 10 0 100 0 5 0 50 0 0 700 0\n"
            "   8       1 sda1 10 0 100 0 5 0 50 0 0 700 0\n"
            "   9       2 md2 1 0 1 0 1 0 1 0 0 1 0\n"
        )
        self.assertEqual(disks, {"sda": (100, 50, 700)})

    def test_store_and_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_cfg([tmp], tmp, metrics_interval=300)
            now = int(time.time())
            for i in range(600):
                metrics.append(cfg, {"t": now - 600 * 60 + i * 60 + 1, "cpu_pct": 10.0})
            rows = metrics.series(cfg, hours=10, buckets=100)
            self.assertLessEqual(len(rows), 101)
            self.assertEqual(rows[0]["cpu_pct"], 10.0)
            self.assertTrue(metrics.volumes([tmp]))


class HttpApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = load_cfg([self.tmp.name], self.tmp.name, actions=True)
        handler = type("H", (cli._Handler,), {"cfg": cfg, "service": cli._Service(cfg)})
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: handler(*a, directory=self.tmp.name))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, path, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("POST", path, body=json.dumps({"action": "recheck", "hashes": [A]}), headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())

    def test_post_guards(self):
        self.assertEqual(self.post("/api/action", {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.post("/api/action", {"X-Seedbox": "1", "Content-Type": "text/plain"})[0], 403)
        status, body = self.post(
            "/api/action", {"X-Seedbox": "1", "Content-Type": "application/json", "Origin": "http://evil.example"}
        )
        self.assertEqual((status, body["error"]), (403, "cross-origin request refused"))

    def test_collect_trigger_is_running_at_once(self):
        with mock.patch.object(cli._Service, "collect"):
            status, body = self.post("/api/collect", {"X-Seedbox": "1", "Content-Type": "application/json"})
        self.assertEqual((status, body["running"]), (202, True))

    def test_collect_state(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", "/api/collect")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertFalse(json.loads(resp.read())["running"])


if __name__ == "__main__":
    unittest.main()
