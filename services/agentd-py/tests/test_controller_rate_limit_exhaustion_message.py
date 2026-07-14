"""Reproduces a live gap found 2026-07-13: a ControllerLoopExhausted failure (e.g.
sustained Ollama Cloud rate-limiting — 429 on every retry) that happens AFTER an
earlier successful turn (handle_message: explore + propose_mode) and DURING a
resolve_mode-entered EDIT phase never appeared as a persisted chat message live —
the transcript just stopped at the last successful breadcrumb with zero indication
the turn then failed. Live rate-limiting blocked further live reproduction, so this
drives the REAL handle_message -> resolve_mode -> _run_loop -> _finish path (no
mocking of _run_loop/_finish, unlike other resolve_mode tests) to check whether the
gap is a reachable code bug or something environmental (an orphaned/duplicate task,
an SSE-layer race) that a direct call sequence can't reproduce.
"""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.patch.engine import PatchEngine
from agentd.workspace.shadow import ShadowWorkspaceManager


class _FakeOrchestrator:
    """Just enough surface for TurnEditSession construction in _run_loop's EDIT
    branch — resolve_mode("edit") requires self._orchestrator is not None."""

    def __init__(self, tmp_path: Path) -> None:
        self._workspace_manager = ShadowWorkspaceManager(tmp_path / "shadows")
        self._patch_engine = PatchEngine()


class _SucceedsThenSustainedRateLimit:
    """1: explore tool_call (succeeds) — creates turn A's in-flight-pills row.
    2: propose_mode (succeeds) — turn A ends at the mode gate.
    3: (resolve_mode re-enters EDIT, a NEW turn_id) a run_command tool_call that
       PAUSES on a command-approval gate — the live scenario had several of these
       (mkdir/go test/xxd/cat/hexdump) resolved via separate HTTP requests before
       the eventual failure; reproducing that interleaving here in case it's the
       ingredient a straight-through tool_call sequence misses.
    4: another explore tool_call (succeeds) after the command resolves.
    5+: raises forever — simulates a rate limit that never clears within the
       loop's retry budget, exactly like the live 429 cascade."""

    def __init__(self) -> None:
        self.calls = 0

    async def create_controller_step(self, *, phase, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"type": "tool_call", "thought": "explore", "tool": "read_file",
                    "args": {"path": "f.py"}}
        if self.calls == 2:
            return {
                "type": "propose_mode", "thought": "propose",
                "plan_sketch": "edit f.py", "recommended": "edit", "reason": "r",
                "options": [{"mode": "edit", "label": "Edit inline now", "description": "d"}],
            }
        if self.calls == 3:
            return {"type": "tool_call", "thought": "check status", "tool": "run_command",
                    "args": {"command": "echo", "args": ["hi"]}}
        if self.calls == 4:
            return {"type": "tool_call", "thought": "explore again", "tool": "read_file",
                    "args": {"path": "f.py"}}
        raise RuntimeError("Ollama returned 429: session usage limit reached")


@pytest.mark.asyncio
async def test_exhaustion_after_resolve_mode_reentry_is_persisted(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), title="t")
    engine = _SucceedsThenSustainedRateLimit()
    ctrl = ChatController(
        workspace_path=str(ws),
        reasoning_engine=engine,
        thread_store=store, orchestrator=_FakeOrchestrator(tmp_path),
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK)

    # Turn A: explore + propose_mode -> parks at a mode gate, no failure yet.
    await ctrl.handle_message(thread.thread_id, "change f.py", channel_id="c1")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    assert reloaded.pending_controller_gate is not None
    assert reloaded.pending_controller_gate.kind == "mode"

    # Turn B: resolve_mode re-enters EDIT with a NEW turn_id — runs as a background
    # task so we can resolve the mid-turn command-approval gate from "outside" the
    # same coroutine, exactly like a separate HTTP request would live — then
    # sustained "429"s exhaust the loop.
    resolve_task = asyncio.create_task(
        ctrl.resolve_mode(thread.thread_id, "edit", channel_id="c1", goal="change f.py"))
    for _ in range(50):
        await asyncio.sleep(0)
        gate = store.get_thread(thread.thread_id).pending_controller_gate
        if gate is not None and gate.kind == "command":
            break
    else:
        raise AssertionError("command gate never appeared")
    assert await ctrl.resolve_command(thread.thread_id, CommandDecision(approve=True)) is True
    await resolve_task

    assert engine.calls == 4 + 4  # 1 initial + 3 retries per _MAX_MALFORMED, all raising

    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    last = reloaded.messages[-1]
    assert last.role == "agent", (
        f"no new agent message was persisted after the exhaustion — last message "
        f"is {reloaded.messages[-1].role!r}: {reloaded.messages[-1].content!r}"
    )
    assert "turn failed" in last.content.lower(), last.content
    assert "429" in last.content or "rate limit" in last.content.lower(), last.content
