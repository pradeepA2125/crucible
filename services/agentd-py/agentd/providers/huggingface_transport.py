from __future__ import annotations

import asyncio
import json
import os
from typing import Any

try:
    from huggingface_hub import InferenceClient as HFInferenceClient
except ImportError:
    HFInferenceClient = None

from agentd.providers.contracts import ModelJsonTransport


class HuggingFaceJsonTransport(ModelJsonTransport):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        max_new_tokens: int = 4096,
        seed: int | None = None,
        timeout_sec: float = 60.0,
        inference_client: Any | None = None,
    ) -> None:
        self._max_new_tokens = max_new_tokens
        self._seed = seed
        self._timeout_sec = timeout_sec

        if inference_client is not None:
            self._client = inference_client
            return

        resolved_api_key = (
            api_key
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGING_FACE_HUB_TOKEN")
            or os.getenv("HUGGINGFACEHUB_API_TOKEN")
        )
        if not resolved_api_key:
            msg = (
                "HF_TOKEN (or HUGGING_FACE_HUB_TOKEN / HUGGINGFACEHUB_API_TOKEN) "
                "is required for HuggingFaceJsonTransport"
            )
            raise RuntimeError(msg)

        self._api_key = resolved_api_key

        if HFInferenceClient is None:
            msg = "huggingface_hub package is required for HuggingFaceJsonTransport"
            raise RuntimeError(msg)

        self._client = HFInferenceClient(
            token=self._api_key,
            timeout=self._timeout_sec,
        )

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> dict[str, object]:
        prompt = self._build_prompt(
            system_instructions=system_instructions,
            schema_name=schema_name,
            schema=schema,
            user_payload=user_payload,
        )
        generation_kwargs: dict[str, object] = {
            "prompt": prompt,
            "model": model,
            "max_new_tokens": self._max_new_tokens,
        }
        if self._seed is not None:
            generation_kwargs["seed"] = self._seed

        try:
            response = await asyncio.to_thread(self._client.text_generation, **generation_kwargs)
        except Exception as exc:
            msg = f"Hugging Face request failed: {exc}"
            raise RuntimeError(msg) from exc

        output_text = self._extract_text(response)
        return self._parse_output_object(output_text, schema_name)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        prompt = (
            f"{system_instructions}\n\n"
            f"Input payload: {json.dumps(user_payload, separators=(',', ':'))}\n\n"
            "Response:"
        )
        generation_kwargs: dict[str, object] = {
            "prompt": prompt,
            "model": model,
            "max_new_tokens": self._max_new_tokens,
        }
        if self._seed is not None:
            generation_kwargs["seed"] = self._seed

        try:
            response = await asyncio.to_thread(self._client.text_generation, **generation_kwargs)
        except Exception as exc:
            msg = f"Hugging Face request failed: {exc}"
            raise RuntimeError(msg) from exc

        return self._extract_text(response)

    def _build_prompt(
        self,
        *,
        system_instructions: str,
        schema_name: str,
        schema: dict[str, object],
        user_payload: dict[str, object],
    ) -> str:
        return (
            f"{system_instructions}\n\n"
            f"Return ONLY a valid JSON object matching schema '{schema_name}'.\n"
            f"JSON schema: {json.dumps(schema, separators=(',', ':'))}\n"
            "Do not return markdown, code fences, or commentary.\n\n"
            f"Input payload: {json.dumps(user_payload, separators=(',', ':'))}\n\n"
            "JSON object:"
        )

    def _extract_text(self, response_payload: Any) -> str:
        if isinstance(response_payload, str) and response_payload.strip():
            return response_payload.strip()
        raise RuntimeError("Hugging Face response contained no text output")

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            msg = f"Hugging Face output is not valid JSON for {schema_name}: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            msg = "Hugging Face output must be a JSON object"
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
