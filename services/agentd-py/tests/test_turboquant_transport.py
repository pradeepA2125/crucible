"""Tests for TurboQuantTransport — streaming SSE path.

Uses realistic chunk sequences that mirror what a live qwen3/turboquant server
actually sends: reasoning_content for thinking tokens, content for JSON output,
terminated by [DONE].

The second half of the test file (test_real_*) uses the actual prompt builders
from agentd.planning.prompts to validate that the full input/output path works
with the same inputs as a live planning run.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentd.planning.prompts import (
    PLANNING_STEP_RESPONSE_SCHEMA,
    build_planning_step_payload,
    format_planning_system_prompt,
)
from agentd.planning.registry import PlanningToolRegistry
from agentd.providers.turboquant_transport import DEVSTRAL, TurboQuantTransport


# ── SSE helpers ────────────────────────────────────────────────────────────────

def _thinking_chunk(text: str) -> str:
    """SSE line: one reasoning_content token."""
    return "data: " + json.dumps({
        "choices": [{"delta": {"reasoning_content": text, "content": None}}]
    })


def _content_chunk(text: str) -> str:
    """SSE line: one content token."""
    return "data: " + json.dumps({
        "choices": [{"delta": {"content": text, "reasoning_content": None}}]
    })


def _done_line() -> str:
    return "data: [DONE]"


def _sse_stream(*payloads: str) -> list[str]:
    """Build a list of SSE lines for a stream, terminated with [DONE]."""
    return [*payloads, _done_line()]


# ── Fake streaming client ──────────────────────────────────────────────────────

class _FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b"error body"


class _FakeStreamCtx:
    def __init__(self, resp: _FakeStreamResponse | Exception) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeStreamResponse:
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp

    async def __aexit__(self, *_: object) -> None:
        pass


class _FakeStreamClient:
    def __init__(self, responses: list[_FakeStreamResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def stream(
        self, method: str, url: str, *, json: dict[str, Any], timeout: Any
    ) -> _FakeStreamCtx:
        self.calls.append({"method": method, "url": url, "body": json})
        resp = self._responses.pop(0)
        return _FakeStreamCtx(resp)

    async def aclose(self) -> None:
        self.closed = True


def _ok_stream(lines: list[str]) -> _FakeStreamResponse:
    return _FakeStreamResponse(status_code=200, lines=lines)


# ── Realistic payload builders ─────────────────────────────────────────────────

def _tool_call_payload() -> dict[str, object]:
    """A realistic planning step response: tool_call for search_code."""
    return {
        "type": "tool_call",
        "thought": "I should search for where create_task is defined to understand the API surface.",
        "tool": "search_code",
        "args": {
            "pattern": "def create_task",
            "path_filter": "*.py",
            "context_lines": 10,
        },
    }


def _emit_plan_payload() -> dict[str, object]:
    """A realistic planning step response: emit_plan."""
    return {
        "type": "emit_plan",
        "thought": "I have examined the relevant files and have enough context for a solid plan.",
        "plan_markdown": (
            "# Plan\n\n"
            "## Step 1 — Add the new endpoint\n"
            "Add `POST /v1/tasks/{id}/resume` route in `api/routes.py`.\n\n"
            "## Step 2 — Wire the orchestrator\n"
            "Call `orchestrator.resume_task()` from the route handler."
        ),
        "files_examined": [
            "agentd/api/routes.py",
            "agentd/orchestrator/engine.py",
        ],
        "confidence": "high",
    }


def _chunk_json(payload: dict[str, object], chunk_size: int = 8) -> list[str]:
    """Split JSON into content chunks of `chunk_size` chars each."""
    text = json.dumps(payload)
    return [_content_chunk(text[i:i + chunk_size]) for i in range(0, len(text), chunk_size)]


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_response_parsed_correctly() -> None:
    """Full stream: thinking + chunked JSON → generate_json returns tool_call dict."""
    tool_call = _tool_call_payload()
    lines = _sse_stream(
        _thinking_chunk("Let me look at the codebase first. "),
        _thinking_chunk("search_code should help me locate create_task."),
        *_chunk_json(tool_call),
    )
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    thinking_received: list[str] = []
    result = await transport.generate_json(
        model="qwen3:8b",
        schema_name="planning_step_response",
        schema={"type": "object"},
        system_instructions="You are a planning agent.",
        user_payload={"goal": "add resume endpoint"},
        on_thinking=thinking_received.append,
    )

    assert result["type"] == "tool_call"
    assert result["tool"] == "search_code"
    assert isinstance(result["args"], dict)
    assert result["args"]["pattern"] == "def create_task"
    assert "".join(thinking_received) == (
        "Let me look at the codebase first. "
        "search_code should help me locate create_task."
    )


@pytest.mark.asyncio
async def test_emit_plan_response_parsed_correctly() -> None:
    """Full stream: thinking + chunked emit_plan JSON → generate_json returns plan dict."""
    emit_plan = _emit_plan_payload()
    lines = _sse_stream(
        _thinking_chunk("I have examined routes.py and engine.py. "),
        _thinking_chunk("Ready to emit the plan."),
        *_chunk_json(emit_plan),
    )
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    result = await transport.generate_json(
        model="qwen3:8b",
        schema_name="planning_step_response",
        schema={"type": "object"},
        system_instructions="You are a planning agent.",
        user_payload={"goal": "add resume endpoint"},
    )

    assert result["type"] == "emit_plan"
    assert result["confidence"] == "high"
    assert "routes.py" in str(result["files_examined"])
    assert "# Plan" in str(result["plan_markdown"])


@pytest.mark.asyncio
async def test_on_thinking_callback_fires_per_token() -> None:
    """on_thinking is called once per reasoning_content SSE chunk, not once total."""
    lines = _sse_stream(
        _thinking_chunk("chunk one "),
        _thinking_chunk("chunk two "),
        _thinking_chunk("chunk three"),
        *_chunk_json({"ok": True}),
    )
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    calls: list[str] = []
    await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
        on_thinking=calls.append,
    )

    assert calls == ["chunk one ", "chunk two ", "chunk three"]


@pytest.mark.asyncio
async def test_no_thinking_tokens_still_parses_content() -> None:
    """Stream with no reasoning_content at all still returns valid JSON."""
    lines = _sse_stream(*_chunk_json({"type": "tool_call", "thought": "x", "tool": "list_directory", "args": {}}))
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    calls: list[str] = []
    result = await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
        on_thinking=calls.append,
    )

    assert result["tool"] == "list_directory"
    assert calls == []


@pytest.mark.asyncio
async def test_json_repair_missing_args_key() -> None:
    """Model drops 'args': key before the args object — repair regex fixes it."""
    # Malformed: "tool": "read_file", {"path": "foo.py"}  (missing "args":)
    malformed_json = '{"type": "tool_call", "thought": "t", "tool": "read_file", {"path": "foo.py"}}'
    lines = _sse_stream(_content_chunk(malformed_json))
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    result = await transport.generate_json(
        model="m",
        schema_name="planning_step_response",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
    )

    assert result["tool"] == "read_file"
    assert result["args"] == {"path": "foo.py"}


@pytest.mark.asyncio
async def test_think_tags_in_content_are_stripped_for_generate_text() -> None:
    """generate_text strips <think>…</think> from content before returning."""
    text_with_think = "<think>reasoning trace</think>Here is the answer."
    lines = _sse_stream(_content_chunk(text_with_think))
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    result = await transport.generate_text(
        model="m",
        system_instructions="s",
        user_payload={},
    )

    assert result == "Here is the answer."
    assert "<think>" not in result


@pytest.mark.asyncio
async def test_request_body_has_stream_true_and_json_object_format() -> None:
    """generate_json sends stream=True and response_format json_object to the server."""
    lines = _sse_stream(*_chunk_json({"ok": True}))
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    await transport.generate_json(
        model="qwen3:8b",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="sys",
        user_payload={"k": "v"},
    )

    assert len(client.calls) == 1
    body = client.calls[0]["body"]
    assert body["stream"] is True
    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == "qwen3:8b"
    assert client.calls[0]["url"].endswith("/v1/chat/completions")


@pytest.mark.asyncio
async def test_retries_on_503_then_succeeds() -> None:
    """503 on first attempt → retry → success on second."""
    lines = _sse_stream(*_chunk_json({"ok": True}))
    responses: list[_FakeStreamResponse] = [
        _FakeStreamResponse(status_code=503, lines=[]),
        _ok_stream(lines),
    ]
    client = _FakeStreamClient(responses)
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client, max_retries=2)

    result = await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
    )

    assert result == {"ok": True}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_read_timeout_retries_then_succeeds() -> None:
    """httpx.ReadTimeout on first stream → retried → succeeds on second attempt."""
    lines = _sse_stream(*_chunk_json({"ok": True}))

    call_count = 0
    original_responses = [_ok_stream(lines)]

    class _TimeoutThenOkClient:
        calls: list[dict[str, Any]] = []
        closed = False

        def stream(self, method: str, url: str, *, json: dict[str, Any], timeout: Any) -> Any:
            self.calls.append({"method": method, "url": url, "body": json})

            class _Ctx:
                _attempt = len(self.calls)

                async def __aenter__(inner) -> _FakeStreamResponse:
                    if inner._attempt == 1:
                        raise httpx.ReadTimeout("no data for 60s", request=None)  # type: ignore[arg-type]
                    return _ok_stream(lines)

                async def __aexit__(inner, *_: object) -> None:
                    pass

            return _Ctx()

        async def aclose(self) -> None:
            self.closed = True

    client = _TimeoutThenOkClient()
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client, max_retries=2)  # type: ignore[arg-type]

    result = await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
    )

    assert result == {"ok": True}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_raises_after_retries_exhausted_on_503() -> None:
    responses = [_FakeStreamResponse(status_code=503, lines=[])] * 3
    client = _FakeStreamClient(responses)
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client, max_retries=2)

    with pytest.raises(RuntimeError, match="stream failed after 2 retries"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )

    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_non_retryable_4xx_raises_immediately() -> None:
    responses = [_FakeStreamResponse(status_code=400, lines=[])]
    client = _FakeStreamClient(responses)
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client, max_retries=2)

    with pytest.raises(RuntimeError, match="400"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )

    assert len(client.calls) == 1  # no retry for 4xx


def test_default_host_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TURBOQUANT_HOST", raising=False)
    t = TurboQuantTransport(profile=DEVSTRAL, http_client=_FakeStreamClient([]))
    assert t._host == "http://localhost:11435"


def test_honors_turboquant_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TURBOQUANT_HOST", "http://gpu-box:11435/")
    t = TurboQuantTransport(profile=DEVSTRAL, http_client=_FakeStreamClient([]))
    assert t._host == "http://gpu-box:11435"


# ── Tests using real prompt builders ─────────────────────────────────────────
# These tests call the same prompt builder functions that PlanningLoop uses on a
# live run, so they catch regressions in payload shape without needing the full
# orchestrator.


def test_real_first_turn_payload_shape(tmp_path: Path) -> None:
    """build_planning_step_payload first-turn produces expected keys."""
    registry = PlanningToolRegistry(real_path=tmp_path)
    tool_defs = [t.model_dump() for t in registry.definitions()]
    plan_context: dict[str, object] = {
        "goal": "add a resume endpoint",
        "workspace_path": str(tmp_path),
    }
    payload = build_planning_step_payload(plan_context, [], tool_defs)

    assert payload["goal"] == "add a resume endpoint"
    assert payload["workspace_path"] == str(tmp_path)
    assert "SEARCHING" in str(payload["instruction"])
    assert "conversation_history" not in payload


def test_real_subsequent_turn_payload_includes_history(tmp_path: Path) -> None:
    """build_planning_step_payload with history includes conversation_history and shifts instruction."""
    registry = PlanningToolRegistry(real_path=tmp_path)
    tool_defs = [t.model_dump() for t in registry.definitions()]
    plan_context: dict[str, object] = {
        "goal": "add a resume endpoint",
        "workspace_path": str(tmp_path),
    }
    history: list[dict[str, object]] = [
        {"role": "assistant", "content": json.dumps({
            "type": "tool_call", "tool": "search_code", "args": {"pattern": "def build_router"},
        })},
        {"role": "tool_result", "tool": "search_code", "content": "api/routes.py:45: def build_router"},
    ]
    payload = build_planning_step_payload(plan_context, history, tool_defs)

    assert "conversation_history" in payload
    assert payload["conversation_history"] == history
    assert "Continue exploring" in str(payload["instruction"])


@pytest.mark.asyncio
async def test_tool_call_with_real_planning_inputs(tmp_path: Path) -> None:
    """generate_json parses tool_call response using real system prompt + schema."""
    registry = PlanningToolRegistry(real_path=tmp_path)
    tool_defs = [t.model_dump() for t in registry.definitions()]
    system_instructions = format_planning_system_prompt(tool_defs)
    user_payload = build_planning_step_payload(
        {"goal": "add a resume endpoint to the API", "workspace_path": str(tmp_path)},
        [],
        tool_defs,
    )

    tool_call = _tool_call_payload()
    lines = _sse_stream(
        _thinking_chunk("I should search for where create_task is defined. "),
        _thinking_chunk("search_code should help me locate it."),
        *_chunk_json(tool_call),
    )
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    thinking_received: list[str] = []
    result = await transport.generate_json(
        model="qwen3:8b",
        schema_name="planning_step_response",
        schema=PLANNING_STEP_RESPONSE_SCHEMA,
        system_instructions=system_instructions,
        user_payload=user_payload,
        on_thinking=thinking_received.append,
    )

    assert result["type"] == "tool_call"
    assert result["tool"] == "search_code"
    assert result["args"]["pattern"] == "def create_task"
    assert len(thinking_received) == 2

    # Request body has real planning system prompt with tool defs embedded
    messages = client.calls[0]["body"]["messages"]
    system_content = messages[0]["content"]
    assert "PLANNING RULES" in system_content
    assert "search_code" in system_content  # tool defs present
    assert "REQUIRED OUTPUT FORMAT" in system_content  # schema appended

    # User message is JSON-encoded payload with goal + first-turn instruction
    user_json = json.loads(messages[1]["content"])
    assert user_json["goal"] == "add a resume endpoint to the API"
    assert "SEARCHING" in user_json["instruction"]
    assert "conversation_history" not in user_json


@pytest.mark.asyncio
async def test_emit_plan_with_real_planning_inputs(tmp_path: Path) -> None:
    """generate_json parses emit_plan response correctly using real schema; subsequent-turn payload includes history."""
    registry = PlanningToolRegistry(real_path=tmp_path)
    tool_defs = [t.model_dump() for t in registry.definitions()]
    history: list[dict[str, object]] = [
        {
            "role": "assistant",
            "content": json.dumps({
                "type": "tool_call",
                "thought": "Let me search first",
                "tool": "search_code",
                "args": {"pattern": "def build_router"},
            }),
        },
        {
            "role": "tool_result",
            "tool": "search_code",
            "content": "api/routes.py:45: def build_router(...)",
        },
    ]
    user_payload = build_planning_step_payload(
        {"goal": "add a resume endpoint", "workspace_path": str(tmp_path)},
        history,
        tool_defs,
    )

    emit_plan = _emit_plan_payload()
    lines = _sse_stream(
        _thinking_chunk("I have enough context. "),
        _thinking_chunk("Ready to emit the plan."),
        *_chunk_json(emit_plan),
    )
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    result = await transport.generate_json(
        model="qwen3:8b",
        schema_name="planning_step_response",
        schema=PLANNING_STEP_RESPONSE_SCHEMA,
        system_instructions=format_planning_system_prompt(tool_defs),
        user_payload=user_payload,
    )

    assert result["type"] == "emit_plan"
    assert result["confidence"] == "high"
    assert "# Plan" in str(result["plan_markdown"])
    assert "routes.py" in str(result["files_examined"])

    # Subsequent-turn payload carries history and the "Continue" instruction
    user_json = json.loads(client.calls[0]["body"]["messages"][1]["content"])
    assert "conversation_history" in user_json
    assert "Continue exploring" in user_json["instruction"]


@pytest.mark.asyncio
async def test_revision_mode_prompt_and_emit_revision_parsing(tmp_path: Path) -> None:
    """Revision mode: system prompt gets REVISION MODE suffix; emit_revision response parses correctly."""
    registry = PlanningToolRegistry(real_path=tmp_path)
    tool_defs = [t.model_dump() for t in registry.definitions()]
    system_instructions = format_planning_system_prompt(tool_defs, revision_mode=True)

    assert "REVISION MODE" in system_instructions
    assert "emit_revision" in system_instructions

    plan_context: dict[str, object] = {
        "goal": "add a resume endpoint",
        "workspace_path": str(tmp_path),
        "revision_request": {
            "step_id": "S1",
            "reason": "function not in planned file",
            "evidence": "grep found it in other.py",
        },
        "plan_steps": [{"id": "S1", "status": "failed"}],
        "revertable_step_ids": [],
    }
    user_payload = build_planning_step_payload(plan_context, [], tool_defs)

    assert "revision_request" in user_payload
    assert "plan_steps" in user_payload
    assert "revertable_step_ids" in user_payload

    emit_revision = {
        "type": "emit_revision",
        "thought": "The function lives in other.py",
        "revised_steps": [
            {
                "step_id": "S1",
                "goal": "Add helper to correct file",
                "targets": [{"path": "other.py", "intent": "existing"}],
                "implementation_details": "add helper function",
                "edge_cases": "",
                "testing_strategy": "run pytest tests/test_other.py",
                "risk": "low",
            }
        ],
        "reverted_step_ids": [],
        "revision_summary": "Switched target to other.py",
    }
    lines = _sse_stream(*_chunk_json(emit_revision))
    client = _FakeStreamClient([_ok_stream(lines)])
    transport = TurboQuantTransport(profile=DEVSTRAL, http_client=client)

    result = await transport.generate_json(
        model="qwen3:8b",
        schema_name="planning_step_response",
        schema=PLANNING_STEP_RESPONSE_SCHEMA,
        system_instructions=system_instructions,
        user_payload=user_payload,
    )

    assert result["type"] == "emit_revision"
    assert len(result["revised_steps"]) == 1
    assert result["revised_steps"][0]["step_id"] == "S1"
    assert result["revision_summary"] == "Switched target to other.py"


# ── Live-server integration test ──────────────────────────────────────────────
# Skipped automatically when the TurboQuant server is not running.
# Run manually with the server up:
#   pytest tests/test_turboquant_transport.py::test_live_server_planning_step -s -v
#
# Uses the same inputs as a live PlanningLoop first turn so you can observe
# real thinking tokens streaming back from qwen3.


@pytest.mark.asyncio
async def test_live_server_planning_step(tmp_path: Path) -> None:
    """Integration: call the real TurboQuant server with live planning prompts.

    Sends the exact same system prompt, user payload, and schema that PlanningLoop
    uses on turn 1. Verifies the server returns a valid planning step response and
    that thinking tokens stream through the on_thinking callback.

    Skipped if the server is not reachable at TURBOQUANT_HOST (default localhost:11435).
    """
    import os

    host = (os.getenv("TURBOQUANT_HOST") or "http://localhost:11435").rstrip("/")
    model = os.getenv("TURBOQUANT_MODEL", "qwen3:8b")

    # Probe liveness — skip instead of failing if the server is down.
    async with httpx.AsyncClient(timeout=3.0) as probe:
        try:
            await probe.get(f"{host}/v1/models")
        except Exception as exc:
            pytest.skip(f"TurboQuant server not reachable at {host}: {exc}")

    registry = PlanningToolRegistry(real_path=tmp_path)
    tool_defs = [t.model_dump() for t in registry.definitions()]
    system_instructions = format_planning_system_prompt(tool_defs)
    user_payload = build_planning_step_payload(
        {
            "goal": "add a POST /v1/tasks/{id}/resume endpoint that creates a child task",
            "workspace_path": str(tmp_path),
        },
        [],
        tool_defs,
    )

    thinking_chunks: list[str] = []
    transport = TurboQuantTransport(profile=DEVSTRAL, max_retries=1)
    try:
        result = await transport.generate_json(
            model=model,
            schema_name="planning_step_response",
            schema=PLANNING_STEP_RESPONSE_SCHEMA,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=thinking_chunks.append,
        )
    finally:
        await transport.aclose()

    # Model must return a valid discriminated-union type
    assert result.get("type") in ("tool_call", "emit_plan", "emit_revision"), (
        f"Unexpected type: {result.get('type')!r} — full response: {result}"
    )
    assert "thought" in result, f"Missing 'thought' field: {result}"

    # qwen3 always emits thinking tokens for planning prompts — verify the
    # on_thinking callback fired and captured at least some content.
    thinking_text = "".join(thinking_chunks)
    assert len(thinking_text) > 0, (
        "Expected thinking tokens from qwen3 but on_thinking was never called. "
        "Check that reasoning_content is present in the SSE stream."
    )
