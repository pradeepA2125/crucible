"""Recover abandoned batch evidence without replaying commands or other side effects."""

from __future__ import annotations


def interrupted_history(history: list[dict]) -> list[dict]:
    return [
        dict(
            entry,
            status="interrupted",
            is_error=True,
            content="Not completed: prior execution was interrupted; it was not replayed.",
        )
        if entry.get("batch_id")
        and entry.get("role") == "tool_result"
        and entry.get("status") in {"pending", "waiting_approval", "running"}
        else dict(entry)
        for entry in history
    ]
