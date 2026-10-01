"""Release description for a tracker upload: the fields its form asks for, and a .nfo.

Two sources, merged:
- the release name: title, year, language, source, group;
- MediaInfo (the `mediainfo` program, in the container image): resolution,
  video and audio codecs, channels, bit depth, HDR / Dolby Vision, and the
  audio and subtitle languages, which also give the language when the name
  says nothing.

Every field can be corrected in the dashboard before the .torrent is created;
the mandatory ones must be filled. The .nfo (UTF-8, its drawing in plain ASCII):
a header (the pirate at his computer, "automated through seedbox"), the
release, its fields, video, audio and subtitle tracks, then MediaInfo's full
text report, the file's path replaced by the release name.
"""

import json
import os
import re
import shutil
import subprocess

from seedbox import titles

MANDATORY = ("title", "year", "language", "resolution", "video_codec", "audio_codec")
FIELDS = MANDATORY[:3] + ("source", "group") + MANDATORY[3:] + ("channels", "bit_depth", "hdr")
LABELS = {
    "title": "Title", "year": "Year", "language": "Language", "source": "Source", "group": "Group",
    "resolution": "Resolution", "video_codec": "Video codec", "audio_codec": "Audio codec",
    "channels": "Audio channels", "bit_depth": "Bit depth", "hdr": "HDR / DV",
}  # fmt: skip
TIMEOUT_S = 120

# Name markers, most specific first.
LANGUAGES = [
    ("MULTI", r"multi(?:[ ._-]?vf[fqi2])?"), ("TRUEFRENCH", r"truefrench"), ("VFF", r"vff"), ("VFQ", r"vfq"),
    ("VFI", r"vfi"), ("VF2", r"vf2"), ("VOSTFR", r"vostfr|subfrench"), ("FRENCH", r"french|vf"),
]  # fmt: skip
SOURCES = [
    ("REMUX", r"remux"), ("BluRay", r"blu[ ._-]?ray|bdremux|bd(?=[ ._-])"), ("BDRip", r"bdrip|brrip"),
    ("HDLight", r"hdlight|4klight|mhd"), ("WEB-DL", r"web[ ._-]?dl"), ("WEBRip", r"web[ ._-]?rip"), ("WEB", r"web"),
    ("HDTV", r"hdtv"), ("DVDRip", r"dvd[ ._-]?rip"), ("DVD", r"dvd[ ._-]?r?|dvd9|dvd5"), ("HDRip", r"hdrip"),
    ("TVRip", r"tv[ ._-]?rip"), ("VHSRip", r"vhs[ ._-]?rip"),
]  # fmt: skip
GROUP = re.compile(r"-([A-Za-z0-9]+)$")
CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}


class NfoError(Exception):
    pass


def _find(patterns, name):
    for label, pattern in patterns:
        if re.search(r"(?i)(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", name):
            return label
    return ""


def from_name(name):
    """Fields the release name gives: title (as written), year, language, source, group."""
    stem = titles._clean(os.path.basename(name))
    parsed = titles.parse(stem)
    # Title as written in the name, not lowercased like titles.parse's key.
    cut = len(stem)
    for m in (titles.YEAR.search(stem, 1), titles.EPISODE.search(stem), titles.STOP.search(stem)):
        if m and m.start() > 0:
            cut = min(cut, m.start())
    title = re.sub(r"\s+", " ", re.sub(r"[._\[\]()]+", " ", stem[:cut])).strip(" -")
    group = GROUP.search(stem)
    return {
        "title": title,
        "year": parsed["year"],
        "language": _find(LANGUAGES, stem),
        "source": _find(SOURCES, stem),
        # "-GROUP" at the end, when it is not a marker ("x264-LOST" yes, "Spider-Man" no: it needs markers before).
        "group": group.group(1) if group and titles.looks_like_release(stem[: group.start()]) else "",
        "resolution": parsed["resolution"],
    }


# ---------- MediaInfo
def available():
    return bool(shutil.which("mediainfo"))


def run(path):
    """(tracks from `mediainfo --Output=JSON`, text report). Reads only the headers."""
    if not available():
        raise NfoError("mediainfo is not installed (it comes with the seedbox container image)")
    try:
        out = subprocess.run(
            ["mediainfo", "--Output=JSON", path], capture_output=True, check=True, timeout=TIMEOUT_S
        ).stdout
        text = subprocess.run(["mediainfo", path], capture_output=True, check=True, timeout=TIMEOUT_S).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise NfoError(f"mediainfo failed on {os.path.basename(path)}: {exc}") from exc
    try:
        tracks = json.loads(out)["media"]["track"]
    except (ValueError, KeyError, TypeError) as exc:
        raise NfoError(f"mediainfo: unreadable output for {os.path.basename(path)}") from exc
    return tracks, text.decode("utf-8", errors="replace")


