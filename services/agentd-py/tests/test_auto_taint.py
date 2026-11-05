"""Prompt-injection Phase 3a: once a turn has read untrusted content, Auto asks the user
before anything that sends a request to a destination the agent picks."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.approvals.auto_gate import TAINTED_REASON, AutoGate
from agentd.approvals.auto_rules import reaches_remote
from agentd.approvals.command_parse import parse_command
from agentd.approvals.types import AutoDecision, JudgeRequest, PendingAction
from agentd.domain.models import CommandRule, Escalation
from agentd.tools.command_rules import CommandRuleStore


class _Judge:
    def __init__(self) -> None:
        self.actions: list[PendingAction] = []

    async def judge(self, request: JudgeRequest, action: PendingAction) -> AutoDecision:
        self.actions.append(action)
        return AutoDecision("run", "", "judge said run")


@pytest.fixture(autouse=True)
def _defense_on(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")


def _gate(tmp_path: Path) -> tuple[AutoGate, _Judge]:
    judge = _Judge()
    return AutoGate(tmp_path, tmp_path / "home", judge=judge), judge


def _req(tmp_path: Path, tainted: bool) -> JudgeRequest:
    return JudgeRequest(thread_id="t", agent_id="main", workspace=tmp_path, tainted=tainted)


@pytest.mark.parametrize("line", [
    "curl https://example.com/x",
    "wget -q https://example.com/x",
    "http GET example.com",
    "nc example.com 80",
    "ssh host.example uptime",
    "scp file host.example:/tmp",
    "git clone https://github.com/a/b",
    "git push origin main",
])
def test_reaches_remote(line, tmp_path):
    words = line.split()
    assert reaches_remote(parse_command(words[0], words[1:], tmp_path))


@pytest.mark.parametrize("line", ["python -m pytest tests", "ls -la", "git status", "npm test"])
def test_local_commands_do_not_reach_remote(line, tmp_path):
    words = line.split()
    assert not reaches_remote(parse_command(words[0], words[1:], tmp_path))


@pytest.mark.asyncio
async def test_tainted_sandboxed_network_command_asks_without_the_judge(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_command("curl", ["https://x.example/ping"],
                                         _req(tmp_path, True), sandboxed=True)
    assert decision == AutoDecision("ask", TAINTED_REASON, None)
    assert judge.actions == []


@pytest.mark.asyncio
async def test_untainted_sandboxed_network_command_still_runs(tmp_path):
    gate, _ = _gate(tmp_path)
    decision = await gate.decide_command("curl", ["https://x.example/ping"],
                                         _req(tmp_path, False), sandboxed=True)
    assert decision.kind == "run"


@pytest.mark.asyncio
async def test_tainted_local_command_still_runs_in_the_sandbox(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_command("python", ["-m", "pytest", "tests"],
                                         _req(tmp_path, True), sandboxed=True)
    assert decision.kind == "run" and judge.actions == []


@pytest.mark.asyncio
async def test_tainted_outward_command_asks_without_the_judge(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_command("git", ["push", "origin", "main"],
                                         _req(tmp_path, True), sandboxed=False)
    assert decision.kind == "ask" and decision.reason == TAINTED_REASON
    assert judge.actions == []


@pytest.mark.asyncio
async def test_a_remembered_rule_still_runs_when_tainted(tmp_path):
    CommandRuleStore(str(tmp_path)).add(
        CommandRule(type="exact", value="curl https://api.example/health", added_at="x"))
    gate, _ = _gate(tmp_path)
    decision = await gate.decide_command("curl", ["https://api.example/health"],
                                         _req(tmp_path, True), sandboxed=True)
    assert decision.kind == "run"


@pytest.mark.asyncio
async def test_the_deny_list_still_denies_when_tainted(tmp_path):
    gate, _ = _gate(tmp_path)
    decision = await gate.decide_command("rm", ["-rf", "/"], _req(tmp_path, True))
    assert decision.kind == "deny"


@pytest.mark.asyncio
async def test_tainted_escalation_asks_without_the_judge(tmp_path):
    gate, judge = _gate(tmp_path)
    escalation = Escalation(kind="allow_hosts", hosts=["x.example"], reason="need it")
    decision = await gate.decide_escalation(escalation, "curl", ["https://x.example"],
                                            _req(tmp_path, True))
    assert decision.kind == "ask" and decision.reason == TAINTED_REASON
    assert judge.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mcp", "web_fetch"])
async def test_tainted_tool_calls_ask_without_the_judge(tmp_path, kind):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_tool_call(kind, "tracker", "send_message", {"to": "a"},
                                           _req(tmp_path, True))
    assert decision.kind == "ask" and decision.reason == TAINTED_REASON
    assert judge.actions == []


@pytest.mark.asyncio
async def test_tainted_web_search_still_goes_to_the_judge(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_tool_call("web_search", "web", "search", {"q": "x"},
                                           _req(tmp_path, True))
    assert decision.kind == "run" and len(judge.actions) == 1


@pytest.mark.asyncio
async def test_taint_is_ignored_with_the_defense_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "0")
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_tool_call("mcp", "tracker", "send_message", {},
                                           _req(tmp_path, True))
    assert decision.kind == "run" and len(judge.actions) == 1
