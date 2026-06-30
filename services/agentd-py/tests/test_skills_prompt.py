from pathlib import Path

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.skills.models import SkillManifest


def _m(name: str, desc: str) -> SkillManifest:
    return SkillManifest(name=name, description=desc, body_path=Path("x"), dir=Path("d"))


def test_catalog_block_present_when_skills_given() -> None:
    out = format_controller_system_prompt(
        [], skills_catalog=[_m("git-commit", "Make a commit.")]
    )
    assert "AVAILABLE SKILLS" in out
    assert "git-commit: Make a commit." in out
    assert "read_skill" in out  # teaching
    assert "scripts/" in out  # run_command worked example


def test_no_catalog_block_when_empty() -> None:
    out = format_controller_system_prompt([], skills_catalog=[])
    assert "AVAILABLE SKILLS" not in out
    out2 = format_controller_system_prompt([], skills_catalog=None)
    assert "AVAILABLE SKILLS" not in out2
