"""Configuration: one TOML file (connections, secrets, schedule), overridable
by environment variables.

Lookup order for the file: --config, $SEEDBOX_CONFIG, ./seedbox.toml,
/config/seedbox.toml. Every secret can also come from a file through the
`<VAR>_FILE` convention (Docker secrets), e.g. SEEDBOX_QBT_PASSWORD_FILE.
"""

import hashlib
import json
import os
import stat
import tomllib
from dataclasses import dataclass, field

from seedbox import schedule as sched

DEFAULT_PATHS = ("seedbox.toml", "/config/seedbox.toml")

DEFAULT_SKIP_DIRS = [".cross-seed", "@eaDir", "#recycle", "#snapshot", ".Trash-1000"]

DEFAULT_MEDIA_EXT = [
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".iso", ".divx", ".flv", ".webm",
]  # fmt: skip


class ConfigError(Exception):
    pass


@dataclass
class Config:
    roots: list = field(default_factory=list)
    max_depth: int = 4
    skip_dirs: list = field(default_factory=lambda: list(DEFAULT_SKIP_DIRS))
    media_ext: list = field(default_factory=lambda: list(DEFAULT_MEDIA_EXT))

    qbt_url: str = "http://localhost:8080"
    qbt_username: str = "admin"
    qbt_password: str = ""
    # qBittorrent path prefix -> same location as seen by seedbox.
    path_map: dict = field(default_factory=dict)

    prowlarr_url: str = ""
    prowlarr_api_key: str = ""
    # TMDB API key (optional): titles to search trackers with, when matching releases.
    tmdb_api_key: str = ""

    # Plex Media Server (optional): the Plex page. Token: here, SEEDBOX_PLEX_TOKEN,
    # or read from Plex's Preferences.xml when its config folder is mounted at data_dir.
    plex_url: str = ""
    plex_token: str = ""
    plex_data_dir: str = "/plex"

    # Announce or indexer host -> tracker display name, to merge hosts that
    # belong to the same tracker or give them a readable name.
    tracker_aliases: dict = field(default_factory=dict)

    # Folders holding cross-seed links (path component names): torrents found
    # there are linked copies, not library content.
    link_dirs: list = field(default_factory=lambda: [".cross-seed"])
    # Folder of transient downloads (partial, one-off, public): finished
    # torrents there may be removed with their files from the dashboard.
    transient_dir: str = ""
    # qBittorrent category of cross-seed link torrents (cross-seed linkCategory).
    link_category: str = "cross-seed-link"
    # A cross-seed match that cannot finish (the missing bytes share a piece with
    # the video, or lie inside it) and has had no seeder for this many days is
    # reported as dead, to remove. 0 = off.
    dead_partial_days: int = 7

    # cross-seed database, read-only (search history per tracker). Missing = off.
    cross_seed_db: str = "/cross-seed/cross-seed.db"

    output_dir: str = "/data"
    csv_delimiter: str = ","
    # .torrent files created for upload kept at most (the oldest one is replaced).
    created_max: int = 10
    # `seedbox run`: fixed schedule ("sun 04:00") or, if empty, a period.
    schedule: str = ""
    interval_hours: float = 24.0
    port: int = 8080
    # Dashboard write actions (move, recheck, start, remove torrents in qBittorrent).
    # Off by default: the dashboard has no authentication.
    actions: bool = False
    # Host and qBittorrent sampling for the system charts (seconds, 0 = off).
    metrics_interval: int = 300
    metrics_days: int = 14

    # Upload API of one tracker (optional): checks, then .torrent + .nfo sent to it.
    upload_tracker: str = ""
    # The API as the tracker defines it ([upload.api]): requests, fields, answers.
    upload_api: dict = field(default_factory=dict)
    # Secret; empty = taken from the tracker's announce URL in qBittorrent.
    upload_passkey: str = ""
    # Real uploads stay off until the tracker approved the account and its rules were checked.
    upload_send: bool = False
    # .nfo sent: "seedbox" (header, summary, MediaInfo report) or "mediainfo" (the report only).
    upload_nfo: str = "seedbox"
    # Library roots holding films (empty = every root; series are told apart by name).
    upload_roots: list = field(default_factory=list)

    source: str = ""
    warnings: list = field(default_factory=list)

    @property
    def prowlarr_enabled(self):
        return bool(self.prowlarr_url)

    @property
    def upload_enabled(self):
        return bool(self.upload_api and self.upload_tracker)

    @property
    def match_enabled(self):
        """Release matching searches the trackers through Prowlarr."""
        return self.prowlarr_enabled


