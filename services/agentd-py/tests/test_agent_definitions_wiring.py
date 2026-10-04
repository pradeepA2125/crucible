"""Agent definitions reach dispatch and shape the child (spec §5.2, §5.3, §9)."""
import logging
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_dispatch_integration import DONE, WAIT, _controller, _dispatch

_REPORT = [{"type": "report", "thought": "t", "summary": "Read it."}]


def _agent_file(ws: Path, name: str, front: str) -> None:
    path = ws / ".crucible" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {name} agent\n{front}---\nPersona.\n",
                    encoding="utf-8")


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ChatThreadStore, str]:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # keep ~/.claude/agents out of it
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    return ws, store, store.create_thread(str(ws), title="t").thread_id


def test_discovered_agents_reach_the_tool_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws, store, _ = _setup(tmp_path, monkeypatch)
    ctrl = _controller(ws, tmp_path, store, ScriptedReasoningEngine(None, []))
    before = ctrl._dispatch_source("t", "u", None).definitions()[0]
    assert "reviewer" not in before.description
    _agent_file(ws, "reviewer", "permissionMode: plan\n")   # added after construction
    after = ctrl._dispatch_source("t", "u", None).definitions()[0]
    # A workspace file is untrusted until the user trusts it: listed, description framed.
    assert "- reviewer:" in after.description and "reviewer agent" in after.description
    assert "(untrusted)" in after.description
    enum = after.parameters["properties"]["agents"]["items"]["properties"]["agent"]["enum"]
    assert enum == ["explore", "general-purpose", "reviewer"]


@pytest.mark.asyncio
async def test_definition_fields_shape_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    ws, store, tid = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("CRUCIBLE_SKILLS_ENABLED", "1")
    skill = ws / ".crucible" / "skills" / "tdd" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: tdd\ndescription: test first\n---\nWrite the test first.\n",
                     encoding="utf-8")
    _agent_file(ws, "reader", "tools: Read, Grep, Bash\ndisallowedTools: Bash\nskills: tdd, nope\n")
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("reader", "r1", "Read a file")), WAIT, DONE], agent_scripts={"r1": _REPORT})
    ctrl = _controller(ws, tmp_path, store, engine)

    with caplog.at_level(logging.WARNING):
        await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    [row] = store.list_agents(tid)
    assert (row.name, row.status) == ("reader", "completed")
    assert ctrl._subagents is not None
    handle = ctrl._subagents.registry.get(row.agent_id)
    assert handle is not None
    # tools ∩ (available − disallowed): Bash is gone, read_skill/write_todos weren't listed.
    assert {d.name for d in handle.loop._registry.definitions()} == {"read_file", "search_code"}
    # No Edit/Write in `tools` → no edit action (spec §5.3).
    assert handle.context.allowed_types == ("tool_call", "progress", "report")
    assert set(handle.loop._active_skills) == {"tdd"}
    assert "Write the test first." in handle.loop._active_skills["tdd"]
    assert "unknown skill 'nope'" in caplog.text


@pytest.mark.asyncio
async def test_skills_need_the_skills_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    ws, store, tid = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv("CRUCIBLE_SKILLS_ENABLED", raising=False)
    _agent_file(ws, "reader", "skills: tdd\n")
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("reader", "r1", "Read a file")), WAIT, DONE], agent_scripts={"r1": _REPORT})
    ctrl = _controller(ws, tmp_path, store, engine)

    with caplog.at_level(logging.WARNING):
        await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    [row] = store.list_agents(tid)
    assert ctrl._subagents is not None
    handle = ctrl._subagents.registry.get(row.agent_id)
    assert handle is not None and handle.loop._active_skills == {}
    assert "CRUCIBLE_SKILLS_ENABLED is off" in caplog.text
