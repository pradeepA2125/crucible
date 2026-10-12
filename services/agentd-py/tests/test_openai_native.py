"""Native function-call adapter for the ChatGPT plan route: actions as function tools,
history as native items — and a lossless round trip back to the engine's payload."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentd.chat.controller_prompts import controller_response_schema
from agentd.providers.openai_native import (
    NAMESPACE,
    NO_RESULT,
    from_native_input,
    native_actions,
    to_native_input,
)

UNION = controller_response_schema(phase="ACTIVE", anyof=True)
FLAT = {"type": "object", "properties": {"headline": {"type": "string"},
                                          "points": {"type": "array", "items": {"type": "string"}}},
        "required": ["headline", "points"]}


def _action(**fields: object) -> dict[str, object]:
    return {"role": "assistant", "content": json.dumps(fields)}


def _result(tool: str, content: str) -> dict[str, object]:
    return {"role": "tool_result", "tool": tool, "content": content}


# Every history shape the three loops write (see controller_loop.py's history writers).
HISTORY: list[dict[str, object]] = [
    {"role": "user", "content": "design a tetris game, use a team"},
    {"role": "user", "content": "[MEMORY] Summary of earlier conversation that was compacted:\n…"},
    _action(type="tool_call", tool="write_todos",
            args={"items": [{"title": "a", "status": "pending"}]}),
    _result("write_todos", "Todo list updated: 1 item"),
    _action(type="tool_call", tool="read_file", args={"path": "src/calc.py"}),
    _result("read_file", "def add(a, b): ..."),
    _action(type="edit",
            patch_ops=[{"op": "create_file", "file": "a.py", "content": "x", "reason": "r"}]),
    _result("edit", "applied+promoted: a.py"),
    _result("retrieval_refresh", "a.py changed"),          # a second result, no new action
    _action(type="answer", answer="done", note="with a note"),
    _result("", "Open todos block answer — finish them first."),   # a correction
    {"role": "assistant", "content": "{}"},                 # the loop's failed-call placeholder
    _result("", "Your last reply was not valid JSON."),
    _action(type="clarify", question="Which file?", options=["a", "b"]),  # no result follows
    {"role": "user", "content": "New message:\nthe second one"},
    _result("read_skill", "[auto-loaded] skill body is active"),  # synthetic, no action
]

PAYLOAD = {
    "workspace_path": "/ws",
    "retrieval_seed": {"files": ["src/calc.py"]},
    "conversation_history": HISTORY,
    "recalled_memories": [],
    "goal": "design a tetris game",
    "instruction": "Take the next step.",
    "budget_status": {"iteration": 7},
}


# ---------------------------------------------------------------- tools


def test_a_union_becomes_one_function_per_action_in_a_namespace() -> None:
    actions = native_actions(UNION, "controller_step_response")
    assert actions.tool_choice == "auto"
    [namespace] = actions.tools
    assert namespace["type"] == "namespace" and namespace["name"] == NAMESPACE
    names = {f["name"] for f in namespace["tools"]}
    assert {"tool_calls", "answer", "edit", "submit_changes"} <= names
    assert "tool_call" not in names
    for fn in namespace["tools"]:
        assert fn["strict"] is True and "type" not in fn["parameters"]["properties"]
        branch = next(b for b in UNION["anyOf"]
                      if b["properties"]["type"]["const"] == fn["name"])
        assert fn["description"] == branch["description"]
        assert "description" not in fn["parameters"]


def test_a_flat_schema_becomes_one_forced_function() -> None:
    actions = native_actions(FLAT, "task narrative!")
    [fn] = actions.tools
    assert fn["name"] == "task_narrative_"
    assert actions.tool_choice == {"type": "function", "name": "task_narrative_"}


def test_native_call_does_not_describe_workspace_tools_as_mandatory() -> None:
    """Under tool_choice "required" Luna read code it already had "because a tool call is
    required before yielding" (replay of a captured step, 2026-10-12: 10/16 answered vs
    16/16 under "auto"). Neither the descriptions nor tool_choice may say a call is owed."""
    actions = native_actions(UNION, "controller_step_response")
    functions = {fn["name"]: fn for fn in actions.tools[0]["tools"]}
    assert "Finish the user turn" in functions["answer"]["description"]
    assert "Use directly" in functions["answer"]["description"]
    assert "only when their results are needed" in functions["tool_calls"]["description"]
    assert actions.tool_choice == "auto"


def test_native_action_descriptions_preserve_schema_specific_guidance() -> None:
    schema = controller_response_schema(phase="ACTIVE", anyof=True)
    for branch in schema["anyOf"]:
        if branch["properties"]["type"]["const"] == "answer":
            branch["description"] = "Answer this task with the requested format."
    actions = native_actions(schema, "controller_step_response")
    answer = next(fn for fn in actions.tools[0]["tools"] if fn["name"] == "answer")
    assert answer["description"] == "Answer this task with the requested format."


def test_calls_decode_to_the_engines_action_dict() -> None:
    actions = native_actions(UNION, "controller_step_response")
    assert actions.decode_call("tool_calls", json.dumps(
        {"thought": "t", "calls": [{"id": "read", "tool": "read_file",
                                   "args": '{"path": "a.py"}'}]})) == {
        "type": "tool_calls", "thought": "t", "calls": [
            {"id": "read", "tool": "read_file", "args": {"path": "a.py"}}]}
    with pytest.raises(RuntimeError, match="unknown function"):
        actions.decode_call("tool_call", '{}')
    flat = native_actions(FLAT, "n")
    assert flat.decode_call("n", '{"headline": "h", "points": ["p"]}') == {
        "headline": "h", "points": ["p"]}


def test_an_unknown_function_is_a_correctable_error() -> None:
    with pytest.raises(RuntimeError, match="no_such_action"):
        native_actions(UNION, "c").decode_call("no_such_action", "{}")


# ---------------------------------------------------------------- history


def test_history_becomes_native_items_in_order() -> None:
    actions = native_actions(UNION, "c")
    items = to_native_input(PAYLOAD, actions)
    kinds = [i.get("type", i.get("role")) for i in items]
    assert kinds[0] == "user" and json.loads(items[0]["content"]) == {
        "workspace_path": "/ws", "retrieval_seed": {"files": ["src/calc.py"]}}
    assert json.loads(items[-1]["content"])["goal"] == "design a tetris game"
    calls = [i for i in items if i.get("type") == "function_call"]
    assert [c["name"] for c in calls] == ["tool_call", "tool_call", "edit", "answer", "clarify"]
    assert all(c["namespace"] == NAMESPACE for c in calls)
    # Every call has exactly one output — the API rejects a call without one.
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert sorted(o["call_id"] for o in outputs) == sorted(c["call_id"] for c in calls)
    clarify_output = next(o for o in outputs if o["call_id"] == calls[-1]["call_id"])
    assert clarify_output["output"] == NO_RESULT
    # Free-form args are replayed in the function's own (strict) shape.
    assert json.loads(calls[1]["arguments"])["args"] == '{"path": "src/calc.py"}'


def test_the_round_trip_loses_nothing() -> None:
    actions = native_actions(UNION, "c")
    back = from_native_input(to_native_input(PAYLOAD, actions), actions, head_keys=2)
    assert _canonical(back) == _canonical(PAYLOAD)


def test_a_payload_without_history_is_one_message_as_before() -> None:
    actions = native_actions(FLAT, "n")
    payload = {"goal": "summarize", "events": [1, 2]}
    items = to_native_input(payload, actions)
    assert items == [{"role": "user", "content": json.dumps(payload)}]
    assert from_native_input(items, actions, head_keys=0) == payload


def test_call_ids_are_stable_across_calls() -> None:
    # The next request must start with this request's exact items for the prompt cache.
    actions = native_actions(UNION, "c")
    short = dict(PAYLOAD, conversation_history=HISTORY[:6])
    a, b = to_native_input(short, actions), to_native_input(PAYLOAD, actions)
    assert a[:-1] == b[:len(a) - 1]


def _canonical(payload: dict[str, object]) -> dict[str, object]:
    """Same information: assistant actions compared as parsed JSON (key order aside)."""
    out = dict(payload)
    history = []
    for entry in payload.get("conversation_history", []):  # type: ignore[union-attr]
        entry = dict(entry)
        if entry.get("role") == "assistant":
            try:
                entry["content"] = json.loads(str(entry["content"]))
            except json.JSONDecodeError:
                pass
        history.append(entry)
    out["conversation_history"] = history
    return out


_FIXTURES = sorted((Path(__file__).parent / "fixtures" / "native_payloads").glob("*.json"))


@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.stem)
def test_real_controller_payloads_round_trip(path: Path) -> None:
    # Real payloads from the 2026-10-07 ChatGPT plan smoke (looping team turn, edits
    # with retrieval refreshes, cross-turn messages), each with the schema it was sent.
    fixture = json.loads(path.read_text())
    actions = native_actions(fixture["schema"], "controller_step_response")
    payload = fixture["user_payload"]
    keys = list(payload)
    head_keys = keys.index("conversation_history") if "conversation_history" in keys else 0
    items = to_native_input(payload, actions)
    assert _canonical(from_native_input(items, actions, head_keys=head_keys)) == _canonical(payload)
    calls = [i for i in items if i.get("type") == "function_call"]
    outputs = {i["call_id"] for i in items if i.get("type") == "function_call_output"}
    assert calls and {c["call_id"] for c in calls} == outputs


def test_batch_envelope_replays_all_member_results_and_following_action(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")
    actions = native_actions(controller_response_schema(phase="ACTIVE", anyof=True), "controller")
    batch = {"type": "tool_calls", "thought": "build then inspect", "batch_id": "saved",
             "provider_call_id": "provider-envelope", "calls": [
                 {"id": "build", "tool": "run_command", "args": {"command": "npm run build"}},
                 {"id": "log", "tool": "read_file", "args": {"path": "build.log"},
                  "after": ["build"]}]}
    results = [dict(role="tool_result", tool="run_command", batch_id="saved", id="build",
                    call_id="saved_build", status="error", is_error=True, content="failed"),
               dict(role="tool_result", tool="read_file", batch_id="saved", id="log",
                    call_id="saved_log", status="success", is_error=False, content="details")]
    history = [_action(**batch), *results,
               _action(type="answer", thought="done", answer="build failed")]
    payload = {"workspace_path": "/ws", "conversation_history": history, "goal": "build"}
    items = to_native_input(payload, actions)
    calls = [i for i in items if i.get("type") == "function_call"]
    assert [i["name"] for i in calls] == ["tool_calls", "answer"]
    assert calls[0]["call_id"] == "provider-envelope"
    decoded = actions.decode_call("tool_calls", calls[0]["arguments"])
    assert decoded["calls"][1]["after"] == ["build"]
    output = next(i for i in items if i.get("type") == "function_call_output")
    assert output["call_id"] == "provider-envelope"
    assert json.loads(output["output"].split("\n", 1)[1]) == results
    assert from_native_input(items, actions, head_keys=1) == payload
