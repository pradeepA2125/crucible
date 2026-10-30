"""Auto always asks before a shell write to a file that grants permission or steers the
agent; other protected-for-edits files (a scratch file in .crucible/) go to the judge."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentd.approvals.auto_gate import NO_JUDGE_REASON, PROTECTED_WRITE_REASON, AutoGate
from agentd.approvals.auto_rules import writes_protected
from agentd.approvals.command_parse import parse_command
from agentd.approvals.types import AutoDecision, JudgeRequest

HOME = Path("/Users/u")
REQ = JudgeRequest("t1", "main", Path("/tmp"))


def _writes(ws: Path, *argv: str) -> bool:
    return writes_protected(parse_command(argv[0], list(argv[1:]), ws), workspace=ws, home=HOME)


@pytest.mark.parametrize("target", [
    "AGENTS.md",
    ".crucible/approved-commands.json",
    ".crucible/approved-mcp-tools.json",
    ".crucible/approved-web-fetch.json",
    ".crucible/mcp.json",
    ".crucible/agents/reviewer.md",
    ".crucible/skills/deploy/SKILL.md",
    ".crucible/prompts/ship.md",
    ".crucible/state/chat.sqlite3",
    ".claude/agents/reviewer.md",
    ".claude/skills/deploy/SKILL.md",
    ".vscode/settings.json",
    ".vscode/tasks.json",
    "app.code-workspace",
    ".git/hooks/pre-commit",
    ".git/config",
])
def test_permission_and_behavior_files_always_ask(tmp_path, target):
    assert _writes(tmp_path, "touch", target)


@pytest.mark.parametrize("target", [
    ".crucible/a.txt",
    ".crucible/index-snapshot.json",
    ".crucible/notes/todo.md",
    "src/app.py",
])
def test_other_files_are_left_to_the_judge(tmp_path, target):
    assert not _writes(tmp_path, "touch", target)


@pytest.mark.parametrize("target", [
    "~/.crucible/trust.json",
    "$HOME/.crucible/run/agentd-8000.token",
    "/Users/u/.claude/agents/reviewer.md",
    "~/.claude/skills/deploy/SKILL.md",
])
def test_home_permission_files_always_ask(tmp_path, target):
    assert _writes(tmp_path, "tee", target)


def test_other_home_files_are_left_to_the_judge(tmp_path):
    assert not _writes(tmp_path, "tee", "~/notes.txt")


def test_home_file_reached_through_cwd(tmp_path):
    p = parse_command("tee", ["trust.json"], tmp_path)
    assert writes_protected(p, workspace=tmp_path, home=HOME, cwd="/Users/u/.crucible")


class _Judge:
    def __init__(self) -> None:
        self.calls = 0

    async def judge(self, request, action):
        self.calls += 1
        return AutoDecision("run", "ok", "Auto: ok")


@pytest.mark.asyncio
async def test_scratch_file_in_crucible_goes_to_the_judge(tmp_path):
    judge = _Judge()
    gate = AutoGate(tmp_path, HOME, judge=judge)
    assert (await gate.decide_command("touch", [".crucible/a.txt"], REQ)).kind == "run"
    assert judge.calls == 1
    no_judge = await AutoGate(tmp_path, HOME, judge=None).decide_command(
        "touch", [".crucible/a.txt"], REQ)
    assert no_judge.reason == NO_JUDGE_REASON


@pytest.mark.asyncio
async def test_permission_file_never_reaches_the_judge(tmp_path):
    judge = _Judge()
    d = await AutoGate(tmp_path, HOME, judge=judge).decide_command(
        "touch", [".crucible/agents/x.md"], REQ)
    assert (d.kind, d.reason, judge.calls) == ("ask", PROTECTED_WRITE_REASON, 0)
