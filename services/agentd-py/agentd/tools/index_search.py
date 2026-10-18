"""Agent-directed, metadata-only hybrid discovery in the current workspace."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from agentd.context_metrics import text_measurement
from agentd.performance import capturing, context_measurement, span
from agentd.retrieval.candidates import explicit_paths, format_catalog, render_candidates
from agentd.tools.registry import ToolDefinition, ToolOutput

SEARCH_INDEX = ToolDefinition(
    name="search_index",
    description=(
        "Find starting files and symbols in this workspace. Ask one focused natural-language "
        "question with relevant technical keywords and any known symbol or path names; "
        "search distinct behaviors separately. "
        "Uses lexical and available semantic retrieval; returns a small candidate catalog, "
        "not source. Prefer when code locations are unknown. Hints may be unrelated, incomplete "
        "or stale: read_file before relying on them; query_graph follows connections. "
        "Skip discovery when current visible source already answers the request."
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 1600,
                  "description": "Code behavior or symbols to locate in the current workspace."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 8,
                  "description": "Maximum candidate files; default 4."},
    }, "required": ["query"], "additionalProperties": False},
)


async def search_index(workspace: Path, retrieval: Any, args: dict[str, object]) -> ToolOutput:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 1600:
        return ToolOutput(output="Error: query must contain 1–1600 characters.", is_error=True)
    limit = args.get("limit", 4)
    if type(limit) is not int or not 1 <= limit <= 8:
        return ToolOutput(output="Error: limit must be an integer from 1 to 8.", is_error=True)
    if retrieval is None:
        return ToolOutput(output="Code index unavailable; use search_code/read_file.",
                          is_error=True)
    started = time.perf_counter()

    def lookup() -> tuple[str, int, list[str]]:
        context, diagnostics = retrieval.load_context(
            str(workspace), query.strip(), discovery_only=True,
            current_query=query.strip(), allow_build=False,
        )
        candidates = getattr(context, "discovery_candidates", [])
        known = {c["path"]: c for c in candidates}
        paths = explicit_paths(query)[:limit]
        selected = ([known.get(p, {"path": p}) for p in paths]
                    + [c for c in candidates if c["path"] not in paths])[:limit]
        text = format_catalog(render_candidates(
            selected, getattr(context, "graph_relationships", []),
            query=query, include_explicit=False))
        warnings = [d.message[:180] for d in diagnostics
                    if getattr(d, "source", "") == "retrieval"][:2]
        return text, len(selected), warnings

    try:
        with span("index_search.retrieval"):
            text, count, warnings = await asyncio.to_thread(lookup)
    except Exception as exc:
        context_measurement({"layer": "index_search", "status": "error",
                             "error_type": type(exc).__name__})
        return ToolOutput(output=f"Code index lookup failed ({type(exc).__name__}); "
                          "use search_code/read_file.", is_error=True)
    output = text or "No indexed candidates found. Try a different query or search_code."
    if warnings:
        output += "\nIndex notes: " + " | ".join(warnings)
    # Only profiles read this record; counting tokens without one is wasted work.
    if capturing():
        context_measurement({"layer": "index_search", "status": "ok", "rows": count,
                             "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                             "result": text_measurement(output)})
    return ToolOutput(output=output)
