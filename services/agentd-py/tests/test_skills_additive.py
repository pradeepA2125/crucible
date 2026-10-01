"""Children accumulate skills; the parent keeps exactly one active skill (spec §5.2)."""
from pathlib import Path

import pytest

from agentd.skills.loader import SkillCatalogLoader
from agentd.skills.tool_source import SkillToolSource


def _write_skill(ws: Path, name: str, body: str) -> None:
    d = ws / ".crucible" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_additive_keeps_every_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tdd", "TDD body")
    _write_skill(tmp_path, "debugging", "Debug body")
    active: dict[str, str] = {}
    src = SkillToolSource(SkillCatalogLoader(str(tmp_path)), active, additive=True)
    await src.execute("read_skill", {"name": "tdd"})
    await src.execute("read_skill", {"name": "debugging"})
    assert sorted(active) == ["debugging", "tdd"]


@pytest.mark.asyncio
async def test_the_default_still_replaces(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tdd", "TDD body")
    _write_skill(tmp_path, "debugging", "Debug body")
    active: dict[str, str] = {}
    src = SkillToolSource(SkillCatalogLoader(str(tmp_path)), active)
    await src.execute("read_skill", {"name": "tdd"})
    await src.execute("read_skill", {"name": "debugging"})
    assert list(active) == ["debugging"]
