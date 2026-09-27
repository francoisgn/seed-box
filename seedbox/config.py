"""Configuration: one TOML file (connections, secrets, schedule), overridable
by environment variables.

Lookup order for the file: --config, $SEEDBOX_CONFIG, ./seedbox.toml,
/config/seedbox.toml. Every secret can also come from a file through the
`<VAR>_FILE` convention (Docker secrets), e.g. SEEDBOX_QBT_PASSWORD_FILE.
"""

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

    # Announce or indexer host -> tracker display name, to merge hosts that
    # belong to the same tracker or give them a readable name.
    tracker_aliases: dict = field(default_factory=dict)

    output_dir: str = "/data"
    csv_delimiter: str = ","
    # `seedbox run`: fixed schedule ("sun 04:00") or, if empty, a period.
    schedule: str = ""
    interval_hours: float = 24.0
    port: int = 8080

    source: str = ""
    warnings: list = field(default_factory=list)

    @property
    def prowlarr_enabled(self):
        return bool(self.prowlarr_url)


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

    qbt = data.get("qbittorrent", {})
    cfg.qbt_url = qbt.get("url", cfg.qbt_url)
    cfg.qbt_username = qbt.get("username", cfg.qbt_username)
    cfg.qbt_password = qbt.get("password", cfg.qbt_password)
    cfg.path_map = dict(qbt.get("path_map", {}))

    prowlarr = data.get("prowlarr", {})
    cfg.prowlarr_url = prowlarr.get("url", cfg.prowlarr_url)
    cfg.prowlarr_api_key = prowlarr.get("api_key", cfg.prowlarr_api_key)

    cfg.tracker_aliases = {k.lower(): v for k, v in data.get("trackers", {}).get("aliases", {}).items()}

    output = data.get("output", {})
    cfg.output_dir = output.get("dir", cfg.output_dir)
    cfg.csv_delimiter = output.get("csv_delimiter", cfg.csv_delimiter)

    service = data.get("service", {})
    cfg.schedule = service.get("schedule", cfg.schedule)
    cfg.interval_hours = float(service.get("interval_hours", cfg.interval_hours))
    cfg.port = int(service.get("port", cfg.port))

    overrides = {
        "SEEDBOX_ROOTS": ("roots", _split),
        "SEEDBOX_MAX_DEPTH": ("max_depth", int),
        "SEEDBOX_QBT_URL": ("qbt_url", str),
        "SEEDBOX_QBT_USERNAME": ("qbt_username", str),
        "SEEDBOX_QBT_PASSWORD": ("qbt_password", str),
        "SEEDBOX_PROWLARR_URL": ("prowlarr_url", str),
        "SEEDBOX_PROWLARR_API_KEY": ("prowlarr_api_key", str),
        "SEEDBOX_OUTPUT_DIR": ("output_dir", str),
        "SEEDBOX_SCHEDULE": ("schedule", str),
        "SEEDBOX_INTERVAL_HOURS": ("interval_hours", float),
        "SEEDBOX_PORT": ("port", int),
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
    if found and (qbt.get("password") or prowlarr.get("api_key")):
        mode = os.stat(found).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            cfg.warnings.append(f"{found} holds secrets but is readable by others (mode {mode & 0o777:o}), use 600")
    if not cfg.roots:
        raise ConfigError("no library root configured ([library] roots or SEEDBOX_ROOTS)")
    if cfg.prowlarr_enabled and not cfg.prowlarr_api_key:
        raise ConfigError("Prowlarr URL set without an API key (SEEDBOX_PROWLARR_API_KEY)")
    return cfg


def map_path(cfg, path):
    """Translate a path reported by qBittorrent into the path seen locally."""
    for prefix, target in cfg.path_map.items():
        if path == prefix or path.startswith(prefix + "/"):
            return target + path[len(prefix) :]
    return path
