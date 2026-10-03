"""Plex page: transcoding rules, stream parsing, library matching, config, page."""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from seedbox import config, dashboard, metrics, plex


def load_cfg(out, **env):
    base = {"SEEDBOX_ROOTS": "/media/films", "SEEDBOX_OUTPUT_DIR": out, **env}
    with mock.patch.dict(os.environ, base, clear=True), mock.patch.object(config, "DEFAULT_PATHS", ()):
        return config.load()


DETAIL = """<MediaContainer><Video ratingKey="1"><Media videoCodec="hevc" container="mkv">
  <Part file="/media/films/films/A.mkv">
    <Stream streamType="1" codec="hevc" DOVIPresent="1" DOVIProfile="7"/>
    <Stream streamType="2" codec="truehd" profile=""/>
    <Stream streamType="2" codec="ac3"/>
    <Stream streamType="3" codec="pgs"/>
  </Part></Media></Video></MediaContainer>"""


class Rules(unittest.TestCase):
    def test_streams(self):
        s = plex.streams_of(ET.fromstring(DETAIL))
        self.assertEqual([x["type"] for x in s], ["video", "audio", "audio", "subtitle"])
        self.assertEqual(s[0]["dovi"], 7)

    def test_risks_per_player(self):
        r = plex.risks({"videoCodec": "hevc", "container": "mkv"}, plex.streams_of(ET.fromstring(DETAIL)))
        ps5, atv = " ".join(r["ps5"]), " ".join(r["appletv"])
        self.assertIn("TrueHD", ps5)  # first audio track is the one played
        self.assertIn("PGS", ps5)
        self.assertIn("profile 7", ps5)
        self.assertIn("profile 7", atv)
        self.assertNotIn("TrueHD", atv)

    def test_old_formats_and_clean_file(self):
        r = plex.risks({"videoCodec": "mpeg4", "container": "avi", "audioCodec": "mp3"}, [])
        self.assertTrue(r["ps5"])
        self.assertFalse(r["appletv"])
        clean = plex.risks(
            {"videoCodec": "h264", "container": "mkv", "audioCodec": "ac3"},
            [{"type": "audio", "codec": "ac3", "profile": "", "dovi": 0}],
        )
        self.assertEqual(clean, {"ps5": [], "appletv": []})
        self.assertIn("AV1", plex.risks({"videoCodec": "av1"}, [])["appletv"][0])
        self.assertTrue(plex.risks({"videoCodec": "h264", "audioCodec": "dca"}, [])["ps5"])

    def test_seed_of_walks_up_to_the_entry(self):
        snap = {
            "entries": [
                {"path": "/media/films/films/Pack", "status": "seeded", "coverage": "partial", "trackers": ["t"]},
                {"path": "/media/films/films/B.mkv", "status": "orphan", "coverage": "none", "trackers": []},
            ]
        }
        idx = plex.entry_index(snap)
        self.assertEqual(plex.seed_of(idx, "/media/films/films/Pack/sub/x.mkv")["trackers"], ["t"])
        self.assertEqual(plex.seed_of(idx, "/media/films/films/B.mkv")["coverage"], "none")
        self.assertIsNone(plex.seed_of(idx, "/media/other/C.mkv"))

    def test_resolution(self):
        self.assertEqual(plex.resolution({"videoResolution": "4k"}), "2160p")
        self.assertEqual(plex.resolution({"videoResolution": "sd"}), "SD")


class Setup(unittest.TestCase):
    def test_config_and_token_from_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_cfg(tmp, SEEDBOX_PLEX_URL="http://plex:32400/")
            self.assertEqual(cfg.plex_url, "http://plex:32400")
            cfg.plex_data_dir = tmp
            self.assertEqual(plex.token(cfg), "")
            os.makedirs(os.path.join(tmp, plex.SUPPORT))
            with open(os.path.join(tmp, plex.SUPPORT, "Preferences.xml"), "w") as handle:
                handle.write('<Preferences PlexOnlineToken="abc123" other="1"/>')
            self.assertEqual(plex.token(cfg), "abc123")
            cfg.plex_token = "explicit"
            self.assertEqual(plex.token(cfg), "explicit")

    def test_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_cfg(tmp)
            self.assertEqual(plex.overview(cfg, {}), (200, {"configured": False}))
            self.assertIsNone(plex.playback(cfg))

    def test_page(self):
        page = dashboard.render({"entries": []}, [], "plex")
        self.assertIn("<h1>Plex Media plane</h1>", page)
        for section in (
            "plex-libraries",
            "plex-playing",
            "plex-unwatched",
            "plex-transcode",
            "plex-recent",
            "plex-server",
            "activity",
        ):
            self.assertIn(f'<section id="{section}">', page)
        self.assertIn('id="a-io"', page)  # disk I/O tile, next to playback
        self.assertIn("initPlex()", page)
        self.assertIn("popcorn", page)
        self.assertIn('href="plex.html"', dashboard.render({"entries": []}, [], "home"))
        self.assertIn('href="plex.html"', dashboard.render({"entries": []}, [], "library"))
        self.assertNotIn("initPlex()", dashboard.render({"entries": []}, [], "home"))

    def test_series_keeps_keys_added_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_cfg(tmp)
            now = int(__import__("time").time())
            for i in range(400):
                point = {"t": now - 3600 + i * 9, "cpu_pct": 5.0}
                if i > 200:
                    point["plex_kbps"] = 8000
                metrics.append(cfg, point)
            rows = metrics.series(cfg, hours=1, buckets=50)
            self.assertTrue(any("plex_kbps" in r for r in rows))


if __name__ == "__main__":
    unittest.main()
