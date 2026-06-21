"""Shared ReAct loop primitives (DRY across planning / execution / controller loops).

Extracted so the controller loop inherits the battle-tested weak-model mitigations
(thought-strip to avoid the repetition attractor, canonical dedup key, correction
texts) rather than reimplementing them. Append-only by construction → KV-cache safe.
"""
from __future__ import annotations

import json


def assistant_turn(response: dict[str, object]) -> dict[str, object]:
    """Append-only assistant history entry with 'thought' stripped.

    Persisting the model's verbatim 'thought' lets a weak model copy-continue its
    own reasoning into a repetition attractor; drop it, keep the actionable fields.
    Mirrors planning/loop.py::_assistant_turn.
    """
    persisted = {k: v for k, v in response.items() if k != "thought"}
    return {"role": "assistant", "content": json.dumps(persisted, default=str)}


def dedup_key(tool: str, args: dict[str, object]) -> str:
    """Canonical (tool, args) key for the duplicate-call guard. search_code's
    context_lines is normalized out so bumping it can't bypass the guard."""
    a = dict(args)
    if tool == "search_code":
        a.pop("context_lines", None)
    return f"{tool}:{json.dumps(a, sort_keys=True, default=str)}"


MALFORMED_CORRECTION = (
    "Your previous response was empty or had no valid 'type'. Reply with EXACTLY ONE JSON object "
    "matching the schema. Do NOT return an empty object or any prose."
)
PARSEFAIL_CORRECTION = (
    "Your previous reply had no JSON object. Respond with ONLY a single JSON object matching the "
    "required schema — no prose, no explanation, no markdown fences."
)
