import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.tools.sources import AggregatingToolRegistry


class _ScriptedReasoning:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    async def create_controller_step(self, **kwargs):
        resp = self._responses[self._i]
        self._i += 1
        return resp


def _edit_sm() -> ControllerPhaseSM:
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    return sm


def _wt(items):
    return {"type": "tool_call", "thought": "todos", "tool": "write_todos",
            "args": {"items": items}}


def _loop(ledger, reasoning):
    return ControllerLoop(
        reasoning, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c1", phase_sm=_edit_sm(), todo_ledger=ledger)


@pytest.mark.asyncio
async def test_submit_blocked_until_ledger_clear():
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "pending"}, {"title": "B", "status": "pending"}]),
        {"type": "submit_changes", "thought": "?", "summary": "early"},     # BLOCKED (2 pending)
        _wt([{"title": "A", "status": "done"}, {"title": "B", "status": "done"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "all done"},  # passes
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert outcome.kind == "submit_changes" and outcome.text == "all done"
    assert any("BLOCKED" in str(m.get("content", "")) for m in (outcome.history or []))


@pytest.mark.asyncio
async def test_blocked_item_does_not_deadlock_submit():
    # One done, one blocked -> nothing pending -> submit must pass (blocked != pending).
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "done"},
             {"title": "B", "status": "blocked", "note": "needs API key"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "A done, B blocked"},
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert outcome.kind == "submit_changes" and outcome.text == "A done, B blocked"


@pytest.mark.asyncio
async def test_gate_block_not_counted_as_malformed():
    ledger = TodoLedger()
    sub = {"type": "submit_changes", "thought": "?", "summary": "x"}
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "pending"}]),
        sub, sub, sub, sub,   # 4 blocked in a row would trip _MAX_MALFORMED (3) if counted
        _wt([{"title": "A", "status": "done"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "done"},
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=20)
    assert outcome.kind == "submit_changes" and outcome.text == "done"


@pytest.mark.asyncio
async def test_submit_passes_with_no_ledger():
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        {"type": "submit_changes", "thought": "ok", "summary": "nothing pending"}]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=5)
    assert outcome.kind == "submit_changes" and outcome.text == "nothing pending"
