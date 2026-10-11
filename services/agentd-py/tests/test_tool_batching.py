import asyncio
import json
import re

import pytest

from agentd.chat.controller import _explore_context_from_history
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.controller_prompts import controller_response_schema
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.providers.openai_native import from_native_input, native_actions, to_native_input
from agentd.tools.batching import (
    Call,
    ExecutionPool,
    Resource,
    resources_for,
    run_batch,
    validate_calls,
)
from agentd.tools.registry import ToolDefinition, ToolOutput
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

DEFS = [ToolDefinition(name="run_command", description="", parameters={"type": "object"})]


@pytest.mark.parametrize(
    "nodes",
    [
        [{"id": "a", "tool": "run_command", "args": {}, "depends_on": ["a"]}],
        [{"id": "a", "tool": "run_command", "args": {}, "after": ["missing"]}],
        [
            {"id": "a", "tool": "run_command", "args": {}, "after": ["b"]},
            {"id": "b", "tool": "run_command", "args": {}, "depends_on": ["a"]},
        ],
        [
            {"id": "a", "tool": "run_command", "args": {}},
            {"id": "a", "tool": "run_command", "args": {}},
        ],
        [{"id": "a", "tool": "hidden", "args": {}}],
    ],
)
def test_graph_validation_rejects_before_execution(nodes):
    with pytest.raises(ValueError):
        validate_calls(nodes, DEFS)


@pytest.mark.asyncio
async def test_parallel_commands_and_failure_dependency_diagnostics():
    calls = [
        Call("a", "run_command", {}),
        Call("b", "run_command", {}),
        Call("blocked", "run_command", {}, ("a",)),
        Call("diagnostics", "run_command", {}, after=("blocked",)),
    ]
    barrier = asyncio.Event()
    started = []
    states = []

    async def prepare(call):
        async def run():
            started.append(call.id)
            if len(started) == 2:
                barrier.set()
            if call.id in {"a", "b"}:
                await asyncio.wait_for(barrier.wait(), 1)
            return ToolOutput(call.id, call.id == "a")

        return run

    async def update(outcome):
        states.append((outcome.call.id, outcome.state))

    pool = ExecutionPool(2)
    results = await run_batch(
        calls, prepare=prepare, resources=lambda c: (), pool=pool, update=update
    )
    assert [o.state for o in results] == ["error", "success", "blocked", "success"]
    assert "blocked" not in started
    assert started.index("diagnostics") > started.index("a")
    assert pool.high_water == 2


@pytest.mark.asyncio
async def test_approval_wait_does_not_hold_worker_or_workspace_lease(tmp_path):
    gate, other_finished = asyncio.Event(), asyncio.Event()
    calls = [Call("waiting", "run_command", {}), Call("other", "run_command", {})]

    async def prepare(call):
        if call.id == "waiting":
            await gate.wait()
            return ToolOutput("Denied", True, status="denied")

        async def run():
            other_finished.set()
            return ToolOutput("done")

        return run

    async def update(outcome):
        pass

    pool = ExecutionPool(1)
    task = asyncio.create_task(
        run_batch(
            calls,
            prepare=prepare,
            resources=lambda c: (Resource(tmp_path, True),),
            pool=pool,
            update=update,
        )
    )
    await asyncio.wait_for(other_finished.wait(), 1)
    gate.set()
    results = await task
    assert [o.state for o in results] == ["denied", "success"]


@pytest.mark.asyncio
async def test_cancel_preserves_completed_result_and_closes_queued_members():
    ready = asyncio.Event()
    calls = [
        Call("done", "run_command", {}),
        Call("waiting", "run_command", {}, ("done",)),
        Call("queued", "run_command", {}, ("waiting",)),
    ]
    updates = {}

    async def prepare(call):
        async def run():
            if call.id == "waiting":
                ready.set()
                await asyncio.Future()
            return ToolOutput("completed")

        return run

    async def update(outcome):
        updates[outcome.call.id] = (outcome.state, outcome.output)

    task = asyncio.create_task(
        run_batch(
            calls, prepare=prepare, resources=lambda c: (), pool=ExecutionPool(), update=update
        )
    )
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert updates["done"][0] == "success"
    assert updates["waiting"][0] == updates["queued"][0] == "cancelled"


def test_command_resources_account_for_shared_environments(tmp_path):
    a = Call("a", "run_command", {"command": "pytest", "cwd": "backend"}, execution="parallel")
    b = Call(
        "b",
        "run_command",
        {"command": "npm", "args": ["install"], "cwd": "frontend"},
        execution="parallel",
    )
    ra, rb = (
        resources_for(a, tmp_path / "shadow", tmp_path / "real"),
        resources_for(b, tmp_path / "shadow", tmp_path / "real"),
    )
    assert Resource((tmp_path / "real/backend/.venv").resolve(), True) in ra
    assert Resource((tmp_path / "real").resolve(), True) in rb


