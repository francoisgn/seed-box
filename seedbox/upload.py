"""Upload films missing on one tracker through its upload API.

Trackers define their own API: request paths, form fields, authentication,
answer codes. Nothing about a given tracker is built in here: `[upload.api]`
in the config describes it (config.upload_profile checks it), and this module
only follows that description:

- probe (optional): a GET telling whether the account may use the API, its
  answer shown as is on the page (categories, presentation types...);
- submit: a multipart POST with the .torrent, the .nfo and the configured
  fields, whose values may use placeholders ({name}, {year}, {tmdb_id}...);
- answer: which JSON keys hold the code, the message, the torrent id, the
  candidates, and which codes mean success or manual review.

The passkey is only ever placed in the configured headers, never in a URL, a
form field, a log or the page. It is taken from the tracker's announce URL in
qBittorrent unless set in the config.

Before anything is sent, `check` looks for the film on the tracker itself:
targeted searches through Prowlarr (title + year + group, + resolution, then
title + year: some indexers return 50 results at most, a popular title has
more releases than that), TMDB (trackers identify the film from the release
name) and MediaInfo. The release name must say what the file holds (naming
rules shared by trackers: dots, no spaces, no language, resolution or codec
the file does not have): a contradiction blocks the upload.

Sending is off until `[upload] send = true`: the tracker must have approved
the account for its API, and its upload rules must have been checked. Each
upload is a job: .torrent creation (the existing one), upload, then seeding
of that same .torrent. Answers marked for review are never retried; the only
automatic retry follows the tracker's Retry-After.
"""

import email.parser
import email.policy
import json
import os
import re
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from seedbox import actions, create, match, nfo, titles, tmdb
from seedbox.api import ApiError, multipart, request
from seedbox.prowlarr import ProwlarrClient, search_indexers

PROBE_TTL_S = 600
SIZE_SLACK = 0.01  # a release within 1 % of the size is the same content
CHECK_TTL_S = 3600  # a check older than this is redone before sending
# Indexers append a language or the extension to result titles: "…x264-GRP.FRENCH", "…-GRP.mkv.FRENCH".
SUFFIX = re.compile(r"(?i)(?:\.(?:french|multi|vostfr|mkv|mp4|avi))+$")
HEVC = re.compile(r"(?i)(?<![a-z0-9])(x265|h\.?265|hevc)(?![a-z0-9])")
AVC = re.compile(r"(?i)(?<![a-z0-9])(x264|h\.?264|avc)(?![a-z0-9])")
FRENCH_ONLY = ("FRENCH", "TRUEFRENCH", "VFF", "VFQ", "VFI")
WAIT_POLL_S = 5
TMDB_LINK = "https://www.themoviedb.org/movie/{}"

_probe = {"at": 0, "value": None}
_passkeys = {}  # tracker -> passkey read from qBittorrent
_checks = {}  # entry index -> check result
_lock = threading.Lock()
_one_upload = threading.Lock()


class UploadError(Exception):
    def __init__(self, message, code="", status=0, body=None):
        super().__init__(message)
        self.code, self.status, self.body = code, status, body or {}


# ---------- passkey and API client
def passkey(cfg, qbt, snapshot):
    if cfg.upload_passkey:
        return cfg.upload_passkey
    with _lock:
        if cfg.upload_tracker in _passkeys:
            return _passkeys[cfg.upload_tracker]
    try:
        announce, _ = create.tracker_setup(cfg, qbt, snapshot, cfg.upload_tracker)
    except create.CreateError as exc:
        raise UploadError(f"passkey unknown: {exc}") from exc
    m = re.search(cfg.upload_api["passkey_pattern"], announce)
    if not m:
        raise UploadError("passkey unknown: not found in the announce URL, set [upload] passkey")
    with _lock:
        _passkeys[cfg.upload_tracker] = m.group(1)
    return m.group(1)


def fill(template, values):
    """A config template with its placeholders replaced; a missing value is an error."""
    names = {n for _, n, _, _ in string.Formatter().parse(template) if n}
    missing = sorted(n for n in names if values.get(n) in (None, ""))
    if missing:
        raise UploadError(f"no value for {{{missing[0]}}} in the upload API settings")
    return template.format(**{n: values[n] for n in names})


