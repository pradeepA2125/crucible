"""Join projected origin refs to ephemeral assets, never render bytes in prompt JSON."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agentd.providers.contracts import (
    normalize_image_attachments,
    payload_without_image_attachments,
)


def redact_image_parts(value: Any) -> Any:
    """Preserve diagnostic shape, never write binary native content to debug artifacts."""
    if isinstance(value, dict):
        if value.get("type") in {"input_image", "image_url", "image"}:
            return {"type": value["type"], "image": "[redacted]"}
        if "inline_data" in value:
            return {"inline_data": "[redacted]"}
        return {key: redact_image_parts(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_image_parts(item) for item in value]
    return value


def origin_images(entry: object, assets: object) -> list[tuple[str, str, str]]:
    if not isinstance(entry, Mapping) or not isinstance(assets, Mapping):
        return []
    refs = entry.get("image_refs", [])
    if not isinstance(refs, list):
        return []
    return normalize_image_attachments([
        assets[ref["asset_id"]] for ref in refs
        if isinstance(ref, Mapping) and ref.get("asset_id") in assets
    ])


def image_content(text: str, images: list[tuple[str, str, str]], serializer: str) -> Any:
    if not images:
        return text
    if serializer == "gemini":
        return [{"text": text}] + [{"inline_data": {"mime_type": mime, "data": encoded}}
                                     for _url, mime, encoded in images]
    if serializer == "anthropic":
        return [{"type": "text", "text": text}] + [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": encoded}}
            for _url, mime, encoded in images]
    if serializer == "responses":
        return [{"type": "input_text", "text": text}] + [
            {"type": "input_image", "image_url": url, "detail": "high"}
            for url, _mime, _encoded in images]
    return [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": url}} for url, _mime, _encoded in images]


def history_messages(payload: dict[str, object], assets: object, serializer: str,
                     attachments: object = None) -> list[dict[str, Any]]:
    """Text envelope entries preserve tool identity; each image stays at its owned origin.

    These schema-response adapters don't replay function calls natively: historical
    tool results remain explicitly labelled text, with native images in the same user
    entry. The Responses native-tool adapter handles call/result pairing separately.
    """
    safe = payload_without_image_attachments(payload)
    history = payload.get("conversation_history")
    messages: list[dict[str, Any]] = []

    def append(text: str, images: list[tuple[str, str, str]]) -> None:
        content = image_content(text, images, serializer)
        if serializer == "gemini":
            parts = content if isinstance(content, list) else [{"text": content}]
            messages.append({"role": "user", "parts": parts})
        else:
            messages.append({"role": "user", "content": content})

    if not isinstance(history, list) or not assets:
        append(json.dumps(safe), normalize_image_attachments(attachments))
        return messages
    keys = list(safe)
    split = keys.index("conversation_history")
    head = {k: safe[k] for k in keys[:split]}
    if head:
        append(json.dumps(head), [])
    for entry in history:
        clean = payload_without_image_attachments(entry) if isinstance(entry, dict) else entry
        append(json.dumps(clean), origin_images(entry, assets))
    tail = {k: safe[k] for k in keys[split + 1:]}
    append(json.dumps(tail), normalize_image_attachments(attachments))
    return messages
