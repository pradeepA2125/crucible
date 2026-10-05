"""Dev-script helper for an authenticated agentd (spec §3.5).

Reads ~/.crucible/run/agentd-<port>.token, checks GET /health?nonce= proves the server
holds it, and only then hands out the Authorization header. Standard library only.

    from _backend_auth import auth_headers, backend_url
    python3 scripts/_backend_auth.py header http://127.0.0.1:8000
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIED: dict[str, str] = {}


class BackendAuthError(RuntimeError):
    """The backend could not be verified, or no usable token file exists."""


def backend_url(base: str) -> str:
    parts = urllib.parse.urlsplit(base.strip())
    host = "127.0.0.1" if parts.hostname == "localhost" else (parts.hostname or "")
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", "")).rstrip("/")


def _port(base: str) -> int:
    parts = urllib.parse.urlsplit(base)
    return parts.port or (443 if parts.scheme == "https" else 80)


def _read_token(port: int) -> str:
    path = Path.home() / ".crucible" / "run" / f"agentd-{port}.token"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BackendAuthError(
            f"no readable token at {path} — is a backend running on port {port} "
            "(started with `python -m agentd.serve`)?") from exc
    try:
        st = os.fstat(fd)
        if os.name == "posix" and (
                not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise BackendAuthError(f"{path} is not a private regular file owned by you")
        text = os.read(fd, 44).decode("ascii", errors="replace")
    finally:
        os.close(fd)
    if not _TOKEN_RE.fullmatch(text):
        raise BackendAuthError(f"{path} does not hold a token")
    return text


def _verify(base: str, token: str) -> None:
    nonce = secrets.token_hex(16)
    try:
        with urllib.request.urlopen(f"{base}/health?nonce={nonce}", timeout=5) as resp:
            body = json.loads(resp.read() or b"{}")
    except OSError as exc:
        raise BackendAuthError(f"backend at {base} is not reachable: {exc}") from exc
    expected = hmac.new(token.encode("ascii"), b"crucible-health-v1\0" + nonce.encode("ascii"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(body.get("proof", "")), expected):
        raise BackendAuthError(
            f"the server at {base} did not prove it holds the token in ~/.crucible/run; "
            "refusing to send it")


def auth_headers(base: str) -> dict[str, str]:
    url = backend_url(base)
    if urllib.parse.urlsplit(url).hostname != "127.0.0.1":
        raise BackendAuthError(f"{url}: the backend must be at 127.0.0.1")
    token = _read_token(_port(url))
    if _VERIFIED.get(url) != token:  # a restart rewrites the token: verify again
        _verify(url, token)
        _VERIFIED[url] = token
    return {"Authorization": f"Bearer {token}"}


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"header", "url"}:
        print("usage: _backend_auth.py header|url <base_url>", file=sys.stderr)
        return 2
    try:
        if argv[0] == "url":
            print(backend_url(argv[1]))
        else:
            (name, value), = auth_headers(argv[1]).items()
            print(f"{name}: {value}")
    except BackendAuthError as exc:
        print(f"crucible auth: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
