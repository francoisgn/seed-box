"""qBittorrent Web API client (v2), read-only."""

import urllib.parse

from seedbox.api import ApiError, decode, request


class QbtClient:
    def __init__(self, base, username="", password=""):
        self.base = base
        self.cookie = None
        if password:
            self._login(username, password)

    def _headers(self):
        headers = {"Referer": self.base}
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _login(self, username, password):
        status, text, headers = request(
            self.base + "/api/v2/auth/login",
            {"username": username, "password": password},
            {"Referer": self.base},
        )
        if status == 403:
            raise ApiError("qBittorrent: IP banned after too many failed logins")
        if status != 200 or text.strip() not in ("Ok.", ""):
            raise ApiError(f"qBittorrent: login refused ({text.strip()[:60] or status}), check username/password")
        for value in headers.get_all("Set-Cookie") or []:
            if value.startswith("SID=") or "SID=" in value.split(";")[0]:
                self.cookie = value.split(";")[0]

    def get(self, path, **params):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        status, text, _ = request(url, headers=self._headers())
        if status == 403:
            raise ApiError("qBittorrent: forbidden, authentication required (set the password)")
        if status != 200:
            raise ApiError(f"qBittorrent: HTTP {status} on {path}")
        return decode(text, f"qBittorrent {path}")

    def version(self):
        status, text, _ = request(self.base + "/api/v2/app/version", headers=self._headers())
        if status != 200:
            raise ApiError(f"qBittorrent: HTTP {status} on /api/v2/app/version")
        return text.strip()

    def torrents(self):
        return self.get("/api/v2/torrents/info") or []

    def trackers(self, torrent_hash):
        return self.get("/api/v2/torrents/trackers", hash=torrent_hash) or []

    def files(self, torrent_hash):
        return self.get("/api/v2/torrents/files", hash=torrent_hash) or []
