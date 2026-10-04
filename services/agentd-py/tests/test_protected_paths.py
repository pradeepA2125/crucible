"""Control-plane files are out of agents' reach (spec §3.9)."""
import os
from pathlib import Path

import pytest

from agentd.chat.edit_session import TurnEditSession
from agentd.chat.protected_paths import (
    AgentProtection, MainProtection, ProtectedPathError, is_protected)
from agentd.patch.engine import PatchEngine
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.parametrize("key,expected", [
    (".crucible/approved-commands.json", True), (".crucible/agents/x.md", True),
    (".claude/agents/x.md", True), (".claude/skills/s/SKILL.md", True), ("AGENTS.md", True),
    (".vscode/settings.json", True), (".vscode/tasks.json", True), ("x.code-workspace", True),
    ("src/app.py", False), (".vscode/extensions.json", False), ("docs/AGENTS.md", False)])
def test_is_protected(key: str, expected: bool) -> None:
    assert is_protected(key) is expected


def _session(tmp_path: Path, protection) -> tuple[TurnEditSession, Path]:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return TurnEditSession(
        turn_id="t", real_path=ws,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "sh"),
        patch_engine=PatchEngine(), protection=protection), ws


def _create(file: str) -> list[dict[str, object]]:
    return [{"op": "create_file", "file": file, "content": "{}\n", "reason": "r"}]


@pytest.mark.asyncio
async def test_agents_are_refused(tmp_path: Path) -> None:
    session, _ = _session(tmp_path, AgentProtection())
    with pytest.raises(ProtectedPathError, match="protected Crucible configuration file"):
        await session.apply(_create(".crucible/mcp.json"))


@pytest.mark.asyncio
async def test_the_main_agent_must_review(tmp_path: Path) -> None:
    session, _ = _session(tmp_path, MainProtection())
    await session.apply(_create("AGENTS.md"))
    assert session.requires_review
    await session.apply(_create("src/ok.py"))
    assert not session.requires_review


@pytest.mark.asyncio
async def test_a_symlink_after_apply_is_refused_at_accept(tmp_path: Path) -> None:
    session, ws = _session(tmp_path, MainProtection())
    (ws / ".crucible").mkdir()
    await session.apply(_create("out/config.json"))
    # Between apply and accept, `out` becomes a link into .crucible.
    os.symlink(ws / ".crucible", ws / "out")
    with pytest.raises(ProtectedPathError):
        await session.accept()


def test_command_mentions_protected() -> None:
    from agentd.chat.protected_paths import command_mentions_protected
    assert command_mentions_protected("cp", ["x", ".crucible/mcp.json"])
    assert not command_mentions_protected("pytest", ["tests/"])
