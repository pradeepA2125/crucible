from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport


class OpenRouterJsonTransport(ModelJsonTransport):
    """
    OpenRouter transport that is OpenAI-compatible but requires specific headers
    and a custom base URL.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str | None = None,
        site_name: str | None = None,
        timeout_sec: float = 120.0,
        completions_client: Any | None = None,
    ) -> None:
        if completions_client is not None:
            self._completions: Any = completions_client
            return

        resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not resolved_api_key:
            msg = "OPENROUTER_API_KEY is required for OpenRouterJsonTransport"
            raise RuntimeError(msg)

        resolved_site_url = site_url or os.getenv("AI_EDITOR_OPENROUTER_SITE_URL")
        resolved_site_name = site_name or os.getenv("AI_EDITOR_OPENROUTER_SITE_NAME")
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

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> dict[str, object]:
        """
        Generates JSON using OpenRouter with robust schema enforcement.
        We attempt 'json_schema' (Structured Outputs) first and fallback
        to 'json_object' if the provider does not support it.
        """
        # Enable reasoning for openrouter/free or via environment variable
        reasoning_enabled = os.getenv("AI_EDITOR_OPENROUTER_REASONING_ENABLED", "true").lower() == "true"
        
        # Strategy 1: Attempt Structured Outputs (json_schema)
        try:
            create_kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
                "temperature": 1.0,
            }
            
            if reasoning_enabled or model == "openrouter/free":
                create_kwargs["extra_body"] = {"reasoning": {"enabled": True}}

            response = await self._completions.create(**create_kwargs)
            output_text = self._extract_text(response)
            return self._parse_output_object(output_text)
            
        except Exception as e:
            # Strategy 2: Fallback to json_object with schema in prompt
            # Some providers (like StepFun) do not support 'json_schema'
            # or 'strict' mode.
            print(f"[DEBUG] OpenRouter json_schema failed, falling back to json_object: {e}")
            
            instructions_with_schema = (
                f"{system_instructions}\n\n"
                f"You MUST return a JSON object that strictly follows this schema:\n"
                f"{json.dumps(schema, indent=2)}"
            )
            
            fallback_kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": instructions_with_schema},
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 1.0,
            }
            
            if reasoning_enabled or model == "openrouter/free":
                fallback_kwargs["extra_body"] = {"reasoning": {"enabled": True}}

            try:
                response = await self._completions.create(**fallback_kwargs)
                output_text = self._extract_text(response)
                return self._parse_output_object(output_text)
            except Exception as e2:
                raise RuntimeError(f"OpenRouter API error (fallback also failed): {e2}") from e2

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        """Generates raw text using OpenRouter."""
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "temperature": 1.0,
        }

        try:
            response = await self._completions.create(**create_kwargs)
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"OpenRouter API error: {e}") from e

    def _extract_text(self, response: Any) -> str:
        if not hasattr(response, "choices") or not response.choices:
            raise RuntimeError("OpenRouter response missing choices")

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("OpenRouter response contained no text output")
        return content.strip()

    def _parse_output_object(self, output_text: str) -> dict[str, object]:
        # Strip potential markdown fences if present (some models do this even with schema)
        payload_text = strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            msg = f"OpenRouter output is not valid JSON: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            msg = "OpenRouter output must be a JSON object"
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
