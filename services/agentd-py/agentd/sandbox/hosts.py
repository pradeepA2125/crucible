"""Hosts sandboxed commands may reach without asking, and host-name validation."""
from __future__ import annotations

import ipaddress
import re

# Package registries and GitHub (spec: the user's default).
DEFAULT_HOSTS = (
    "registry.npmjs.org", "registry.yarnpkg.com", "pypi.org", "files.pythonhosted.org",
    "crates.io", "index.crates.io", "static.crates.io", "proxy.golang.org", "sum.golang.org",
    "repo.maven.apache.org", "github.com", "api.github.com", "codeload.github.com",
    "objects.githubusercontent.com", "raw.githubusercontent.com",
)
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")


def normalize_host(raw: str) -> str:
    """A bare host name or IP address, lower-cased. Raises ValueError for anything else
    (a scheme, port, path or space), so a stored host always means exactly one host."""
    host = raw.strip().lower().rstrip(".")
    if not host:
        raise ValueError("empty host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if len(host) > 253 or not _HOSTNAME.fullmatch(host):
        raise ValueError(f"not a host name: {raw!r}")
    return host
