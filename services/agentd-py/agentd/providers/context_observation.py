"""Physical request input observation, deliberately separate from usage accounting.

Observers are synchronous and optional. Each begin replaces the previous physical
request; finish settles an unreported request as an estimate, never provider usage.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Protocol

_UNKNOWN_IMAGE_TOKENS = 16_384
# Inspect headers only, never decode pixels or allocate in proportion to a payload.
# Normalized PNGs expose their dimensions immediately; unusually long JPEG headers
# can fall back to the fixed allowance instead of expanding the whole image.
_IMAGE_HEADER_CHARS = 128 * 1024


def _image_tokens(part: dict[str, Any]) -> int:
    from agentd.images import estimate_image_tokens

    url = part.get('image_url', '')
    if isinstance(url, dict):
        url = url.get('url', '')
    source = part.get('source') or part.get('inline_data') or part.get('inlineData')
    encoded = source.get('data') if isinstance(source, dict) else None
    if isinstance(url, str) and url.startswith('data:image/'):
        offset = url.find(',')
        if offset >= 0 and ';base64' in url[:offset]:
            encoded = url[offset + 1:offset + 1 + _IMAGE_HEADER_CHARS]
    if not isinstance(encoded, str) or not encoded:
        # Remote images stay remote: estimation must never fetch a URL.
        return _UNKNOWN_IMAGE_TOKENS

    import base64
    import io
    import warnings

    from PIL import Image

    try:
        header = base64.b64decode(encoded[:_IMAGE_HEADER_CHARS], validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(header)) as image:
                return estimate_image_tokens(*image.size)
    except Exception:
        # Malformed/truncated image headers must never fail the provider request.
        return _UNKNOWN_IMAGE_TOKENS


def _text_and_image_tokens(value: Any) -> tuple[Any, int]:
    """Copy provider content with image bytes replaced by a framing placeholder."""
    if isinstance(value, (list, tuple)):
        parts = [_text_and_image_tokens(item) for item in value]
        return [part for part, _ in parts], sum(tokens for _, tokens in parts)
    if isinstance(value, dict):
        kind = value.get('type')
        inline = value.get('inline_data') or value.get('inlineData')
        mime = inline.get('mime_type', inline.get('mimeType', '')) \
            if isinstance(inline, dict) else ''
        if ((isinstance(kind, str) and kind in {'image', 'input_image', 'image_url'})
                or isinstance(mime, str) and mime.startswith('image/')):
            return {'image': '[image]'}, _image_tokens(value)
        parts = {key: _text_and_image_tokens(item) for key, item in value.items()}
        return {key: part for key, (part, _) in parts.items()}, sum(
            tokens for _, tokens in parts.values())
    return value, 0


class ContextInputObserver(Protocol):
    def begin(self, model: str, body: dict[str, Any]) -> None: ...
    def reported(self, value: object) -> None: ...
    def finish(self) -> None: ...


def valid_input_tokens(value: object) -> int | None:
    """Unlike accounting coercions, zero is valid and bool is not an integer."""
    return value if type(value) is int and value >= 0 else None


def estimate_request_input(model: str, body: dict[str, Any]) -> int:
    """Estimate text characters / four plus a separate native-image allowance.

    This is explicitly a heuristic, not provider accuracy. Include framing,
    instructions, history, native tool tails and every tool/grammar schema.
    Image cost uses the application's size-based heuristic, not encoded file size
    or a provider billing formula. Unknown images get a fixed conservative budget.
    Exclude dispatch controls and output reservations. The model argument leaves
    room for model-specific tokenizers without changing the observer interface.
    """
    fields = ('instructions', 'input', 'messages', 'contents', 'tools', 'tool_choice',
              'functions', 'function_call', 'text', 'response_format')
    visible = {key: body[key] for key in fields if key in body}
    extra = body.get('extra_body')
    if isinstance(extra, dict):
        visible.update({key: extra[key] for key in fields if key in extra})
    image_tokens = 0
    # Only message content contains native images. Tool/schema examples and text
    # that happens to contain a data URL are still ordinary model-visible text.
    for key in ('input', 'messages', 'contents'):
        if key in visible:
            visible[key], tokens = _text_and_image_tokens(visible[key])
            image_tokens += tokens
    serialized = json.dumps(visible, ensure_ascii=False, separators=(',', ':'), default=str)
    return (len(serialized) + 3) // 4 + image_tokens


def _safe(observer: ContextInputObserver | None, method: str, *args: Any) -> None:
    if observer is None:
        return
    try:
        getattr(observer, method)(*args)
    except Exception:
        # Telemetry must not break a chat or change retry/accounting semantics.
        pass


def begin_context(observer: ContextInputObserver | None, body: dict[str, Any]) -> None:
    from agentd.performance import capturing, context_measurement

    if capturing():
        from agentd.context_metrics import physical_context
        context_measurement(physical_context(body))
    if observer is None:
        return
    try:
        # Neither observer mutations nor later retry mutations may affect the
        # actual dispatched body / the previous request snapshot.
        snapshot = copy.deepcopy(body)
    except Exception:
        return
    _safe(observer, 'begin', str(body.get('model', '')), snapshot)


def report_context(observer: ContextInputObserver | None, usage: Any, field: str) -> None:
    from agentd.performance import capturing, context_measurement

    if capturing():
        raw = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        value = valid_input_tokens(raw)
        if value is not None:
            context_measurement({"layer": "reported", "input_tokens": value})
    if observer is None:
        return
    try:
        raw = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        value = valid_input_tokens(raw)
        if value is not None:
            _safe(observer, 'reported', value)
    except Exception:
        pass


def finish_context(observer: ContextInputObserver | None) -> None:
    _safe(observer, 'finish')
