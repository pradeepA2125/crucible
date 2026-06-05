"""Tolerate common tool-argument aliases that weaker models emit.

Models frequently fall back to alternate parameter names from their training
data — e.g. ``regex`` instead of ``pattern``, or ``file`` instead of ``path``.
A single mismatch silently fails the tool call (``search_code`` with no
``pattern`` returns an error), which for a weak model cascades into bad
exploration and hallucinated targets. Rather than fail, we rename known
aliases to the canonical key the tool expects — but ONLY when the canonical
key is absent, so an explicit correct value always wins. Unknown extra keys
(e.g. ``output_mode``) are left untouched; the per-tool dispatch already reads
only the keys it knows, so they're harmless.
"""
from __future__ import annotations

# tool_name -> {alias: canonical}. Conservative: only well-known synonyms.
_ALIASES: dict[str, dict[str, str]] = {
    # `path`/`file`/`glob` → `path_filter`: weak models reuse `path` here (as they
    # correctly do for read_file/list_directory), which silently drops the ripgrep
    # glob and searches the whole workspace. Tool-keyed, so read_file's canonical
    # `path` is unaffected.
    "search_code": {
        "regex": "pattern", "query": "pattern", "search": "pattern",
        "path": "path_filter", "file": "path_filter", "glob": "path_filter",
    },
    "search_semantic": {"q": "query", "text": "query", "prompt": "query"},
    "read_file": {
        "file": "path", "filepath": "path", "file_path": "path",
        "start": "start_line", "end": "end_line",
        "start_line_number": "start_line", "end_line_number": "end_line",
    },
    "list_directory": {"dir": "path", "directory": "path", "folder": "path"},
    "query_graph": {"symbol": "node", "target": "node", "file": "node"},
}


def normalize_tool_args(tool_name: str, args: dict[str, object]) -> dict[str, object]:
    """Return a copy of ``args`` with known aliases renamed to canonical keys.

    Only fills a canonical key that is missing — an explicit canonical value is
    never overwritten. Returns ``args`` unchanged when there are no aliases.
    """
    aliases = _ALIASES.get(tool_name)
    if not aliases or not isinstance(args, dict):
        return args
    normalized = dict(args)
    for alias, canonical in aliases.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized[alias]
    return normalized
