"""Auto decision order for run_command in the chat controller (spec §6.2)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.approvals.auto_gate import AutoGate
from agentd.approvals.types import AutoDecision, JudgeRequest, PendingAction
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, CommandRule, ShellPolicy
from agentd.tools.command_rules import CommandRuleStore
from tests.gate_helpers import first_gate
from tests.test_controller_command_gate import _controller


def _setup(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    return _controller(tmp_path, store, ShellPolicy.AUTO), store, thread.thread_id


async def _gate(store, tid):
    for _ in range(50):
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            return gate
        await asyncio.sleep(0)
    raise AssertionError("no gate raised")


def _cb(ctrl, tid: str, command: str, args: list[str]):
    return ctrl._command_approval_cb(tid, f"chat:{tid}", command, args, "")


class _ScriptedJudge:
    def __init__(self, decision: AutoDecision) -> None:
        self.decision = decision
        self.actions: list[PendingAction] = []

    async def judge(self, request: JudgeRequest, action: PendingAction) -> AutoDecision:
        self.actions.append(action)
        return self.decision


class _BlockingJudge:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def judge(self, request: JudgeRequest, action: PendingAction) -> AutoDecision:
        self.entered.set()
        await self.release.wait()
        return AutoDecision("run", "you asked", "Auto: you asked")


@pytest.mark.asyncio
async def test_auto_denies_catastrophic_without_gate(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=None)
    out = await ctrl._command_approval_cb(tid, f"chat:{tid}", "rm", ["-rf", "/Users/u"], "")
    assert not out.approved and out.denied_by == "policy" and "home" in (out.reason or "")
    assert first_gate(store.get_thread(tid)) is None
    texts = [m.content for m in store.get_thread(tid).messages]
    assert any(t.startswith("✗ Auto blocked:") for t in texts)


@pytest.mark.asyncio
async def test_auto_runs_safe_read_only(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    out = await ctrl._command_approval_cb(tid, f"chat:{tid}", "git", ["status"], "")
    assert out.approved and out.note is None
    assert first_gate(store.get_thread(tid)) is None


@pytest.mark.asyncio
async def test_auto_without_judge_asks_for_test_runner(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    task = asyncio.create_task(ctrl._command_approval_cb(tid, f"chat:{tid}", "pytest", ["-x"], ""))
    gate = await _gate(store, tid)
    assert gate.payload["auto_reason"] == "Needs your approval in Auto"
    await ctrl.resolve_command(tid, CommandDecision(approve=True))
    assert (await task).approved


@pytest.mark.asyncio
async def test_auto_ignores_broad_rule_and_asks(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    CommandRuleStore(tmp_path).add(CommandRule(type="prefix", value="uv", added_at="t"))
    task = asyncio.create_task(_cb(ctrl, tid, "uv", ["run", "x.py"]))
    assert (await _gate(store, tid)).payload["auto_reason"]
    task.cancel()


@pytest.mark.asyncio
async def test_auto_narrow_rule_runs(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    CommandRuleStore(tmp_path).add(CommandRule(type="exact", value="uv run pytest", added_at="t"))
    assert (await _cb(ctrl, tid, "uv", ["run", "pytest"])).approved


@pytest.mark.asyncio
async def test_protected_write_asks_even_if_rule_matches(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    CommandRuleStore(tmp_path).add(CommandRule(type="exact", value="tee AGENTS.md", added_at="t"))
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"),
                               judge=_ScriptedJudge(AutoDecision("run", "ok", "Auto: ok")))
    task = asyncio.create_task(_cb(ctrl, tid, "tee", ["AGENTS.md"]))
    assert (await _gate(store, tid)).payload["auto_reason"] == "writes a protected file"
    task.cancel()


@pytest.mark.asyncio
async def test_judge_run_carries_note_and_summary(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    judge = _ScriptedJudge(AutoDecision("run", "you asked to push", "Auto: you asked to push"))
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=judge)
    out = await ctrl._command_approval_cb(tid, f"chat:{tid}", "git", ["push"], "")
    assert out.approved and out.note == "Auto: you asked to push"
    assert judge.actions[0].kind == "command" and "git push" in judge.actions[0].summary


@pytest.mark.asyncio
async def test_mode_switch_during_decision_uses_new_mode(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    judge = _BlockingJudge()
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=judge)
    task = asyncio.create_task(ctrl._command_approval_cb(tid, f"chat:{tid}", "git", ["push"], ""))
    await judge.entered.wait()
    ctrl._shell_policy = ShellPolicy.ASK
    judge.release.set()
    gate = await _gate(store, tid)
    assert "auto_reason" not in gate.payload  # the ordinary Ask card, not an Auto run
    task.cancel()


@pytest.mark.asyncio
async def test_dontask_child_ask_becomes_policy_denial(tmp_path):
    ctrl, store, tid = _setup(tmp_path)
    child = _child(tid, no_ask=True)
    out = await ctrl._child_command_approval_cb(child, "pytest", ["-x"], "")
    assert not out.approved and out.denied_by == "policy" and out.reason


def _child(tid: str, *, no_ask: bool):
    from types import SimpleNamespace
    context = SimpleNamespace(no_ask=no_ask, label="w", name="general-purpose")
    return SimpleNamespace(thread_id=tid, agent_id="a1", context=context, team_id=None,
                           broadcaster=None, transcript=None)
