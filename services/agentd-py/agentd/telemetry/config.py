"""Tracing switches, read from the environment (observability spec §6.1)."""
from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"
DEFAULT_PROJECT = "crucible"
# Per-attribute cap on captured text; the transcript's tool-output cap is the precedent.
TEXT_CAP = 4000

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _flag(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return None


def is_loopback_endpoint(url: str) -> bool:
    """True when the endpoint's host is this machine, so captured content never leaves it."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool
    endpoint: str
    project: str
    capture_content: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TracingConfig:
        source = os.environ if env is None else env
        endpoint = source.get("CRUCIBLE_TRACING_ENDPOINT") or DEFAULT_ENDPOINT
        explicit = _flag(source.get("CRUCIBLE_TRACING_CONTENT"))
        return cls(
            enabled=_flag(source.get("CRUCIBLE_TRACING_ENABLED")) is True,
            endpoint=endpoint,
            project=source.get("CRUCIBLE_TRACING_PROJECT") or DEFAULT_PROJECT,
            # Content (messages, tool args and outputs) stays on this machine unless asked.
            capture_content=explicit if explicit is not None else is_loopback_endpoint(endpoint),
        )
