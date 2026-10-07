"""Physical discs in Plex: one short placeholder video per disc you own.

`seedbox disc add "Title" YEAR` makes a short clip (a film's opening found on
YouTube, a given URL, or a plain title card), converts it to a Direct Play
file (H.264 + AAC in .mkv), puts it in the folder of a Plex library kept for
physical discs as "Title (Year)/Title (Year).mkv", asks Plex to scan it and
tags it into a collection if asked. Plex matches the film from the name: the
library shows the real poster and summary, playing it shows the clip, a
reminder to put the disc in.

Runs where ffmpeg (and yt-dlp for clips) and an SSH access to the NAS are,
not in the container. Settings: the [physical] section of the seedbox TOML
file, plus [plex] url; nothing else of the file is validated.
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from seedbox import ui
from seedbox.api import ApiError, request

DEFAULT_CONFIG = "~/.config/seedbox/seedbox.toml"
MAX_LENGTH = 600
SEARCH_COUNT = 6
# Characters Plex, SMB and the shell handle badly in a folder name.
FORBIDDEN = re.compile(r'[\\/*?"<>|\x00-\x1f]')


class DiscError(Exception):
    pass


@dataclass
class Settings:
    host: str = ""  # ssh alias of the machine holding the folder; "" = a local folder
    dir: str = ""  # folder of the Plex library for physical discs, on that machine
    plex_dir: str = ""  # the same folder as Plex sees it (default: dir)
    plex_url: str = ""
    plex_token: str = ""
    plex_token_cmd: str = ""  # shell command printing the Plex token
    length: int = 150  # seconds kept from a clip
    card_text: str = "Physical disc"
    search_suffix: str = "opening scene"
    collection: str = ""  # collection every disc goes to (besides --collection)
    playlists: list = field(default_factory=list)  # [[physical.playlists]]: {title, steps}


def load_settings(path=None):
    """[physical] and [plex] url/token of the seedbox TOML file. A missing file gives defaults."""
    path = os.path.expanduser(path or os.environ.get("SEEDBOX_CONFIG") or DEFAULT_CONFIG)
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DiscError(f"{path}: {exc}") from exc
    phys, plex = data.get("physical", {}), data.get("plex", {})
    s = Settings()
    for key in ("host", "dir", "plex_dir", "plex_token_cmd", "card_text", "search_suffix", "collection"):
        setattr(s, key, str(phys.get(key, getattr(s, key))))
    s.length = int(phys.get("length", s.length))
    s.playlists = [p for p in phys.get("playlists", []) if isinstance(p, dict)]
    s.plex_url = os.environ.get("SEEDBOX_PLEX_URL", plex.get("url", "")).rstrip("/")
    s.plex_token = os.environ.get("SEEDBOX_PLEX_TOKEN", plex.get("token", ""))
    s.dir = s.dir.rstrip("/")
    s.plex_dir = (s.plex_dir or s.dir).rstrip("/")
    return s


# ---------- pure helpers (tested)


def plex_name(title, year, edition=""):
    """ "Title (Year)" as Plex expects it, with an optional {edition-…} tag."""
    title = FORBIDDEN.sub("", title.replace(":", " -")).strip()
    title = re.sub(r"\s+", " ", title).strip(" .")
    if not title:
        raise DiscError("empty title")
    if not re.fullmatch(r"(18|19|20)\d\d", str(year)):
        raise DiscError(f"year: four digits, got {year!r}")
    edition = FORBIDDEN.sub("", edition.replace("{", "").replace("}", "")).strip()
    return f"{title} ({year})" + (f" {{edition-{edition}}}" if edition else "")


def search_query(title, year, suffix):
    return " ".join(part for part in (title, str(year), suffix) if part)


def encode_command(src, dst, title, start=0, length=150):
    """ffmpeg arguments: [start, start + length] of src as H.264 1080p + AAC stereo .mkv."""
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-ss", str(start), "-t", str(min(length, MAX_LENGTH)), "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-vf", "scale=-2:'min(1080,ih)'",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-metadata", f"title={title}", dst,
    ]  # fmt: skip


def card_srt(title, text, seconds=10):
    """The card's text as a subtitle: ffmpeg builds often lack drawtext, players all show subtitles."""
    end = f"00:00:{min(seconds, 59):02d},000"
    return f"1\n00:00:00,000 --> {end}\n{title}\n{text}\n"


