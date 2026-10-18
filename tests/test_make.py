"""`seedbox make`: .torrent + .nfo for plain paths."""

import os
import tempfile
import unittest
from unittest import mock

from seedbox import make, nfo
from seedbox import torrentfile as tf

ANNOUNCE = "https://announce.tracker-a.example/SECRETPASSKEY/announce"
FOUND = {"fields": dict.fromkeys(nfo.FIELDS, ""), "details": {"files": 1}, "report": "General\nFormat : Matroska"}


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


class MakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "src")
        self.out = os.path.join(self.tmp.name, "out")

    def test_folder_skips_sidecars_and_names_the_biggest_media(self):
        write(os.path.join(self.src, "Film", "film.mkv"), b"v" * 5000)
        write(os.path.join(self.src, "Film", "extra.mkv"), b"e" * 10)
        write(os.path.join(self.src, "Film", "old.nfo"), b"x")
        write(os.path.join(self.src, "Film", "@eaDir", "thumb.jpg"), b"x")
        spec = make.spec_for(os.path.join(self.src, "Film"))
        self.assertEqual(sorted(f[1][-1] for f in spec["files"]), ["extra.mkv", "film.mkv"])
        self.assertTrue(make.main_file(spec).endswith("film.mkv"))

    def test_each_makes_files_beside_the_source_not_in_it(self):
        write(os.path.join(self.src, "A.2020.1080p-X", "a.mkv"), b"a" * 3000)
        write(os.path.join(self.src, "B.2021.1080p-X.mkv"), b"b" * 3000)
        with mock.patch.object(nfo, "describe", return_value=FOUND):
            rc = make.main(["--each", "--announce", ANNOUNCE, "--source", "SRC", "--out", self.out, self.src])
        self.assertEqual(rc, 0)
        self.assertEqual(
            sorted(os.listdir(self.out)),
            ["A.2020.1080p-X.nfo", "A.2020.1080p-X.torrent", "B.2021.1080p-X.mkv.nfo", "B.2021.1080p-X.mkv.torrent"],
        )
        self.assertEqual(os.listdir(self.src).count("out"), 0)
        with open(os.path.join(self.out, "A.2020.1080p-X.torrent"), "rb") as handle:
            meta = tf.parse(handle.read())
        self.assertEqual(meta["source"], "SRC")

    def test_several_trackers_share_one_hashing(self):
        write(os.path.join(self.src, "A.2020.1080p-X", "a.mkv"), b"a" * 3000)
        lines = os.path.join(self.tmp.name, "trackers.tsv")
        write(lines, f"# c\none\t{ANNOUNCE}\tS1\ntwo\t{ANNOUNCE}2\n".encode())
        with mock.patch.object(nfo, "describe", return_value=FOUND):
            rc = make.main(["--trackers", lines, "--out", self.out, os.path.join(self.src, "A.2020.1080p-X")])
        self.assertEqual(rc, 0)
        self.assertEqual(
            sorted(os.listdir(self.out)),
            ["A.2020.1080p-X.nfo", "A.2020.1080p-X.one.torrent", "A.2020.1080p-X.two.torrent"],
        )
        metas = []
        for k in ("one", "two"):
            with open(os.path.join(self.out, f"A.2020.1080p-X.{k}.torrent"), "rb") as handle:
                metas.append(tf.parse(handle.read()))
        self.assertNotEqual(metas[0]["infohash"], metas[1]["infohash"])
        self.assertEqual(metas[0]["pieces"], metas[1]["pieces"])

    def test_announce_required_and_bad_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(make.main(["x"]), 2)
        with self.assertRaises(make.MakeError):
            make.parse_fields(["nope=1"])
        self.assertEqual(make.parse_fields(["year=2020"]), {"year": "2020"})


if __name__ == "__main__":
    unittest.main()
