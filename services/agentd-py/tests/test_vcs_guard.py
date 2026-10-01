"""The sub-agent VCS guard (spec §4.6.6, §15): mutations refused before any card."""
from pathlib import Path

import pytest

from agentd.domain.models import ApprovalOutcome, CommandDecision
from agentd.subagents.vcs_guard import VCS_REFUSAL, vcs_refusal
from agentd.tools.registry import ToolRegistry

REFUSED = [
    "git commit -am x", "git checkout .", "git stash", "cd x && git commit", "ls ; git stash",
    "git -C . commit", "git -c user.name=x commit", "env GIT_DIR=. git reset",
    "/usr/bin/git checkout .", 'bash -c "git stash"', "git ci", "a&&git commit",
    "( git commit )", "$(git commit -am x)", "xargs git checkout", r"find . -exec git rm {} \;",
    "timeout 10 git commit", 'eval "git stash"', 'timeout 5 bash -c "git commit"',
    "xargs sh -c 'git stash'", "echo 'unbalanced", "git config user.name x",
    'echo "$(git push)"', "git --version", "nice git reset --hard", "bash -lc 'git stash'",
]
ALLOWED = [
    "git status", "git diff", "git log", "git -C sub status", "echo $(date)",
    "git config --get user.name", "git config --list", "git show HEAD:README.md",
    "pytest tests/test_x.py -x", 'grep "don\'t" notes.md', "echo '$(git commit)'", "git",
    "ls | wc -l",
]


@pytest.mark.parametrize("line", REFUSED)
def test_refused(line: str) -> None:
    assert vcs_refusal(line) == VCS_REFUSAL


@pytest.mark.parametrize("line", ALLOWED)
def test_allowed(line: str) -> None:
    assert vcs_refusal(line) is None


def test_the_refusal_text() -> None:
    assert VCS_REFUSAL == (
        "Version-control changes are blocked for sub-agents: other agents are editing this "
        "workspace, and the main agent commits after the work is done. Read-only git "
        "(status/diff/log/show) is fine.")


@pytest.mark.asyncio
async def test_a_refused_command_never_reaches_the_approval_card(tmp_path: Path) -> None:
    asked: list[tuple[str, list[str]]] = []

    async def approve(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        asked.append((command, args))
        return ApprovalOutcome.from_command(CommandDecision(approve=True))

    reg = ToolRegistry(tmp_path, tmp_path, command_approval_callback=approve,
                       command_guard=vcs_refusal)
    refused = await reg.execute("run_command", {"command": "git", "args": ["stash"]})
    packed = await reg.execute("run_command", {"command": "cd sub && git commit -am x"})
    assert refused.is_error and refused.output == VCS_REFUSAL
    assert packed.is_error and packed.output == VCS_REFUSAL
    assert asked == []
    await reg.execute("run_command", {"command": "git", "args": ["status"]})
    assert asked == [("git", ["status"])]