def card_command(dst, srt, title, seconds=10):
    """ffmpeg arguments: a black 1080p card, silent audio, the srt as a default forced subtitle."""
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r=25:d={seconds}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}",
        "-i", srt,
        "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt",
        "-disposition:s:0", "default+forced", "-shortest",
        "-metadata", f"title={title}", dst,
    ]  # fmt: skip


def parse_candidates(text):
    """yt-dlp --print lines "id|duration|channel|title" → list of dicts."""
    out = []
    for line in text.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and re.fullmatch(r"[\w-]{6,}", parts[0]):
            out.append({"id": parts[0], "duration": parts[1], "channel": parts[2], "title": parts[3]})
    return out


def parse_step(step):
    """'show: Title | 1,2' or 'movie: Title' → (kind, title in lower case, seasons or None)."""
    kind, sep, rest = step.partition(":")
    kind = kind.strip().lower()
    if not sep or kind not in ("show", "movie"):
        raise DiscError(f"playlist step {step!r}: 'show: Title [| 1,2]' or 'movie: Title'")
    title, _, seasons = rest.partition("|")
    title = title.strip().lower()
    if not title:
        raise DiscError(f"playlist step {step!r}: no title")
    if kind == "movie" and seasons.strip():
        raise DiscError(f"playlist step {step!r}: a film has no seasons")
    try:
        picked = [int(n) for n in seasons.replace(" ", "").split(",") if n] or None
    except ValueError as exc:
        raise DiscError(f"playlist step {step!r}: seasons are numbers, e.g. | 1,2") from exc
    return kind, title, picked


def pick_item(items, title):
    """The item for a step among {title, original}: exact title first, else the shortest containing it."""
    exact = [i for i in items if title in (i["title"].lower(), i.get("original", "").lower())]
    if exact:
        return exact[0]
    hits = [i for i in items if title in i["title"].lower() or title in i.get("original", "").lower()]
    return min(hits, key=lambda i: len(i["title"])) if hits else None


def remote_command(*args):
    """A command line for ssh, every argument quoted for the remote shell."""
    return " ".join(shlex.quote(a) for a in args)


# ---------- side effects


def _need(tool):
    path = os.environ.get(tool.upper().replace("-", "_")) or shutil.which(tool)
    if not path:
        hint = {"yt-dlp": "brew install yt-dlp (or pipx install yt-dlp)", "ffmpeg": "brew install ffmpeg"}[tool]
        raise DiscError(f"{tool} not found: {hint}")
    return path


def _run(cmd, **kw):
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if res.returncode != 0:
        raise DiscError(f"{os.path.basename(cmd[0])} failed: {(res.stderr or res.stdout).strip()[-400:]}")
    return res.stdout


def search(query, count=SEARCH_COUNT):
    out = _run(
        [_need("yt-dlp"), "--flat-playlist", "--print", "%(id)s|%(duration_string)s|%(channel)s|%(title)s",
         f"ytsearch{count}:{query}"]
    )  # fmt: skip
    return parse_candidates(out)


def download(url, folder):
    _run([_need("yt-dlp"), "-q", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]",
          "-o", os.path.join(folder, "source.%(ext)s"), url])  # fmt: skip
    files = [f for f in os.listdir(folder) if f.startswith("source.")]
    if not files:
        raise DiscError("yt-dlp gave no file")
    return os.path.join(folder, files[0])


def _ssh(s, *args):
    return _run(["ssh", s.host, remote_command(*args)])


def exists(s, name):
    path = f"{s.dir}/{name}"
    if s.host:
        return _ssh(s, "sh", "-c", 'test -e "$1" && echo yes || true', "sh", path).strip() == "yes"
    return os.path.exists(path)


def listing(s):
    if s.host:
        out = _ssh(s, "sh", "-c", 'ls -1 -- "$1" 2>/dev/null || true', "sh", s.dir)
    else:
        out = "\n".join(sorted(os.listdir(s.dir))) if os.path.isdir(s.dir) else ""
    return [line for line in out.splitlines() if line and not line.startswith((".", "@", "#"))]


