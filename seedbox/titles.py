"""Release names -> comparable identity: title, year, episode, quality.

Good enough to group versions of the same work ("Warcraft (2016) [BD UHDR10…]"
and "Warcraft.2016.MULTi.1080p…"), not a full scene-name parser. Titles in
different languages (Finding Dory / Le Monde de Dory) are not matched here:
identical files are grouped by inode instead.
"""

import re

EPISODE = re.compile(r"(?i)\bS(\d{1,2})[ ._-]?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b")
# "DBZ - 001 Titre", "Bleach - 01 - Titre", "Tintin - 03 - …": numbered episodes.
# "Show.Name.01.FR.720p": two/three-digit number between dots.
NUMBERED = re.compile(r"(?i)(?:\s-\s|\bep(?:isode)?[ ._-]?)(\d{1,3})(?:\s|$|\s?-|\.)|\.(\d{2,3})\.")
SEASON = re.compile(r"(?i)^(?:season|saison)[ ._-]?\d{1,2}$|^s\d{1,2}$|\bS\d{1,2}\b(?![ ._-]?E\d)")
YEAR = re.compile(r"(?<![0-9])(19[2-9]\d|20[0-4]\d)(?![0-9])")
RESOLUTION = re.compile(r"(?i)\b(2160p|4k|uhd(?=\b|r10)|1080p|720p|576p|480p)")
# Words that end the title part of a release name.
STOP = re.compile(
    r"(?i)\b(multi|vff|vfq|vf2|vfi|vostfr|subfrench|truefrench|french|custom|remux|bluray|bdrip|brrip|"
    r"web(?:-dl|rip)?|hdtv|dvdrip|hdrip|x264|x265|h264|h265|hevc|avc|hdr|dv|imax|unrated|extended|"
    r"repack|proper|2160p|1080p|720p|576p|480p|uhd|4klight|hybrid)\b"
)
# "Film-CD1.avi", "Film.Part.2.mkv", "Film disc1": one work split in several files.
PART = re.compile(r"(?i)[ ._-]*\b(?:cd|part|disc|disk)[ ._-]?(\d{1,2})\b")
TAG = re.compile(r"^\[[^\]]*\]\s*")  # "[nextorrent.net] Finding.Dory…"
_RES_RANK = {"2160p": 4, "4k": 4, "uhd": 4, "1080p": 3, "720p": 2, "576p": 1, "480p": 1}


def _clean(name):
    name = TAG.sub("", name)
    name = re.sub(r"\.(mkv|mp4|avi|m4v|mov|wmv|mpg|mpeg|ts|m2ts|iso|divx)$", "", name, flags=re.I)
    return name


def part_key(name):
    """Name without its part number, or None when the name has no part number."""
    stem = _clean(name)
    if not PART.search(stem):
        return None
    return PART.sub("", stem).lower()


def looks_like_release(name):
    """Whether a name carries release markers (year, resolution, codec, language)."""
    name = _clean(name)
    return bool(YEAR.search(name[1:]) or RESOLUTION.search(name) or STOP.search(name))


def is_episode(name):
    name = _clean(name)
    return bool(EPISODE.search(name) or NUMBERED.search(name))


def is_season_folder(name):
    return bool(SEASON.search(name))


def resolution(name):
    m = RESOLUTION.search(name)
    return m.group(1).lower().replace("4k", "2160p").replace("uhd", "2160p") if m else ""


def rank(name):
    return _RES_RANK.get(resolution(name), 0)


def parse(name):
    """{'title', 'year', 'episode', 'resolution', 'key'} for a release or file name."""
    name = _clean(name)
    ep = EPISODE.search(name)
    episode = ""
    if ep:
        season, number = (ep.group(1), ep.group(2)) if ep.group(1) else (ep.group(3), ep.group(4))
        episode = f"S{int(season):02d}E{int(number):02d}"
    # A leading number is part of the title ("1917.2019…", "300.Rise…").
    years = [m for m in YEAR.finditer(name) if m.start() > 0]
    year_match = years[0] if years else None
    # Title ends at the first of: year, episode tag, a quality/language word.
    cut = len(name)
    for m in (year_match, ep, STOP.search(name)):
        if m and m.start() > 0:
            cut = min(cut, m.start())
    title = re.sub(r"[._\-\[\]()]+", " ", name[:cut])
    title = re.sub(r"\s+", " ", title).strip().lower()
    title = re.sub(r"^(the|le|la|les|l)\s+", "", title)
    year = year_match.group(1) if year_match and (not ep or year_match.start() < ep.start()) else ""
    return {
        "title": title,
        "year": year,
        "episode": episode,
        "resolution": resolution(name),
        "key": f"{title}|{year}|{episode}",
    }