@pytest.mark.asyncio
async def test_controller_batch_reads_one_model_step_and_durable_id_results(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text(name)
    nodes = [{"id": name, "tool": "read_file", "args": {"path": name}} for name in ("a.py", "b.py")]
    engine = ScriptedReasoningEngine(
        None,
        [],
        controller_step_responses=[
            {"type": "tool_calls", "thought": "both", "calls": nodes},
            {"type": "answer", "answer": "done"},
        ],
    )
    registry = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)]
    )
    bc = EventBroadcaster()
    queue = bc.subscribe("batch")
    loop = ControllerLoop(engine, registry, bc, channel_id="batch", phase_sm=ControllerPhaseSM())
    persisted = []
    history_updates = []
    iterations = []

    async def update(pills, thinking):
        persisted.append(pills)

    result = await loop.run(
        {"goal": "compare", "workspace_path": str(tmp_path)},
        max_iters=8,
        on_pills_update=update,
        history_update_cb=lambda history: history_updates.append(json.loads(json.dumps(history))),
        iteration_cb=lambda history: iterations.append(len(history)),
    )
    calls = [json.loads(h["content"]) for h in result.history if h["role"] == "assistant"]
    assert len(history_updates) > len(iterations)
    assert any(h.get("status") == "pending" for h in history_updates[0])
    assert calls[0]["type"] == "tool_calls"
    outputs = [h for h in result.history if h["role"] == "tool_result"]
    assert [h["id"] for h in outputs] == ["a.py", "b.py"]
    assert all(h["status"] == "success" for h in outputs)
    assert any(any(not pill["done"] for pill in pills) for pills in persisted)
    assert all(pill["done"] for pill in persisted[-1])
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert {e["payload"]["call_index"] for e in events if e["type"] == "tool_result"} == {0, 1}
    assert len(_explore_context_from_history(result.history)) == 2
    actions = native_actions(controller_response_schema(phase="ACTIVE", anyof=True), "controller")
    payload = {
        "workspace_path": str(tmp_path),
        "conversation_history": result.history,
        "goal": "compare",
    }
    native = to_native_input(payload, actions)
    restored = from_native_input(native, actions, head_keys=1)
    # JSON property order is not significant in persisted action content.
    for before, after in zip(
        payload["conversation_history"], restored["conversation_history"], strict=True
    ):
        if before["role"] == "assistant":
            assert json.loads(before["content"]) == json.loads(after["content"])
        else:
            assert before == after