# Values an [upload.api] template may use. {passkey} only in headers: never in a URL or a form field.
UPLOAD_PLACEHOLDERS = {
    "passkey",
    "name",
    "title",
    "year",
    "tmdb_id",
    "imdb_id",
    "size",
    "resolution",
    "language",
    "group",
}
UPLOAD_ANSWER_DEFAULTS = {
    "code": "code", "message": "message", "success": [], "review": [], "id": "id", "infohash": "",
    "candidates": "", "candidate_name": "name", "retry_status": [429],
}  # fmt: skip
UPLOAD_LIMIT_DEFAULTS = {"per_hour": 30, "nfo_max_bytes": 65535, "torrent_max_bytes": 3 * 1048576}


def _templates(where, values, passkey_ok):
    import string

    out = {}
    for key, value in (values or {}).items():
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ConfigError(f"[upload.api] {where}.{key}: a string or a number")
        value = str(value)
        try:
            names = {n for _, n, _, _ in string.Formatter().parse(value) if n is not None}
        except ValueError as exc:
            raise ConfigError(f"[upload.api] {where}.{key}: {exc}") from exc
        unknown = names - UPLOAD_PLACEHOLDERS
        if unknown:
            raise ConfigError(f"[upload.api] {where}.{key}: unknown placeholder {{{sorted(unknown)[0]}}}")
        if "passkey" in names and not passkey_ok:
            raise ConfigError(f"[upload.api] {where}.{key}: {{passkey}} goes in headers only")
        out[str(key)] = value
    return out


