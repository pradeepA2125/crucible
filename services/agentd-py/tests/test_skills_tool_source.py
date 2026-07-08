import asyncio
from pathlib import Path

from agentd.skills.loader import SkillCatalogLoader
from agentd.skills.tool_source import SkillToolSource


def _write_skill(root: Path, name: str, body: str) -> None:
    d = root / ".crucible" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}\n", encoding="utf-8"
    )


def test_definitions_expose_read_skill(tmp_path: Path) -> None:
    src = SkillToolSource(SkillCatalogLoader(tmp_path), {})
    assert [d.name for d in src.definitions()] == ["read_skill"]
    assert src.owns("read_skill") and not src.owns("read_file")


def test_read_skill_returns_body_and_marks_active(tmp_path: Path) -> None:
    _write_skill(tmp_path, "git-commit", "STEP 1: stage. STEP 2: commit.")
    active: dict[str, str] = {}
    src = SkillToolSource(SkillCatalogLoader(tmp_path), active)
    out = asyncio.run(src.execute("read_skill", {"name": "git-commit"}))
    assert not out.is_error
    assert "STEP 1: stage." in out.output
    assert "git-commit" in active and "STEP 1" in active["git-commit"]


def test_read_skill_unknown_name_is_error(tmp_path: Path) -> None:
    src = SkillToolSource(SkillCatalogLoader(tmp_path), {})
    out = asyncio.run(src.execute("read_skill", {"name": "nope"}))
    assert out.is_error and "no skill" in out.output.lower()


def test_read_skill_caps_large_body(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SKILLS_BODY_MAX_CHARS", "50")
    _write_skill(tmp_path, "big", "x" * 500)
    src = SkillToolSource(SkillCatalogLoader(tmp_path), {})
    out = asyncio.run(src.execute("read_skill", {"name": "big"}))
    assert "truncated" in out.output and len(out.output) < 200