def test_native_terminal_only_schema_can_replay_saved_batch(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")
    action = {
        "type": "tool_calls",
        "batch_id": "batch-old",
        "calls": [
            {
                "id": "a",
                "tool": "read_file",
                "args": {"path": "a.py"},
                "provider_call_id": "original-a",
            }
        ],
    }
    history = [
        {"role": "assistant", "content": json.dumps(action)},
        {
            "role": "tool_result",
            "tool": "read_file",
            "batch_id": "batch-old",
            "id": "a",
            "call_id": "batch-old_a",
            "provider_call_id": "original-a",
            "status": "success",
            "content": "a.py",
            "is_error": False,
        },
    ]
    actions = native_actions(
        controller_response_schema(phase="ACTIVE", anyof=True, allowed_types=["answer"]),
        "controller",
    )
    payload = {"workspace_path": "/ws", "conversation_history": history, "goal": "finish"}
    items = to_native_input(payload, actions)
    native_calls = [item for item in items if item.get("type") == "function_call"]
    assert len(native_calls) == 1 and native_calls[0]["name"] == "tool_calls"
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    assert len(outputs) == 1 and outputs[0]["call_id"] == native_calls[0]["call_id"]
    restored = from_native_input(items, actions, head_keys=1)
    assert json.loads(restored["conversation_history"][0]["content"]) == action
    assert restored["conversation_history"][1] == history[1]


def test_restart_closes_only_abandoned_members_without_replaying():
    from agentd.chat.batch_history import interrupted_history

    original = [
        {"role": "tool_result", "batch_id": "b", "status": "success", "content": "done"},
        {"role": "tool_result", "batch_id": "b", "status": "pending", "content": "waiting"},
    ]
    restored = interrupted_history(original)
    assert restored[0] == original[0]
    assert restored[1]["status"] == "interrupted" and restored[1]["is_error"]
    assert original[1]["status"] == "pending"


@pytest.mark.asyncio
async def test_dispatch_wait_does_not_deadlock_children_using_same_pool(tmp_path):
    pool = ExecutionPool(1)

    async def prepare(call):
        async def run():
            async with pool.lease((Resource(tmp_path, True),)):
                return ToolOutput("child finished")

        return run

    async def update(outcome):
        pass

    result = await asyncio.wait_for(
        run_batch(
            [Call("dispatch", "dispatch_agents", {})],
            prepare=prepare,
            resources=lambda c: (Resource(tmp_path, True),),
            pool=pool,
            update=update,
        ),
        1,
    )
    assert result[0].state == "success"


@pytest.mark.asyncio
async def test_insufficient_headroom_prevents_tool_side_effects():
    async def prepare(call):
        pytest.fail("tool must not be prepared or executed without result headroom")

    async def update(outcome):
        pass

    results = await run_batch(
        [Call("a", "run_command", {})],
        prepare=prepare,
        resources=lambda c: (),
        pool=ExecutionPool(),
        update=update,
        budget=0,
    )
    assert results[0].state == "blocked"
    assert not results[0].started


def test_duplicate_provider_ids_and_non_string_execution_are_rejected():
    nodes = [
        {"id": name, "tool": "run_command", "args": {}, "provider_call_id": "same"}
        for name in ("a", "b")
    ]
    with pytest.raises(ValueError, match="duplicate provider"):
        validate_calls(nodes, DEFS)
    with pytest.raises(ValueError, match="execution"):
        validate_calls([{"id": "a", "tool": "run_command", "args": {}, "execution": {}}], DEFS)


@pytest.mark.asyncio
async def test_command_guard_is_rechecked_after_preparation(tmp_path):
    from agentd.tools.registry import ToolRegistry

    denial = None
    registry = ToolRegistry(tmp_path, tmp_path, command_guard=lambda line: denial)
    prepared = await registry.prepare_command({"command": "echo", "args": ["must-not-run"]})
    denial = "Workspace ownership changed while awaiting execution"
    result = await prepared()
    assert result.is_error and result.output == denial


def test_shared_batch_examples_validate_and_follow_feature_flag(monkeypatch, tmp_path):
    from agentd.chat.controller_prompts import format_controller_system_prompt
    from agentd.tools.registry import ToolRegistry

    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "index-snapshot.json").write_text("{}")
    definitions = ToolRegistry(
        tmp_path, tmp_path, semantic_index=object()
    ).definitions("act")
    prompt = format_controller_system_prompt([], memory_enabled=False)
    examples = [json.loads(line) for line in prompt.splitlines()
                if line.startswith('{"type":"tool_calls"')]
    assert len(examples) == 5
    for example in examples:
        assert len(validate_calls(example["calls"], definitions)) >= 2
    assert examples[1]["calls"][1]["depends_on"] == ["install"]
    assert examples[1]["calls"][2]["after"] == ["build"]
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "0")
    assert "TOOL BATCHES" not in format_controller_system_prompt([], memory_enabled=False)


@pytest.mark.parametrize("phase", ["PLAN", "ACTIVE", "AGENT"])
def test_controller_has_only_array_tool_actions_with_flag_on_or_off(monkeypatch, phase):
    for enabled, maximum in [("0", 1), ("1", 8)]:
        monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", enabled)
        schema = controller_response_schema(phase=phase, anyof=True)
        branches = {b["properties"]["type"]["const"]: b for b in schema["anyOf"]}
        assert "tool_call" not in branches and "tool_calls" in branches
        calls = branches["tool_calls"]["properties"]["calls"]
        assert calls["minItems"] == 1 and calls["maxItems"] == maximum
        actions = native_actions(schema, "controller")
        assert "tool_call" not in actions.codecs and "tool_calls" in actions.codecs


def test_teaching_examples_cover_the_previous_tool_scenarios(monkeypatch, tmp_path):
    from agentd.chat.controller_prompts import format_controller_system_prompt
    from agentd.skills.models import SkillManifest

    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")
    names = ["write_todos", "dispatch_agents", "wait_agents", "message_agent", "create_team",
             "web_search", "web_fetch", "start_session"]
    prompt = format_controller_system_prompt(
        [{"name": name} for name in names],
        skills_catalog=[SkillManifest(name="debugging", description="Use for debugging.",
                                      body_path=tmp_path / "SKILL.md", dir=tmp_path)])
    assert '"type":"tool_call"' not in prompt and "type='tool_call'" not in prompt
    examples = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{"type":"tool_calls"', prompt):
        try:
            example, _ = decoder.raw_decode(prompt[match.start():])
        except json.JSONDecodeError:  # explicitly labelled invalid patch example
            continue
        examples.append(example)
    tools = {c["tool"] for e in examples for c in e["calls"]}
    assert {"search_code", "read_file", "read_skill", "web_search", "web_fetch",
            "dispatch_agents", "wait_agents", "message_agent", "create_team",
            "run_command"} <= tools
    assert any(len(e["calls"]) > 1 for e in examples)
    for example in examples:
        assert example["thought"] and example["calls"]
        assert len({c["id"] for c in example["calls"]}) == len(example["calls"])
        assert all(isinstance(c["args"], dict) for c in example["calls"])