def _int(value):
    try:
        return int(float(str(value).split(" ")[0]))
    except (TypeError, ValueError):
        return 0


def resolution(width, height):
    """Resolution class from the frame size; width counts too (1920x800 is 1080p)."""
    for label, w, h in (("2160p", 3200, 1800), ("1080p", 1700, 1000), ("720p", 1200, 700), ("576p", 0, 560)):
        if width >= w and w or height >= h:
            return label
    return f"{height}p" if height else ""


def video_codec(v):
    library = (v.get("Encoded_Library_Name") or "").lower()
    if library in ("x264", "x265"):
        return library
    codec_id = (v.get("CodecID") or "").upper()
    if codec_id in ("XVID", "DX50", "DIVX", "DIV3", "DIV4", "DIV5", "DIV6"):
        return "XviD" if codec_id == "XVID" else "DivX"
    return {
        "AVC": "H264", "HEVC": "H265", "AV1": "AV1", "VP9": "VP9", "VC-1": "VC-1",
        "MPEG-4 VISUAL": "MPEG-4", "MPEG VIDEO": "MPEG-2" if v.get("Format_Version") == "2" else "MPEG-1",
    }.get((v.get("Format") or "").upper(), v.get("Format") or "")  # fmt: skip


def audio_codec(a):
    fmt = (a.get("Format") or "").upper()
    extra = (a.get("Format_AdditionalFeatures") or "").upper()
    commercial = (a.get("Format_Commercial_IfAny") or "").upper()
    atmos = " Atmos" if "ATMOS" in commercial or "JOC" in extra else ""
    if fmt == "DTS":
        if "XLL" in extra or "MASTER AUDIO" in commercial:
            return "DTS-HD MA"
        return "DTS-HD HRA" if "XBR" in extra else "DTS:X" if extra == "X" else "DTS"
    if fmt == "MLP FBA":
        return "TrueHD" + atmos
    if fmt == "E-AC-3":
        return "EAC3" + atmos
    if fmt == "MPEG AUDIO":
        return "MP3" if (a.get("Format_Profile") or "").startswith("Layer 3") else "MP2"
    return {"AC-3": "AC3", "AAC": "AAC", "FLAC": "FLAC", "OPUS": "Opus", "PCM": "PCM", "VORBIS": "Vorbis"}.get(
        fmt, a.get("Format") or ""
    )


def channels(a):
    n = _int(a.get("Channels"))
    layout = a.get("ChannelLayout") or ""
    if n and layout and "LFE" not in layout.split():
        return f"{n}.0"
    return CHANNELS.get(n, f"{n}ch" if n else "")


def hdr(v):
    fmt = v.get("HDR_Format") or ""
    compat = v.get("HDR_Format_Compatibility") or ""
    found = []
    if "Dolby Vision" in fmt:
        found.append("DV")
    if "2094 App 4" in fmt or "HDR10+" in compat:
        found.append("HDR10+")
    elif "2086" in fmt or "HDR10" in compat or v.get("transfer_characteristics") == "PQ":
        found.append("HDR10")
    if v.get("transfer_characteristics") == "HLG":
        found.append("HLG")
    return " ".join(found)


def _lang(track):
    return (track.get("Language") or "").split("-")[0].strip().lower()


def from_mediainfo(tracks):
    """Technical fields, plus audio and subtitle details for the .nfo."""
    kinds = {}
    for t in tracks:
        kinds.setdefault(t.get("@type"), []).append(t)
    video = (kinds.get("Video") or [{}])[0]
    audio = kinds.get("Audio") or []
    subs = kinds.get("Text") or []
    general = (kinds.get("General") or [{}])[0]
    first = audio[0] if audio else {}
    depth = _int(video.get("BitDepth"))
    return {
        "resolution": resolution(_int(video.get("Width")), _int(video.get("Height"))),
        "video_codec": video_codec(video) if video else "",
        "audio_codec": audio_codec(first) if first else "",
        "channels": channels(first) if first else "",
        "bit_depth": f"{depth} bits" if depth else "",
        "hdr": hdr(video),
        "audio": [
            {"language": _lang(a), "codec": audio_codec(a), "channels": channels(a), "title": a.get("Title") or ""}
            for a in audio
        ],
        "subtitles": [
            {"language": _lang(s), "title": s.get("Title") or "", "forced": s.get("Forced") == "Yes"} for s in subs
        ],
        "duration_s": _int(float(general.get("Duration") or 0)),
    }


def language_from_tracks(audio, subs):
    """MULTI (French + another), FRENCH, VOSTFR (French subtitles only) or ""."""
    langs = {a["language"] for a in audio if a["language"]}
    if "fr" in langs:
        return "MULTI" if langs - {"fr"} else "FRENCH"
    if langs and any(s["language"] == "fr" for s in subs):
        return "VOSTFR"
    return ""


