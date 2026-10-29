"""Pi-style Claude Messages adapter; Crucible owns every prompt and action.

The short Claude Code identity block and OAuth headers are compatibility requirements
observed in local probes. No Claude Code process, agent loop or built-in tools run here.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jsonschema

from agentd.providers.anthropic_transport import strip_json_code_fences
from agentd.providers.context_observation import begin_context, finish_context, report_context
from agentd.providers.context_overflow import ContextWindowExceeded, detect_overflow
from agentd.providers.contracts import normalize_image_attachments
from agentd.providers.image_parts import image_content
from agentd.providers.openai_native import SessionInputs, session_id_for, to_native_input
from agentd.providers.plan_access import ProviderAccessStopped
from agentd.providers.reasoning_effort import (
    EFFORT_OVERRIDE,
    EffortControlBuilder,
    EffortSupport,
    ReasoningEffort,
)
from agentd.providers.usage import METER, USAGE_OWNER

IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
TOOL_NAME = "crucible_step"
CACHE = {"type": "ephemeral"}
MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class ClaudeAccessStopped(ProviderAccessStopped):
    provider = "claude_subscription"

    def __init__(self, message: str, *, kind: str, status: int, request_id: str | None):
        super().__init__(message, status=status, request_id=request_id)
        self.kind = kind


class ClaudeRequestError(RuntimeError):
    """Any other HTTP failure. `status` lets callers tell a rejected request (400/404,
    e.g. an unknown model) from a server error without reading the body."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


class _Actions:
    """History encoder uses the existing, tested batch and result pairing machinery."""

    union = False

    def encode_action(self, action: dict) -> tuple[str, str]:
        return TOOL_NAME, json.dumps({k: v for k, v in action.items() if k != "provider_call_id"})


def messages_from_items(items: list[dict]) -> list[dict]:
    """Convert internal native history items into Anthropic's alternating messages."""
    messages: list[dict] = []
    for item in items:
        kind = item.get("type")
        if kind == "function_call":
            role = "assistant"
            blocks = [
                {
                    "type": "tool_use",
                    "id": item["call_id"],
                    "name": TOOL_NAME,
                    "input": json.loads(item["arguments"]),
                }
            ]
        elif kind == "function_call_output":
            role = "user"
            blocks = [
                {"type": "tool_result", "tool_use_id": item["call_id"], "content": item["output"]}
            ]
        else:
            role = item.get("role", "user")
            content = item.get("content", "")
            blocks = []
            if isinstance(content, str):
                blocks = [{"type": "text", "text": content or "(empty message)"}]
            else:
                for part in content:
                    if part.get("type") == "input_image":
                        images = normalize_image_attachments([part["image_url"]])
                        blocks.extend(image_content("", images, "anthropic")[1:])
                    else:
                        blocks.append({"type": "text", "text": part.get("text") or " "})
        # Anthropic merges consecutive roles; doing it here keeps tool results together.
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})
    return messages


