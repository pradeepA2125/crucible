"""Shared ReAct loop primitives (DRY across planning / execution / controller loops).

Extracted so the controller loop inherits the battle-tested weak-model mitigations
(thought-strip to avoid the repetition attractor, canonical dedup key, correction
texts) rather than reimplementing them. Append-only by construction → KV-cache safe.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from functools import lru_cache

from agentd.prompting.tagged import RenderContext, render_prompt, tagged


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


# Tagged (spec §4.7): the main render is byte-identical to the historical constant; the
# allowed-type suffix is child-only so the main correction text never changes (§4.7.4).
_MALFORMED_TEMPLATE = tagged("malformed", (
    "Your previous response was empty or had no valid 'type'. Reply with EXACTLY ONE JSON object "
    "matching the schema. Do NOT return an empty object or any prose."
    "<<child>> Allowed types right now: {allowed}.<</child>>"
))
MALFORMED_CORRECTION = render_prompt(_MALFORMED_TEMPLATE, RenderContext.main())


def malformed_correction(ctx: RenderContext, allowed_types: Sequence[str]) -> str:
    """The malformed-response correction for ctx; a child also gets the allowed types."""
    return render_prompt(_MALFORMED_TEMPLATE, ctx).replace("{allowed}", ", ".join(allowed_types))


@lru_cache(maxsize=128)
def _declared_kwargs(fn: object) -> frozenset[str] | None:
    try:
        params = inspect.signature(fn).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return frozenset(params)


def accepts_kwarg(fn: object, name: str) -> bool:
    """True when fn declares `name` (or **kwargs). Same rule as engine._accepts: ask the
    callee rather than maintain a capability flag — lets the loop pass new keywords to
    engines that declare them without breaking fixed-signature fakes."""
    params = _declared_kwargs(fn)
    return params is None or name in params
PARSEFAIL_CORRECTION = (
    "Your previous reply had no JSON object. Respond with ONLY a single JSON object matching the "
    "required schema — no prose, no explanation, no markdown fences."
)
