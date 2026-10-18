"""TMDB API (v3), read-only: the titles a film is known under, to search trackers."""

import re
import urllib.parse

from seedbox.api import ApiError, decode, request

BASE = "https://api.themoviedb.org/3"
LATIN = re.compile(r"[A-Za-zÀ-ÿ]")


def _get(api_key, path, **params):
    params["api_key"] = api_key
    status, text, _ = request(f"{BASE}{path}?{urllib.parse.urlencode(params)}", timeout=20)
    if status == 401:
        raise ApiError("TMDB: API key refused")
    if status == 404:
        raise ApiError(f"TMDB: nothing at {path}")
    if status != 200:
        raise ApiError(f"TMDB: HTTP {status} on {path}")
    return decode(text, f"TMDB {path}") or {}


def movie(api_key, tmdb_id):
    """{'id', 'titles', 'year', 'imdb'}: French, English and original titles
    (only those a tracker can match: Latin script), de-duplicated."""
    fr = _get(api_key, f"/movie/{int(tmdb_id)}", language="fr-FR", append_to_response="external_ids")
    en = _get(api_key, f"/movie/{int(tmdb_id)}", language="en-US")
    titles = []
    for t in (fr.get("title"), en.get("title"), fr.get("original_title")):
        if t and LATIN.search(t) and t not in titles:
            titles.append(t)
    return {
        "id": int(tmdb_id),
        "titles": titles,
        "year": (fr.get("release_date") or "")[:4],
        "imdb": (fr.get("external_ids") or {}).get("imdb_id") or "",
    }


def search(api_key, title, year=""):
    """Best TMDB matches for a title (and year): [{'id', 'title', 'year'}]."""
    params = {"query": title, "language": "fr-FR"}
    if year:
        params["year"] = year
    found = _get(api_key, "/search/movie", **params).get("results") or []
    if not found and year:
        found = _get(api_key, "/search/movie", query=title, language="fr-FR").get("results") or []
    return [{"id": r["id"], "title": r.get("title", ""), "year": (r.get("release_date") or "")[:4]} for r in found[:5]]
