"""The environment a sandboxed command gets: the backend's, minus secrets, plus the proxy.

Blocking ~/.ssh and ~/.aws would be moot if `GITHUB_TOKEN` or a provider key rode in on the
environment. A command run outside the sandbox gets the full environment back.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping

_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
_SECRET_PREFIXES = ("AWS_", "AZURE_")
_SECRET_NAMES = frozenset({"GOOGLE_APPLICATION_CREDENTIALS"})
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
# Loopback stays direct: the sandbox allows it, and the proxy refuses it.
_NO_PROXY = "localhost,127.0.0.1,::1"


def _is_secret(name: str) -> bool:
    upper = name.upper()
    return (upper in _SECRET_NAMES or upper.startswith(_SECRET_PREFIXES)
            or any(marker in upper for marker in _SECRET_MARKERS))


def _is_proxy_var(name: str) -> bool:
    return name.upper() in (*_PROXY_VARS, "NO_PROXY")


def _without_system_ca(env: dict[str, str]) -> None:
    """Node with the system CA store reads the Keychain, and segfaults when the sandbox's
    Keychain deny makes that fail (seen as `npm install` exit 139). The deny has to stay: it
    is what keeps a credential helper from handing tokens to a command."""
    env.pop("NODE_USE_SYSTEM_CA", None)
    options = [o for o in env.get("NODE_OPTIONS", "").split() if o != "--use-system-ca"]
    if options:
        env["NODE_OPTIONS"] = " ".join(options)
    else:
        env.pop("NODE_OPTIONS", None)


def sandbox_env(env: Mapping[str, str], proxy_url: str | None, *,
                platform: str = sys.platform) -> dict[str, str]:
    result = {k: v for k, v in env.items() if not _is_secret(k) and not _is_proxy_var(k)}
    if platform == "darwin":
        _without_system_ca(result)
    if proxy_url is not None:
        for name in _PROXY_VARS:
            result[name] = proxy_url
            result[name.lower()] = proxy_url
        result["NO_PROXY"] = result["no_proxy"] = _NO_PROXY
        # Node's own fetch ignores the proxy variables without this.
        result["NODE_USE_ENV_PROXY"] = "1"
    return result
