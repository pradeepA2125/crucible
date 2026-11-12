"""Values for the ${VAR} references in mcp.json, supplied by the host (VS Code SecretStorage,
the desktop keychain) instead of the backend's own environment.

Kept in their own map, never in os.environ: a variable named PATH or NODE_OPTIONS must not
change how the backend or the commands it runs behave, and run_command children inherit the
backend environment. The host hands the map over once at startup (CRUCIBLE_MCP_SECRETS, a
JSON object, removed from the environment as soon as it is read) and updates it through
PUT /v1/mcp/secrets. Values are never logged or returned by any route.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping

logger = logging.getLogger(__name__)

STARTUP_ENV = "CRUCIBLE_MCP_SECRETS"
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_VALUE_CHARS = 65536


class McpSecrets:
    """Thread-safe name → value map. Reads happen on connect, writes from the settings route."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> McpSecrets:
        """Takes the host's startup map out of the environment. A malformed map or entry is
        logged by name only and skipped: secrets must never stop the backend starting."""
        secrets = cls()
        raw = os.environ.pop(STARTUP_ENV, None)
        if not raw:
            return secrets
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("[mcp] %s is not a JSON object; no MCP secrets loaded", STARTUP_ENV)
            return secrets
        if not isinstance(data, dict):
            logger.warning("[mcp] %s is not a JSON object; no MCP secrets loaded", STARTUP_ENV)
            return secrets
        for name, value in data.items():
            try:
                secrets.update({name: value})
            except ValueError:
                logger.warning("[mcp] skipped an invalid MCP secret entry: %r", name)
        return secrets

    def update(self, values: Mapping[str, object]) -> None:
        """Sets each name to its value; None removes it. Validates everything before changing
        anything, so a bad entry leaves the map untouched."""
        checked: dict[str, str | None] = {}
        for name, value in values.items():
            if not isinstance(name, str) or not _NAME_RE.match(name):
                raise ValueError(
                    f"invalid MCP secret name {name!r}: use letters, digits and underscores"
                )
            if value is not None and not isinstance(value, str):
                raise ValueError(f"MCP secret {name} must be text")
            if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
                raise ValueError(
                    f"MCP secret {name} is too long (over {MAX_VALUE_CHARS} characters)"
                )
            checked[name] = value
        with self._lock:
            for name, value in checked.items():
                if value is None:
                    self._values.pop(name, None)
                else:
                    self._values[name] = value

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._values.get(name)

    def names(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._values)


def secret_references(entry: Mapping[str, object]) -> list[str]:
    """The distinct ${VAR} names in a raw mcp.json entry's env and headers, sorted."""
    names: set[str] = set()
    for key in ("env", "headers"):
        mapping = entry.get(key)
        if isinstance(mapping, Mapping):
            for value in mapping.values():
                if isinstance(value, str):
                    names.update(_REF_RE.findall(value))
    return sorted(names)
