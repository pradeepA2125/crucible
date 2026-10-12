"""Native search uses durable gates, isolated rules and effective child policy."""
import asyncio
from functools import partial
from types import SimpleNamespace

import pytest

from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import McpToolDecision
from agentd.mcp.rules import McpRuleStore
from agentd.tools.sources import AggregatingToolRegistry
from agentd.web.config import WebSearchRuleStore
from agentd.web.tool_source import WebSearchToolSource
from tests.gate_helpers import first_gate
from tests.test_controller_mcp_gate import _controller
from tests.test_web_tool_source import Client


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_WEB_SEARCH_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="search")
    ctrl = _controller(tmp_path, store)
    return ctrl, store, thread.thread_id


def child(thread_id, permission="default", no_ask=False):
    return SimpleNamespace(thread_id=thread_id, agent_id="agent-1", loop=None,
                           broadcaster=None, transcript=None,
                           context=SimpleNamespace(permission=permission, no_ask=no_ask,
                                                   label="searcher", name="helper"))


def call(ctrl, tid, *, native=True):
    return ctrl._mcp_approval_cb(tid, f"chat:{tid}", "Exa", "web_search",
                                 {"query": "public docs"}, native_web=native)


@pytest.mark.asyncio
@pytest.mark.parametrize("approve,remember", [(True, False), (True, True), (False, False)])
async def test_durable_native_payload_and_decisions(env, tmp_path, approve, remember):
    ctrl, store, tid = env
    queue = ctrl._broadcaster.subscribe(f"chat:{tid}")
    task = asyncio.create_task(call(ctrl, tid))
    await asyncio.sleep(0)
    gate = first_gate(store.get_thread(tid))
    assert gate.kind == "mcp_tool"
    assert gate.payload == {"server": "Exa", "tool": "web_search",
                            "args": {"query": "public docs"}, "native_web": True}
    poke = queue.get_nowait()
    assert poke["payload"]["native_web"] is True
    assert await ctrl.resolve_mcp(
        tid, McpToolDecision(approve=approve, remember=remember), gate.gate_id)
    assert (await task).approved is approve
    assert not ctrl._pending_mcp and first_gate(store.get_thread(tid)) is None
    native = WebSearchRuleStore(tmp_path)
    assert native.matches("Exa", "web_search") is remember
    assert not McpRuleStore(tmp_path).matches("Exa", "web_search")
    if remember:
        assert [(r["server"], r["tool"]) for r in native.load()] == [("Exa", "web_search")]
        assert (await call(ctrl, tid)).approved


@pytest.mark.asyncio
async def test_timeout_and_cancellation_cleanup(env, monkeypatch):
    ctrl, store, tid = env
    monkeypatch.setenv("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", "0.01")
    outcome = await call(ctrl, tid)
    assert not outcome.approved and outcome.denied_by == "timeout"
    assert first_gate(store.get_thread(tid)) is None and not ctrl._pending_mcp
    monkeypatch.setenv("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", "0")
    task = asyncio.create_task(call(ctrl, tid))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert first_gate(store.get_thread(tid)) is None and not ctrl._pending_mcp


