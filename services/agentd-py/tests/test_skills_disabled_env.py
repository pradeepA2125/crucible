from pathlib import Path

import pytest

from agentd.skills.loader import SkillCatalogLoader


def _write_skill(ws: Path, name: str) -> None:
    d = ws / ".ai-editor" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def test_disabled_env_filters_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_skill(tmp_path, "keep-me")
    _write_skill(tmp_path, "drop-me")
    loader = SkillCatalogLoader(tmp_path)
    monkeypatch.setenv("CRUCIBLE_SKILLS_DISABLED", " drop-me , ,missing")
    assert [m.name for m in loader.load_catalog()] == ["keep-me"]
    monkeypatch.delenv("CRUCIBLE_SKILLS_DISABLED")
    assert sorted(m.name for m in loader.load_catalog()) == ["drop-me", "keep-me"]
