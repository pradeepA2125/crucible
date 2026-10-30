"""Leaving the sandbox or reaching a new host: who approves, and what is remembered."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentd.approvals.auto_gate import AutoGate
from agentd.approvals.types import AutoDecision
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import (
    ApprovalOutcome,
    CommandDecision,
    CommandRule,
    Escalation,
    ShellPolicy,
)
from agentd.sandbox.runtime import SandboxRuntime
from agentd.tools.command_rules import CommandRuleStore
from agentd.tools.registry import ToolRegistry
from tests.test_auto_gate_commands import _gate, _ScriptedJudge
from tests.test_controller_command_gate import _controller

OUTSIDE = Escalation(kind="outside_sandbox", reason="needs ~/.npmrc")
HOSTS = Escalation(kind="allow_hosts", hosts=["db.example.com"], reason="query staging db")


def _setup(tmp_path: Path, policy: ShellPolicy):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, policy)
    ctrl._sandbox = SandboxRuntime(tmp_path)
    return ctrl, store, tid


def _esc(ctrl, tid: str, escalation: Escalation, command: str = "npm", args=("publish",)):
    return ctrl._escalation_approval_cb(tid, f"chat:{tid}", escalation, command, list(args), "")


@pytest.mark.asyncio
async def test_full_access_escalation_runs_without_asking(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.ALLOW_ALL)
    assert (await _esc(ctrl, tid, OUTSIDE)).approved
    assert store.get_thread(tid).pending_controller_gates == []


@pytest.mark.asyncio
async def test_ask_mode_outside_always_cards_even_with_rule(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.ASK)
    CommandRuleStore(tmp_path).add(CommandRule(type="binary", value="npm", added_at="t"))
    task = asyncio.create_task(_esc(ctrl, tid, OUTSIDE))
    gate = await _gate(store, tid)
    assert gate.payload["escalation"] == {"kind": "outside_sandbox", "hosts": [],
                                          "reason": "needs ~/.npmrc"}
    await ctrl.resolve_command(tid, CommandDecision(approve=True, remember=True), gate.gate_id)
    assert (await task).approved
    # "Remember" never stores a command rule or anything else for leaving the sandbox.
    assert len(CommandRuleStore(tmp_path).load()) == 1
    assert ctrl._sandbox.store.hosts() == []


@pytest.mark.asyncio
async def test_allow_hosts_remember_writes_store(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.ASK)
    task = asyncio.create_task(_esc(ctrl, tid, HOSTS, "psql", ["-h", "db.example.com"]))
    gate = await _gate(store, tid)
    await ctrl.resolve_command(tid, CommandDecision(approve=True, remember=True), gate.gate_id)
    assert (await task).approved
    assert ctrl._sandbox.store.hosts() == ["db.example.com"]
    assert CommandRuleStore(tmp_path).load() == []


@pytest.mark.asyncio
async def test_rejected_escalation_denied(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.ASK)
    task = asyncio.create_task(_esc(ctrl, tid, OUTSIDE))
    gate = await _gate(store, tid)
    await ctrl.resolve_command(tid, CommandDecision(approve=False), gate.gate_id)
    outcome = await task
    assert not outcome.approved and outcome.denied_by == "user"


@pytest.mark.asyncio
async def test_auto_escalation_goes_to_judge(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.AUTO)
    judge = _ScriptedJudge(AutoDecision("run", "you asked to publish", "Auto: you asked"))
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=judge)
    outcome = await _esc(ctrl, tid, OUTSIDE)
    assert outcome.approved and outcome.note == "Auto: you asked"
    assert judge.actions[0].kind == "sandbox_escalation"
    assert "outside the sandbox" in judge.actions[0].summary
    assert judge.actions[0].agent_reason == "needs ~/.npmrc"


@pytest.mark.asyncio
async def test_auto_judge_host_approval_not_remembered(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.AUTO)
    judge = _ScriptedJudge(AutoDecision("run", "ok", "Auto: ok"))
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=judge)
    assert (await _esc(ctrl, tid, HOSTS)).approved
    assert ctrl._sandbox.store.hosts() == []


@pytest.mark.asyncio
async def test_auto_escalation_deny_list_still_denies(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.AUTO)
    judge = _ScriptedJudge(AutoDecision("run", "ok", "Auto: ok"))
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=judge)
    outcome = await _esc(ctrl, tid, OUTSIDE, "rm", ["-rf", "/"])
    assert not outcome.approved and outcome.denied_by == "policy" and judge.actions == []


@pytest.mark.asyncio
async def test_dontask_child_denied_outside_auto(tmp_path):
    ctrl, store, tid = _setup(tmp_path, ShellPolicy.ASK)
    ctrl._team_tool_refusal = lambda *_a: None  # type: ignore[method-assign]
    child = SimpleNamespace(thread_id=tid, agent_id="a1", team_id=None, broadcaster=None,
                            transcript=None, context=SimpleNamespace(
                                no_ask=True, label="w", name="x", permission="dontAsk"))
    outcome = await ctrl._child_escalation_approval_cb(child, OUTSIDE, "npm", ["publish"], "")
    assert not outcome.approved and outcome.denied_by == "policy"
    assert "leave the sandbox" in (outcome.reason or "")


async def _allow(*_a, **_k):
    return ApprovalOutcome.allow()


@pytest.fixture
def probe():
    path = Path.home() / f".crucible-sandbox-probe-{uuid.uuid4().hex}"
    yield path
    path.unlink(missing_ok=True)


darwin = pytest.mark.skipif(sys.platform != "darwin", reason="spawns Seatbelt processes")


@darwin
@pytest.mark.asyncio
async def test_registry_outside_sandbox_runs_unwrapped(tmp_path, probe):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    seen = []

    async def approve(escalation, command, args, cwd):
        seen.append(escalation)
        return ApprovalOutcome.allow()

    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime,
                            command_approval_callback=_allow,
                            escalation_approval_callback=approve)
    out = await registry.execute("run_command", {
        "command": "touch", "args": [str(probe)], "outside_sandbox": True,
        "reason": "write a probe"})
    assert not out.is_error and out.outside_sandbox and not out.sandboxed
    assert probe.exists() and seen[0].kind == "outside_sandbox"


@darwin
@pytest.mark.asyncio
async def test_rejected_escalation_does_not_run(tmp_path, probe):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()

    async def reject(*_a):
        return ApprovalOutcome.deny("user")

    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime,
                            command_approval_callback=_allow,
                            escalation_approval_callback=reject)
    out = await registry.execute("run_command", {
        "command": "touch", "args": [str(probe)], "outside_sandbox": True})
    assert out.is_error and not probe.exists()


@darwin
@pytest.mark.asyncio
async def test_allowed_host_reaches_proxy_for_that_command_only(tmp_path):
    runtime = SandboxRuntime(tmp_path)
    await runtime.start()
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime,
                            command_approval_callback=_allow,
                            escalation_approval_callback=_allow)
    leases = []
    original = runtime.proxy.lease

    def spy(*args, **kwargs):
        lease = original(*args, **kwargs)
        leases.append(lease)
        return lease

    runtime.proxy.lease = spy  # type: ignore[method-assign]
    await registry.execute("run_command", {"command": "true", "allow_hosts": ["db.example.com"],
                                           "reason": "db"})
    await registry.execute("run_command", {"command": "true"})
    assert leases[0].extra_hosts == frozenset({"db.example.com"})
    assert leases[1].extra_hosts == frozenset()
    await runtime.close()


@pytest.mark.asyncio
async def test_arguments_ignored_when_sandbox_off(tmp_path):
    runtime = SandboxRuntime(tmp_path)
    runtime.enabled = False
    escalations = []

    async def approve(escalation, *_a):
        escalations.append(escalation)
        return ApprovalOutcome.allow()

    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime,
                            command_approval_callback=_allow,
                            escalation_approval_callback=approve)
    out = await registry.execute("run_command", {"command": "true", "outside_sandbox": True})
    assert not out.is_error and escalations == [] and not out.outside_sandbox
    names = {p for d in registry.definitions() if d.name == "run_command"
             for p in d.parameters["properties"]}
    assert "outside_sandbox" not in names


@darwin
@pytest.mark.asyncio
async def test_schema_offers_escalation_when_active(tmp_path):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime)
    props = next(d for d in registry.definitions() if d.name == "run_command").parameters[
        "properties"]
    assert {"outside_sandbox", "allow_hosts", "reason"} <= set(props)


@pytest.mark.asyncio
async def test_escalation_with_no_one_to_approve_it_is_refused(tmp_path):
    # A registry wired without the callback must not treat "no gate" as "allowed".
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime,
                            command_approval_callback=_allow)
    if not runtime.active():
        pytest.skip("no sandbox here")
    out = await registry.execute("run_command", {
        "command": "touch", "args": ["outside.txt"], "outside_sandbox": True, "reason": "r"})
    assert out.is_error and not out.outside_sandbox
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.asyncio
async def test_a_bad_host_is_refused_before_anyone_is_asked(tmp_path):
    asked: list[object] = []

    async def approve(escalation, command, args, cwd):
        asked.append(escalation)
        return ApprovalOutcome.allow()

    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    if not runtime.active():
        pytest.skip("no sandbox here")
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime,
                            command_approval_callback=_allow,
                            escalation_approval_callback=approve)
    out = await registry.execute("run_command", {
        "command": "curl", "args": ["x"], "allow_hosts": ["https://x.example/a"], "reason": "r"})
    assert out.is_error and "https://x.example/a" in out.output
    assert asked == []
