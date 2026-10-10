"""Content-free context attribution; character counts are not tokenizer measurements."""

from __future__ import annotations

import json
from typing import Any


def serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def object_components(value: dict[str, Any]) -> dict[str, int]:
    """Allocate exact compact JSON size to fields and framing, without storing values."""
    fields = {key: serialized_size(key) + 1 + serialized_size(item) for key, item in value.items()}
    fields["framing"] = 2 + max(0, len(value) - 1)
    return fields


def logical_context(
    system: str, payload: dict[str, Any], schema: dict[str, Any], system_parts: dict[str, int]
) -> dict[str, Any]:
    return {
        "layer": "logical",
        "system_chars": len(system),
        "system_parts": system_parts,
        "payload_parts": object_components(payload),
        "retrieval_parts": object_components(payload["retrieval_seed"])
        if isinstance(payload.get("retrieval_seed"), dict)
        else {},
        "schema_chars": serialized_size(schema),
        "estimate_note": "characters; tokens/4 is a heuristic; nested parts are not additive",
    }


def physical_context(body: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "instructions",
        "input",
        "messages",
        "tools",
        "tool_choice",
        "functions",
        "function_call",
        "text",
        "response_format",
    )
    visible = {key: body[key] for key in keys if key in body}
    extra = body.get("extra_body")
    if isinstance(extra, dict):
        visible.update({key: extra[key] for key in keys if key in extra})
    total = serialized_size(visible)
    return {
        "layer": "physical",
        "parts": object_components(visible),
        "total_chars": total,
        "estimated_tokens": (total + 3) // 4,
        "estimate_method": "serialized_chars/4",
    }
