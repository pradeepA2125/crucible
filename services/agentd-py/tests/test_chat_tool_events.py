"""Tool calls leave durable pill records in the chat transcript.

Live pills stream over SSE into the webview bubble and vanish on reload; the
persisted ``metadata.tool_events`` message (ToolEventView shape) is the durable
record so the user can re-read or copy commands later.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.chat.tool_events import TOOL_EVENT_MAX_OUTPUT_CHARS, trace_to_tool_events
from agentd.domain.models import AgentToolTrace, TaskRecord, ToolCall, ToolResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _trace(*pairs: tuple[ToolCall, ToolResult | None]) -> AgentToolTrace:
    trace = AgentToolTrace(step_id="s1")
    for call, result in pairs:
        trace.calls.append(call)
        if result is not None:
            trace.results.append(result)
    return trace


# ── trace_to_tool_events ─────────────────────────────────────────────────────


def test_trace_to_tool_events_joins_calls_with_results() -> None:
    trace = _trace(
        (
            ToolCall(call_id="c1", tool_name="run_command",
                     arguments={"command": "pytest -q"}, thought="run the tests"),
            ToolResult(call_id="c1", tool_name="run_command", output="3 passed", is_error=False),
        ),
        (
            ToolCall(call_id="c2", tool_name="read_file", arguments={"path": "a.py"}),
            ToolResult(call_id="c2", tool_name="read_file", output="x = 1", is_error=True),
        ),
    )

    events = trace_to_tool_events(trace, "execution")

    assert events == [
        {
            "id": 0, "tool": "run_command", "args": {"command": "pytest -q"},
            "source": "execution", "done": True, "thought": "run the tests",
            "output": "3 passed", "isError": False,
        },
        {
            "id": 1, "tool": "read_file", "args": {"path": "a.py"},
            "source": "execution", "done": True, "output": "x = 1", "isError": True,
        },
    ]


def test_trace_to_tool_events_truncates_long_output() -> None:
    long_output = "x" * (TOOL_EVENT_MAX_OUTPUT_CHARS + 100)
    trace = _trace((
        ToolCall(call_id="c1", tool_name="run_command", arguments={}),
        ToolResult(call_id="c1", tool_name="run_command", output=long_output),
    ))

    [event] = trace_to_tool_events(trace, "execution")

    assert len(event["output"]) < len(long_output)
    assert event["output"].endswith("… truncated")


def test_trace_to_tool_events_call_without_result() -> None:
    trace = _trace(
        (ToolCall(call_id="c1", tool_name="search_code", arguments={"query": "foo"}), None)
    )

    [event] = trace_to_tool_events(trace, "planning")

    assert event["tool"] == "search_code"
    assert event["source"] == "planning"
    assert "output" not in event and "isError" not in event
    assert event["done"] is True


# ── engine writer ────────────────────────────────────────────────────────────


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _make(tmp_path: Path) -> tuple[AgentOrchestrator, ChatThreadStore, str]:
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    thread = chat_store.create_thread(str(tmp_path))
    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        chat_store=chat_store,
    )
    return orch, chat_store, thread.thread_id


def _pill_messages(chat_store: ChatThreadStore, thread_id: str) -> list:
    thread = chat_store.get_thread(thread_id)
    assert thread is not None
    return [m for m in thread.messages if m.metadata.get("tool_events")]


def test_write_chat_tool_events_persists_pills_message(tmp_path: Path) -> None:
    orch, chat_store, thread_id = _make(tmp_path)
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path),
                      chat_channel_id=f"chat:{thread_id}")
    trace = _trace((
        ToolCall(call_id="c1", tool_name="run_command", arguments={"command": "ls"}),
        ToolResult(call_id="c1", tool_name="run_command", output="a.py"),
    ))

    orch._write_chat_tool_events(task, trace, "execution", step_id="s1", step_title="add the thing")

    [msg] = _pill_messages(chat_store, thread_id)
    assert msg.content == ""
    assert msg.metadata["step_id"] == "s1"
    assert msg.metadata["step_title"] == "add the thing"
    [event] = msg.metadata["tool_events"]
    assert event["tool"] == "run_command"
    assert event["source"] == "execution"


def test_write_chat_tool_events_skips_empty_trace(tmp_path: Path) -> None:
    orch, chat_store, thread_id = _make(tmp_path)
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path),
                      chat_channel_id=f"chat:{thread_id}")

    orch._write_chat_tool_events(task, AgentToolTrace(step_id="s1"), "planning")

    assert _pill_messages(chat_store, thread_id) == []


def test_write_chat_tool_events_skips_without_chat_channel(tmp_path: Path) -> None:
    orch, chat_store, thread_id = _make(tmp_path)
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    trace = _trace((ToolCall(call_id="c1", tool_name="read_file", arguments={}), None))

    orch._write_chat_tool_events(task, trace, "execution")

    assert _pill_messages(chat_store, thread_id) == []


# ── inline change diff card ──────────────────────────────────────────────────


class _EmitPatchEngine:
    """Scripted reasoning engine: reads the file once, then emits a search_replace patch."""

    def __init__(self) -> None:
        self._did_read = False

    async def create_tool_step(self, step_context, history, tool_definitions,
                               on_thinking=None, state_description="", allowed_action_types=None):
        in_verify = any(
            isinstance(msg.get("content"), str) and "Patch applied successfully" in msg["content"]
            for msg in history
        )
        if in_verify:
            return {"type": "verify_done", "thought": "done", "verified": True, "test_output": ""}
        if not self._did_read:
            self._did_read = True
            return {"type": "tool_call", "thought": "check the file",
                    "tool": "read_file", "args": {"path": "a.py"}}
        return {
            "type": "emit_patch",
            "thought": "patching",
            "patch_ops": [{"op": "search_replace", "file": "a.py",
                           "search": "x = 1", "replace": "x = 2", "reason": "r"}],
        }

    async def create_patch(self, *a, **kw): return {}
    async def create_planning_step(self, *a, **kw): return {}
    async def create_plan(self, *a, **kw): return {}


class _RecordingStore:
    def __init__(self) -> None:
        self.messages: list = []

    def append_message(self, thread_id: str, message: object) -> None:
        self.messages.append(message)


class _AlwaysPassValidator:
    async def run(self, workspace_path): ...


@pytest.mark.asyncio
async def test_inline_change_diff_card_carries_tool_events(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n")

    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_EmitPatchEngine(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    orch.broadcaster.subscribe("chat:t1")
    store = _RecordingStore()
    explore_events = [{
        "id": 0, "tool": "read_file", "args": {"path": "a.py"},
        "source": "explore", "output": "x = 1", "isError": False, "done": True,
    }]

    await orch.run_inline_change(
        thread_id="t1",
        goal="change x to 2",
        workspace_path=str(ws),
        plan_markdown="- change x to 2",
        explore_context=[{"tool": "read_file", "args": {"path": "a.py"}, "result": "x = 1"}],
        channel_id="chat:t1",
        store=store,
        explore_events=explore_events,
    )

    diff_cards = [m for m in store.messages if getattr(m, "type", "") == "diff_card"]
    assert len(diff_cards) == 1
    events = diff_cards[0].metadata["tool_events"]
    sources = [e["source"] for e in events]
    assert sources[0] == "explore"
    assert "execution" in sources, f"expected execution pills, got {sources}"
    # ids re-sequenced so they stay unique within the message
    assert [e["id"] for e in events] == list(range(len(events)))