def upload_profile(raw):
    """[upload.api] checked and completed: every request, field and answer key
    comes from the config, nothing about a given tracker is built in."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("[upload.api]: a table")
    base = str(raw.get("base", "")).rstrip("/")
    if not base.startswith("https://"):
        raise ConfigError("[upload.api] base: an https:// URL (the passkey travels with each request)")
    submit = raw.get("submit") or {}
    for key in ("path", "torrent_field", "nfo_field"):
        if not submit.get(key):
            raise ConfigError(f"[upload.api] submit.{key} is required")
    probe = raw.get("probe") or {}
    answer = {**UPLOAD_ANSWER_DEFAULTS, **(raw.get("answer") or {})}
    for key in ("success", "review", "retry_status"):
        if not isinstance(answer[key], list):
            raise ConfigError(f"[upload.api] answer.{key}: a list")
    limits = {**UPLOAD_LIMIT_DEFAULTS, **(raw.get("limits") or {})}
    try:
        limits = {k: int(v) for k, v in limits.items()}
    except (TypeError, ValueError) as exc:
        raise ConfigError("[upload.api] limits: numbers") from exc
    return {
        "base": base,
        "headers": _templates("headers", raw.get("headers"), True),
        "probe": {"path": str(probe["path"]), "method": str(probe.get("method", "GET")).upper()}
        if probe.get("path")
        else {},
        "submit": {
            "path": str(submit["path"]),
            "method": str(submit.get("method", "POST")).upper(),
            "torrent_field": str(submit["torrent_field"]),
            "nfo_field": str(submit["nfo_field"]),
            "fields": _templates("submit.fields", submit.get("fields"), False),
        },
        "answer": answer,
        "limits": limits,
        "timeout": int(raw.get("timeout", 300)),
        "passkey_pattern": str(raw.get("passkey_pattern", r"/([A-Za-z0-9]{8,64})/announce")),
    }


def _env(name):
    """Value of $NAME, or content of the file named by $NAME_FILE."""
    value = os.environ.get(name)
    if value:
        return value
    path = os.environ.get(name + "_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                # An empty file means "not set", so the config file value applies.
                return handle.read().strip() or None
        except OSError as exc:
            raise ConfigError(f"cannot read {name}_FILE ({path}): {exc}") from exc
    return None


def _find_file(explicit):
    if explicit:
        if not os.path.isfile(explicit):
            raise ConfigError(f"config file not found: {explicit}")
        return explicit
    from_env = os.environ.get("SEEDBOX_CONFIG")
    if from_env:
        if not os.path.isfile(from_env):
            raise ConfigError(f"SEEDBOX_CONFIG points to a missing file: {from_env}")
        return from_env
    for path in DEFAULT_PATHS:
        if os.path.isfile(path):
            return path
    return None


def _split(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def load(path=None):
    cfg = Config()
    found = _find_file(path)
    data = {}
    if found:
        try:
            with open(found, "rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"invalid config file {found}: {exc}") from exc
        cfg.source = found

    library = data.get("library", {})
    cfg.roots = list(library.get("roots", cfg.roots))
    cfg.max_depth = int(library.get("max_depth", cfg.max_depth))
    cfg.skip_dirs = list(library.get("skip_dirs", cfg.skip_dirs))
    cfg.media_ext = [e.lower() for e in library.get("media_ext", cfg.media_ext)]
    cfg.link_dirs = list(library.get("link_dirs", cfg.link_dirs))
    cfg.transient_dir = library.get("transient_dir", cfg.transient_dir).rstrip("/")
    cfg.link_category = data.get("cross_seed", {}).get("link_category", cfg.link_category)
    cfg.cross_seed_db = data.get("cross_seed", {}).get("db", cfg.cross_seed_db)
    cfg.dead_partial_days = int(data.get("cross_seed", {}).get("dead_partial_days", cfg.dead_partial_days))

    qbt = data.get("qbittorrent", {})
    cfg.qbt_url = qbt.get("url", cfg.qbt_url)
    cfg.qbt_username = qbt.get("username", cfg.qbt_username)
    cfg.qbt_password = qbt.get("password", cfg.qbt_password)
    cfg.path_map = dict(qbt.get("path_map", {}))

    prowlarr = data.get("prowlarr", {})
    cfg.prowlarr_url = prowlarr.get("url", cfg.prowlarr_url)
    cfg.prowlarr_api_key = prowlarr.get("api_key", cfg.prowlarr_api_key)

    cfg.tmdb_api_key = data.get("tmdb", {}).get("api_key", cfg.tmdb_api_key)

    plex = data.get("plex", {})
    cfg.plex_url = plex.get("url", cfg.plex_url)
    cfg.plex_token = plex.get("token", cfg.plex_token)
    cfg.plex_data_dir = plex.get("data_dir", cfg.plex_data_dir)

    cfg.tracker_aliases = {k.lower(): v for k, v in data.get("trackers", {}).get("aliases", {}).items()}

    output = data.get("output", {})
    cfg.output_dir = output.get("dir", cfg.output_dir)
    cfg.csv_delimiter = output.get("csv_delimiter", cfg.csv_delimiter)
    cfg.created_max = max(int(output.get("created_max", cfg.created_max)), 1)

    upload = data.get("upload", {})
    cfg.upload_tracker = str(upload.get("tracker", cfg.upload_tracker)).lower()
    cfg.upload_api = upload_profile(upload.get("api"))
    cfg.upload_passkey = upload.get("passkey", cfg.upload_passkey)
    cfg.upload_send = bool(upload.get("send", cfg.upload_send))
    cfg.upload_nfo = upload.get("nfo", cfg.upload_nfo)
    cfg.upload_roots = [r.rstrip("/") for r in upload.get("roots", cfg.upload_roots)]

    service = data.get("service", {})
    cfg.schedule = service.get("schedule", cfg.schedule)
    cfg.interval_hours = float(service.get("interval_hours", cfg.interval_hours))
    cfg.port = int(service.get("port", cfg.port))
    cfg.actions = bool(service.get("actions", cfg.actions))
    cfg.metrics_interval = int(service.get("metrics_interval", cfg.metrics_interval))
    cfg.metrics_days = int(service.get("metrics_days", cfg.metrics_days))

    overrides = {
        "SEEDBOX_ROOTS": ("roots", _split),
        "SEEDBOX_MAX_DEPTH": ("max_depth", int),
        "SEEDBOX_QBT_URL": ("qbt_url", str),
        "SEEDBOX_QBT_USERNAME": ("qbt_username", str),
        "SEEDBOX_QBT_PASSWORD": ("qbt_password", str),
        "SEEDBOX_PROWLARR_URL": ("prowlarr_url", str),
        "SEEDBOX_PROWLARR_API_KEY": ("prowlarr_api_key", str),
        "SEEDBOX_TMDB_API_KEY": ("tmdb_api_key", str),
        "SEEDBOX_PLEX_URL": ("plex_url", str),
        "SEEDBOX_PLEX_TOKEN": ("plex_token", str),
        "SEEDBOX_UPLOAD_PASSKEY": ("upload_passkey", str),
        "SEEDBOX_OUTPUT_DIR": ("output_dir", str),
        "SEEDBOX_SCHEDULE": ("schedule", str),
        "SEEDBOX_INTERVAL_HOURS": ("interval_hours", float),
        "SEEDBOX_PORT": ("port", int),
        "SEEDBOX_ACTIONS": ("actions", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
        "SEEDBOX_METRICS_INTERVAL": ("metrics_interval", int),
    }
    for name, (attr, cast) in overrides.items():
        value = _env(name)
        if value is not None:
            try:
                setattr(cfg, attr, cast(value))
            except ValueError as exc:
                raise ConfigError(f"invalid value for {name}: {value!r}") from exc

    cfg.qbt_url = cfg.qbt_url.rstrip("/")
    cfg.prowlarr_url = cfg.prowlarr_url.rstrip("/")
    cfg.plex_url = cfg.plex_url.rstrip("/")
    cfg.roots = [r.rstrip("/") or "/" for r in cfg.roots]
    # Longest prefix first, so nested mappings win.
    cfg.path_map = dict(
        sorted(((k.rstrip("/"), v.rstrip("/")) for k, v in cfg.path_map.items()), key=lambda kv: -len(kv[0]))
    )

    if cfg.schedule:
        try:
            sched.parse(cfg.schedule)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if found and (
        qbt.get("password")
        or prowlarr.get("api_key")
        or data.get("tmdb", {}).get("api_key")
        or upload.get("passkey")
        or plex.get("token")
    ):
        mode = os.stat(found).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            cfg.warnings.append(f"{found} holds secrets but is readable by others (mode {mode & 0o777:o}), use 600")
    if not cfg.roots:
        raise ConfigError("no library root configured ([library] roots or SEEDBOX_ROOTS)")
    if cfg.upload_api and not cfg.upload_tracker:
        raise ConfigError("[upload.api] set without the tracker it belongs to ([upload] tracker)")
    if cfg.upload_nfo not in ("mediainfo", "seedbox"):
        raise ConfigError('[upload] nfo: "mediainfo" or "seedbox"')
    if cfg.prowlarr_enabled and not cfg.prowlarr_api_key:
        raise ConfigError("Prowlarr URL set without an API key (SEEDBOX_PROWLARR_API_KEY)")
    return cfg


def fingerprint(cfg):
    """Short hash of what shapes a collection (no secret, no schedule/output)."""
    relevant = {
        "roots": cfg.roots, "max_depth": cfg.max_depth, "skip_dirs": cfg.skip_dirs, "media_ext": cfg.media_ext,
        "qbt_url": cfg.qbt_url, "qbt_username": cfg.qbt_username, "path_map": cfg.path_map,
        "prowlarr_url": cfg.prowlarr_url, "tracker_aliases": cfg.tracker_aliases, "link_dirs": cfg.link_dirs,
        "cross_seed_db": cfg.cross_seed_db,
    }  # fmt: skip
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:12]


def map_path(cfg, path):
    """Translate a path reported by qBittorrent into the path seen locally."""
    for prefix, target in cfg.path_map.items():
        if path == prefix or path.startswith(prefix + "/"):
            return target + path[len(prefix) :]
    return path


def unmap_path(cfg, path):
    """Inverse of map_path: a local path as qBittorrent sees it."""
    for prefix, target in sorted(cfg.path_map.items(), key=lambda kv: -len(kv[1])):
        if path == target or path.startswith(target + "/"):
            return prefix + path[len(target) :]
    return path
