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
        "contents",
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
    images: list[dict[str, Any]] = []

    def text_only(value: Any) -> Any:
        if isinstance(value, list):
            return [clean for item in value if (clean := text_only(item)) is not None]
        if isinstance(value, dict):
            # A JSON schema's "type" can be a list (["string", "null"]), which a set
            # lookup cannot hash: only a string names a content part's kind.
            kind = value.get("type")
            if ((isinstance(kind, str) and kind in {"image", "input_image", "image_url"})
                    or "inline_data" in value or "inlineData" in value):
                images.append(value)
                return None
            return {key: text_only(item) for key, item in value.items()
                    if key not in {"base64_data", "data_url"}}
        return value

    visible = text_only(visible)
    image_bytes = 0
    for image in images:
        source = image.get("source") or image.get("inline_data") or image.get("inlineData") or {}
        raw = source.get("data", "") if isinstance(source, dict) else ""
        url = image.get("image_url", "")
        if isinstance(url, dict):
            url = url.get("url", "")
        if isinstance(url, str) and url.startswith("data:"):
            raw = url.partition(",")[2]
        image_bytes += max(0, len(raw) * 3 // 4 - len(raw) + len(raw.rstrip("=")))
    total = serialized_size(visible)
    return {
        "layer": "physical",
        "parts": object_components(visible),
        "total_chars": total,
        "estimated_tokens": (total + 3) // 4,
        "estimate_method": "serialized_text_chars/4" if images else "serialized_chars/4",
        "image_count": len(images),
        "image_bytes": image_bytes,
        "estimated_image_tokens": sum(int(image.get("estimated_tokens", 16384))
                                      for image in images),
    }
