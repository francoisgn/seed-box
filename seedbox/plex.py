"""Plex Media Server, read-only: what the Plex page shows.

Libraries (size, items, watched, last scan, unmatched, missing files), films
never watched with their seeding status, playback in progress with the disk
load, films likely to be transcoded on a given player, recently added, server
state (version, update, maintenance tasks, size of its database and metadata).

The token comes from [plex] token / SEEDBOX_PLEX_TOKEN, or from Plex's own
Preferences.xml when its config folder is mounted (read-only) at [plex]
data_dir. Plex and seedbox see the media under the same /media paths, so a
file Plex plays is matched to the library entry that holds it.
"""

import json
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

from seedbox.api import ApiError, request

SUPPORT = "Library/Application Support/Plex Media Server"
# Folders of Plex's data directory worth knowing the size of.
DATA_PARTS = {"database": "Plug-in Support/Databases", "metadata": "Metadata", "media": "Media", "cache": "Cache"}
DATA_TTL_S = 6 * 3600
LIBRARY_TTL_S = 600

# Players and what makes them leave Direct Play (the server, a NAS without
# HEVC 10-bit decoding, cannot transcode 4K): (test, device, text).
DEVICES = {"ps5": "PS5 (Plex app)", "appletv": "Apple TV 4K (Plex app)"}
OLD_VIDEO = {
    "mpeg4",
    "msmpeg4",
    "msmpeg4v2",
    "msmpeg4v3",
    "xvid",
    "divx",
    "mpeg2video",
    "mpeg1video",
    "vc1",
    "wmv3",
    "h263",
}
OLD_CONTAINER = {"avi", "mpeg", "mpegts", "wmv", "asf", "divx"}
IMAGE_SUBS = {"pgs", "hdmv_pgs_subtitle", "vobsub", "dvd_subtitle", "dvdsub"}
STYLED_SUBS = {"ass", "ssa"}

_cache = {"library": None, "at": 0, "data": None, "data_at": 0, "details_running": False}
_lock = threading.Lock()


class PlexError(Exception):
    pass


def token(cfg):
    """The configured token, else the one in Plex's Preferences.xml (mounted config)."""
    if cfg.plex_token:
        return cfg.plex_token
    path = os.path.join(cfg.plex_data_dir, SUPPORT, "Preferences.xml")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            m = re.search(r'PlexOnlineToken="([^"]+)"', handle.read())
    except OSError:
        return ""
    return m.group(1) if m else ""


class PlexClient:
    def __init__(self, url, tok, timeout=30):
        self.url = url.rstrip("/")
        self.tok = tok

    def get(self, path, **params):
        params["X-Plex-Token"] = self.tok
        status, body, _ = request(f"{self.url}{path}?{urllib.parse.urlencode(params)}", timeout=30)
        if status == 401:
            raise ApiError("Plex: token refused")
        if status != 200:
            raise ApiError(f"Plex: HTTP {status} on {path}")
        try:
            return ET.fromstring(body)
        except ET.ParseError as exc:
            raise ApiError(f"Plex: unreadable answer on {path}") from exc


def client(cfg):
    if not cfg.plex_url:
        raise PlexError("Plex is not configured ([plex] url)")
    tok = token(cfg)
    if not tok:
        raise PlexError("no Plex token: [plex] token, SEEDBOX_PLEX_TOKEN, or Plex's config mounted at [plex] data_dir")
    return PlexClient(cfg.plex_url, tok)


# ---------- pure helpers (tested)
def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def external_ids(item):
    return {g.get("id", "").split("://")[0]: g.get("id", "").split("://", 1)[-1] for g in item.findall("Guid")}


def media_of(item):
    """(Media element attributes, [files], total size) of the first version of an item."""
    m = item.find("Media")
    if m is None:
        return {}, [], 0
    parts = m.findall("Part")
    return dict(m.attrib), [p.get("file", "") for p in parts], sum(_int(p.get("size")) for p in parts)


def streams_of(detail):
    """[{'type': video|audio|subtitle, 'codec', 'profile', 'dovi'}] from /library/metadata/<id>."""
    kinds = {"1": "video", "2": "audio", "3": "subtitle"}
    out = []
    m = detail.find(".//Media")
    if m is None:
        return out
    for s in m.iter("Stream"):
        kind = kinds.get(s.get("streamType"))
        if kind:
            out.append(
                {
                    "type": kind,
                    "codec": (s.get("codec") or "").lower(),
                    "profile": (s.get("profile") or "").lower(),
                    "dovi": _int(s.get("DOVIProfile")) if s.get("DOVIPresent") == "1" or s.get("DOVIProfile") else 0,
                }
            )
    return out