def install(s, local, name):
    """Copy the clip to <dir>/<name>/<name>.mkv on the host (or locally)."""
    folder, target = f"{s.dir}/{name}", f"{s.dir}/{name}/{name}.mkv"
    if not s.host:
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(local, target)
        return target
    with open(local, "rb") as handle:
        res = subprocess.run(
            ["ssh", s.host, remote_command("sh", "-c", 'mkdir -p -- "$1" && cat > "$2"', "sh", folder, target)],
            stdin=handle,
            capture_output=True,
            text=True,
        )
    if res.returncode != 0:
        raise DiscError(f"copy to {s.host} failed: {res.stderr.strip()[-300:]}")
    return target


def plex_token(s):
    if s.plex_token:
        return s.plex_token
    if s.plex_token_cmd:
        res = subprocess.run(s.plex_token_cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        raise DiscError(f"plex_token_cmd failed: {res.stderr.strip()[-200:]}")
    return ""


class Plex:
    def __init__(self, url, tok):
        self.url, self.tok = url, tok

    def call(self, path, method="GET", **params):
        params["X-Plex-Token"] = self.tok
        status, body, _ = request(f"{self.url}{path}?{urllib.parse.urlencode(params)}", method=method, timeout=120)
        if status not in (200, 201, 204):
            raise DiscError(f"Plex: HTTP {status} on {path}")
        return ET.fromstring(body) if body.strip() else None

    def section_for(self, folder):
        for d in self.call("/library/sections").iter("Directory"):
            if any(loc.get("path", "").rstrip("/") == folder for loc in d.iter("Location")):
                return d.get("key")
        raise DiscError(f"no Plex library has the folder {folder}")

    def catalog(self, kind):
        """Every film or show of every library of that kind: [{key, title, original, year}]."""
        out = []
        for d in self.call("/library/sections").iter("Directory"):
            if d.get("type") != kind:
                continue
            tag = "Video" if kind == "movie" else "Directory"
            for m in self.call(f"/library/sections/{d.get('key')}/all").iter(tag):
                out.append(
                    {
                        "key": m.get("ratingKey"),
                        "title": m.get("title", ""),
                        "original": m.get("originalTitle", ""),
                        "year": m.get("year", ""),
                    }
                )
        return out

    def children(self, key):
        root = self.call(f"/library/metadata/{key}/children")
        items = [c for c in root if c.get("ratingKey")]
        return sorted(items, key=lambda c: int(c.get("index", 0)))

    def episodes(self, show, seasons=None):
        """Episode keys of a show in order, season 0 (specials) left out unless asked."""
        keys = []
        for season in self.children(show):
            index = int(season.get("index", 0))
            if (seasons and index not in seasons) or (not seasons and index == 0):
                continue
            keys += [e.get("ratingKey") for e in self.children(season.get("ratingKey"))]
        return keys

    def replace_playlist(self, title, keys, chunk=80):
        """Drop the playlist of that title (if any) and create it with keys in order. Returns its key."""
        for p in self.call("/playlists").iter("Playlist"):
            if p.get("title") == title:
                self.call(f"/playlists/{p.get('ratingKey')}", method="DELETE")
        machine = self.call("/identity").get("machineIdentifier")
        base = f"server://{machine}/com.plexapp.plugins.library/library/metadata/"
        parts = [keys[i : i + chunk] for i in range(0, len(keys), chunk)]
        made = self.call("/playlists", method="POST", type="video", title=title, smart=0, uri=base + ",".join(parts[0]))
        pid = next(made.iter("Playlist")).get("ratingKey")
        for part in parts[1:]:
            self.call(f"/playlists/{pid}/items", method="PUT", uri=base + ",".join(part))
        return pid

    def find(self, section, path):
        for video in self.call(f"/library/sections/{section}/all").iter("Video"):
            if any(p.get("file") == path for p in video.iter("Part")):
                return video
        return None


def to_plex(s, name, collections, wait=180):
    """Scan the new folder and tag the item into the collections. Returns the matched Plex item or None."""
    if not s.plex_url:
        ui.warn("Plex not configured ([plex] url): scan it yourself")
        return None
    tok = plex_token(s)
    if not tok:
        ui.warn("no Plex token ([plex] token, SEEDBOX_PLEX_TOKEN or [physical] plex_token_cmd): scan it yourself")
        return None
    plex = Plex(s.plex_url, tok)
    section = plex.section_for(s.plex_dir)
    plex.call(f"/library/sections/{section}/refresh", path=f"{s.plex_dir}/{name}")
    target = f"{s.plex_dir}/{name}/{name}.mkv"
    item = None
    with ui.Spinner("Waiting for Plex to scan it") as spin:
        deadline = time.time() + wait
        while time.time() < deadline and item is None:
            time.sleep(5)
            item = plex.find(section, target)
            spin.update(f"Waiting for Plex to scan it ({int(deadline - time.time())} s left)")
    if item is None:
        ui.warn(f"Plex has not listed it after {wait} s: check the library later")
        return None
    if collections:
        params = {f"collection[{i}].tag.tag": c for i, c in enumerate(collections)}
        plex.call(f"/library/sections/{section}/all", method="PUT", type=1, id=item.get("ratingKey"), **params)
    return item


# ---------- commands


def cmd_add(s, args):
    name = plex_name(args.title, args.year, args.edition)
    if not s.dir:
        raise DiscError("[physical] dir is not set: the folder of the Plex library for physical discs")
    if exists(s, name) and not args.force:
        raise DiscError(f"{name} is already there (--force to replace it)")
    ffmpeg = _need("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="seedbox-disc-") as tmp:
        out = os.path.join(tmp, f"{name}.mkv")
        if args.card:
            srt = os.path.join(tmp, "card.srt")
            with open(srt, "w", encoding="utf-8") as handle:
                handle.write(card_srt(name, args.text or s.card_text))
            _run([ffmpeg] + card_command(out, srt, name)[1:])
            ui.ok("title card made")
        else:
            url = args.url
            if not url:
                query = args.query or search_query(args.title, args.year, s.search_suffix)
                with ui.Spinner(f"Searching YouTube: {query}"):
                    found = search(query)
                if not found:
                    raise DiscError(f"nothing found for {query!r}: give --url or --card")
                for i, c in enumerate(found, 1):
                    ui.info(f"{i}. {c['title']}  [{c['duration']}, {c['channel']}]  https://youtu.be/{c['id']}")
                pick = 1
                if not args.yes:
                    answer = input(f"Clip to use [1-{len(found)}, Enter = 1, q = quit]: ").strip().lower()
                    if answer == "q":
                        return 1
                    pick = int(answer) if answer.isdigit() and 1 <= int(answer) <= len(found) else 1
                url = f"https://www.youtube.com/watch?v={found[pick - 1]['id']}"
            if args.dry_run:
                ui.info(f"would download {url}, keep {args.length or s.length} s from {args.start} s, as {name}")
                return 0
            with ui.Spinner("Downloading the clip"):
                src = download(url, tmp)
            with ui.Spinner("Converting it for Direct Play (H.264 + AAC)"):
                _run([ffmpeg] + encode_command(src, out, name, args.start, args.length or s.length)[1:])
            ui.ok(f"clip ready ({os.path.getsize(out) / 1e6:.1f} MB)")
        if args.dry_run:
            ui.info(f"would install {name}")
            return 0
        with ui.Spinner(f"Copying to {s.host or 'local'}:{s.dir}"):
            target = install(s, out, name)
    ui.ok(f"installed {target}")
    collections = [c for c in (s.collection, *(args.collection or [])) if c]
    item = to_plex(s, name, collections)
    if item is not None:
        matched = f"{item.get('title')} ({item.get('year', '?')})"
        (ui.ok if item.get("guid", "").startswith("plex://movie") else ui.warn)(f"Plex: {matched}")
        if collections:
            ui.ok("collection: " + ", ".join(collections))
    return 0


def cmd_playlist(s, args):
    """List the playlists defined in [[physical.playlists]], or (re)build the one named."""
    if not s.playlists:
        raise DiscError("no [[physical.playlists]] in the config")
    if not args.title:
        for p in s.playlists:
            print(f"{p.get('title', '?')}  ({len(p.get('steps', []))} steps)")
        return 0
    wanted = [p for p in s.playlists if args.title.lower() in p.get("title", "").lower()]
    if len(wanted) != 1:
        raise DiscError(f"{len(wanted)} playlists match {args.title!r}: give more of the title")
    title, steps = wanted[0]["title"], [parse_step(x) for x in wanted[0].get("steps", [])]
    if not s.plex_url:
        raise DiscError("Plex is not configured ([plex] url)")
    tok = plex_token(s)
    if not tok:
        raise DiscError("no Plex token ([plex] token, SEEDBOX_PLEX_TOKEN or [physical] plex_token_cmd)")
    plex = Plex(s.plex_url, tok)
    with ui.Spinner("Reading the Plex libraries"):
        catalogs = {"movie": plex.catalog("movie"), "show": plex.catalog("show")}
    keys, missing = [], []
    with ui.Spinner("Resolving the steps") as spin:
        for kind, name, seasons in steps:
            spin.update(f"Resolving {name}")
            item = pick_item(catalogs[kind], name)
            if item is None:
                missing.append(name)
                continue
            found = [item["key"]] if kind == "movie" else plex.episodes(item["key"], seasons)
            keys += found
            what = "film" if kind == "movie" else f"{len(found)} episodes" + (f", seasons {seasons}" if seasons else "")
            ui.info(f"{item['title']} ({item['year']}): {what}")
    for name in missing:
        ui.warn(f"not in Plex yet: {name}")
    if not keys:
        raise DiscError("nothing found in Plex for this playlist")
    if args.dry_run:
        ui.info(f"would rebuild {title!r} with {len(keys)} items")
        return 0
    pid = plex.replace_playlist(title, keys)
    ui.ok(
        f"playlist {title!r} rebuilt: {len(keys)} items (Plex {pid})" + (f", {len(missing)} missing" if missing else "")
    )
    return 0


def cmd_list(s, args):
    if not s.dir:
        raise DiscError("[physical] dir is not set")
    names = listing(s)
    for n in names:
        print(n)
    ui.info(f"{len(names)} disc(s) in {s.host + ':' if s.host else ''}{s.dir}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seedbox disc", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "-c", "--config", help=f"TOML file with [physical] (default: $SEEDBOX_CONFIG, {DEFAULT_CONFIG})"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a disc you own to the Plex library of physical discs")
    add.add_argument("title", help="film title, as on TMDB (Plex matches the film from it)")
    add.add_argument("year", help="release year")
    add.add_argument("--edition", default="", help='Plex edition, e.g. "4K" or "Director\'s Cut"')
    src = add.add_mutually_exclusive_group()
    src.add_argument("--url", help="clip to use (any URL yt-dlp reads)")
    src.add_argument("--query", help="YouTube search instead of '<title> <year> <search_suffix>'")
    src.add_argument("--card", action="store_true", help="no clip: a black card with the title")
    add.add_argument("--text", help="card: line under the title (default [physical] card_text)")
    add.add_argument("--start", type=float, default=0, help="clip: seconds skipped at the start")
    add.add_argument("--length", type=int, help="clip: seconds kept (default [physical] length)")
    add.add_argument("--collection", action="append", help="Plex collection to tag it with (repeatable)")
    add.add_argument("-y", "--yes", action="store_true", help="take the first search result without asking")
    add.add_argument("-n", "--dry-run", action="store_true", help="show what would be done, change nothing")
    add.add_argument("--force", action="store_true", help="replace a disc already there")
    sub.add_parser("list", help="discs already in the folder")
    play = sub.add_parser("playlist", help="list the playlists of [[physical.playlists]], or rebuild one in Plex")
    play.add_argument("title", nargs="?", help="part of the playlist title (none: list them)")
    play.add_argument("-n", "--dry-run", action="store_true", help="resolve the steps, change nothing")
    args = parser.parse_args(argv)
    try:
        s = load_settings(args.config)
        return {"add": cmd_add, "list": cmd_list, "playlist": cmd_playlist}[args.command](s, args)
    except (DiscError, ApiError) as exc:
        ui.ko(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
