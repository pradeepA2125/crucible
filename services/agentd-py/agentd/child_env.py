"""Environment for every subprocess the backend starts (spec §3.5)."""
from __future__ import annotations

import os
from collections.abc import Mapping

# Set by agentd.serve for this process only. A child that inherited them could start a
# nested app that believes it owns the parent's port and token.
_SERVE_ONLY = ("CRUCIBLE_LISTEN_PORT", "CRUCIBLE_SERVE_PID")


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if k not in _SERVE_ONLY}
