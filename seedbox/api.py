"""Minimal JSON-over-HTTP helper on top of urllib (no third-party dependency)."""

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ApiError(Exception):
    pass


def request(url, data=None, headers=None, timeout=60, raw=False):
    """Return (status, body, response headers). Raises ApiError on network errors.

    data: a dict (form-encoded) or bytes sent as is (set Content-Type in headers).
    raw: body returned as bytes instead of text."""
    body = data if isinstance(data, bytes) else urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})

    def read(resp):
        content = resp.read()
        return content if raw else content.decode("utf-8", errors="replace")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, read(resp), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, read(exc), exc.headers
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # ValueError: a redirect to a scheme urllib cannot open (magnet:).
        raise ApiError(f"{urllib.parse.urlsplit(url).netloc}: {exc}") from exc


def multipart(fields, files):
    """(body, content type) for a multipart/form-data POST.

    fields: {name: str}; files: {name: (filename, bytes)}."""
    boundary = "seedbox" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content) in files.items():
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: application/x-bittorrent\r\n\r\n"
        )
        parts.append(head.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def decode(text, where):
    try:
        return json.loads(text) if text.strip() else None
    except json.JSONDecodeError as exc:
        raise ApiError(f"{where}: invalid JSON response") from exc
