from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport

logger = logging.getLogger(__name__)

_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
_MODEL_CAPS_TTL_SEC = 3600.0


class _ModelCapabilityCache:
    """Process-wide cache of OpenRouter's own /api/v1/models registry — the
    authoritative source for what a model actually supports (does it take
    `reasoning`, what's its real default temperature, its real max output
    tokens), instead of guessing from the model name. A hardcoded name-substring
    list needs updating by hand for every new model family and silently goes
    stale — it missed NVIDIA's Nemotron 3 family entirely until caught live
    (Nemotron IS a genuine reasoning model, confirmed via the Ollama transport's
    structured `thinking` field). This can't go stale the same way, short of
    OpenRouter itself changing its API shape. Degrades to the name-substring
    heuristic on any fetch failure (network issue, endpoint change) rather than
    hard-failing a turn over a metadata lookup.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client or httpx.AsyncClient()
        self._owns_client = http_client is None
        self._by_model: dict[str, dict[str, Any]] | None = None
        self._fetched_at = 0.0

    async def get(self, model: str) -> dict[str, Any] | None:
        now = time.monotonic()
        if self._by_model is None or now - self._fetched_at > _MODEL_CAPS_TTL_SEC:
            try:
                resp = await self._client.get(_MODELS_ENDPOINT, timeout=10.0)
                resp.raise_for_status()
                entries = resp.json().get("data", [])
                self._by_model = {e["id"]: e for e in entries if "id" in e}
                self._fetched_at = now
            except Exception:
                logger.debug("openrouter: model registry fetch failed", exc_info=True)
                if self._by_model is None:
                    return None
        return (self._by_model or {}).get(model)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class OpenRouterJsonTransport(OpenAICompatibleTransport):
    """OpenRouter-flavoured OpenAI-compatible transport.

    Everything vendor-neutral (request building, retry/backoff, streaming,
    json_object fallback, output parsing) lives in OpenAICompatibleTransport.
    This subclass only supplies the three vendor hooks plus the model-capability
    registry it owns.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str | None = None,
        site_name: str | None = None,
        max_tokens: int = 4096,
        json_max_tokens: int = 16384,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        require_parameters: bool = True,
        completions_client: Any | None = None,
        model_capabilities: _ModelCapabilityCache | None = None,
    ) -> None:
        # When True, the strict json_schema call pins provider.require_parameters so
        # OpenRouter only routes to providers that honor response_format. On a key/tier
        # where no provider supports it (e.g. free), that forces a guaranteed 404 →
        # fallback every turn; set False to let strict route to the default provider
        # (may still succeed there, else the fallback catches it) and skip the hard 404.
        self._require_parameters = require_parameters
        # Test/fake path: stays exactly what's passed (None by default), so
        # existing completions_client-injected tests make zero network calls and
        # always take the name-substring fallback — unchanged behavior unless a
        # test explicitly injects a fake capability provider.
        self._model_caps = model_capabilities

        if completions_client is None:
            resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
            if not resolved_api_key:
                msg = "OPENROUTER_API_KEY is required for OpenRouterJsonTransport"
                raise RuntimeError(msg)
        else:
            resolved_api_key = "unused"

        # Assigned BEFORE super().__init__(): the base constructor calls
        # self._default_headers(), which reads both of these.
        self._site_url = site_url or os.getenv("CRUCIBLE_OPENROUTER_SITE_URL")
        self._site_name = site_name or os.getenv("CRUCIBLE_OPENROUTER_SITE_NAME")

        super().__init__(
            api_key=resolved_api_key,
            base_url=base_url,
            vendor="openrouter",
            label="OpenRouter",
            max_tokens=max_tokens,
            json_max_tokens=json_max_tokens,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            completions_client=completions_client,
        )
        if completions_client is None and self._model_caps is None:
            self._model_caps = _ModelCapabilityCache()

    def _default_headers(self) -> dict[str, str] | None:
        extra_headers: dict[str, str] = {}
        if self._site_url:
            extra_headers["HTTP-Referer"] = self._site_url
        if self._site_name:
            extra_headers["X-Title"] = self._site_name
        return extra_headers or None

    def _build_extra_body(
        self, model: str, is_reasoning: bool, *, for_json: bool
    ) -> dict[str, Any]:
        # require_parameters: only route to providers that actually honor the
        # parameters we send (response_format), so strict json_schema is enforced
        # instead of silently dropped by a non-supporting backend. Gated so it can be
        # disabled on tiers where no provider supports it (avoids a guaranteed 404).
        #
        # for_json only: the guard exists to protect response_format, which is sent
        # by structured-output calls alone. Pinning it on a text completion would
        # narrow routing with nothing to protect, and the json_object fallback needs
        # it dropped so it can route anywhere after the strict call already failed.
        extra_body = super()._build_extra_body(model, is_reasoning, for_json=for_json)
        if for_json and self._require_parameters:
            extra_body["provider"] = {"require_parameters": True}
        return extra_body

    async def _reasoning_config(self, model: str) -> tuple[bool, float]:
        """(is_reasoning, temperature) — from OpenRouter's own model registry when
        available (the model's REAL supported_parameters + default temperature),
        falling back to the name-substring heuristic when the registry is
        unavailable (test/fake mode, or the fetch failed) or doesn't know this
        model."""
        if self._model_caps is not None:
            caps = await self._model_caps.get(model)
            if caps is not None:
                supported = caps.get("supported_parameters") or []
                is_reasoning = "reasoning" in supported
                default_temp = (caps.get("default_parameters") or {}).get("temperature")
                temperature = (
                    float(default_temp) if isinstance(default_temp, int | float)
                    else (1.0 if is_reasoning else 0.0)
                )
                return is_reasoning, temperature
        return await super()._reasoning_config(model)

    async def aclose(self) -> None:
        if self._model_caps is not None:
            await self._model_caps.aclose()
