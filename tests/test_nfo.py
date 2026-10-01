"""Release description: fields from the name, from MediaInfo tracks, merge and .nfo text."""

import unittest
from unittest import mock

from seedbox import nfo

UHD = [
    {"@type": "General", "Duration": "6180.5"},
    {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "1600", "BitDepth": "10",
     "HDR_Format": "Dolby Vision / SMPTE ST 2086", "HDR_Format_Compatibility": "Blu-ray / HDR10",
     "transfer_characteristics": "PQ"},
    {"@type": "Audio", "Format": "E-AC-3", "Format_Commercial_IfAny": "Dolby Digital Plus with Dolby Atmos",
     "Format_AdditionalFeatures": "JOC", "Channels": "6", "ChannelLayout": "L R C LFE Ls Rs", "Language": "fr-CA",
     "Title": "VFQ"},
    {"@type": "Audio", "Format": "DTS", "Format_AdditionalFeatures": "XLL", "Channels": "8",
     "ChannelLayout": "L R C LFE Ls Rs Lb Rb", "Language": "en"},
    {"@type": "Text", "Language": "fr", "Forced": "Yes"},
]  # fmt: skip
XVID = [
    {"@type": "General", "Duration": "5400"},
    {"@type": "Video", "Format": "MPEG-4 Visual", "CodecID": "XVID", "Width": "640", "Height": "272", "BitDepth": "8"},
    {"@type": "Audio", "Format": "MPEG Audio", "Format_Profile": "Layer 3", "Channels": "2", "ChannelLayout": "L R"},
]  # fmt: skip


class Name(unittest.TestCase):
    def test_scene_name(self):
        f = nfo.from_name("Some.Entry.2016.MULTi.VFF.1080p.BluRay.x264-GRP.mkv")
        self.assertEqual(
            (f["title"], f["year"], f["language"], f["source"], f["group"], f["resolution"]),
            ("Some Entry", "2016", "MULTI", "BluRay", "GRP", "1080p"),
        )

    def test_plain_names(self):
        f = nfo.from_name("Some Entry - Other Part (2012).avi")
        self.assertEqual(
            (f["title"], f["year"], f["language"], f["group"]), ("Some Entry - Other Part", "2012", "", "")
        )
        self.assertEqual(nfo.from_name("Spider-Man.2002.TRUEFRENCH.DVDRip.XviD-ABC.avi")["title"], "Spider-Man")
        self.assertEqual(nfo.from_name("Film.2019.VOSTFR.WEB-DL.1080p.x264-X")["source"], "WEB-DL")
        self.assertEqual(nfo.from_name("Film.2019.FRENCH.WEBRip.x264")["language"], "FRENCH")


class Tracks(unittest.TestCase):
    def test_uhd_hdr_dv(self):
        t = nfo.from_mediainfo(UHD)
        self.assertEqual(
            (t["resolution"], t["video_codec"], t["audio_codec"], t["channels"], t["bit_depth"], t["hdr"]),
            ("2160p", "H265", "EAC3 Atmos", "5.1", "10 bits", "DV HDR10"),
        )
        self.assertEqual([(a["language"], a["codec"], a["channels"]) for a in t["audio"]],
                         [("fr", "EAC3 Atmos", "5.1"), ("en", "DTS-HD MA", "7.1")])  # fmt: skip
        self.assertEqual(nfo.language_from_tracks(t["audio"], t["subtitles"]), "MULTI")

    def test_xvid(self):
        t = nfo.from_mediainfo(XVID)
        self.assertEqual(
            (t["resolution"], t["video_codec"], t["audio_codec"], t["channels"], t["hdr"]),
            ("272p", "XviD", "MP3", "2.0", ""),
        )
        self.assertEqual(nfo.resolution(1920, 800), "1080p")
        self.assertEqual(nfo.resolution(1280, 536), "720p")
        self.assertEqual(nfo.language_from_tracks([{"language": "en"}], [{"language": "fr"}]), "VOSTFR")


class Describe(unittest.TestCase):
    def test_merge_and_render(self):
        with mock.patch.object(nfo, "run", return_value=(XVID, "Complete name : /media/x/Some Entry (2012).avi\n")):
            d = nfo.describe("Some Entry (2012).avi", "/media/x/Some Entry (2012).avi")
        # The name gives no language and MediaInfo no audio language: to fill in.
        self.assertEqual(d["missing"], ["language"])
        self.assertNotIn("/media/x", d["report"])
        with self.assertRaisesRegex(nfo.NfoError, "Language"):
            nfo.clean_fields(d["fields"])
        fields = nfo.clean_fields(dict(d["fields"], language=" FRENCH \n"))
        text = nfo.render("Some Entry (2012).avi", fields, d["details"], d["report"], 700_000_000)
        self.assertIn("  LANGUAGE   FRENCH\n", text)
        self.assertIn("  VIDEO      272p / XviD", text)
        self.assertIn("  DURATION   1h30\n", text)
        self.assertIn("automated through seedbox", text)
        self.assertNotIn("http", text.split("MEDIAINFO")[0])  # no link
        self.assertTrue(nfo.ART.isascii())
        self.assertTrue(text.rstrip().endswith("Some Entry (2012).avi"))
        self.assertFalse([line for line in text.splitlines() if line != line.rstrip()])
        # Over the tracker's limit: the header goes, then it is an error.
        small = nfo.render(
            "Some Entry (2012).avi", fields, d["details"], d["report"], 1, max_bytes=len(text.encode()) - 1
        )
        self.assertNotIn("(######)", small)
        self.assertIn("automated through seedbox", small)
        with self.assertRaisesRegex(nfo.NfoError, "over the tracker"):
            nfo.render("Some Entry (2012).avi", fields, d["details"], d["report"], 1, max_bytes=100)


if __name__ == "__main__":
    unittest.main()
