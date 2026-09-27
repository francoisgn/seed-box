"""Prowlarr API client (v1), read-only: which torrent indexers exist and how they do."""

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
