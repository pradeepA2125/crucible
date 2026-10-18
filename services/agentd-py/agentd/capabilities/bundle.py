"""Select tools within a namespace and render the loader's bundle (pure)."""
from __future__ import annotations

import json
import re

from agentd.capabilities.catalog import short_name, tool_version

MAX_BUNDLE_CHARS = 32_000
DEFAULT_LIMIT = 4
MAX_LIMIT = 8


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) >= 3}


def select_tools(
    definitions: list[dict[str, object]], *, query: str | None,
    tool_names: list[str] | None, limit: int,
) -> list[dict[str, object]]:
    if tool_names:
        by_short = {short_name(str(d.get("name", ""))): d for d in definitions}
        missing = [n for n in tool_names if n not in by_short]
        if missing:
            raise KeyError(", ".join(missing))
        return [by_short[n] for n in tool_names]
    if not query:
        return list(definitions)
    wanted = _words(query)
    scored = []
    for index, d in enumerate(definitions):
        words = set(short_name(str(d.get("name", ""))).lower().split("_"))
        words |= _words(str(d.get("description", "")))
        score = len(wanted & words)
        if score:
            scored.append((-score, index, d))
    return [d for _, _, d in sorted(scored, key=lambda t: (t[0], t[1]))[:limit]]


def render_bundle(
    namespace: str, definitions: list[dict[str, object]], guidance: str | None,
) -> str:
    return json.dumps({
        "kind": "capability_bundle", "namespace": namespace, "tools": definitions,
        "versions": {str(d.get("name", "")): tool_version(d) for d in definitions},
        "guidance": guidance or None, "effective_from": "next_model_request",
    }, separators=(",", ":"))