def _headers(cfg, key):
    head = {"Accept": "application/json", "User-Agent": "seedbox"}
    head.update({k: fill(v, {"passkey": key}) for k, v in cfg.upload_api["headers"].items()})
    return head


def _scrub(text, key):
    return str(text).replace(key, "***") if key else str(text)


def _get(answer, path):
    """answer['a']['b'] for the config key "a.b"; None when absent."""
    for part in str(path or "").split(".") if path else []:
        if not isinstance(answer, dict):
            return None
        answer = answer.get(part)
    return answer if path else None


def parse_answer(raw, ctype):
    """(JSON body, attached .torrent or None): a JSON answer, or a multipart one
    (JSON part + a .torrent part, when the tracker rewrote the torrent)."""
    if ctype.startswith("multipart/"):
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw
        )
        body, torrent = {}, None
        for part in msg.iter_parts():
            data = part.get_payload(decode=True) or b""
            if part.get_content_type() == "application/json":
                body = json.loads(data.decode("utf-8", errors="replace"))
            elif part.get_content_type() == "application/x-bittorrent":
                torrent = data
        return body, torrent
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        return (json.loads(text) if text else {}), None
    except ValueError:
        return {"message": text[:300]}, None


def probe(cfg, key, fresh=False):
    """The probe's answer (cached a few minutes); UploadError when access is refused."""
    spec = cfg.upload_api["probe"]
    if not spec:
        return None
    now = time.time()
    with _lock:
        if not fresh and _probe["value"] is not None and now - _probe["at"] < PROBE_TTL_S:
            return _probe["value"]
    status, raw, head = request(cfg.upload_api["base"] + spec["path"], headers=_headers(cfg, key), timeout=60, raw=True)
    body, _ = parse_answer(raw, (head or {}).get("Content-Type", "") if head is not None else "")
    if not 200 <= status < 300:
        raise _refusal(cfg, status, body, key)
    with _lock:
        _probe.update(at=now, value=body)
    return body


def _refusal(cfg, status, body, key):
    keys = cfg.upload_api["answer"]
    code = str(_get(body, keys["code"]) or "")
    message = _scrub(_get(body, keys["message"]) or f"HTTP {status}", key)
    return UploadError(message, code, status, body)


def send(cfg, key, torrent, nfo_text, values):
    """POST the upload as configured. Returns (HTTP status, JSON body, attached .torrent, Retry-After)."""
    spec = cfg.upload_api["submit"]
    name = re.sub(r'["\\\r\n]', "_", values["name"])
    body, ctype = multipart(
        {k: fill(v, values) for k, v in spec["fields"].items()},
        {
            spec["torrent_field"]: (f"{name}.torrent", torrent),
            spec["nfo_field"]: (f"{name}.nfo", nfo_text.encode("utf-8"), "text/plain; charset=utf-8"),
        },
    )
    headers = {**_headers(cfg, key), "Content-Type": ctype}
    status, raw, head = request(
        cfg.upload_api["base"] + spec["path"],
        data=body,
        headers=headers,
        timeout=cfg.upload_api["timeout"],
        raw=True,
    )
    head = head if head is not None else {}
    answer, attached = parse_answer(raw, head.get("Content-Type", ""))
    return status, answer, attached, head.get("Retry-After")


def retry_after(value):
    """Seconds to wait after a rate limit: the tracker's Retry-After, else an hour."""
    value = str(value or "")
    return int(value) if value.isdigit() else 3600


# ---------- rate: attempts in the last hour, and the tracker's Retry-After
def _rate_path(cfg):
    return os.path.join(cfg.output_dir, "upload-rate.json")