def describe(name, media_path, file_count=1):
    """{'fields', 'missing', 'details', 'report'} for one entry."""
    fields = dict.fromkeys(FIELDS, "")
    fields.update(from_name(name))
    tracks, report = run(media_path)
    tech = from_mediainfo(tracks)
    for key in ("resolution", "video_codec", "audio_codec", "channels", "bit_depth", "hdr"):
        # The file says what it is; the name only fills what MediaInfo cannot tell.
        fields[key] = tech[key] or fields.get(key, "")
    fields["language"] = fields["language"] or language_from_tracks(tech["audio"], tech["subtitles"])
    report = report.replace(media_path, os.path.basename(media_path))
    return {
        "fields": fields,
        "missing": [k for k in MANDATORY if not fields.get(k)],
        "details": {
            "audio": tech["audio"],
            "subtitles": tech["subtitles"],
            "duration_s": tech["duration_s"],
            "files": file_count,
        },
        "report": report,
    }


def clean_fields(raw):
    """Fields sent back by the page: known keys, one line each, bounded."""
    if not isinstance(raw, dict):
        raise NfoError("fields: an object")
    fields = {k: re.sub(r"\s+", " ", str(raw.get(k) or "")).strip()[:200] for k in FIELDS}
    missing = [LABELS[k] for k in MANDATORY if not fields[k]]
    if missing:
        raise NfoError("missing: " + ", ".join(missing))
    return fields


def _duration(seconds):
    h, m = divmod(seconds // 60, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


WIDTH = 78
ART = r"""
                        .------------------------------------.
                        | o o o                              |
                        | $ seedbox upload                   |
                        | > mediainfo ................... ok |
                        | > hash ........................ ok |
                        | > nfo ......................... ok |
                        | $ _                                |
                        |____________________________________|
                        \____________________________________/
                                      |________|
       ______________________        /__________\
      |'-.__                 |
    []|  _  '-.__  (######)  |\_
      | |#|      '-(######)  |  \__     __________________________
      |______________________|     \__ [::::::::::::::::::::::::::]
         ||  ||        ||  ||
"""
SIGNATURE = "automated through seedbox"


def _rows(label, values):
    values = [v for v in values if v] or [""]
    lines = [f"  {label.ljust(10)} {values[0]}"]
    lines += [f"  {'':10} {v}" for v in values[1:]]
    return lines


def render(release, fields, details, report, size, max_bytes=0):
    """The .nfo text. max_bytes: the tracker's limit (0 = none); the header
    goes first when the text is too long, an error if it still is."""
    rule, double = " " + "-" * (WIDTH - 1), " " + "=" * (WIDTH - 1)
    files = details.get("files", 1)
    head = _rows("RELEASE", [release])
    head += _rows("SIZE", [f"{size / 1e9:.2f} GB" + (f", {files} files" if files > 1 else "")])
    if details.get("duration_s"):
        head += _rows("DURATION", [_duration(details["duration_s"])])
    about = []
    for key in ("title", "year", "language", "source", "group"):
        if fields.get(key):
            about += _rows(LABELS[key].upper(), [fields[key]])
    video = " / ".join(fields[k] for k in ("resolution", "video_codec", "bit_depth", "hdr") if fields.get(k))
    tracks = _rows("VIDEO", [video])
    audio = [
        " ".join(x for x in (f"#{n}", a["language"].upper(), a["codec"], a["channels"]) if x)
        + (f" ({a['title']})" if a["title"] else "")
        for n, a in enumerate(details.get("audio") or [], 1)
    ]
    if not audio and fields.get("audio_codec"):
        audio = [" ".join(fields[k] for k in ("audio_codec", "channels") if fields.get(k))]
    tracks += _rows("AUDIO", audio)
    subs = [
        f"#{n} {s['language'].upper() or '?'}"
        + (" forced" if s["forced"] else "")
        + (f" ({s['title']})" if s["title"] else "")
        for n, s in enumerate(details.get("subtitles") or [], 1)
    ]
    tracks += _rows("SUBTITLES", subs or ["none"])
    body = [double, *head, rule, *about, rule, *tracks, double, "", " MEDIAINFO", rule, ""]
    tail = "\n".join(body) + "\n" + report.strip() + "\n"
    text = ART.strip("\n") + "\n\n" + SIGNATURE.center(WIDTH).rstrip() + "\n" + tail
    if max_bytes and len(text.encode()) > max_bytes:
        text = SIGNATURE.center(WIDTH).rstrip() + "\n" + tail
        if len(text.encode()) > max_bytes:
            raise NfoError(f".nfo of {len(text.encode())} bytes, over the tracker's {max_bytes}")
    return text
