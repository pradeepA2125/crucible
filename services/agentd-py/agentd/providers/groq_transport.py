from __future__ import annotations

import json
import os
from typing import Any

try:
    from groq import AsyncGroq as AsyncGroqClient
except ImportError:
    AsyncGroqClient = None

from agentd.providers.contracts import ModelJsonTransport
from agentd.runtime.artifacts import provider_debug_root


class GroqJsonTransport(ModelJsonTransport):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        max_tokens: int = 4096,
        timeout_sec: float = 60.0,
        reasoning_effort: str | None = None,
        completions_client: Any | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort or os.getenv(
            "AI_EDITOR_GROQ_REASONING_EFFORT", "high"
        )

        if completions_client is not None:
            self._completions: Any = completions_client
            return

        resolved_api_key = api_key or os.getenv("GROQ_API_KEY")
        if not resolved_api_key:
            msg = "GROQ_API_KEY is required for GroqJsonTransport"
            raise RuntimeError(msg)
        if AsyncGroqClient is None:
            msg = "groq package is required for GroqJsonTransport"
            raise RuntimeError(msg)

        client_kwargs: dict[str, Any] = {
            "api_key": resolved_api_key,
            "timeout": timeout_sec,
        }
        if endpoint:
            client_kwargs["base_url"] = endpoint.rstrip("/")

        client = AsyncGroqClient(**client_kwargs)
        self._completions = client.chat.completions

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> dict[str, object]:
        # Normalize schema name to alphanumeric for Groq
        safe_schema_name = "".join(c for c in schema_name if c.isalnum())

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": safe_schema_name,
                    "schema": schema,
                },
            },
            "max_completion_tokens": self._max_tokens,
            "temperature": 1,
            "include_reasoning": False,
        }
        if self._reasoning_effort and "deepseek" in model.lower():
            create_kwargs["reasoning_effort"] = self._reasoning_effort

        # Debug: dump request
        out_dir = provider_debug_root("groq")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"debug-req-{safe_schema_name}.json").write_text(
                json.dumps(create_kwargs, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

        try:
            response = await self._completions.create(**create_kwargs)
            output_text = self._extract_text(response)
            return self._parse_output_object(output_text)
        except Exception as e:
            # Capture and dump Groq body if available
            if hasattr(e, "body"):
                try:
                    (out_dir / f"debug-err-{safe_schema_name}.json").write_text(
                        json.dumps(e.body, indent=2), encoding="utf-8"
                    )
                except Exception:
                    pass
            raise

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        """Generates raw text using Groq."""
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "max_completion_tokens": self._max_tokens,
            "temperature": 1,
        }
        if self._reasoning_effort and "deepseek" in model.lower():
            create_kwargs["reasoning_effort"] = self._reasoning_effort

        try:
            response = await self._completions.create(**create_kwargs)
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"Groq API error: {e}") from e

    def _extract_text(self, response: Any) -> str:
        choices = read_value(response, "choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Groq response missing choices")

        first_choice = choices[0]
        message = read_value(first_choice, "message")
        content = read_value(message, "content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq response contained no text output")
        return content.strip()

    def _parse_output_object(self, output_text: str) -> dict[str, object]:
        payload_text = strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            msg = f"Groq output is not valid JSON: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            msg = "Groq output must be a JSON object"
            raise RuntimeError(msg)

        return payload


def strip_json_code_fences(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw

    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def read_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