class ClaudeSubscriptionTransport:
    supports_vision = True
    supports_oneof_grammar = False

    def __init__(
        self,
        bearer: Callable[[], Awaitable[str]],
        *,
        registration_id: str,
        client: httpx.AsyncClient | None = None,
        model_capacity: Callable[[str], Awaitable[int | None]] | None = None,
        model_lookup: Callable[[str], Awaitable[dict | None]] | None = None,
        max_tokens: int = 16384,
        timeout_sec: float = 120,
    ) -> None:
        self.bearer = bearer
        self._model_capacity = model_capacity
        self._model_lookup = model_lookup
        self._effort: ReasoningEffort | None = None
        self.registration_id = registration_id
        self.client = client
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.sessions = SessionInputs()

    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        self._effort = level

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        try:
            entry = await self._model_lookup(model) if self._model_lookup else None
        except Exception:
            entry = None  # Unknown capabilities retain the adapter's wire constraints.
        metadata = entry.get("reasoning_effort") if isinstance(entry, dict) else None
        if isinstance(metadata, dict) and type(metadata.get("supported")) is bool:
            builder = EffortControlBuilder("catalog")
            if not metadata["supported"]:
                return builder.unavailable(
                    "This Claude model does not expose effort control."
                ).build()
            for value in (
                ReasoningEffort.LOW,
                ReasoningEffort.MEDIUM,
                ReasoningEffort.HIGH,
                ReasoningEffort.XHIGH,
                ReasoningEffort.MAX,
            ):
                if metadata.get(value.value) is True:
                    builder.option(value)
            if builder.options:
                return builder.build()
        # Effort and thinking mode are separate Claude parameters. Off never
        # maps to output_config.effort; lack of metadata must not invent that wire.
        return EffortSupport(unsupported={ReasoningEffort.OFF: "Claude effort has no Off level."})

    async def context_capacity(self, model: str) -> int | None:
        return await self._model_capacity(model) if self._model_capacity else None

    def _body(
        self, model: str, instructions: str, messages: list[dict], schema: dict | None = None
    ) -> dict:
        messages = copy.deepcopy(messages)
        # Four breakpoints at most: tools, two system blocks, last user block.
        messages[-1]["content"][-1]["cache_control"] = CACHE.copy()
        body: dict = {
            "model": model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "system": [
                {"type": "text", "text": IDENTITY, "cache_control": CACHE.copy()},
                {
                    "type": "text",
                    "text": instructions or "Follow the user's request.",
                    "cache_control": CACHE.copy(),
                },
            ],
            "messages": messages,
        }
        effort = EFFORT_OVERRIDE.get() or self._effort
        if effort is not None:
            body["output_config"] = {"effort": effort.value}
        if schema is not None:
            body["tools"] = [
                {
                    "name": TOOL_NAME,
                    "description": "Return one Crucible action.",
                    "input_schema": schema,
                    "cache_control": CACHE.copy(),
                }
            ]
            # Sonnet 5.5 rejects forced tool selection; ask in the prompt, validate after.
            body["tool_choice"] = {"type": "auto"}
        return body

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict,
        system_instructions: str,
        user_payload: dict,
        image_attachments: object = None,
        image_assets: object = None,
        on_thinking: Callable | None = None,
        on_retry: Callable | None = None,
        on_usage: Callable | None = None,
        on_context_input: Any = None,
        on_progress: Callable | None = None,
    ) -> dict:
        instructions = (
            system_instructions + "\n\nReturn exactly one action by calling "
            f"{TOOL_NAME}. Its input is the complete response object. "
            "Crucible executes its own tools and provides their results next turn."
        )
        items = to_native_input(user_payload, _Actions(), image_assets)  # type: ignore[arg-type]
        attached = normalize_image_attachments(image_attachments)
        if attached:
            tail = items[-1]
            content = tail.get("content", "")
            if isinstance(content, str):
                tail["content"] = image_content(content, attached, "responses")
            else:
                tail["content"] = content + image_content("", attached, "responses")[1:]
        keys = list(user_payload)
        has_head = "conversation_history" in keys and keys.index("conversation_history") > 0
        session = session_id_for(USAGE_OWNER.get(), instructions, items[0] if has_head else None)
        key = None
        sent = items
        if session and isinstance(user_payload.get("conversation_history"), list):
            key = SessionInputs.key(session, schema_name, instructions, [schema])
            sent = self.sessions.build(key, items)
        response = await self._request(
            self._body(model, instructions, messages_from_items(sent), schema),
            on_thinking,
            on_usage,
            on_context_input,
        )
        calls = [c for c in response["content"] if c.get("type") == "tool_use"]
        if calls:
            if len(calls) != 1 or calls[0].get("name") != TOOL_NAME:
                raise RuntimeError("Claude must return exactly one Crucible action.")
            result = calls[0].get("input")
        else:
            text = "\n".join(
                c.get("text", "") for c in response["content"] if c.get("type") == "text"
            )
            try:
                result = json.loads(strip_json_code_fences(text))
            except ValueError:
                raise RuntimeError("Claude returned no valid Crucible action.") from None
        if not isinstance(result, dict):
            raise RuntimeError("Claude's action must be an object.")
        try:
            jsonschema.validate(result, schema)
        except jsonschema.ValidationError:
            raise RuntimeError(
                "Claude returned an action that does not match the requested schema."
            ) from None
        if key:
            self.sessions.commit(key, items, sent)
        return result

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict,
        on_thinking: Callable | None = None,
    ) -> str:
        items = to_native_input(user_payload, _Actions())  # type: ignore[arg-type]
        response = await self._request(
            self._body(model, system_instructions, messages_from_items(items)), on_thinking
        )
        text = "\n".join(
            c.get("text", "") for c in response["content"] if c.get("type") == "text"
        ).strip()
        if not text:
            raise RuntimeError("Claude returned no text.")
        return text

    async def _request(
        self,
        body: dict,
        on_thinking: Callable | None = None,
        on_usage: Callable | None = None,
        on_context_input: Any = None,
    ) -> dict:
        headers = {
            "Authorization": "Bearer " + await self.bearer(),
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "user-agent": "claude-cli/2.1.280",
            "x-app": "cli",
            "anthropic-dangerous-direct-browser-access": "true",
            "accept": "application/json",
        }
        begin_context(on_context_input, body)
        try:
            if self.client is not None:
                result = await self._stream(self.client, body, headers, on_thinking)
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_sec, follow_redirects=False
                ) as client:
                    result = await self._stream(client, body, headers, on_thinking)
            usage = result.get("usage", {})
            # Anthropic input_tokens excludes cache reads and writes.
            cached = usage.get("cache_read_input_tokens", 0)
            prompt = (
                usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + cached
            )
            report_context(on_context_input, {"input_tokens": prompt}, "input_tokens")
            METER.record(USAGE_OWNER.get(), cached=cached)
            if on_usage:
                on_usage(prompt, usage.get("output_tokens", 0))
            if result.get("stop_reason") == "model_context_window_exceeded":
                raise ContextWindowExceeded("Claude reached the model's context window.")
            if result.get("stop_reason") == "max_tokens":
                raise RuntimeError("Claude's response was truncated at its output limit.")
            return result
        finally:
            finish_context(on_context_input)

    @staticmethod
    def _error(status: int, data: dict, request_id: str | None) -> Exception:
        error = data.get("error", {})
        message = str(error.get("message", "")) if isinstance(error, dict) else ""
        overflow = detect_overflow(status, message)
        if overflow:
            return overflow
        if status in (401, 403):
            return ClaudeAccessStopped(
                "Claude refused this connection. Connect again in Crucible settings.",
                kind="session_invalid",
                status=status,
                request_id=request_id,
            )
        if status == 429:
            return ClaudeAccessStopped(
                "Claude's rate or usage limit was reached. Check Manage usage, then retry later.",
                kind="usage_limit",
                status=status,
                request_id=request_id,
            )
        # Never echo a provider response body into logs/UI (credentials may be reflected).
        return ClaudeRequestError(
            f"Claude could not complete this request (HTTP {status}).", status=status)

    async def _stream(
        self, client: httpx.AsyncClient, body: dict, headers: dict, on_thinking: Callable | None
    ) -> dict:
        async with client.stream("POST", MESSAGES_URL, json=body, headers=headers) as response:
            if response.is_error:
                await response.aread()
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                raise self._error(response.status_code, data, response.headers.get("request-id"))
            if "text/event-stream" not in response.headers.get("content-type", ""):
                await response.aread()
                return response.json()
            result: dict = {"content": [], "usage": {}}
            blocks: dict[int, dict] = {}
            fragments: dict[int, str] = {}
            stopped = False
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                kind = event.get("type")
                if kind == "error":
                    raise self._error(503, event, response.headers.get("request-id"))
                if kind == "message_start":
                    result["usage"].update(event["message"].get("usage", {}))
                elif kind == "content_block_start":
                    blocks[event["index"]] = copy.deepcopy(event["content_block"])
                elif kind == "content_block_delta":
                    index, delta = event["index"], event["delta"]
                    if delta["type"] == "input_json_delta":
                        fragments[index] = fragments.get(index, "") + delta["partial_json"]
                    elif delta["type"] == "text_delta":
                        blocks[index]["text"] = blocks[index].get("text", "") + delta["text"]
                    elif delta["type"] == "thinking_delta" and on_thinking:
                        on_thinking(delta["thinking"])
                elif kind == "message_delta":
                    result.update(event.get("delta", {}))
                    result["usage"].update(event.get("usage", {}))
                elif kind == "message_stop":
                    stopped = True
            if not stopped:
                raise RuntimeError("Claude's response stream ended before completion.")
            for index, block in sorted(blocks.items()):
                if index in fragments:
                    block["input"] = json.loads(fragments[index])
                result["content"].append(block)
            return result
