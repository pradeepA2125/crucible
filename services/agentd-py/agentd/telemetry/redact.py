"""What captured text may contain, and how long it may be (observability spec §6.1)."""
from __future__ import annotations

import re
from collections.abc import Mapping

from agentd.sandbox.env import is_secret_env
from agentd.telemetry.config import TEXT_CAP


def cap_text(text: str, cap: int = TEXT_CAP) -> str:
    if len(text) <= cap:
        return text
    return f"{text[:cap]}… [truncated {len(text) - cap} chars]"


_REDACTED = "[redacted]"
# Keys whose whole value is redacted whatever their name says.
_ALWAYS_SECRET_KEYS = frozenset({"authorization", "cookie", "headers"})
# NAME=value as written in a shell command or env listing.
_ASSIGNMENT = re.compile(r"""\b([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|\S+)""")
# The userinfo part of scheme://user:pass@host.
_URL_USERINFO = re.compile(r"\b([A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")


def _is_secret_key(key: str) -> bool:
    return key.lower() in _ALWAYS_SECRET_KEYS or is_secret_env(key, "")


def _redact_text(text: str) -> str:
    text = _URL_USERINFO.sub(rf"\1{_REDACTED}@", text)
    return _ASSIGNMENT.sub(
        lambda m: f"{m.group(1)}={_REDACTED}" if is_secret_env(m.group(1), "") else m.group(0),
        text)


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _REDACTED if _is_secret_key(str(k)) else _redact(v)
                for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def redact_args(args: Mapping[str, object]) -> dict[str, object]:
    """Tool arguments safe to put on a span: secret-named keys, secret env assignments and
    credentials in URLs are replaced, at any depth."""
    redacted = _redact(args)
    assert isinstance(redacted, dict)
    return redacted
