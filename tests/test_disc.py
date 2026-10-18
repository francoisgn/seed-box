"""Physical discs: names for Plex, ffmpeg and ssh command lines, settings."""

import os
import shlex
import tempfile
import unittest
from unittest import mock

from seedbox import cli, disc


class Names(unittest.TestCase):
    def test_plex_name(self):
        self.assertEqual(disc.plex_name("Some Film", 1999), "Some Film (1999)")
        self.assertEqual(disc.plex_name("Some Film: Part Two", "2004"), "Some Film - Part Two (2004)")
        self.assertEqual(disc.plex_name(' A/B "C"?  ', 2010), "AB C (2010)")
        self.assertEqual(disc.plex_name("Some Film", 2001, "4K"), "Some Film (2001) {edition-4K}")
        with self.assertRaises(disc.DiscError):
            disc.plex_name("Some Film", "99")
        with self.assertRaises(disc.DiscError):
            disc.plex_name(" / ", 2000)

    def test_search_query(self):
        self.assertEqual(disc.search_query("Some Film", 1999, "opening scene"), "Some Film 1999 opening scene")
        self.assertEqual(disc.search_query("Some Film", 1999, ""), "Some Film 1999")


class Playlists(unittest.TestCase):
    def test_parse_step(self):
        self.assertEqual(disc.parse_step("show: Some Show | 1, 2"), ("show", "some show", [1, 2]))
        self.assertEqual(disc.parse_step(" Movie :Some Film "), ("movie", "some film", None))
        self.assertEqual(disc.parse_step("show: Some Show"), ("show", "some show", None))
        for bad in ("Some Film", "book: Some Film", "show: | 1", "movie: Some Film | 1", "show: Some Show | one"):
            with self.assertRaises(disc.DiscError):
                disc.parse_step(bad)

    def test_pick_item(self):
        items = [
            {"key": "1", "title": "Some Saga: Some Film", "original": ""},
            {"key": "2", "title": "Le Film", "original": "Some Film"},
            {"key": "3", "title": "Some Film Returns", "original": ""},
        ]
        self.assertEqual(disc.pick_item(items, "some film")["key"], "2")  # exact original title wins
        self.assertEqual(disc.pick_item(items, "returns")["key"], "3")
        self.assertEqual(disc.pick_item(items, "some saga")["key"], "1")
        self.assertIsNone(disc.pick_item(items, "other"))

    def test_settings_read_playlists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seedbox.toml")
            with open(path, "w") as handle:
                handle.write(
                    '[[physical.playlists]]\ntitle = "Saga"\nsteps = ["movie: Some Film", "show: Some Show | 1"]\n'
                )
            with mock.patch.dict(os.environ, {}, clear=True):
                s = disc.load_settings(path)
        self.assertEqual([p["title"] for p in s.playlists], ["Saga"])
        self.assertEqual(len(s.playlists[0]["steps"]), 2)


class Commands(unittest.TestCase):
    def test_encode_caps_length(self):
        cmd = disc.encode_command("in.webm", "out.mkv", "Some Film (1999)", start=5, length=10_000)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "5")
        self.assertEqual(cmd[cmd.index("-t") + 1], str(disc.MAX_LENGTH))
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[-1], "out.mkv")

    def test_card_subtitle(self):
        self.assertEqual(
            disc.card_srt("Some Film (1999)", "Physical disc"),
            "1\n00:00:00,000 --> 00:00:10,000\nSome Film (1999)\nPhysical disc\n",
        )
        cmd = disc.card_command("out.mkv", "card.srt", "Some Film (1999)")
        self.assertEqual(cmd[cmd.index("-disposition:s:0") + 1], "default+forced")
        self.assertNotIn("-vf", cmd)  # no drawtext: builds without freetype work

    def test_parse_candidates(self):
        text = "abcDEF123_-|2:05|Chan|A title | with bar\nnoise line\nxyz|1:00|C|too short id\n"
        found = disc.parse_candidates(text)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "A title | with bar")

    def test_remote_command_quotes(self):
        line = disc.remote_command("sh", "-c", 'mkdir -p -- "$1"', "sh", "/v/Some Film (1999)")
        self.assertEqual(shlex.split(line)[-1], "/v/Some Film (1999)")


class Settings(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seedbox.toml")
            with open(path, "w") as handle:
                handle.write(
                    '[library]\nroots = []\n[plex]\nurl = "http://plex.lan:32400/"\n'
                    '[physical]\nhost = "nas"\ndir = "/volume/physical/"\nplex_dir = "/media/physical"\nlength = 90\n'
                )
            with mock.patch.dict(os.environ, {}, clear=True):
                s = disc.load_settings(path)
        self.assertEqual((s.host, s.dir, s.plex_dir, s.length), ("nas", "/volume/physical", "/media/physical", 90))
        self.assertEqual(s.plex_url, "http://plex.lan:32400")

    def test_missing_file_gives_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s = disc.load_settings("/nonexistent/seedbox.toml")
        self.assertEqual((s.dir, s.length), ("", 150))

    def test_add_without_dir_fails_cleanly(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cli.main(["disc", "-c", "/nonexistent.toml", "add", "Some Film", "1999", "--card"]), 1)

    def test_local_install_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = disc.Settings(dir=os.path.join(tmp, "physical"))
            clip = os.path.join(tmp, "clip.mkv")
            with open(clip, "wb") as handle:
                handle.write(b"x")
            target = disc.install(s, clip, "Some Film (1999)")
            self.assertTrue(target.endswith("Some Film (1999)/Some Film (1999).mkv"))
            self.assertTrue(disc.exists(s, "Some Film (1999)"))
            self.assertEqual(disc.listing(s), ["Some Film (1999)"])


if __name__ == "__main__":
    unittest.main()
