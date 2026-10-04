"""qBittorrent Web API client (v2): reads, plus the few writes the dashboard offers."""

import urllib.parse

from seedbox.api import ApiError, decode, multipart, request


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
        # Success: "200 Ok." on older qBittorrent, "204" with an empty body on recent
        # ones. Failure: "200 Fails." before, "401 Unauthorized" now; 403 = IP banned.
        if status == 403:
            raise ApiError("qBittorrent: IP banned after too many failed logins")
        if status not in (200, 204) or text.strip() not in ("Ok.", ""):
            raise ApiError(f"qBittorrent: login refused ({text.strip()[:60] or status}), check username/password")
        # Cookie "SID" before 5.x, "QBT_SID_<port>" since.
        for value in headers.get_all("Set-Cookie") or []:
            name = value.split("=", 1)[0].strip()
            if name == "SID" or name.startswith("QBT_SID"):
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

    def export(self, torrent_hash):
        """The .torrent file of a torrent (qBittorrent 4.5+), as bytes."""
        status, content, _ = request(
            self.base + "/api/v2/torrents/export?" + urllib.parse.urlencode({"hash": torrent_hash}),
            headers=self._headers(),
            raw=True,
        )
        if status != 200:
            raise ApiError(f"qBittorrent: HTTP {status} on /api/v2/torrents/export")
        return content

    def files(self, torrent_hash):
        return self.get("/api/v2/torrents/files", hash=torrent_hash) or []

    def maindata(self):
        return self.get("/api/v2/sync/maindata")

    def preferences(self):
        return self.get("/api/v2/app/preferences")

    def log(self):
        return self.get("/api/v2/log/main", last_known_id=-1) or []

    def categories(self):
        return self.get("/api/v2/torrents/categories") or {}

    def transfer(self):
        return self.get("/api/v2/transfer/info") or {}

    def post(self, path, data):
        status, text, _ = request(self.base + path, data, self._headers())
        if status == 403:
            raise ApiError("qBittorrent: forbidden, authentication required (set the password)")
        if status == 404:
            raise NotFound(path)
        if status not in (200, 204):
            raise ApiError(f"qBittorrent: HTTP {status} on {path}: {text.strip()[:120]}")
        return text

    def set_location(self, hashes, location):
        self.post("/api/v2/torrents/setLocation", {"hashes": "|".join(hashes), "location": location})

    def recheck(self, hashes):
        self.post("/api/v2/torrents/recheck", {"hashes": "|".join(hashes)})

    def start(self, hashes):
        # "start" since qBittorrent 5.0, "resume" before.
        try:
            self.post("/api/v2/torrents/start", {"hashes": "|".join(hashes)})
        except NotFound:
            self.post("/api/v2/torrents/resume", {"hashes": "|".join(hashes)})

    def delete(self, hashes, delete_files=False):
        self.post(
            "/api/v2/torrents/delete",
            {"hashes": "|".join(hashes), "deleteFiles": "true" if delete_files else "false"},
        )

    def remove_trackers(self, torrent_hash, urls):
        self.post("/api/v2/torrents/removeTrackers", {"hash": torrent_hash, "urls": "|".join(urls)})

    def set_category(self, hashes, category):
        self.post("/api/v2/torrents/setCategory", {"hashes": "|".join(hashes), "category": category})

    def auto_management(self, hashes, enable):
        self.post(
            "/api/v2/torrents/setAutoManagement",
            {"hashes": "|".join(hashes), "enable": "true" if enable else "false"},
        )

    def add_torrent(self, content, save_path, category="", stopped=True, layout="NoSubfolder", skip_checking=False):
        """Add a .torrent: files straight in save_path (no root folder) unless layout
        says otherwise, no automatic management (it would move the files to the
        category folder), never in qBittorrent's incomplete-downloads folder: the
        files are already in save_path, a check must look there. skip_checking:
        seed at once, the data is known complete."""
        fields = {
            "savepath": save_path,
            "category": category,
            "autoTMM": "false",
            "contentLayout": layout,
            # "Keep incomplete torrents in" would point an unchecked torrent at an empty temp folder.
            "useDownloadPath": "false",
            "skip_checking": "true" if skip_checking else "false",
            # "stopped" since qBittorrent 5.0, "paused" before.
            "stopped": "true" if stopped else "false",
            "paused": "true" if stopped else "false",
        }
        body, ctype = multipart(fields, {"torrents": ("release.torrent", content)})
        status, text, _ = request(
            self.base + "/api/v2/torrents/add", body, dict(self._headers(), **{"Content-Type": ctype})
        )
        if status not in (200, 204) or text.strip() == "Fails.":
            raise ApiError(f"qBittorrent: torrent refused ({text.strip()[:80] or status})")

    def rename_file(self, torrent_hash, old_path, new_path):
        """Rename one file of a torrent, on disk too."""
        self.post("/api/v2/torrents/renameFile", {"hash": torrent_hash, "oldPath": old_path, "newPath": new_path})

    def file_priority(self, torrent_hash, ids, priority):
        self.post(
            "/api/v2/torrents/filePrio",
            {"hash": torrent_hash, "id": "|".join(str(i) for i in ids), "priority": str(priority)},
        )


class NotFound(ApiError):
    pass
