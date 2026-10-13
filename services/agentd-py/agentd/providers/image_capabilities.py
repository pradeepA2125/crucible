"""Conservative image admission: explicit model IDs and verified endpoint serializers.

Sources: developers.openai.com/api/docs/guides/images-vision,
platform.claude.com/docs/en/build-with-claude/vision,
ai.google.dev/gemini-api/docs/image-understanding,
openrouter.ai/docs/guides/overview/multimodal/image-understanding.
Limits here are application caps, not claims about provider maximums.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageCapability:
    supported: bool
    reason: str
    model_modality: bool = False
    serializer: str | None = None
    media_types: tuple[str, ...] = ("image/png",)
    max_images: int = 8


# Deliberately finite: unlisted aliases/snapshots fail closed, not prefix-matched.
# Individually fetched model pages verify image input and Responses support:
# https://developers.openai.com/api/docs/models/gpt-4o
# https://developers.openai.com/api/docs/models/gpt-5.2
# https://developers.openai.com/api/docs/models/gpt-5.3-codex
# https://developers.openai.com/api/docs/models/gpt-5.6-sol (gpt-5.6 alias documented)
_OPENAI = frozenset({"gpt-4o", "gpt-5.2", "gpt-5.3-codex", "gpt-5.6", "gpt-5.6-sol"})
# https://platform.claude.com/docs/en/models/overview: all current models accept images.
_ANTHROPIC = frozenset({
    "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
})
# https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash
# 2026-09-22 update: images supported; existing-user access restriction is separate.
_GEMINI = frozenset({"gemini-2.5-flash"})


async def resolve_image_capability(transport: object, model: str) -> ImageCapability:
    """Resolve image admission for THIS model on THIS transport; never guess by branding."""
    from agentd.providers.anthropic_transport import AnthropicJsonTransport
    from agentd.providers.claude_subscription import ClaudeSubscriptionTransport
    from agentd.providers.gemini_transport import GeminiJsonTransport
    from agentd.providers.openai_transport import OpenAIJsonTransport
    from agentd.providers.openrouter_transport import OpenRouterJsonTransport
    from agentd.providers.rate_limit import RateLimitedTransport

    # build_transport wraps every production provider. Admission must inspect the
    # provider, not mistake the rate limiter for an unsupported endpoint. Keep
    # the wrapper in place for actual requests and unwrap only known decorators.
    while isinstance(transport, RateLimitedTransport):
        transport = transport.wrapped

    serializer = None
    modality = False
    endpoint_ok = False
    if isinstance(transport, OpenAIJsonTransport):
        serializer, endpoint_ok, modality = "openai_responses", True, model in _OPENAI
        if getattr(transport, "_plan_route", False):
            lookup = getattr(transport, "_plan_model_lookup", None)
            if lookup is None:
                return ImageCapability(False, "ChatGPT account catalog lookup is unavailable; "
                                       "refresh your signed-in account's models.",
                                       False, serializer)
            try:
                entry = await lookup(model)
            except Exception:
                return ImageCapability(False, "Could not refresh the selected ChatGPT account's "
                                       "model catalog; check sign-in and retry.", False, serializer)
            if entry is None:
                return ImageCapability(False, "Selected model is not listed for this ChatGPT "
                                       "account; refresh the account model picker.",
                                       False, serializer)
            modalities = getattr(entry, "input_modalities", None)
            if modalities is None:
                return ImageCapability(True, "Account-listed model has no modality metadata; "
                                       "attempting native images, subject to server acceptance.",
                                       False, serializer)
            modality = "image" in modalities
    elif isinstance(transport, AnthropicJsonTransport):
        serializer = "anthropic_messages"
        endpoint_ok = getattr(transport, "_image_endpoint", "") in {
            "https://api.anthropic.com", "https://api.anthropic.com/v1/messages"}
        modality = model in _ANTHROPIC
    elif isinstance(transport, ClaudeSubscriptionTransport):
        serializer, endpoint_ok, modality = "anthropic_messages", True, model in _ANTHROPIC
    elif isinstance(transport, GeminiJsonTransport):
        serializer = "gemini_generate_content"
        client = getattr(transport, "_client", None)
        if client is None:  # Explicit injected test models client.
            endpoint_ok = True
        else:
            api_client = getattr(client, "_api_client", None)
            options = getattr(api_client, "_http_options", None)
            endpoint_ok = getattr(options, "base_url", "") in {
                "https://generativelanguage.googleapis.com/",
                "https://generativelanguage.googleapis.com"}
        modality = model.removeprefix("models/") in _GEMINI
    elif isinstance(transport, OpenRouterJsonTransport):
        serializer = "openrouter_chat"
        endpoint_ok = getattr(transport, "_image_endpoint", "") == "https://openrouter.ai/api/v1"
        registry = getattr(transport, "_model_caps", None)
        if endpoint_ok and registry is not None:
            try:
                metadata = await registry.get(model)
                modalities = (metadata or {}).get("architecture", {}).get("input_modalities", [])
                modality = "image" in modalities
            except Exception:
                modality = False
    if serializer is None:
        return ImageCapability(False, (
            "Image serializer deferred for this provider; select a verified vision model "
            "on OpenAI, Anthropic, Gemini or OpenRouter."))
    if not endpoint_ok:
        return ImageCapability(False, (
            "Images are not verified on this custom endpoint; use the official provider endpoint."
        ), modality, serializer)
    if not modality:
        return ImageCapability(False, (
            f"Image input is not verified for model {model!r}; "
            "select an explicitly supported vision model."
        ), False, serializer)
    return ImageCapability(
        True, "Verified image-input model and endpoint serializer.", True, serializer)


async def require_image_capability(
    transport: object, model: str, assets: object, attachments: object,
) -> None:
    if assets or attachments:
        capability = await resolve_image_capability(transport, model)
        if not capability.supported:
            raise ValueError(capability.reason)
