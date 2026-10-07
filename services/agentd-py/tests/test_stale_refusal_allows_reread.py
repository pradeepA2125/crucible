"""A stale-edit refusal says "read_file it again"; the duplicate-call guard must not then
block that re-read (live: run 4, the main agent gave up on styles.css going round in
circles between the two guards)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.turn_control import ChatTurnControl
from agentd.domain.models import DiffEntry
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, WriteGuard
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


def _edit(search: str, replace: str) -> dict[str, object]:
    return {"type": "edit", "thought": "t", "patch_ops": [
        {"op": "search_replace", "file": "f.py", "search": search, "replace": replace,
         "reason": "r"}]}


def _read(path: str) -> dict[str, object]:
    return {"type": "tool_call", "thought": "read", "tool": "read_file",
            "args": {"path": path}}


def _setup(tmp_path: Path) -> tuple[Path, WorkspaceWriteLog, TurnEditSession,
                                    AggregatingToolRegistry]:
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    session = TurnEditSession(
        turn_id="t", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"), patch_engine=PatchEngine(),
        write_guard=WriteGuard(log, MAIN_AGENT_ID, "main", "main"))
    registry = AggregatingToolRegistry([BuiltinToolSource(
        shadow_root=real, real_workspace_path=real,
        read_observer=log.read_observer(real, MAIN_AGENT_ID))])
    return real, log, session, registry


def _contents(history: list[dict[str, object]] | None) -> list[str]:
    return [str(m.get("content", "")) for m in history or []]


@pytest.mark.asyncio
async def test_a_refusal_at_apply_lets_the_same_read_run_again(tmp_path: Path) -> None:
    real, log, session, registry = _setup(tmp_path)
    # "./f.py": the refusal names the canonical "f.py"; the read's own spelling differs.
    steps = [_read("./f.py"), _edit("y = 9", "y = 10"), _read("./f.py"),
             _edit("y = 9", "y = 10"),
             {"type": "submit_changes", "thought": "d", "summary": "ok"}]
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=steps)
    loop = ControllerLoop(
        engine, registry, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: session)
    original = registry.execute

    async def execute(tool, args):  # type: ignore[no-untyped-def]
        out = await original(tool, args)
        if tool == "read_file" and not (real / "f.py").read_text().startswith("x = 1\ny"):
            # A sibling promotes f.py right after this agent's first read.
            (real / "f.py").write_text("x = 1\ny = 9\n")
            log.note_promote("a1", "builder", "general-purpose", ["f.py"])
        return out

    registry.execute = execute  # type: ignore[method-assign]
    out = await loop.run({"goal": "g", "workspace_path": str(real)}, max_iters=10,
                         turn_control=ChatTurnControl(auto_accept_edits=True))
    contents = _contents(out.history)
    assert any(c.startswith("PATCH FAILED") and "builder" in c for c in contents)
    assert not any(c.startswith("DUPLICATE BLOCKED") for c in contents)
    assert out.kind == "submit_changes"
    assert (real / "f.py").read_text() == "x = 1\ny = 10\n"


@pytest.mark.asyncio
async def test_a_refusal_at_accept_lets_the_same_read_run_again(tmp_path: Path) -> None:
    real, log, session, registry = _setup(tmp_path)
    steps = [_read("f.py"), _edit("x = 1", "x = 2"), _read("f.py"), _edit("y = 9", "y = 10"),
             {"type": "submit_changes", "thought": "d", "summary": "ok"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        registry, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: session)
    decisions = 0

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        nonlocal decisions
        decisions += 1
        if decisions == 1:
            (real / "f.py").write_text("x = 1\ny = 9\n")
            log.note_promote("a1", "builder", "general-purpose", ["f.py"])
        return {"decision": "accept", "reason": ""}

    out = await loop.run({"goal": "g", "workspace_path": str(real)}, max_iters=10,
                         turn_control=ChatTurnControl(auto_accept_edits=False),
                         edit_decision_cb=decide)
    contents = _contents(out.history)
    assert not any(c.startswith("DUPLICATE BLOCKED") for c in contents)
    assert out.kind == "submit_changes"
    assert (real / "f.py").read_text() == "x = 1\ny = 10\n"


@pytest.mark.asyncio
async def test_other_repeated_reads_stay_blocked(tmp_path: Path) -> None:
    real, _log, session, registry = _setup(tmp_path)
    (real / "g.py").write_text("z = 0\n")
    steps = [_read("g.py"), _read("g.py"),
             {"type": "answer", "thought": "d", "answer": "ok"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        registry, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: session)
    out = await loop.run({"goal": "g", "workspace_path": str(real)}, max_iters=10,
                         turn_control=ChatTurnControl(auto_accept_edits=True))
    assert any(c.startswith("DUPLICATE BLOCKED") for c in _contents(out.history))