def _rate(cfg):
    try:
        with open(_rate_path(cfg), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    now = time.time()
    return {
        "attempts": [t for t in data.get("attempts", []) if now - t < 3600],
        "not_before": data.get("not_before", 0),
    }


def _save_rate(cfg, data):
    os.makedirs(cfg.output_dir, exist_ok=True)
    with open(_rate_path(cfg), "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def rate_state(cfg):
    data, limit = _rate(cfg), cfg.upload_api["limits"]["per_hour"]
    now = time.time()
    wait = max(data["not_before"] - now, 0)
    if len(data["attempts"]) >= limit:
        wait = max(wait, min(data["attempts"]) + 3600 - now)
    return {"used": len(data["attempts"]), "limit": limit, "wait_s": int(wait)}


def _attempt(cfg):
    data = _rate(cfg)
    data["attempts"].append(time.time())
    _save_rate(cfg, data)


def _hold(cfg, seconds):
    data = _rate(cfg)
    data["not_before"] = time.time() + seconds
    _save_rate(cfg, data)


def _log(cfg, record):
    """uploads.jsonl: what was sent and what the tracker answered (no secret)."""
    os.makedirs(cfg.output_dir, exist_ok=True)
    with open(os.path.join(cfg.output_dir, "uploads.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": time.time(), **record}, ensure_ascii=False) + "\n")


# ---------- candidates: films missing on the tracker
def is_film(cfg, entry):
    name = os.path.basename(entry.get("name", ""))
    if cfg.upload_roots and not any(actions._under(entry["path"], r) for r in cfg.upload_roots):
        return False
    return not (titles.is_episode(name) or titles.is_season_folder(name))


def language_group(name):
    """MULTI, FRENCH, VOSTFR or VO from the release name (MediaInfo tells more at check time)."""
    lang = nfo.from_name(name)["language"]
    if lang in ("MULTI", "VF2"):
        return "MULTI"
    if lang == "VOSTFR":
        return "VOSTFR"
    return "FRENCH" if lang else "VO"


def candidates(cfg, snapshot):
    key = cfg.upload_tracker
    seeds = {t["hash"]: t.get("seeds") or 0 for t in snapshot.get("torrents", [])}
    out = []
    for i, e in enumerate(snapshot.get("entries", [])):
        if key in (e.get("trackers") or []) or not is_film(cfg, e):
            continue
        name = os.path.basename(e["name"])
        release = nfo.from_name(name)
        with _lock:
            done = _checks.get(i)
        out.append(
            {
                "index": i,
                "name": e["name"],
                "folder": e.get("folder", ""),
                "size": e.get("size", 0),
                "resolution": e.get("resolution") or release["resolution"],
                "language": language_group(name),
                "group": release["group"],
                "year": release["year"],
                "trackers": e.get("trackers") or [],
                "uploaded": e.get("uploaded", 0),
                "seeds": max((seeds.get(h, 0) for h in e.get("torrents") or []), default=0),
                "search": ((e.get("search") or {}).get(key) or {}).get("verdict", ""),
                "check": {k: done[k] for k in ("verdict", "reasons", "at")} if done else None,
            }
        )
    return out


# ---------- check: is the film already there, does its name say what it holds
def _indexer(cfg):
    if not cfg.prowlarr_enabled:
        raise UploadError("the duplicate check searches the tracker through Prowlarr: set [prowlarr]")
    client = ProwlarrClient(cfg.prowlarr_url, cfg.prowlarr_api_key)
    found = [ix for ix in search_indexers(client, cfg.tracker_aliases) if ix["key"] == cfg.upload_tracker]
    if not found:
        raise UploadError(f"no enabled Prowlarr indexer for {cfg.upload_tracker}: the duplicate check needs one")
    return client, found[0]


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", titles.parse(text)["title"])


def classify(result, local):
    """same_size, same_release (group + resolution), same_resolution, other, or None (another film)."""
    title = result.get("title", "")
    parsed = titles.parse(title)
    if local["year"] and parsed["year"] and parsed["year"] != local["year"]:
        return None
    if parsed["episode"]:
        return None
    if not any(n and (n in _norm(title) or _norm(title) in n) for n in local["titles"]):
        return None
    size = int(result.get("size") or 0)
    if size and abs(size - local["size"]) <= local["size"] * SIZE_SLACK:
        return "same_size"
    group = nfo.from_name(SUFFIX.sub("", title))["group"].lower()
    same_res = bool(local["resolution"]) and parsed["resolution"] == local["resolution"]
    if same_res and group and group == local["group"].lower():
        return "same_release"
    return "same_resolution" if same_res else "other"


def naming(name, release, tech):
    """(blocking, warnings): what the release name says against what the file holds.

    Shared naming rules: dots between the parts, no spaces; never a language,
    resolution or codec the file does not have."""
    blocking, warnings = [], []
    if " " in name:
        warnings.append("the name has spaces: trackers ask for dots, rename the file first (Release matching)")
    if not release["year"]:
        warnings.append("no year in the name")
    if not release["resolution"]:
        warnings.append("no resolution in the name")
    if not tech:
        return blocking, warnings
    if release["resolution"] and tech["resolution"] and release["resolution"] != tech["resolution"]:
        blocking.append(f"the name says {release['resolution']}, the file is {tech['resolution']}")
    codec = tech["video_codec"].lower()
    if HEVC.search(name) and codec in ("x264", "h264"):
        blocking.append(f"the name says HEVC / x265, the video is {tech['video_codec']}")
    if AVC.search(name) and codec in ("x265", "h265"):
        blocking.append(f"the name says AVC / x264, the video is {tech['video_codec']}")
    audio = [a["language"] for a in tech["audio"]]
    langs, subs = set(audio) - {"", "und", "zxx"}, {s["language"] for s in tech["subtitles"]}
    said = release["language"]
    if len(langs) < len(set(audio)):
        warnings.append("an audio track has no language tag: the languages cannot all be confirmed")
    if said in ("MULTI",) and len(langs) < 2 and len(set(audio)) < 2:
        blocking.append(f"the name says MULTI, the file has one audio language ({', '.join(sorted(langs)) or '?'})")
    if said in (*FRENCH_ONLY, "VF2", "MULTI") and langs and "fr" not in langs:
        blocking.append(f"the name says {said}, no French audio track ({', '.join(sorted(langs))})")
    if said == "VF2" and audio.count("fr") < 2:
        warnings.append("VF2 means a VFF and a VFQ track: only one French track found")
    if said == "VOSTFR" and ("fr" in langs or "fr" not in subs):
        blocking.append("VOSTFR means original audio with French subtitles: not what the file holds")
    if not said and "fr" in langs:
        warnings.append("French audio, but no language in the name")
    return blocking, warnings


def check(cfg, snapshot, index):
    """{'verdict': clear|warn|blocked|incomplete, 'reasons', 'matches', 'tmdb', 'languages', ...}."""
    entry = match._entry(snapshot, index)
    if cfg.upload_tracker in (entry.get("trackers") or []):
        raise UploadError(f"already seeded on {cfg.upload_tracker}")
    if not is_film(cfg, entry):
        raise UploadError("films only: this entry looks like a series")
    spec = create.content(cfg, entry)
    name = spec["name"]
    release = nfo.from_name(name)
    blocking, reasons = [], []

    # MediaInfo: the name must say what the file holds.
    tech, languages = None, {}
    try:
        tracks, _ = nfo.run(match.main_file(cfg, entry))
        tech = nfo.from_mediainfo(tracks)
        languages = {
            "audio": [a["language"] or "?" for a in tech["audio"]],
            "subtitles": [s["language"] or "?" for s in tech["subtitles"]],
            "group": nfo.language_from_tracks(tech["audio"], tech["subtitles"]) or "VO",
        }
    except (nfo.NfoError, match.MatchError) as exc:
        reasons.append(f"MediaInfo: {exc}")
    named_block, named_warn = naming(name, release, tech)
    blocking += named_block
    reasons += named_warn

    # TMDB: trackers identify the film from the release name; same search here.
    tmdb_found, ident = [], {"titles": [release["title"]], "year": release["year"], "imdb": "", "id": None}
    if cfg.tmdb_api_key and release["title"]:
        try:
            tmdb_found = tmdb.search(cfg.tmdb_api_key, release["title"], release["year"])
            if tmdb_found:
                ident = tmdb.movie(cfg.tmdb_api_key, tmdb_found[0]["id"])
        except ApiError as exc:
            reasons.append(f"TMDB: {exc}")
    tmdb_ok = bool(tmdb_found) and (not release["year"] or tmdb_found[0]["year"] == release["year"])
    if not cfg.tmdb_api_key:
        reasons.append("no TMDB key: the film's identification is not checked")
    elif not tmdb_ok:
        reasons.append("TMDB knows no film of that title and year: the tracker may not identify it")

    # The tracker itself, through Prowlarr.
    client, indexer = _indexer(cfg)
    local = {
        "titles": [n for n in {_norm(t) for t in ident["titles"] + [release["title"]]} if n],
        "year": release["year"],
        "group": release["group"],
        "resolution": release["resolution"],
        "size": spec["total"],
    }
    queries = match.queries(ident, indexer, release)
    results, errors = [], []

    def run(q):
        try:
            return q, client.search(q, indexer["id"]), None
        except ApiError as exc:
            return q, [], str(exc)

    with ThreadPoolExecutor(max_workers=3) as pool:
        for q, found, err in pool.map(run, queries):
            if err:
                errors.append(f"{q}: {err}")
            results.extend(found)
    seen, matches = set(), []
    for r in results:
        key = (r.get("title"), int(r.get("size") or 0))
        if key in seen:
            continue
        seen.add(key)
        kind = classify(r, local)
        if kind:
            matches.append(
                {
                    "kind": kind,
                    "title": r.get("title", ""),
                    "size": int(r.get("size") or 0),
                    "seeders": int(r.get("seeders") or 0),
                    "info_url": r.get("infoUrl") or "",
                }
            )
    order = {"same_size": 0, "same_release": 1, "same_resolution": 2, "other": 3}
    matches.sort(key=lambda m: (order[m["kind"]], -m["seeders"]))
    kinds = {m["kind"] for m in matches}

    if kinds & {"same_size", "same_release"}:
        blocking.insert(0, "the tracker already has this release (same size, or same group and resolution)")
    if blocking:
        verdict = "blocked"
    elif errors:
        verdict = "incomplete"
        reasons.insert(0, f"{len(errors)} search(es) failed: the tracker may have it")
    elif "same_resolution" in kinds or reasons:
        verdict = "warn"
        if "same_resolution" in kinds:
            reasons.insert(0, "another release in the same resolution is there: may be refused as a dupe")
    else:
        verdict = "clear"
    result = {
        "index": int(index),
        "name": name,
        "size": spec["total"],
        "verdict": verdict,
        "reasons": blocking + reasons,
        "matches": matches[:20],
        "searched": queries,
        "errors": errors,
        "tmdb": [{**t, "url": TMDB_LINK.format(t["id"])} for t in tmdb_found[:3]],
        "tmdb_id": ident.get("id") if tmdb_ok else None,
        "imdb_id": ident.get("imdb") if tmdb_ok else "",
        "languages": languages,
        "at": time.time(),
    }
    with _lock:
        _checks[int(index)] = result
    return result


# ---------- status and sending
def status(cfg, qbt, snapshot):
    """What the page shows above the list: API access, settings, rate."""
    out = {
        "tracker": cfg.upload_tracker,
        "base": cfg.upload_api["base"],
        "send": cfg.upload_send,
        "fields": cfg.upload_api["submit"]["fields"],
        "nfo": cfg.upload_nfo,
        "rate": rate_state(cfg),
        "probe": bool(cfg.upload_api["probe"]),
        "approved": None,
        "access": "",
        "answer": None,
    }
    if not out["probe"]:
        out["access"] = "no probe configured ([upload.api.probe]): access is known at the first upload"
        return out
    key = ""
    try:
        key = passkey(cfg, qbt, snapshot)
        out["answer"] = probe(cfg, key)
        out["approved"] = True
    except UploadError as exc:
        out["approved"] = False if exc.status in (401, 403) else None
        out["access"] = f"{exc.code}: {exc}" if exc.code else str(exc)
        out["answer"] = exc.body or None
    except ApiError as exc:
        out["access"] = _scrub(exc, key)
    if out["answer"] is not None:
        out["answer"] = json.loads(_scrub(json.dumps(out["answer"]), key))
    return out


def start(cfg, qbt_factory, snapshot, indexes):
    """Queue uploads of checked entries. Returns the jobs."""
    if not cfg.upload_send:
        raise UploadError("sending is off ([upload] send = false): approval and the tracker's rules first")
    if not isinstance(indexes, list) or not indexes:
        raise UploadError("entries: a list of library entries")
    jobs = []
    for index in indexes:
        entry = match._entry(snapshot, index)
        with _lock:
            done = _checks.get(int(index))
        if not done or time.time() - done["at"] > CHECK_TTL_S:
            raise UploadError(f"{os.path.basename(entry['name'])}: check it first")
        if done["verdict"] not in ("clear", "warn"):
            raise UploadError(f"{os.path.basename(entry['name'])}: check verdict {done['verdict']}, not sent")
        jobs.append(
            actions.add_job(
                cfg,
                {
                    "action": "upload",
                    "hash": "",
                    "name": done["name"],
                    "from": entry["path"],
                    "target": cfg.upload_tracker,
                    "status": "running",
                    "note": "queued",
                    "entry": int(index),
                },
            )
        )
    threading.Thread(target=_run_all, args=(cfg, qbt_factory, snapshot, jobs), daemon=True).start()
    return jobs


def _run_all(cfg, qbt_factory, snapshot, jobs):
    with _one_upload:
        for job in jobs:
            try:
                _run(cfg, qbt_factory, snapshot, job)
            except (UploadError, create.CreateError, nfo.NfoError, match.MatchError, ApiError, OSError) as exc:
                actions.update_job(cfg, job["id"], status="failed", note=str(exc), finished=time.time())


def _wait_job(cfg, job_id):
    while True:
        job = next((j for j in actions.load_jobs(cfg) if j["id"] == job_id), None)
        if not job or job["status"] in actions.FINISHED:
            return job
        time.sleep(WAIT_POLL_S)


def nfo_text(cfg, release, found, size):
    """The .nfo sent: seedbox's layout or MediaInfo's report alone, within the
    tracker's size limit, and never a path of this machine."""
    limit = cfg.upload_api["limits"]["nfo_max_bytes"]
    if cfg.upload_nfo == "mediainfo":
        text = found["report"].strip() + "\n"
        if len(text.encode()) > limit:
            raise UploadError(f"MediaInfo report of {len(text.encode())} bytes, over the tracker's {limit}")
    else:
        text = nfo.render(release, found["fields"], found["details"], found["report"], size, max_bytes=limit)
    for root in [*cfg.roots, cfg.output_dir, *cfg.path_map]:
        if root and root != "/" and root.rstrip("/") + "/" in text:
            raise UploadError("the .nfo would show a path of this machine: not sent")
    return text


def _run(cfg, qbt_factory, snapshot, job):
    note = lambda text: actions.update_job(cfg, job["id"], note=text)  # noqa: E731
    entry = match._entry(snapshot, job["entry"])
    spec = create.content(cfg, entry)
    found = nfo.describe(spec["name"], match.main_file(cfg, entry), len(spec["files"]))
    if found["missing"]:
        labels = ", ".join(nfo.LABELS[k] for k in found["missing"])
        raise UploadError(f"the .nfo misses {labels}: create it from the Library to fill them, then upload by hand")
    text = nfo_text(cfg, spec["name"], found, spec["total"])
    with _lock:
        checked = _checks.get(job["entry"]) or {}
    fields = found["fields"]
    values = {
        "name": spec["name"],
        "title": fields.get("title", ""),
        "year": fields.get("year", ""),
        "tmdb_id": checked.get("tmdb_id") or "",
        "imdb_id": checked.get("imdb_id") or "",
        "size": str(spec["total"]),
        "resolution": fields.get("resolution", ""),
        "language": fields.get("language", ""),
        "group": fields.get("group", ""),
    }
    for template in cfg.upload_api["submit"]["fields"].values():
        fill(template, values)  # every placeholder has a value, before hashing anything
    qbt = qbt_factory()
    key = passkey(cfg, qbt, snapshot)

    note("creating the .torrent")
    made = create.start(cfg, qbt, snapshot, job["entry"], cfg.upload_tracker, fields)
    made = _wait_job(cfg, made["id"])
    if not made or made["status"] != "done":
        raise UploadError(f".torrent creation {made['status'] if made else 'lost'}: {(made or {}).get('note', '')}")
    with open(create.path_for(cfg, made), "rb") as handle:
        torrent = handle.read()
    limit = cfg.upload_api["limits"]["torrent_max_bytes"]
    if len(torrent) > limit:
        raise UploadError(f".torrent of {len(torrent)} bytes, over the tracker's {limit}")

    keys = cfg.upload_api["answer"]
    while True:
        wait = rate_state(cfg)["wait_s"]
        if wait:
            note(f"waiting {wait // 60 + 1} min: upload rate limit")
            time.sleep(min(wait, 600))
            continue
        note("sending to the tracker")
        _attempt(cfg)
        http_status, answer, attached, retry = send(cfg, key, torrent, text, values)
        if http_status in keys["retry_status"]:
            # The tracker's limit: wait what it says, then try again (the only automatic retry).
            _hold(cfg, retry_after(retry))
            continue
        break
    code = str(_get(answer, keys["code"]) or "")
    tracker_id = _get(answer, keys["id"])
    _log(
        cfg,
        {
            "name": spec["name"],
            "size": spec["total"],
            "infohash": made["hash"],
            "http": http_status,
            "code": code,
            "torrent_id": tracker_id,
        },
    )
    ok = code in keys["success"] if keys["success"] else 200 <= http_status < 300
    if not ok:
        detail = _scrub(_get(answer, keys["message"]) or f"HTTP {http_status}", key)
        if code in keys["review"]:
            names = [
                str(_get(c, keys["candidate_name"]) or "")
                for c in (_get(answer, keys["candidates"]) or [])[:3]
                if isinstance(c, dict)
            ]
            detail = f"manual review: {detail}" + (f" ({'; '.join(names)})" if any(names) else "")
        raise UploadError(f"{code or 'error'}: {detail}", code)
    registered = str(_get(answer, keys["infohash"]) or made["hash"]).lower()
    if registered != made["hash"] or attached:
        raise UploadError(f"{code or 'accepted'}: the tracker registered another torrent, add its .torrent by hand")
    note(f"{code or 'accepted'} (torrent {tracker_id}): seeding")
    create.seed(cfg, qbt, made["id"])
    actions.update_job(
        cfg,
        job["id"],
        status="done",
        hash=made["hash"],
        note=f"{code or 'accepted'}: torrent {tracker_id}, seeding",
        finished=time.time(),
    )


def handle(cfg, qbt_factory, body):
    """HTTP helper for POST /api/upload: (status code, response dict).

    {"op": "check", "entry": i} | {"op": "send", "entries": [i, ...]}"""
    try:
        request_ = json.loads(body or b"{}")
        if not isinstance(request_, dict):
            raise ValueError
    except ValueError:
        return 400, {"error": "invalid JSON body"}
    if not cfg.upload_enabled:
        return 404, {"error": "no upload API configured ([upload] tracker and [upload.api])"}
    if not cfg.actions:
        return 403, {"error": "actions are disabled ([service] actions = true to enable)"}
    op = request_.get("op")
    try:
        snapshot = match.load_snapshot(cfg)
        if op == "check":
            return 200, check(cfg, snapshot, request_.get("entry"))
        if op == "send":
            return 200, {"jobs": start(cfg, qbt_factory, snapshot, request_.get("entries"))}
    except (UploadError, create.CreateError, match.MatchError, nfo.NfoError) as exc:
        return 400, {"error": str(exc)}
    except ApiError as exc:
        return 502, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": f"file access: {exc}"}
    return 400, {"error": f"unknown op: {op!r}"}


def overview(cfg, qbt_factory):
    """GET /api/upload: status and candidates."""
    if not cfg.upload_enabled:
        return 404, {"error": "no upload API configured ([upload] tracker and [upload.api])"}
    try:
        snapshot = match.load_snapshot(cfg)
    except match.MatchError as exc:
        return 400, {"error": str(exc)}
    try:
        state = status(cfg, qbt_factory(), snapshot)
    except ApiError as exc:
        state = {"access": str(exc)}
    return 200, {"status": state, "actions": cfg.actions, "films": candidates(cfg, snapshot)}
