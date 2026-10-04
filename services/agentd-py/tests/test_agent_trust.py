"""Workspace definitions are capped until trusted (spec §3.12, E16)."""
import hashlib
from pathlib import Path

from agentd.subagents.agent_files import AgentCatalogLoader
from agentd.subagents.trust import TrustStore

_FILE = """---
name: {name}
description: d
permissionMode: acceptEdits
---
You are helpful.
"""


def _write(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(_FILE.format(name=name))
    return path


def _loader(tmp_path: Path) -> tuple[AgentCatalogLoader, Path, TrustStore]:
    ws = tmp_path / "ws"
    store = TrustStore(tmp_path / "trust.json")
    return AgentCatalogLoader(ws, user_agents_dir=tmp_path / "home", trust_store=store), ws, store


def test_workspace_files_are_capped_and_user_files_trusted(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    _write(ws / ".claude" / "agents", "repo-agent")
    _write(tmp_path / "home", "mine")
    catalog = loader.load()
    assert catalog["repo-agent"].trust == "capped"
    assert catalog["mine"].trust == "trusted"


def test_trusting_by_hash_and_editing_revokes(tmp_path: Path) -> None:
    loader, ws, store = _loader(tmp_path)
    path = _write(ws / ".crucible" / "agents", "helper")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    store.trust(str(ws), str(path), digest)
    assert loader.load()["helper"].trust == "trusted"
    path.write_text(path.read_text() + "\nmore\n")
    assert loader.load()["helper"].trust == "capped"


def test_a_capped_file_never_shadows_a_builtin(tmp_path: Path) -> None:
    loader, ws, store = _loader(tmp_path)
    path = _write(ws / ".claude" / "agents", "general-purpose")
    assert loader.load()["general-purpose"].source == "built-in"
    store.trust(str(ws), str(path), hashlib.sha256(path.read_bytes()).hexdigest())
    assert loader.load()["general-purpose"].source == str(path)


def test_capped_descriptions_are_framed_in_the_dispatch_tool(tmp_path: Path) -> None:
    from agentd.subagents.tool_source import SubAgentToolSource

    loader, ws, _ = _loader(tmp_path)
    _write(ws / ".claude" / "agents", "repo-agent")
    [tool] = SubAgentToolSource(loader.load(), dispatch=None).definitions()  # type: ignore[arg-type]
    assert '<<<agent-content author="definition' in tool.description
    assert "(untrusted)" in tool.description
