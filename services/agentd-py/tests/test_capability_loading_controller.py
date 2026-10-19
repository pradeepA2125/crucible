"""On-demand MCP capability loading in the main controller loop (Part B, Task 9)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentd.capabilities.bundle import render_bundle
from agentd.chat.controller import ChatController
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.chat.storage import ChatThreadStore
from agentd.mcp.rules import McpRuleStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.registry import ToolDefinition
from tests.test_dispatch_integration import _controller as _dispatch_controller
from tests.test_dispatch_integration import _dispatch

FETCH = ToolDefinition(name="mcp__web__fetch", description="Fetch a URL",
                       parameters={"type": "object", "properties": {"url": {"type": "string"}},
                                   "required": ["url"]})


class _Manager:
    def __init__(self, defs: list[ToolDefinition] | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._defs = defs if defs is not None else [FETCH]

    def tool_definitions(self) -> list[ToolDefinition]:
        return list(self._defs)

    def refusal(self, server: str, tool: str) -> str | None:
        return None

    async def call_tool(self, server: str, tool: str, args: dict) -> object:
        self.calls.append((server, tool, args))

        class _Result:
            content = [type("T", (), {"type": "text", "text": "page body"})()]
            isError = False
        return _Result()


@dataclass
class _Step:
    label: str
    names: list[str]
    directory: str | None
    system: str
    history: list[dict[str, object]]


class _Recording(ScriptedReasoningEngine):
    """Records what each step saw: the catalog, the directory and the history."""

    def __init__(self, responses, agent_scripts=None) -> None:
        super().__init__(None, [], controller_step_responses=responses,
                         agent_scripts=agent_scripts)
        self.steps: list[_Step] = []
        self.result_budget: int | None = None

    async def create_controller_step(  # type: ignore[override]
            self, plan_context, history, tool_definitions, *,
            capability_directory: str | None = None, **kwargs):
        ctx = kwargs.get("render_ctx")
        if self.result_budget is not None:
            plan_context["tool_result_budget"] = self.result_budget
        self.steps.append(_Step(
            label=getattr(ctx, "agent_label", "") if ctx is not None else "",
            names=[str(d.get("name")) for d in tool_definitions],
            directory=capability_directory,
            system=format_controller_system_prompt(
                tool_definitions, render_ctx=ctx, capability_directory=capability_directory),
            history=[dict(m) for m in history],
        ))
        return await super().create_controller_step(plan_context, history, tool_definitions,
                                                    **kwargs)


def _call(tool: str, args: dict, ident: str = "c1") -> dict:
    return {"id": ident, "tool": tool, "args": args}


def _batch(*calls: dict) -> dict:
    return {"type": "tool_calls", "thought": "t", "calls": list(calls)}


ANSWER = {"type": "answer", "thought": "t", "answer": "done"}
LOAD = _batch(_call("load_capabilities", {"namespace": "mcp.web"}))
FETCH_CALL = _batch(_call("mcp__web__fetch", {"url": "https://example.com"}))


def _setup(tmp_path: Path, engine, *, manager=None):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    manager = manager or _Manager()
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=engine,
                          thread_store=store, orchestrator=None,
                          broadcaster=_broadcaster(), retrieval_client=None,
                          mcp_manager=manager)
    McpRuleStore(str(tmp_path)).add("web", "fetch")
    return ctrl, tid, manager


def _broadcaster():
    from agentd.orchestrator.broadcaster import EventBroadcaster
    return EventBroadcaster()


async def _run(ctrl, tid, *, seed_history=None):
    return await ctrl._run_loop(tid, f"chat:{tid}", "go", seed_history=seed_history,
                                step_review=None, turn_id="turn-1")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_CAPABILITY_LOADING_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")


def _results(history):
    return [m for m in history if m.get("role") == "tool_result"]


@pytest.mark.asyncio
async def test_first_request_has_directory_not_schemas(tmp_path, on):
    engine = _Recording([ANSWER])
    ctrl, tid, _ = _setup(tmp_path, engine)
    await _run(ctrl, tid)
    step = engine.steps[0]
    assert not any(n.startswith("mcp__") for n in step.names)
    assert "load_capabilities" in step.names
    assert step.directory is not None and "mcp.web" in step.directory
    assert "OPTIONAL CAPABILITIES" in step.system and "mcp.web" in step.system
    assert "EXTERNAL MCP TOOLS" not in step.system


@pytest.mark.asyncio
async def test_load_then_call_next_step(tmp_path, on):
    engine = _Recording([LOAD, FETCH_CALL, ANSWER])
    ctrl, tid, manager = _setup(tmp_path, engine)
    await _run(ctrl, tid)
    bundle = json.loads(_results(engine.steps[1].history)[-1]["content"])
    assert bundle["kind"] == "capability_bundle"
    assert "EXTERNAL MCP TOOLS" in bundle["guidance"]
    assert manager.calls == [("web", "fetch", {"url": "https://example.com"})]


@pytest.mark.asyncio
async def test_unloaded_call_is_refused_before_execution(tmp_path, on):
    (tmp_path / "a.py").write_text("A = 1\n")
    engine = _Recording([
        _batch(_call("read_file", {"path": "a.py"}, "r"),
               _call("mcp__web__fetch", {"url": "https://example.com"}, "f")),
        ANSWER])
    ctrl, tid, manager = _setup(tmp_path, engine)
    await _run(ctrl, tid)
    later = engine.steps[1].history
    assert any("not loaded" in str(m.get("content")) for m in later)
    assert {m.get("status") for m in _results(later)} == {"blocked"}
    assert manager.calls == []


@pytest.mark.asyncio
async def test_unloaded_after_eviction_is_refused(tmp_path, on):
    # The bundle was in an earlier turn's history, then compaction evicted it: the
    # seed history holds only the summary, so the tool is unloaded again.
    seed = [{"role": "user", "content": "[MEMORY] Summary: mcp.web was loaded."}]
    engine = _Recording([FETCH_CALL, ANSWER])
    ctrl, tid, manager = _setup(tmp_path, engine)
    await _run(ctrl, tid, seed_history=seed)
    assert any('load_capabilities with namespace "mcp.web"' in str(m.get("content"))
               for m in engine.steps[1].history)
    assert manager.calls == []


@pytest.mark.asyncio
async def test_loaded_in_earlier_turn_is_callable(tmp_path, on):
    bundle = render_bundle("mcp.web", [FETCH.model_dump()], "guidance")
    seed = [{"role": "tool_result", "tool": "load_capabilities", "is_error": False,
             "content": bundle}]
    engine = _Recording([FETCH_CALL, ANSWER])
    ctrl, tid, manager = _setup(tmp_path, engine)
    await _run(ctrl, tid, seed_history=seed)
    assert len(manager.calls) == 1


@pytest.mark.asyncio
async def test_loader_must_run_alone(tmp_path, on):
    (tmp_path / "a.py").write_text("A = 1\n")
    engine = _Recording([
        _batch(_call("load_capabilities", {"namespace": "mcp.web"}, "l"),
               _call("read_file", {"path": "a.py"}, "r")),
        ANSWER])
    ctrl, tid, _ = _setup(tmp_path, engine)
    await _run(ctrl, tid)
    later = engine.steps[1].history
    assert any("alone" in str(m.get("content")) for m in later)
    assert {m.get("status") for m in _results(later)} == {"blocked"}


@pytest.mark.asyncio
async def test_flag_off_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_CAPABILITY_LOADING_ENABLED", "0")
    engine = _Recording([ANSWER])
    ctrl, tid, _ = _setup(tmp_path, engine)
    await _run(ctrl, tid)
    step = engine.steps[0]
    assert "mcp__web__fetch" in step.names and "load_capabilities" not in step.names
    assert step.directory is None
    assert "EXTERNAL MCP TOOLS" in step.system
    assert "OPTIONAL CAPABILITIES" not in step.system


@pytest.mark.asyncio
async def test_child_keeps_full_catalog(tmp_path, on, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = _Recording(
        [_dispatch(("general-purpose", "helper", "Look around")),
         _batch(_call("wait_agents", {})), ANSWER],
        agent_scripts={"helper": [{"type": "report", "thought": "t", "summary": "ok"}]})
    ctrl = _dispatch_controller(ws, tmp_path, store, engine)
    ctrl._mcp_manager = _Manager()
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    child = next(s for s in engine.steps if s.label == "helper")
    assert "mcp__web__fetch" in child.names and "load_capabilities" not in child.names
    assert child.directory is None
    main = next(s for s in engine.steps if s.label == "")
    assert "load_capabilities" in main.names


class _SystemTransport:
    def __init__(self) -> None:
        self.system = ""

    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_thinking=None, on_retry=None):
        self.system = system_instructions
        return {"type": "answer", "thought": "t", "answer": "ok"}


@pytest.mark.asyncio
async def test_engine_puts_directory_in_system_prompt():
    from agentd.reasoning.engine import DefaultReasoningEngine

    transport = _SystemTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    await engine.create_controller_step(
        plan_context={"goal": "g"}, history=[], tool_definitions=[], phase="ACTIVE",
        capability_directory="- mcp.web (1 tools): fetch")
    assert "OPTIONAL CAPABILITIES" in transport.system
    assert "OPTIONAL CAPABILITIES" in transport.system.split("- mcp.web (1 tools): fetch")[0]


def test_bundle_guidance_points_at_the_bundle():
    from agentd.chat.controller_prompts import mcp_bundle_guidance
    from agentd.prompting.tagged import RenderContext

    text = mcp_bundle_guidance(RenderContext.main())
    assert text.startswith("EXTERNAL MCP TOOLS")
    assert "in TOOLS" not in text and "in this bundle" in text
    assert "approval card" in text


@pytest.mark.asyncio
async def test_large_bundle_reaches_history_whole_under_a_small_result_budget(tmp_path, on):
    big = [ToolDefinition(name=f"mcp__web__t{i}", description="y" * 3700,
                          parameters={"type": "object", "properties": {}}) for i in range(8)]
    engine = _Recording([LOAD, ANSWER])
    engine.result_budget = 16_000
    ctrl, tid, _ = _setup(tmp_path, engine, manager=_Manager(big))
    await _run(ctrl, tid)
    content = str(_results(engine.steps[1].history)[-1]["content"])
    assert 16_000 < len(content) <= 32_000
    assert len(json.loads(content)["tools"]) == 8


@pytest.mark.asyncio
async def test_tiny_mcp_budget_still_lists_every_tool_in_the_directory(
        tmp_path, on, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_MCP_TOOLS_MAX_CHARS", "10")
    many = [ToolDefinition(name=f"mcp__web__t{i}", description="d" * 100,
                           parameters={"type": "object", "properties": {}}) for i in range(5)]
    engine = _Recording([ANSWER])
    ctrl, tid, _ = _setup(tmp_path, engine, manager=_Manager(many))
    await _run(ctrl, tid)
    assert "mcp.web (5 tools)" in str(engine.steps[0].directory)
