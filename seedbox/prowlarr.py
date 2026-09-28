"""Prowlarr API client (v1): which torrent indexers exist and how they do, and
on-demand searches (release matching). Search results carry Prowlarr's API key
in their download link: they never leave the server."""

import urllib.parse

from seedbox import trackers as trk
from seedbox.api import ApiError, decode, request


class ProwlarrClient:
    def __init__(self, base, api_key):
        self.base = base
        self.api_key = api_key

    def get(self, path):
        status, text, _ = request(self.base + path, headers={"X-Api-Key": self.api_key, "Accept": "application/json"})
        if status == 401:
            raise ApiError("Prowlarr: API key refused")
        if status != 200:
            raise ApiError(f"Prowlarr: HTTP {status} on {path}")
        return decode(text, f"Prowlarr {path}")

    def version(self):
        return (self.get("/api/v1/system/status") or {}).get("version", "?")

    def search(self, query, indexer_id, categories=(2000,)):
        """Movie search on one indexer; query may be an id token ("{ImdbId:tt…}")."""
        params = [("query", query), ("type", "movie"), ("indexerIds", indexer_id)]
        params += [("categories", c) for c in categories]
        status, text, _ = request(
            f"{self.base}/api/v1/search?{urllib.parse.urlencode(params)}",
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=90,
        )
        if status != 200:
            raise ApiError(f"Prowlarr: HTTP {status} on search")
        return decode(text, "Prowlarr search") or []

    def download(self, url):
        """The .torrent behind a result's download link (bytes)."""
        if not url.startswith(self.base + "/"):
            raise ApiError("Prowlarr: download link outside Prowlarr")
        status, body, headers = request(url, headers={"X-Api-Key": self.api_key}, timeout=60, raw=True)
        if status != 200:
            raise ApiError(f"Prowlarr: HTTP {status} on download")
        if not body.startswith(b"d"):
            raise ApiError("Prowlarr: the tracker sent no .torrent file (magnet only?)")
        return body


def _indexer_urls(indexer):
    urls = list(indexer.get("indexerUrls") or [])
    for item in indexer.get("fields") or []:
        if item.get("name") == "baseUrl" and item.get("value"):
            urls.insert(0, item["value"])
    return urls


def indexers(client, aliases):
    """Torrent indexers keyed by tracker key.

    Each value: name, enabled, privacy, failing (Prowlarr disabled it after
    errors), queries, grabs, failed queries.
    """
    status = {s.get("indexerId"): s for s in client.get("/api/v1/indexerstatus") or []}
    try:
        stats = client.get("/api/v1/indexerstats") or {}
    except ApiError:
        stats = {}
    stats = {s.get("indexerId"): s for s in stats.get("indexers", [])}

    result = {}
    for indexer in client.get("/api/v1/indexer") or []:
        if indexer.get("protocol") != "torrent":
            continue
        key = None
        for url in _indexer_urls(indexer):
            key = trk.key_for_url(url, aliases)
            if key:
                break
        if not key:
            key = aliases.get(indexer.get("name", "").lower(), indexer.get("name", "?"))
        ident = indexer.get("id")
        st = stats.get(ident, {})
        result[key] = {
            "name": indexer.get("name", key),
            "enabled": bool(indexer.get("enable")),
            "privacy": indexer.get("privacy", ""),
            "failing": ident in status and bool(status[ident].get("disabledTill")),
            "queries": st.get("numberOfQueries", 0),
            "grabs": st.get("numberOfGrabs", 0),
            "failed_queries": st.get("numberOfFailedQueries", 0),
        }
    return result


def search_indexers(client, aliases):
    """Enabled torrent indexers: [{'id', 'name', 'key', 'imdb'}], imdb = search by IMDb id supported."""
    out = []
    for indexer in client.get("/api/v1/indexer") or []:
        if indexer.get("protocol") != "torrent" or not indexer.get("enable"):
            continue
        key = None
        for url in _indexer_urls(indexer):
            key = trk.key_for_url(url, aliases)
            if key:
                break
        params = (indexer.get("capabilities") or {}).get("movieSearchParams") or []
        out.append(
            {
                "id": indexer.get("id"),
                "name": indexer.get("name", "?"),
                "key": key or aliases.get(indexer.get("name", "").lower(), indexer.get("name", "?")),
                "imdb": "imdbId" in params,
            }
        )
    return out
