from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable

try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.foundation_models.schema import (
        TextChatParameters,
        TextChatResponseFormat,
        TextChatResponseFormatType,
        TextChatResponseJsonSchema,
    )
except ImportError:
    Credentials = None
    ModelInference = None
    TextChatParameters = None
    TextChatResponseFormat = None
    TextChatResponseJsonSchema = None
    TextChatResponseFormatType = None

from agentd.providers.contracts import ModelJsonTransport, narrow_schema_for_type

logger = logging.getLogger(__name__)


class WatsonxJsonTransport(ModelJsonTransport):
    """
    IBM watsonx.ai transport for foundation models.
    Requires WATSONX_API_KEY, WATSONX_PROJECT_ID (or WATSONX_SPACE_ID), and WATSONX_URL.

    Uses model.achat() with TextChatParameters and response_format=json_schema
    (strict=True) — token-level constrained generation, the same mechanism as
    TurboQuant's strict json_schema grammar. Strict json_schema is always on; the
    deployed models we target support it and there is no reliable fallback that
    keeps per-type action-field enforcement.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        project_id: str | None = None,
        url: str | None = None,
        space_id: str | None = None,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or os.getenv("WATSONX_API_KEY")
        self.project_id = project_id or os.getenv("WATSONX_PROJECT_ID")
        self.url = url or os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
        self.space_id = space_id or os.getenv("WATSONX_SPACE_ID")
        self._max_retries = max(0, max_retries)

        if (
            Credentials is None
            or ModelInference is None
            or TextChatResponseFormat is None
        ):
            raise RuntimeError(
                "ibm-watsonx-ai (with TextChat schema support) is required for "
                "WatsonxJsonTransport"
            )
        if not self.api_key:
            raise RuntimeError("WATSONX_API_KEY is required")
        if not self.project_id and not self.space_id:
            raise RuntimeError("WATSONX_PROJECT_ID or WATSONX_SPACE_ID is required")

        self.credentials = Credentials(url=self.url, api_key=self.api_key)

        # Token-level json_schema grammar (strict) — always on.
        # oneOf is left unsupported until measured on watsonx; anyOf gives per-variant
        # token-level enforcement, which the engine reads via this flag.
        self.supports_oneof_grammar: bool = False
        self.supports_anyof_grammar: bool = True
        self.requires_all_fields: bool = False

    async def aclose(self) -> None:
        """No persistent resources to release — provided for interface parity."""

    # ------------------------------------------------------------------
    # ModelJsonTransport interface
    # ------------------------------------------------------------------

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
    ) -> dict[str, object]:
        """
        Generate JSON via model.achat() with constrained output.

        Passes response_format=json_schema with strict=True — token-level
        enforcement identical to TurboQuant's grammar mode.

        For the controller_step_response schema, if the model returns a valid `type`
        but omits that type's required action fields (e.g. `tool_call` with no `tool`),
        the schema is narrowed to only that type's required fields and retried once.
        This gives precise per-type enforcement without requiring all fields up-front.

        Retries: transient (429/5xx/network) and malformed-JSON errors back off
        exponentially up to `max_retries`; permanent errors (401/403/400) surface
        immediately. The one-shot schema narrowing is not an error and does not
        consume an error-retry budget or incur a backoff sleep.
        """
        model_inference = self._make_model(model)
        active_schema = schema
        messages = self._build_json_messages(system_instructions, user_payload, active_schema)

        narrowing_used = False
        error_retries = 0
        last_exc: Exception | None = None
        while True:
            try:
                params = self._build_chat_params(schema=active_schema, schema_name=schema_name)
                response = await model_inference.achat(messages=messages, params=params)
                logger.info("watsonx call: model=%s schema=%s", model, schema_name)

                raw = response["choices"][0]["message"]["content"]

                # Extract thinking if present (some models prepend reasoning blocks)
                thinking, raw = _split_thinking(raw)
                if thinking:
                    logger.info("watsonx think (%s): %s", schema_name, thinking[:300])
                    _log_thinking(thinking, model)
                    if on_thinking:
                        on_thinking(thinking)

                result = _extract_json_object(raw, schema_name)

                # Type-specific narrowing: if the model chose a type but omitted that
                # type's required action fields, narrow the schema to just those fields
                # and retry once — strict enforcement then forces them at token level.
                # This is a tighten, not a failure: no backoff, no error-budget spend.
                if schema_name == "controller_step_response" and not narrowing_used:
                    narrowed = narrow_schema_for_type(schema, result)
                    if narrowed is not None:
                        logger.warning(
                            "watsonx: %s returned type=%r but missing action fields — "
                            "retrying with narrowed schema",
                            schema_name, result.get("type"),
                        )
                        narrowing_used = True
                        active_schema = narrowed
                        messages = self._build_json_messages(
                            system_instructions, user_payload, active_schema
                        )
                        continue

                return result
            except Exception as exc:  # noqa: BLE001 — classified below
                if not (_is_transient(exc) or _is_json_shape_error(exc)):
                    raise
                last_exc = exc
                if error_retries >= self._max_retries:
                    break
                error_retries += 1
                delay = min(5.0 * (2 ** (error_retries - 1)), 60.0)
                logger.warning(
                    "Watsonx generate_json error for %s (retry %d/%d) in %.0fs: %s",
                    schema_name, error_retries, self._max_retries, delay, exc,
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> str:
        """Generate raw text via model.achat()."""
        messages = [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": json.dumps(user_payload)},
        ]
        params = TextChatParameters(max_tokens=4096)
        model_inference = self._make_model(model)

        error_retries = 0
        last_exc: Exception | None = None
        while True:
            try:
                response = await model_inference.achat(messages=messages, params=params)
                raw = response["choices"][0]["message"]["content"]
                thinking, text = _split_thinking(raw)
                if thinking:
                    _log_thinking(thinking, model)
                    if on_thinking:
                        on_thinking(thinking)
                return (text or raw).strip()
            except Exception as exc:  # noqa: BLE001 — classified below
                # Only transient conditions are worth retrying; a permanent error
                # (401/403/400) surfaces immediately instead of burning the backoff.
                if not _is_transient(exc):
                    raise
                last_exc = exc
                if error_retries >= self._max_retries:
                    break
                error_retries += 1
                delay = min(5.0 * (2 ** (error_retries - 1)), 60.0)
                logger.warning(
                    "Watsonx generate_text transient error (retry %d/%d) in %.0fs: %s",
                    error_retries, self._max_retries, delay, exc,
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise RuntimeError(
            f"Watsonx generate_text failed after {self._max_retries} retries: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_model(self, model_id: str) -> ModelInference:
        return ModelInference(
            model_id=model_id,
            credentials=self.credentials,
            project_id=self.project_id,
            space_id=self.space_id,
        )

    @staticmethod
    def _build_json_messages(
        system_instructions: str,
        user_payload: dict[str, object],
        schema: dict[str, object],
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_instructions},
            {
                "role": "user",
                "content": (
                    f"{json.dumps(user_payload)}\n\n"
                    "REQUIRED OUTPUT FORMAT — return ONLY a JSON object matching this schema "
                    "(no markdown fences, no commentary):\n"
                    f"{json.dumps(schema, indent=2)}"
                ),
            },
        ]

    def _build_chat_params(
        self,
        schema: dict[str, object],
        schema_name: str,
    ) -> TextChatParameters:
        response_format = TextChatResponseFormat(
            type=TextChatResponseFormatType.JSON_SCHEMA,
            json_schema=TextChatResponseJsonSchema(
                name=schema_name or "response",
                schema=schema,
                strict=True,
            ),
        )
        return TextChatParameters(
            max_tokens=8192,
            response_format=response_format,
        )


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------

_JSON_SHAPE_PHRASES = (
    "not valid JSON",
    "must be a JSON object",
    "no JSON object",
)

# Unambiguous transient signals to fall back on when no HTTP status is exposed.
_TRANSIENT_PHRASES = (
    "too many requests",
    "rate limit",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "connection aborted",
    "connection error",
    "econnreset",
)


def _is_json_shape_error(exc: Exception) -> bool:
    """A malformed/parse error raised by _extract_json_object — worth a retry."""
    return isinstance(exc, RuntimeError) and any(
        phrase in str(exc) for phrase in _JSON_SHAPE_PHRASES
    )


def _http_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    return status if isinstance(status, int) else None


def _is_transient(exc: Exception) -> bool:
    """Retry only recoverable conditions (429/5xx/network). Permanent errors
    (401/403 auth, 400 bad request) are NOT transient and must surface immediately."""
    status = _http_status(exc)
    if status is not None:
        return status == 429 or 500 <= status < 600
    return any(sig in str(exc).lower() for sig in _TRANSIENT_PHRASES)


def _split_thinking(response: str) -> tuple[str, str]:
    """Extract <think>…</think> block. Returns (thinking, remainder)."""
    if "<think>" not in response:
        return "", response
    if "</think>" in response:
        start = response.find("<think>") + 7
        end = response.find("</think>")
        thinking = response[start:end].strip()
        remainder = response[end + 8:].strip()
    else:
        # Unclosed tag — treat everything after <think> as reasoning, no JSON follows.
        start = response.find("<think>") + 7
        thinking = response[start:].strip()
        remainder = ""
    return thinking, remainder


def _log_thinking(thinking: str, model: str) -> None:
    log_dir = os.getenv("CRUCIBLE_LOG_DIR", ".tmp/reasoning")
    os.makedirs(log_dir, exist_ok=True)
    with open(f"{log_dir}/watsonx_thinking.log", "a") as f:
        f.write(f"--- MODEL: {model} ---\n{thinking}\n\n")


def _repair_json(text: str) -> str:
    """Repair known JSON malformations before parsing."""
    # Repair: unquoted property names — {type: "foo"} → {"type": "foo"}
    text = re.sub(
        r'([{,]\s*)([a-zA-Z_]\w*)(\s*:(?!\s*/))',
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        text,
    )
    return text


def _extract_json_object(text: str, schema_name: str) -> dict[str, object]:
    """Extract and parse the first JSON object from text; attempt repair on failure."""
    start = text.find("{")
    if start == -1:
        raise RuntimeError(
            f"Watsonx output contains no JSON object for {schema_name}: {text[:500]}"
        )
    text = text[start:]
    try:
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        repaired = _repair_json(text)
        if repaired == text:
            raise RuntimeError(
                f"Watsonx output is not valid JSON for {schema_name}: {text[:500]}"
            ) from None
        logger.warning("Watsonx malformed JSON for %s — applied repair", schema_name)
        try:
            payload, _ = json.JSONDecoder().raw_decode(repaired)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Watsonx output is not valid JSON for {schema_name} "
                f"(after repair): {repaired[:500]}"
            ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Watsonx output must be a JSON object")
    return payload