@pytest.mark.asyncio
async def test_concurrent_gates_and_restart_orphan(env):
    ctrl, store, tid = env
    tasks = [asyncio.create_task(call(ctrl, tid)) for _ in range(2)]
    await asyncio.sleep(0)
    gates = store.get_thread(tid).pending_controller_gates
    assert len(gates) == 2
    await ctrl.resolve_mcp(tid, McpToolDecision(approve=False), gates[1].gate_id)
    await ctrl.resolve_mcp(tid, McpToolDecision(approve=True), gates[0].gate_id)
    assert [o.approved for o in await asyncio.gather(*tasks)] == [True, False]
    gate = store.add_controller_gate(tid, PendingGate.new("mcp_tool", {
        "server": "Exa", "tool": "web_search", "native_web": True}))
    assert not await ctrl.resolve_mcp(tid, McpToolDecision(approve=True), gate.gate_id)
    assert first_gate(store.get_thread(tid)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("native_remembered", [False, True])
async def test_bidirectional_rule_isolation_main_and_no_ask_child(env, tmp_path, native_remembered):
    ctrl, store, tid = env
    rules = WebSearchRuleStore(tmp_path) if native_remembered else McpRuleStore(tmp_path)
    rules.add("Exa", "web_search")
    task = asyncio.create_task(call(ctrl, tid, native=not native_remembered))
    await asyncio.sleep(0)
    assert first_gate(store.get_thread(tid)) is not None
    await ctrl.resolve_mcp(tid, McpToolDecision(approve=False))
    assert not (await task).approved
    handle = child(tid, "dontAsk", True)
    native = await ctrl._child_web_approval_cb(handle, "Exa", "web_search", {})
    external = await ctrl._child_mcp_approval_cb(handle, "Exa", "web_search", {})
    assert native.approved is native_remembered
    assert external.approved is not native_remembered
    assert first_gate(store.get_thread(tid)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("server,tool", [("forged", "web_search"), ("Exa", "forged")])
@pytest.mark.parametrize("is_child", [False, True])
async def test_forged_identity_never_looks_up_rules_or_gates(
    env, tmp_path, monkeypatch, server, tool, is_child,
):
    ctrl, store, tid = env
    WebSearchRuleStore(tmp_path).add(server, tool)

    def forbidden(*args):
        pytest.fail("forged identities must be denied before rule access")

    monkeypatch.setattr(WebSearchRuleStore, "matches", forbidden)
    monkeypatch.setattr(WebSearchRuleStore, "add", forbidden)
    monkeypatch.setattr(store, "add_controller_gate", forbidden)
    if is_child:
        out = await ctrl._child_web_approval_cb(child(tid), server, tool, {})
    else:
        out = await ctrl._mcp_approval_cb(tid, f"chat:{tid}", server, tool, {}, native_web=True)
    assert not out.approved and out.denied_by == "policy"
    assert not ctrl._pending_mcp


@pytest.mark.asyncio
@pytest.mark.parametrize("permission,no_ask,remembered,expected", [
    ("plan", False, True, False), ("plan", False, False, False),
    ("dontAsk", True, False, False), ("dontAsk", True, True, True),
    ("default", True, False, False), ("default", False, True, True),
])
async def test_effective_child_policy(env, tmp_path, permission, no_ask, remembered, expected):
    ctrl, store, tid = env
    if remembered:
        WebSearchRuleStore(tmp_path).add("Exa", "web_search")
    outcome = await ctrl._child_web_approval_cb(
        child(tid, permission, no_ask), "Exa", "web_search", {})
    assert outcome.approved is expected
    assert first_gate(store.get_thread(tid)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("is_child", [False, True])
async def test_real_denial_prevents_network(env, monkeypatch, is_child):
    ctrl, store, tid = env
    client = Client()
    monkeypatch.setattr("agentd.web.exa.ExaSearchClient", lambda: client)
    if is_child:
        source = WebSearchToolSource(client, partial(ctrl._child_web_approval_cb, child(tid)),
                                     enabled=lambda: True)
        registry = AggregatingToolRegistry([source])
    else:
        registry = ctrl._build_registry(thread_id=tid)
    task = asyncio.create_task(registry.execute("web_search", {"query": "docs"}))
    await asyncio.sleep(0)
    assert first_gate(store.get_thread(tid)) is not None
    assert not client.calls
    await ctrl.resolve_mcp(tid, McpToolDecision(approve=False))
    assert (await task).is_error and not client.calls


def test_registration_without_mcp_and_disable(env, monkeypatch):
    ctrl, _, tid = env
    assert ctrl._mcp_manager is None
    registry = ctrl._build_registry(thread_id=tid)
    assert "web_search" in [d.name for d in registry.definitions()]
    monkeypatch.setenv("CRUCIBLE_WEB_SEARCH_ENABLED", "0")
    assert "web_search" not in [d.name for d in registry.definitions()]
    assert "web_search" not in [d.name for d in ctrl._build_registry(thread_id=tid).definitions()]


@pytest.mark.asyncio
@pytest.mark.parametrize("permission,flag,disallowed,expected", [
    ("default", "1", False, True), ("dontAsk", "1", False, True),
    ("plan", "1", False, False), ("default", "0", False, False),
    ("default", "1", True, False),
])
async def test_actual_child_activation_registration_and_filtering(
    tmp_path, monkeypatch, permission, flag, disallowed, expected,
):
    from dataclasses import replace

    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from tests.test_child_gates import _setup

    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch, permission=permission)
    monkeypatch.setenv("CRUCIBLE_WEB_SEARCH_ENABLED", flag)
    client = Client()
    monkeypatch.setattr("agentd.web.exa.ExaSearchClient", lambda: client)
    WebSearchRuleStore(tmp_path).add("Exa", "web_search")
    handle.definition = replace(
        handle.definition, tools=frozenset({"web_search"}),
        disallowed_tools=frozenset({"web_search"}) if disallowed else frozenset())
    ctrl._reasoning = ScriptedReasoningEngine(None, [], controller_step_responses=[
        {
            "type": "tool_calls",
            "thought": "search",
            "calls": [{"id": "call1", "tool": "web_search", "args": {"query": "docs"}}],
        },
        {"type": "report", "thought": "done", "summary": "Finished.", "status": "completed"},
    ])
    seen = []
    original = ctrl._reasoning.create_controller_step

    async def capture(**kwargs):
        seen.append(kwargs["tool_definitions"])
        kwargs.pop("on_context_input", None)
        return await original(**kwargs)

    monkeypatch.setattr(ctrl._reasoning, "create_controller_step", capture)
    await ctrl._activate(handle)
    assert bool(client.calls) is expected
    assert ("web_search" in [d["name"] for d in seen[0]]) is expected
    assert first_gate(store.get_thread(tid)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("compact_context", [False, True])
async def test_openai_and_anthropic_share_definition_and_tool_source(
    env, tmp_path, monkeypatch, compact_context
):
    monkeypatch.setenv("CRUCIBLE_COMPACT_CONTEXT_ENABLED", "1" if compact_context else "0")
    import json

    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.providers.anthropic_transport import AnthropicJsonTransport
    from agentd.providers.openai_transport import OpenAIJsonTransport
    from agentd.reasoning.engine import DefaultReasoningEngine
    from tests.test_anthropic_transport import FakeMessagesClient, FakeResponse, FakeTextBlock
    from tests.test_openai_transport import FakeResponses, _completed, _delta

    ctrl, _, tid = env
    client = Client()
    monkeypatch.setattr("agentd.web.exa.ExaSearchClient", lambda: client)
    WebSearchRuleStore(tmp_path).add("Exa", "web_search")
    registry = ctrl._build_registry(thread_id=tid)
    definition = next(d for d in registry.definitions() if d.name == "web_search")
    steps = [
        {
            "type": "tool_calls",
            "thought": "search",
            "calls": [
                {
                    "id": "call1",
                    "tool": "web_search",
                    "args": {"query": "public docs", "max_results": 5},
                }
            ],
        },
        {"type": "answer", "thought": "done", "answer": "See https://example.com"},
    ]
    openai_replies = [{"action": {
        **s, **({"args": json.dumps(s["args"])} if "args" in s else {}),
                **({"calls": [{**c, "args": json.dumps(c["args"])} for c in s["calls"]]}
                   if "calls" in s else {}),
    }} for s in steps]
    openai = FakeResponses(*[[_delta(json.dumps(s)), _completed()] for s in openai_replies])
    anthropic = FakeMessagesClient([
        FakeResponse([FakeTextBlock("text", json.dumps(s))]) for s in steps])
    transports = [
        ("mock-openai-model", OpenAIJsonTransport(responses_client=openai, native_tools=False)),
        ("mock-anthropic-model", AnthropicJsonTransport(messages_client=anthropic)),
    ]
    for model, transport in transports:
        engine = DefaultReasoningEngine(model=model, transport=transport)
        loop = ControllerLoop(engine, registry, ctrl._broadcaster,
                              channel_id=f"chat:{tid}", phase_sm=ControllerPhaseSM())
        out = await loop.run({"goal": "Find public docs", "workspace_path": str(tmp_path)},
                             max_iters=4)
        assert any("Untrusted external search results from Exa" in str(m.get("content", ""))
                   for m in out.history or [])
    # Both real provider transports received the same native definition in the prompt.
    for prompt in (openai.calls[0]["instructions"], anthropic.calls[0]["system"]):
        catalog, _ = json.JSONDecoder().raw_decode(prompt.split("Available tools:\n", 1)[1])
        received = [d for d in catalog if d["name"] == "web_search"]
        assert received == [definition.model_dump()]
    assert client.calls == [("public docs", 5), ("public docs", 5)]
