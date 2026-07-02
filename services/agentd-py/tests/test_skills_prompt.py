from pathlib import Path

from agentd.chat.controller_prompts import (
    build_controller_step_payload,
    format_controller_system_prompt,
)
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


def _skills_block(out: str) -> str:
    return out.split("AVAILABLE SKILLS")[1]


def test_worked_pattern_has_no_creative_work_collision() -> None:
    """Live TQP finding 2026-07-02: our illustrative example used the phrase
    'creative work' — the exact head-phrase of a real skill's description — which
    hard-wired every writing-ish request onto the read_skill rail regardless of the
    line's enumerated scope. Our examples must not seed trigger phrases."""
    out = format_controller_system_prompt([], skills_catalog=[_m("s", "d")])
    assert "creative work" not in _skills_block(out)


def test_worked_pattern_includes_a_negative_example() -> None:
    """Every example previously ended in read_skill — the 'no line matched ->
    proceed' path was referenced but never demonstrated, so weak models never
    pattern-matched a non-match. One negative worked example, conditional on the
    catalog (docs-writing may legitimately be a skill in some workspace)."""
    block = _skills_block(
        format_controller_system_prompt([], skills_catalog=[_m("s", "d")]))
    assert "no read_skill" in block


def test_match_is_bounded_by_the_lines_enumeration() -> None:
    """The model matched a description's headline word and ignored its
    enumeration. Both the block and the per-turn skill check must teach that an
    author's enumeration bounds the trigger."""
    block = _skills_block(
        format_controller_system_prompt([], skills_catalog=[_m("s", "d")]))
    assert "enumeration bounds" in block
    payload = build_controller_step_payload(
        {"goal": "g", "decide_entry": True}, [], [],
        phase="DECIDE", skills_available=True)
    instruction = str(payload.get("instruction", ""))
    assert "SKILL CHECK" in instruction
    assert "enumeration bounds" in instruction
