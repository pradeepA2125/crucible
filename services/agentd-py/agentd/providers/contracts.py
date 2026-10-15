from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Protocol

from agentd.history_metadata import PRIVATE_HISTORY_KEYS


def payload_without_image_attachments(payload: dict[str, object]) -> dict[str, object]:
    """Copy a model payload without transport images or private history evidence.

    Images are provider content parts, not prompt JSON. Keeping this boundary explicit
    prevents a data URL from being rendered as ordinary text if a caller places the
    transport option in the payload as well as the dedicated argument.
    """
    public_payload = dict(payload)
    history = public_payload.get("conversation_history")
    if isinstance(history, (list, tuple)):
        public_payload["conversation_history"] = [
            {key: value for key, value in entry.items() if key not in PRIVATE_HISTORY_KEYS}
            if isinstance(entry, Mapping) else entry for entry in history
        ]

    def clean(value: object) -> object:
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if key in {"image_attachments", "imageAttachments", "image_refs", "image_assets"}:
                    continue
                result[key] = clean(item)
            return result
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    return clean(public_payload)  # type: ignore[return-value]


def normalize_image_attachments(attachments: object) -> list[tuple[str, str, str]]:
    """Return valid (data_url, mime_type, base64_data) attachments only."""
    if not isinstance(attachments, (list, tuple)):
        return []
    result: list[tuple[str, str, str]] = []
    for item in attachments:
        if isinstance(item, str):
            data_url = item
        elif isinstance(item, Mapping):
            value = item.get("data_url") or item.get("url")
            data_url = value if isinstance(value, str) else ""
        else:
            continue
        if not data_url.startswith("data:") or "," not in data_url:
            continue
        header, encoded = data_url.split(",", 1)
        if ";base64" not in header or not encoded:
            continue
        mime = header[5:].split(";", 1)[0].strip()
        if not mime or any(ch.isspace() for ch in encoded):
            continue
        result.append((data_url, mime, encoded))
    return result


# Per-type required action fields — mirrors controller_loop.py's _empty_action_correction.
# Maps each controller response type to the fields that MUST be non-empty for that type.
TYPE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "tool_call": ["tool", "args"],
    "answer": ["answer"],
    "clarify": ["question"],
    "propose_mode": ["plan_sketch", "recommended", "reason", "options"],
    "edit": ["patch_ops"],
    "submit_changes": ["summary"],
    "progress": ["note"],
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
    # Whether the current model/provider accepts image content parts. Providers that
    # cannot prove this remain False, causing chat to return an actionable message.
    supports_vision: bool = False

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
        image_attachments: object = None,
        image_assets: object = None,
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
