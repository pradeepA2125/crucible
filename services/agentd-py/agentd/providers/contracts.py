from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Protocol


# Per-type required action fields — mirrors controller_loop.py's _empty_action_correction.
# Maps each controller response type to the fields that MUST be non-empty for that type.
TYPE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "tool_call": ["tool", "args"],
    "answer": ["answer"],
    "clarify": ["question"],
    "propose_mode": ["plan_sketch", "recommended", "reason", "options"],
    "edit": ["patch_ops"],
    "submit_changes": ["summary"],
}


def narrow_schema_for_type(
    schema: dict[str, object], result: dict[str, object]
) -> dict[str, object] | None:
    """Return a copy of the schema with `required` narrowed to type + thought + that
    type's action fields, if any of those action fields are missing from `result`.
    Returns None if no narrowing is needed (all required fields already present).

    Used by transports that lack token-level `anyOf` enforcement to retry with a
    tighter schema when the model returns a valid `type` but omits action fields.
    """
    atype = str(result.get("type", ""))
    action_fields = TYPE_REQUIRED_FIELDS.get(atype)
    if not action_fields:
        return None
    missing = [f for f in action_fields if not result.get(f)]
    if not missing:
        return None
    narrowed = copy.deepcopy(schema)
    narrowed["required"] = ["type", "thought"] + action_fields
    return narrowed


class ModelJsonTransport(Protocol):
    # Whether this transport's grammar engine enforces a JSON-schema `oneOf` at the
    # token level (lets the controller use a tight discriminated-union schema instead
    # of the flat fallback). Default False — only a transport that has MEASURED its
    # engine enforces `oneOf` (e.g. TurboQuant/llama.cpp) overrides this. Gemini
    # deadlocks on `oneOf`, so it stays False. Read defensively via getattr so a
    # transport predating this attribute is treated as non-supporting.
    supports_oneof_grammar: bool = False

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, object]: ...

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> str: ...
