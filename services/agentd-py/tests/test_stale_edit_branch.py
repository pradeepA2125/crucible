"""The loop's "stale" accept branch + its durable record (spec §7.4)."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.storage import ChatThreadStore
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


@pytest.mark.asyncio
async def test_an_accepted_edit_that_went_stale_at_the_gate_is_refused(tmp_path: Path) -> None:
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
    steps = [
        _edit("x = 1", "x = 2"),
        {"type": "tool_call", "thought": "reread", "tool": "read_file",
         "args": {"path": "f.py"}},
        _edit("y = 9", "y = 10"),
        {"type": "submit_changes", "thought": "d", "summary": "ok"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        registry, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: session)
    records: list[tuple[str, str, bool]] = []
    decisions = 0

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        nonlocal decisions
        decisions += 1
        if decisions == 1:
            # A sibling promotes f.py while the first edit waits for the user.
            (real / "f.py").write_text("x = 1\ny = 9\n")
            log.note_promote("a1", "impl", "general-purpose", ["f.py"])
        return {"decision": "accept", "reason": ""}

    async def record(diff: list[DiffEntry], decision: str, reason: str, gated: bool) -> None:
        records.append((decision, reason, gated))

    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=8,
        turn_control=ChatTurnControl(auto_accept_edits=False),
        edit_decision_cb=decide, edit_record_cb=record)
    assert out.kind == "submit_changes"
    assert records == [("stale", "f.py changed since it was read (by impl)", True),
                       ("accept", "", True)]
    contents = [str(m.get("content", "")) for m in out.history or []]
    failed = next(c for c in contents if c.startswith("PATCH FAILED: `f.py`"))
    assert "was modified by agent `impl` (general-purpose)" in failed
    assert "read_file it again" in failed  # the STALE_READ guidance
    assert (real / "f.py").read_text() == "x = 1\ny = 10\n"


@pytest.mark.asyncio
async def test_the_stale_record_is_discarded_and_always_breadcrumbed(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    diff = [DiffEntry(path="f.py", additions=1, deletions=0, temp_path="/s/f.py",
                      unified_diff="@@\n+x = 2")]
    await ctrl._edit_record_cb(tid, f"chat:{tid}", diff, "stale",
                               "f.py changed since it was read (by impl)", False)
    thread = store.get_thread(tid)
    assert thread is not None
    card = next(m for m in thread.messages if m.type == "diff_card")
    assert card.metadata["resolved"] == "discarded"
    assert any(m.content == "✗ Not applied: f.py changed since it was read (by impl)"
               for m in thread.messages)
