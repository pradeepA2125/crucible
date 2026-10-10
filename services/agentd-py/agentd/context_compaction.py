"""Deterministic structural compaction, retaining all tool access and pinned seeds."""

from __future__ import annotations

from agentd.context_metrics import serialized_size

TREE_ENTRIES = 300
TREE_CHARS = 16_000
OMITTED = "[Repository structure omitted beyond budget; use list_directory to expand.]"


def bounded_tree(
    entries: list[str], *, max_entries: int = TREE_ENTRIES, max_chars: int = TREE_CHARS
) -> list[str]:
    kept = []
    used = 2 + serialized_size(OMITTED)
    for entry in entries:
        size = serialized_size(entry) + 1
        if len(kept) >= max_entries or used + size > max_chars:
            return kept + [OMITTED]
        kept.append(entry)
        used += size
    return kept
