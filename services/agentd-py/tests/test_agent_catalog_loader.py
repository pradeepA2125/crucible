"""Agent discovery: precedence, recursion, built-ins, mtime cache (spec §9.1)."""
import logging
import os
from pathlib import Path

import pytest

from agentd.subagents.agent_files import AgentCatalogLoader
from agentd.subagents.definitions import BUILTIN_AGENTS


def _agent(path: Path, name: str, description: str = "d", body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    workspace, user = tmp_path / "ws", tmp_path / "home-agents"
    workspace.mkdir()
    return workspace, user


def test_no_files_means_the_built_ins(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    catalog = AgentCatalogLoader(workspace, user_agents_dir=user).load()
    assert list(catalog) == ["explore", "general-purpose"]
    assert catalog["explore"] is BUILTIN_AGENTS["explore"]


def test_precedence_crucible_then_claude_then_user_then_built_in(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/a.md", "shared", "from crucible")
    _agent(workspace / ".claude/agents/b.md", "shared", "from claude")
    _agent(user / "c.md", "shared", "from user")
    _agent(workspace / ".claude/agents/d.md", "explore", "project explore")
    _agent(user / "e.md", "only-user", "user only")
    catalog = AgentCatalogLoader(workspace, user_agents_dir=user).load()
    assert catalog["shared"].description == "from crucible"
    assert catalog["explore"].description == "project explore"   # a file overrides a built-in
    assert catalog["only-user"].description == "user only"
    assert list(catalog) == sorted(catalog)                      # deterministic, by name


def test_recursive_and_identity_from_name(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/team/backend/api-expert.md", "api", "nested")
    assert AgentCatalogLoader(workspace, user_agents_dir=user).load()["api"].description == "nested"


def test_duplicate_within_one_root_keeps_first_and_warns(
    roots: tuple[Path, Path], caplog: pytest.LogCaptureFixture,
) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/a.md", "dup", "first")
    _agent(workspace / ".crucible/agents/b.md", "dup", "second")
    with caplog.at_level(logging.WARNING):
        catalog = AgentCatalogLoader(workspace, user_agents_dir=user).load()
    assert catalog["dup"].description == "first" and "dup" in caplog.text


def test_malformed_file_is_skipped(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    (workspace / ".crucible/agents").mkdir(parents=True)
    (workspace / ".crucible/agents/bad.md").write_text("no frontmatter", encoding="utf-8")
    _agent(workspace / ".crucible/agents/good.md", "good")
    assert "good" in AgentCatalogLoader(workspace, user_agents_dir=user).load()


def test_cache_hit_and_nested_edit_invalidates(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    nested = workspace / ".crucible/agents/team/x.md"
    _agent(nested, "x", "v1")
    loader = AgentCatalogLoader(workspace, user_agents_dir=user)
    first = loader.load()
    assert loader.load() is first                       # unchanged → same object
    _agent(nested, "x", "v2")                           # a nested edit: the root dir's mtime
    stat = nested.stat()                                # does not move, the file's does
    os.utime(nested, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert loader.load()["x"].description == "v2"


def test_new_and_deleted_files_take_effect(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    loader = AgentCatalogLoader(workspace, user_agents_dir=user)
    assert "late" not in loader.load()
    _agent(workspace / ".claude/agents/late.md", "late")
    assert "late" in loader.load()
    (workspace / ".claude/agents/late.md").unlink()
    assert "late" not in loader.load()


def test_non_md_files_are_ignored(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/notes.txt", "txt")
    assert "txt" not in AgentCatalogLoader(workspace, user_agents_dir=user).load()
