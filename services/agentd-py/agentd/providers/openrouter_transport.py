from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
from openai import AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport, narrow_schema_for_type
from agentd.runtime.artifacts import provider_debug_root

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503})

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


def _is_reasoning_model(model: str) -> bool:
    """Name-substring FALLBACK, used only when the live capability registry
    (_ModelCapabilityCache) is unavailable or doesn't recognize the model —
    see its docstring for why that's the primary path now. Covers DeepSeek-R1
    family, Qwen3 family (including qwen3-coder), and Nemotron. openrouter/free
    is intentionally excluded — it routes to whatever is available and reasoning
    params cause it to return empty choices.
    """
    m = model.lower()
    return any(x in m for x in ("deepseek-r1", "deepseek-r2", "qwen3", "nemotron"))


def _is_retryable(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES


def _classify_retry_reason(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limited"
    if isinstance(status_code, int):
        return "server_error"
    return "network_error"


class OpenRouterJsonTransport(ModelJsonTransport):
    supports_anyof_grammar: bool = True

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
        # max_tokens (generate_text): small, deliberately anti-runaway. json_max_tokens
        # (generate_json/controller_step_response): a JSON payload carrying a full
        # file's content (create_file/search_replace) plus schema/escaping overhead
        # routinely exceeds 4096 tokens — confirmed live on the Ollama transport,
        # where the equivalent under-provisioned budget silently truncated real
        # file writes into invalid JSON (Finding #11). Split like Ollama's
        # json_num_predict vs its fixed small text num_predict, same reasoning.
        self._max_tokens = max_tokens
        self._json_max_tokens = json_max_tokens
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
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

        if completions_client is not None:
            self._completions: Any = completions_client
            return

        resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not resolved_api_key:
            msg = "OPENROUTER_API_KEY is required for OpenRouterJsonTransport"
            raise RuntimeError(msg)

        resolved_site_url = site_url or os.getenv("CRUCIBLE_OPENROUTER_SITE_URL")
        resolved_site_name = site_name or os.getenv("CRUCIBLE_OPENROUTER_SITE_NAME")
        extra_headers: dict[str, str] = {}
        if resolved_site_url:
            extra_headers["HTTP-Referer"] = resolved_site_url
        if resolved_site_name:
            extra_headers["X-Title"] = resolved_site_name

        client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=base_url,
            timeout=timeout_sec,
            default_headers=extra_headers or None,
        )
        self._completions = client.chat.completions
        if self._model_caps is None:
            self._model_caps = _ModelCapabilityCache()

    async def aclose(self) -> None:
        if self._model_caps is not None:
            await self._model_caps.aclose()

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
        is_reasoning = _is_reasoning_model(model)
        return is_reasoning, (1.0 if is_reasoning else 0.0)

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        result = await self._generate_json_once(
            model=model,
            schema_name=schema_name,
            schema=schema,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
            on_retry=on_retry,
        )
        # Type-specific narrowing: the tight anyOf schema enforces each variant's
        # required fields at the token level, but if the grammar was ignored (an
        # underlying provider that silently dropped response_format, or the json_object
        # fallback fired), the model can still return a valid `type` with its action
        # fields missing. Narrow `required` to just that type's fields and retry once.
        if schema_name == "controller_step_response":
            narrowed = narrow_schema_for_type(schema, result)
            if narrowed is not None:
                logger.warning(
                    "openrouter: %s returned type=%r but missing action fields — "
                    "retrying with narrowed schema",
                    schema_name, result.get("type"),
                )
                result = await self._generate_json_once(
                    model=model,
                    schema_name=schema_name,
                    schema=narrowed,
                    system_instructions=system_instructions,
                    user_payload=user_payload,
                    on_thinking=on_thinking,
                    on_retry=on_retry,
                )
        return result

    async def _get_completion_text(
        self, create_kwargs: dict[str, Any], on_thinking: Any, on_retry: Any = None,
    ) -> str:
        """Route through the streaming path (forwarding reasoning deltas to
        on_thinking live, as they arrive) when a callback is given, else the plain
        non-streaming call. Previously on_thinking was accepted by generate_json
        but silently never used — every controller_step_response call (the one
        driving every turn of a live-driven session) rendered nothing until the
        whole call completed, identical to the gap fixed on the Ollama transport."""
        if callable(on_thinking):
            return await self._stream_with_thinking(
                create_kwargs, on_thinking=on_thinking, on_retry=on_retry
            )
        response = await self._call_with_retry(create_kwargs, on_retry=on_retry)
        return self._extract_text(response)

    async def _generate_json_once(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        safe_schema_name = "".join(c for c in schema_name if c.isalnum())

        # Reasoning models require temperature=1 per OpenRouter docs (confirmed
        # live via the model's own default_parameters where the registry knows it).
        is_reasoning, temperature = await self._reasoning_config(model)

        # require_parameters: only route to providers that actually honor the
        # parameters we send (response_format), so strict json_schema is enforced
        # instead of silently dropped by a non-supporting backend. Gated so it can be
        # disabled on tiers where no provider supports it (avoids a guaranteed 404).
        extra_body: dict[str, Any] = {}
        if self._require_parameters:
            extra_body["provider"] = {"require_parameters": True}
        if is_reasoning:
            extra_body["reasoning"] = {"enabled": True}

        base_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            # A JSON payload carrying a full file's content (create_file/
            # search_replace) plus schema/escaping overhead routinely exceeds a
            # few thousand tokens — json_max_tokens (default 16384) is deliberately
            # much larger than generate_text's anti-runaway self._max_tokens.
            "max_completion_tokens": self._json_max_tokens,
            "temperature": temperature,
            "extra_body": extra_body,
        }

        create_kwargs = {
            **base_kwargs,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": safe_schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }

        out_dir = provider_debug_root("openrouter")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"debug-req-{safe_schema_name}.json").write_text(
                json.dumps(create_kwargs, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

        try:
            output_text = await self._get_completion_text(create_kwargs, on_thinking, on_retry)
            return self._parse_output_object(output_text, schema_name)
        except Exception as e:
            # Fall back to json_object with schema injected into system prompt.
            # Some models/providers don't support json_schema strict mode.
            logger.warning(
                "OpenRouter json_schema failed for %s, falling back to json_object: %s",
                schema_name, e,
            )
            # The fallback must be permissive: drop the `provider.require_parameters`
            # guard so it can route to ANY provider (the strict path already failed
            # precisely because no provider honored response_format). Keep any other
            # extra_body (e.g. reasoning). Without this, the fallback inherits the same
            # routing restriction and 404s too — defeating its whole purpose.
            fallback_extra_body = {
                k: v for k, v in extra_body.items() if k != "provider"
            }
            fallback_kwargs: dict[str, Any] = {
                **base_kwargs,
                "extra_body": fallback_extra_body,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"{system_instructions}\n\n"
                            f"You MUST return a JSON object matching this schema:\n"
                            f"{json.dumps(schema, indent=2)}"
                        ),
                    },
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
                "response_format": {"type": "json_object"},
            }
            last_parse_exc: Exception | None = None
            for attempt in range(self._max_retries + 1):
                if attempt > 0:
                    delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                    logger.warning(
                        "OpenRouter malformed JSON for %s (attempt %d/%d), retrying in %.0fs",
                        schema_name, attempt, self._max_retries, delay,
                    )
                    # A malformed-JSON retry cycle can run for minutes with the UI
                    # otherwise showing nothing — on_retry (structured, distinct
                    # from on_thinking) lets the caller show a retry is happening.
                    if callable(on_retry):
                        on_retry(
                            attempt, self._max_retries, "malformed_response",
                            f"⏳ Malformed JSON response — retrying in {delay:.0f}s "
                            f"(attempt {attempt}/{self._max_retries})…",
                        )
                    await asyncio.sleep(delay)
                try:
                    output_text = await self._get_completion_text(fallback_kwargs, on_thinking, on_retry)
                    return self._parse_output_object(output_text, schema_name)
                except RuntimeError as e2:
                    if "not valid JSON" in str(e2) or "must be a JSON object" in str(e2):
                        last_parse_exc = e2
                        continue
                    raise RuntimeError(
                        f"OpenRouter API error for {schema_name} (fallback also failed): {e2}"
                    ) from e2
                except Exception as e2:
                    raise RuntimeError(
                        f"OpenRouter API error for {schema_name} (fallback also failed): {e2}"
                    ) from e2
            assert last_parse_exc is not None
            raise RuntimeError(
                f"OpenRouter API error for {schema_name} (fallback malformed JSON after retries): {last_parse_exc}"
            ) from last_parse_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
    ) -> str:
        is_reasoning, temperature = await self._reasoning_config(model)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "max_completion_tokens": self._max_tokens,
            "temperature": temperature,
        }
        if is_reasoning:
            create_kwargs["extra_body"] = {"reasoning": {"enabled": True}}

        if callable(on_thinking):
            return await self._stream_with_thinking(create_kwargs, on_thinking=on_thinking)

        try:
            response = await self._call_with_retry(create_kwargs)
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"OpenRouter API error: {e}") from e

    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
    ) -> str:
        """Stream response forwarding reasoning chunks to on_thinking callback.

        OpenRouter surfaces reasoning in delta.reasoning (same field as Groq).
        """
        kwargs = {**create_kwargs, "stream": True}
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "OpenRouter transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                if callable(on_retry):
                    reason = _classify_retry_reason(last_exc) if last_exc else "network_error"
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)
            try:
                content_parts: list[str] = []
                stream = await asyncio.wait_for(
                    self._completions.create(**kwargs),
                    timeout=self._timeout_sec,
                )
                async for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    reasoning = getattr(delta, "reasoning", None)
                    if reasoning:
                        on_thinking(reasoning)
                    content = getattr(delta, "content", None) or ""
                    if content:
                        content_parts.append(content)
                return "".join(content_parts).strip()
            except TimeoutError as exc:
                raise RuntimeError(
                    f"OpenRouter streaming timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    async def _call_with_retry(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_retry: Any = None,
    ) -> Any:
        """Call chat.completions.create with timeout and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "OpenRouter transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                if callable(on_retry):
                    reason = _classify_retry_reason(last_exc) if last_exc else "network_error"
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)
            try:
                return await asyncio.wait_for(
                    self._completions.create(**create_kwargs),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                raise RuntimeError(
                    f"OpenRouter chat.completions timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    def _extract_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            try:
                raw = response.model_dump() if hasattr(response, "model_dump") else vars(response)
                logger.warning("OpenRouter response missing choices — full response: %s", raw)
            except Exception:
                logger.warning("OpenRouter response missing choices — response: %r", response)
            raise RuntimeError("OpenRouter response missing choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter response contained no text output")
        return content.strip()

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = _strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenRouter output is not valid JSON for {schema_name}: {output_text[:500]}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("OpenRouter output must be a JSON object")
        return payload


def _strip_json_code_fences(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
