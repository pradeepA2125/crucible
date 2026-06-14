"""Unified-diff capping + per-file DiffEntry computation.

Canonical home (extracted from orchestrator/engine.py for DRY): the orchestrator's
`_cap_unified_diff`/`_compute_diff_entries` and the chat controller's TurnEditSession
both use these free functions, so there is a single diff implementation.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from agentd.domain.models import DiffEntry

_DIFF_MAX_LINES = 400
_DIFF_MAX_CHARS = 24_000
_DIFF_TRUNCATION_MARKER = "… diff truncated — open in editor for the full diff"


def cap_unified_diff(diff_text: str) -> str:
    """Bound per-file diff text for chat payload/persistence."""
    lines = diff_text.splitlines()
    truncated = False
    if len(lines) > _DIFF_MAX_LINES:
        lines = lines[:_DIFF_MAX_LINES]
        truncated = True
    text = "\n".join(lines)
    if len(text) > _DIFF_MAX_CHARS:
        text = text[:_DIFF_MAX_CHARS]
        truncated = True
    if truncated:
        text += "\n" + _DIFF_TRUNCATION_MARKER
    return text


def compute_diff_entries(
    real_path: Path, shadow_path: Path, touched: list[str], key: str
) -> list[DiffEntry]:
    """Per-file diff of `real` (before) vs `shadow` (after) for the touched files.

    `key` is an opaque id carried for callers that tag entries (unused in the diff
    math; kept for signature parity with the orchestrator method).
    """
    _ = key
    entries: list[DiffEntry] = []
    for rel in touched:
        shadow_file = shadow_path / rel
        real_file = real_path / rel
        if not shadow_file.exists():
            continue
        shadow_lines = shadow_file.read_text(errors="replace").splitlines(keepends=True)
        real_lines = (
            real_file.read_text(errors="replace").splitlines(keepends=True)
            if real_file.exists()
            else []
        )
        diff = list(difflib.unified_diff(real_lines, shadow_lines, lineterm=""))
        additions = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
        entries.append(
            DiffEntry(
                path=rel,
                additions=additions,
                deletions=deletions,
                temp_path=str(shadow_file),
                unified_diff=cap_unified_diff("\n".join(diff)),
            )
        )
    return entries
