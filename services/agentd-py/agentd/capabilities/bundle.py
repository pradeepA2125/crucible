"""Select tools within a namespace and render the loader's bundle (pure)."""
from __future__ import annotations

import json
import re

from agentd import context_metrics
from agentd.capabilities.catalog import short_name, tool_version

# A loaded bundle is resent with every later request of the turn, so its ceiling is a share
# of the context window: a fifth, never more than 50k tokens. GitHub's full 63-tool server
# (~15.7k tokens) loads whole at 128k and up; at 64k it goes through query/tool_names.
BUNDLE_WINDOW_FRACTION = 0.20
MAX_BUNDLE_TOKENS = 50_000
# The compaction window when none is known (MemoryConfig's default).
_DEFAULT_WINDOW_TOKENS = 128_000
DEFAULT_LIMIT = 4
MAX_LIMIT = 8


def bundle_token_limit(window_tokens: int | None) -> int:
    window = window_tokens if window_tokens and window_tokens > 0 else _DEFAULT_WINDOW_TOKENS
    return min(MAX_BUNDLE_TOKENS, int(window * BUNDLE_WINDOW_FRACTION))


def bundle_tokens(bundle: str) -> int:
    """o200k_base tokens; chars/3 until the encoding loads, which over-counts JSON schemas,
    so the fallback refuses early rather than late."""
    counted = context_metrics.count_text_tokens(bundle)
    return counted if counted is not None else (len(bundle) + 2) // 3


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) >= 3}


def select_tools(
    definitions: list[dict[str, object]], *, query: str | None,
    tool_names: list[str] | None, limit: int,
) -> list[dict[str, object]]:
    if tool_names:
        by_name = {short_name(str(d.get("name", ""))): d for d in definitions}
        by_name.update({str(d.get("name", "")): d for d in definitions})
        missing = [n for n in tool_names if n not in by_name]
        if missing:
            raise KeyError(", ".join(missing))
        return [by_name[n] for n in tool_names]
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
