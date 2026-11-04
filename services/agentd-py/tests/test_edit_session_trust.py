"""An approved edit to a trusted file keeps it trusted (prompt-injection Phase 1, spec §4.1).

AGENTS.md and .crucible/skills are protected paths, so the main agent's edits to them always go
through a review card; accepting that card is the user's own change."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentd.chat.edit_session import TurnEditSession
from agentd.patch.engine import PatchEngine
from agentd.trust.project_trust import ProjectTrust
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _session(ws: Path, tmp_path: Path) -> TurnEditSession:
    return TurnEditSession(turn_id="t1", real_path=ws,
                           workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
                           patch_engine=PatchEngine())


def _sr(file: str, search: str, replace: str) -> list[dict]:
    return [{"op": "search_replace", "file": file, "search": search, "replace": replace,
             "reason": "r"}]


def _trust_agents(ws: Path, text: str) -> None:
    ProjectTrust(ws).trust("agents_md", hashlib.sha256(text.encode()).hexdigest())


@pytest.mark.asyncio
async def test_accepted_edit_to_trusted_agents_md_stays_trusted(ws: Path, tmp_path: Path) -> None:
    (ws / "AGENTS.md").write_text("use npm\n")
    _trust_agents(ws, "use npm\n")
    sess = _session(ws, tmp_path)
    await sess.apply(_sr("AGENTS.md", "npm", "pnpm"))
    await sess.accept()
    assert ProjectTrust(ws).state("agents_md") == "trusted"
    await sess.close()


@pytest.mark.asyncio
async def test_accepted_edit_to_untrusted_agents_md_stays_untrusted(
        ws: Path, tmp_path: Path) -> None:
    (ws / "AGENTS.md").write_text("planted\n")
    sess = _session(ws, tmp_path)
    await sess.apply(_sr("AGENTS.md", "planted", "planted, tidied"))
    await sess.accept()
    assert ProjectTrust(ws).state("agents_md") == "untrusted"
    await sess.close()


@pytest.mark.asyncio
async def test_accepted_edit_creating_agents_md_is_trusted(ws: Path, tmp_path: Path) -> None:
    sess = _session(ws, tmp_path)
    await sess.apply([{"op": "create_file", "file": "AGENTS.md", "content": "use pnpm\n",
                       "reason": "r"}])
    await sess.accept()
    assert ProjectTrust(ws).state("agents_md") == "trusted"
    await sess.close()


@pytest.mark.asyncio
async def test_rejected_edit_records_nothing(ws: Path, tmp_path: Path) -> None:
    sess = _session(ws, tmp_path)
    await sess.apply([{"op": "create_file", "file": "AGENTS.md", "content": "x\n",
                       "reason": "r"}])
    await sess.reject()
    assert ProjectTrust(ws).state("agents_md") == "absent"
    assert not (tmp_path / "home" / ".crucible" / "trust.json").exists()
    await sess.close()


@pytest.mark.asyncio
async def test_accepted_skill_edit_keeps_trusted_set_trusted(ws: Path, tmp_path: Path) -> None:
    for name in ("a", "b"):
        folder = ws / ".crucible" / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")
    trust = ProjectTrust(ws)
    trust.trust("skills", trust.fingerprint("skills"))  # type: ignore[arg-type]
    sess = _session(ws, tmp_path)
    await sess.apply(_sr(".crucible/skills/a/SKILL.md", "body", "better body"))
    await sess.accept()
    assert ProjectTrust(ws).state("skills") == "trusted"
    await sess.close()