def risks(media, streams):
    """{'ps5': [reasons], 'appletv': [reasons]}: what leaves Direct Play on each player.

    media: Media attributes (container, videoCodec, audioCodec); streams: streams_of().
    The first audio track counts (the one played by default)."""
    out = {k: [] for k in DEVICES}
    video = (media.get("videoCodec") or "").lower()
    container = (media.get("container") or "").lower()
    audio = [s for s in streams if s["type"] == "audio"]
    first_audio = audio[0]["codec"] if audio else (media.get("audioCodec") or "").lower()
    first_profile = audio[0]["profile"] if audio else ""
    dovi = max([s["dovi"] for s in streams if s["type"] == "video"] or [0])
    subs = {s["codec"] for s in streams if s["type"] == "subtitle"}
    if video == "av1":
        out["ps5"].append("AV1 video: transcoded (impossible on this NAS)")
        out["appletv"].append("AV1 video: no hardware decoding on Apple TV 4K")
    if video in OLD_VIDEO or container in OLD_CONTAINER:
        out["ps5"].append(f"{(container or video).upper()} / {video or '?'}: transcoded")
    if video in ("vc1", "wmv3"):
        out["appletv"].append("VC-1 / WMV video: transcoded")
    if dovi == 5:
        out["ps5"].append("Dolby Vision profile 5: wrong colours, no HDR10 layer")
    if dovi == 7:
        for k in out:
            out[k].append("Dolby Vision profile 7: HDR10 fallback")
    if first_audio == "truehd":
        out["ps5"].append("TrueHD audio: audio transcoded")
    if first_audio in ("dca", "dts") or first_profile.startswith("dts"):
        out["ps5"].append("DTS audio: audio transcoded")
    if subs & IMAGE_SUBS:
        out["ps5"].append("PGS/VobSub subtitles: burned in (video transcode) when shown")
    if subs & STYLED_SUBS:
        out["ps5"].append("ASS subtitles: burned in when shown")
    return out


def resolution(media):
    r = (media.get("videoResolution") or "").lower()
    return {"4k": "2160p", "1080": "1080p", "720": "720p", "576": "SD", "480": "SD", "sd": "SD"}.get(r, r or "?")


def entry_index(snapshot):
    """Library entry path -> entry, to find the entry that holds a file Plex plays."""
    return {e["path"].rstrip("/"): e for e in snapshot.get("entries", []) if e.get("path")}


def seed_of(index, path):
    p = path
    while p and p != "/":
        e = index.get(p)
        if e:
            return {"status": e.get("status"), "coverage": e.get("coverage"), "trackers": e.get("trackers", [])}
        p = os.path.dirname(p)
    return None


# ---------- data directory size (slow on a busy disk: computed in the background)
def _tree_size(top):
    total, stack = 0, [top]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for x in it:
                    try:
                        if x.is_dir(follow_symlinks=False):
                            stack.append(x.path)
                        elif x.is_file(follow_symlinks=False):
                            total += x.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _refresh_data(base):
    sizes = {k: _tree_size(os.path.join(base, SUPPORT, v)) for k, v in DATA_PARTS.items()}
    with _lock:
        _cache.update(data={"sizes": sizes, "at": int(time.time())}, data_at=time.time())


def data_sizes(cfg):
    base = cfg.plex_data_dir
    if not base or not os.path.isdir(os.path.join(base, SUPPORT)):
        return None
    with _lock:
        stale = time.time() - _cache["data_at"] > DATA_TTL_S
        if stale:
            _cache["data_at"] = time.time()  # one refresh at a time
    if stale:
        threading.Thread(target=_refresh_data, args=(base,), daemon=True).start()
    return _cache["data"]


# ---------- stream details of films, cached on disk (one request per film, once)
def _details_path(cfg):
    return os.path.join(cfg.output_dir, "plex-streams.json")


