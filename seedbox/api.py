"""Minimal JSON-over-HTTP helper on top of urllib (no third-party dependency)."""

import json
import urllib.error
import urllib.parse
import urllib.request


class ApiError(Exception):
    pass


def request(url, data=None, headers=None, timeout=60):
    """Return (status, body text, response headers). Raises ApiError on network errors."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(f"{urllib.parse.urlsplit(url).netloc}: {exc}") from exc


def decode(text, where):
    try:
        return json.loads(text) if text.strip() else None
    except json.JSONDecodeError as exc:
        raise ApiError(f"{where}: invalid JSON response") from exc
