"""Tracker identity: map announce URLs and indexer URLs onto one key.

qBittorrent knows a tracker by its announce host (often a subdomain),
Prowlarr by the site URL. Both are reduced to the registrable domain, with a
small list of two-level public suffixes; aliases from the config win.
"""

import urllib.parse

_TWO_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu", "ne", "or"}


def domain(host):
    host = (host or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) <= 2 or all(p.isdigit() for p in parts):
        return host
    if parts[-2] in _TWO_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def key_for_url(url, aliases=None):
    """Tracker key for a URL, or None for pseudo-trackers (DHT, PeX, LSD)."""
    if not url or url.startswith("**"):
        return None
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return None
    aliases = aliases or {}
    host = host.lower()
    if host in aliases:
        return aliases[host]
    base = domain(host)
    return aliases.get(base, base)
