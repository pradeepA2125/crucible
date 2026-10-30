"""Auto with the sandbox on: only outward actions and escalations reach the judge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentd.approvals.auto_gate import AutoGate
from agentd.approvals.auto_rules import is_outward
from agentd.approvals.command_parse import parse_command
from agentd.approvals.types import AutoDecision, JudgeRequest
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandRule, ShellPolicy
from agentd.sandbox.runtime import SandboxRuntime
from agentd.tools.command_rules import CommandRuleStore
from tests.test_auto_gate_commands import _ScriptedJudge
from tests.test_controller_command_gate import _controller

REQ = JudgeRequest("t1", "main", Path("/tmp"))


def _gate(tmp_path: Path) -> tuple[AutoGate, _ScriptedJudge]:
    judge = _ScriptedJudge(AutoDecision("run", "ok", "Auto: ok"))
    return AutoGate(tmp_path, Path("/Users/u"), judge=judge), judge


@pytest.mark.parametrize("cmd,args", [
    ("git", ["push", "origin", "main"]),
    ("gh", ["pr", "create", "--fill"]),
    ("gh", ["pr", "merge", "3"]),
    ("gh", ["release", "create", "v1"]),
    ("gh", ["api", "-X", "DELETE", "repos/o/r"]),
    ("gh", ["api", "--method=POST", "repos/o/r/issues"]),
    ("npm", ["publish"]),
    ("pnpm", ["publish"]),
    ("cargo", ["publish"]),
    ("twine", ["upload", "dist/*"]),
    ("uv", ["publish"]),
    ("docker", ["push", "img"]),
    ("sh", ["-c", "make && git push"]),
    # Global flags before the subcommand: the user's real token in ~/.npmrc is readable.
    ("npm", ["--registry", "x", "publish"]), ("npm", ["-w", "a", "publish"]),
    ("pnpm", ["-r", "publish"]), ("yarn", ["npm", "publish"]), ("npm", ["dist-tag", "add", "a"]),
    ("npm", ["owner", "add", "a"]), ("cargo", ["--locked", "publish"]),
    ("cargo", ["+stable", "publish"]), ("cargo", ["yank", "--vers", "1"]),
    ("cargo", ["owner", "--add", "a"]), ("uv", ["--directory", "x", "publish"]),
    ("poetry", ["publish"]), ("docker", ["--context", "c", "push", "i"]),
    ("docker", ["image", "push", "i"]), ("docker", ["buildx", "build", "--push", "."]),
    ("podman", ["push", "i"]), ("python", ["-m", "twine", "upload", "d"]),
    ("python3", ["-m", "twine", "upload", "d"]),
    # gh is an allowlist of reads: anything else changes something.
    ("gh", ["api", "/x", "-XPOST"]), ("gh", ["api", "/x", "-fa=b"]), ("gh", ["gist", "create", "f"]),
    ("gh", ["secret", "set", "S"]), ("gh", ["workflow", "run", "w"]), ("gh", ["repo", "sync"]),
    ("gh", ["repo", "deploy-key", "add", "k"]), ("gh", ["release", "delete-asset", "v", "a"]),
    ("gh", ["ssh-key", "add", "k"]), ("gh", ["newly-added-thing", "do"]),
    # Other ways to reach a push.
    ("git", ["-c", "alias.p=push", "p"]), ("git", ["send-pack", "o"]), ("git", ["lfs", "push", "o"]),
    ("xargs", ["git", "push"]), ("find", [".", "-exec", "git", "push", ";"]),
])
def test_outward_actions(tmp_path, cmd, args):
    assert is_outward(parse_command(cmd, args, tmp_path))


@pytest.mark.parametrize("cmd,args", [
    ("git", ["fetch"]), ("git", ["commit", "-m", "x"]), ("gh", ["pr", "list"]),
    ("gh", ["api", "repos/o/r"]), ("npm", ["install"]), ("pytest", ["-q"]),
    ("gh", ["pr", "view", "3"]), ("gh", ["pr", "diff", "3"]), ("gh", ["issue", "list"]),
    ("gh", ["run", "view", "1"]), ("gh", ["repo", "view"]), ("gh", ["search", "code", "x"]),
    ("gh", ["auth", "status"]), ("gh", ["--repo", "o/r", "pr", "list"]), ("gh", []),
    ("gh", ["api", "repos/o/r", "-X", "GET"]), ("gh", ["api", "repos/o/r", "--jq", ".a"]),
    ("npm", ["run", "build"]), ("npm", ["ci"]), ("cargo", ["build", "--locked"]),
    ("docker", ["build", "-t", "x/push", "."]), ("docker", ["ps"]), ("uv", ["sync"]),
    ("python", ["-m", "pytest"]), ("python3", ["script.py", "push"]), ("git", ["log", "push"]),
    ("git", ["-c", "color.ui=always", "status"]), ("grep", ["-r", "push", "."]),
])
def test_not_outward(tmp_path, cmd, args):
    assert not is_outward(parse_command(cmd, args, tmp_path))


@pytest.mark.asyncio
async def test_sandboxed_pytest_runs_without_judge(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_command("pytest", ["-q"], REQ, sandboxed=True)
    assert decision.kind == "run" and decision.note is None and judge.actions == []


@pytest.mark.asyncio
async def test_sandboxed_git_push_goes_to_judge(tmp_path):
    gate, judge = _gate(tmp_path)
    assert (await gate.decide_command("git", ["push"], REQ, sandboxed=True)).kind == "run"
    assert judge.actions[0].summary == "run_command: git push"


@pytest.mark.asyncio
async def test_sandboxed_remembered_rule_still_runs_outward(tmp_path):
    CommandRuleStore(tmp_path).add(CommandRule(type="exact", value="git push", added_at="t"))
    gate, judge = _gate(tmp_path)
    assert (await gate.decide_command("git", ["push"], REQ, sandboxed=True)).kind == "run"
    assert judge.actions == []


@pytest.mark.asyncio
async def test_sandboxed_deny_list_still_denies(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_command("rm", ["-rf", "/"], REQ, sandboxed=True)
    assert decision.kind == "deny" and judge.actions == []


@pytest.mark.asyncio
async def test_sandboxed_protected_write_still_asks(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_command("touch", ["AGENTS.md"], REQ, sandboxed=True)
    assert decision.kind == "ask" and judge.actions == []


@pytest.mark.asyncio
async def test_unsandboxed_unchanged(tmp_path):
    gate, judge = _gate(tmp_path)
    await gate.decide_command("pytest", ["-q"], REQ)
    assert len(judge.actions) == 1


@pytest.mark.asyncio
async def test_shell_stdin_git_push_judged_when_sandboxed(tmp_path):
    gate, judge = _gate(tmp_path)
    await gate.decide_stdin("bash", "git push origin main\n", REQ, sandboxed=True)
    assert judge.actions and "git push" in judge.actions[0].summary


@pytest.mark.asyncio
async def test_shell_stdin_routine_line_runs_when_sandboxed(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_stdin("zsh", "npm test\n", REQ, sandboxed=True)
    assert decision.kind == "run" and judge.actions == []


@pytest.mark.asyncio
async def test_repl_stdin_runs_when_sandboxed(tmp_path):
    gate, judge = _gate(tmp_path)
    decision = await gate.decide_stdin("python3", "import os; os.listdir('.')\n", REQ,
                                       sandboxed=True)
    assert decision.kind == "run" and judge.actions == []


@pytest.mark.asyncio
async def test_unsandboxed_shell_stdin_still_judged(tmp_path):
    gate, judge = _gate(tmp_path)
    await gate.decide_stdin("bash", "npm test\n", REQ)
    assert len(judge.actions) == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="needs a working sandbox")
@pytest.mark.asyncio
async def test_controller_skips_judge_for_sandboxed_routine_commands(tmp_path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, ShellPolicy.AUTO)
    ctrl._sandbox = SandboxRuntime(tmp_path)
    await ctrl._sandbox.probe()
    gate, judge = _gate(tmp_path)
    ctrl._auto_gate = gate
    outcome = await ctrl._command_approval_cb(tid, f"chat:{tid}", "pytest", ["-q"], "")
    assert outcome.approved and judge.actions == []
    ctrl._sandbox.enabled = False
    await ctrl._command_approval_cb(tid, f"chat:{tid}", "pytest", ["-q"], "")
    assert len(judge.actions) == 1


def test_judge_policy_mentions_the_sandbox():
    from agentd.approvals.judge_policy import JUDGE_POLICY
    assert "outside the sandbox" in JUDGE_POLICY


@pytest.mark.asyncio
async def test_leaving_the_sandbox_to_write_a_protected_file_asks_the_user(tmp_path):
    from agentd.domain.models import Escalation
    gate, judge = _gate(tmp_path)
    escalation = Escalation(kind="outside_sandbox", reason="r")
    decision = await gate.decide_escalation(
        escalation, "sh", ["-c", "echo x >> .crucible/approved-commands.json"], REQ)
    assert decision.kind == "ask"
    assert not judge.actions