def _load_details(cfg):
    try:
        with open(_details_path(cfg), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_details(cfg, details):
    os.makedirs(cfg.output_dir, exist_ok=True)
    tmp = _details_path(cfg) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(details, handle)
    os.replace(tmp, _details_path(cfg))


def _fetch_details(cfg, wanted):
    """Background: stream details of the films not analysed yet, one request each, saved as they come."""
    try:
        px = client(cfg)
        details = _load_details(cfg)
        for n, (rk, upd) in enumerate(wanted, 1):
            try:
                details[rk] = {"u": upd, "s": streams_of(px.get(f"/library/metadata/{rk}"))}
            except ApiError:
                continue
            if n % 25 == 0:
                _save_details(cfg, details)
        _save_details(cfg, details)
        with _lock:
            _cache["at"] = 0  # the next call lists again, with the new details
    except (PlexError, OSError):
        pass
    finally:
        with _lock:
            _cache["details_running"] = False


def _start_details(cfg, wanted):
    with _lock:
        if _cache["details_running"] or not wanted:
            return
        _cache["details_running"] = True
    threading.Thread(target=_fetch_details, args=(cfg, wanted), daemon=True).start()


# ---------- libraries
def _library(px, cfg, snapshot):
    sections = px.get("/library/sections").findall("Directory")
    index = entry_index(snapshot)
    details = _load_details(cfg)
    wanted = []
    libs, films, progress = [], [], []
    for s in sections:
        key, kind = s.get("key"), s.get("type")
        lib = {
            "key": key,
            "title": s.get("title"),
            "type": kind,
            "items": 0,
            "seasons": 0,
            "episodes": 0,
            "bytes": 0,
            "watched": 0,
            "in_progress": 0,
            "unmatched": 0,
            "missing": 0,
            "scanned_at": _int(s.get("scannedAt")),
            "refreshing": s.get("refreshing") == "1",
            "paths": [loc.get("path") for loc in s.findall("Location")],
        }
        if kind == "movie":
            for v in px.get(f"/library/sections/{key}/all", includeGuids=1).findall("Video"):
                media, files, size = media_of(v)
                lib["items"] += 1
                lib["bytes"] += size
                lib["missing"] += sum(1 for f in files if f and not os.path.exists(f))
                ids = external_ids(v)
                if not (ids.get("tmdb") or ids.get("imdb") or ids.get("tvdb")):
                    lib["unmatched"] += 1
                viewed = _int(v.get("viewCount")) > 0
                lib["watched"] += viewed
                if _int(v.get("viewOffset")):
                    lib["in_progress"] += 1
                    progress.append(_progress(v, s.get("title")))
                rk, upd = v.get("ratingKey"), v.get("updatedAt")
                d = details.get(rk)
                if not d or d.get("u") != upd:
                    wanted.append((rk, upd))
                    d = None
                film = {
                    "title": v.get("title"),
                    "year": v.get("year"),
                    "library": s.get("title"),
                    "bytes": size,
                    "resolution": resolution(media),
                    "video": media.get("videoCodec"),
                    "audio": media.get("audioCodec"),
                    "watched": viewed,
                    "added_at": _int(v.get("addedAt")),
                    "file": files[0] if files else "",
                    "seed": seed_of(index, files[0]) if files else None,
                    "analysed": d is not None,
                }
                film["risks"] = risks(media, d["s"] if d else [])
                films.append(film)
        elif kind == "show":
            shows = px.get(f"/library/sections/{key}/all", includeGuids=1).findall("Directory")
            lib["items"] = len(shows)
            lib["seasons"] = sum(_int(x.get("childCount")) for x in shows)
            lib["unmatched"] = sum(
                1 for x in shows if not any(external_ids(x).get(k) for k in ("tmdb", "tvdb", "imdb"))
            )
            for e in px.get(f"/library/sections/{key}/all", type=4).findall("Video"):
                _, files, size = media_of(e)
                lib["episodes"] += 1
                lib["bytes"] += size
                lib["missing"] += sum(1 for f in files if f and not os.path.exists(f))
                lib["watched"] += _int(e.get("viewCount")) > 0
                if _int(e.get("viewOffset")):
                    lib["in_progress"] += 1
                    progress.append(_progress(e, s.get("title")))
        libs.append(lib)
    _start_details(cfg, wanted)
    pending = sum(1 for f in films if not f["analysed"])
    return {
        "libraries": libs,
        "films": films,
        "progress": progress,
        "analysis_pending": pending,
        "at": int(time.time()),
    }


def _progress(v, library):
    duration = _int(v.get("duration"))
    title = v.get("title")
    if v.get("type") == "episode":
        title = f"{v.get('grandparentTitle')} · S{_int(v.get('parentIndex')):02d}E{_int(v.get('index')):02d} · {title}"
    return {
        "title": title,
        "library": library,
        "pct": round(_int(v.get("viewOffset")) / duration * 100, 1) if duration else 0,
        "at": _int(v.get("lastViewedAt") or v.get("updatedAt")),
    }


def library(cfg, snapshot, force=False):
    """The library part (cached LIBRARY_TTL_S: listing every section is not free)."""
    with _lock:
        cached = _cache["library"]
        if cached and not force and time.time() - _cache["at"] < LIBRARY_TTL_S:
            return cached
    data = _library(client(cfg), cfg, snapshot)
    with _lock:
        _cache.update(library=data, at=time.time())
    return data


# ---------- live part
def sessions(px):
    out = []
    for v in px.get("/status/sessions"):
        player = v.find("Player")
        ts = v.find("TranscodeSession")
        media = v.find("Media")
        sess = v.find("Session")
        title = v.get("title")
        if v.get("type") == "episode":
            title = (
                f"{v.get('grandparentTitle')} · S{_int(v.get('parentIndex')):02d}E{_int(v.get('index')):02d} · {title}"
            )
        if ts is None:
            decision = "direct play"
        elif "transcode" in (ts.get("videoDecision"), ts.get("audioDecision")):
            decision = "transcode" if ts.get("videoDecision") == "transcode" else "audio transcode"
        else:
            decision = "direct stream"
        duration = _int(v.get("duration"))
        out.append(
            {
                "title": title,
                "year": v.get("year"),
                "player": (player.get("title") or player.get("product")) if player is not None else "?",
                "product": player.get("product") if player is not None else "",
                "state": player.get("state") if player is not None else "",
                "decision": decision,
                "kbps": _int(sess.get("bandwidth") if sess is not None else 0)
                or _int(media.get("bitrate") if media is not None else 0),
                "resolution": resolution(dict(media.attrib)) if media is not None else "?",
                "progress": round(_int(v.get("viewOffset")) / duration * 100, 1) if duration else 0,
            }
        )
    return out


def live(cfg):
    px = client(cfg)
    ident = px.get("/")
    activities = [
        {"title": a.get("title"), "subtitle": a.get("subtitle"), "progress": _int(a.get("progress"))}
        for a in px.get("/activities").findall("Activity")
    ]
    butler = [
        {
            "name": t.get("name"),
            "title": t.get("title") or t.get("name"),
            "enabled": t.get("enabled") == "1",
            "interval": _int(t.get("interval")),
        }
        for t in px.get("/butler").findall("ButlerTask")
    ]
    prefs = {p.get("id"): p.get("value") for p in px.get("/:/prefs").findall("Setting")}
    upd = px.get("/updater/status")
    releases = [{"version": r.get("version"), "state": r.get("state")} for r in upd.findall("Release")]
    recent = []
    for x in list(px.get("/library/recentlyAdded", **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 15})):
        title = x.get("title")
        if x.get("type") == "season":
            title = f"{x.get('parentTitle')} · {title}"
        elif x.get("type") == "episode":
            title = f"{x.get('grandparentTitle')} · {title}"
        recent.append(
            {
                "title": title,
                "year": x.get("year") or x.get("parentYear"),
                "type": x.get("type"),
                "library": x.get("librarySectionTitle"),
                "added_at": _int(x.get("addedAt")),
            }
        )
    return {
        "server": {
            "name": ident.get("friendlyName"),
            "version": ident.get("version"),
            "platform": ident.get("platform"),
            "update": releases[0] if releases else None,
            "update_checked": _int(upd.get("checkedAt")),
            "window": [_int(prefs.get("ButlerStartHour")), _int(prefs.get("ButlerEndHour"))],
            "butler": butler,
            "data": data_sizes(cfg),
        },
        "activities": activities,
        "sessions": sessions(px),
        "recent": recent,
    }


def playback(cfg):
    """Streams playing now and their bitrate, for the system metrics (None when Plex is not set)."""
    if not cfg.plex_url:
        return None
    try:
        s = sessions(client(cfg))
    except (ApiError, PlexError):
        return None
    return {
        "plex_streams": len(s),
        "plex_kbps": sum(x["kbps"] for x in s),
        "plex_transcodes": sum(1 for x in s if x["decision"] == "transcode"),
    }


def overview(cfg, snapshot, force=False):
    """GET /api/plex: (status code, body)."""
    if not cfg.plex_url:
        return 200, {"configured": False}
    try:
        body = {"configured": True, **live(cfg), **library(cfg, snapshot, force)}
    except (ApiError, PlexError) as exc:
        return 502, {"configured": True, "error": str(exc)}
    return 200, body
