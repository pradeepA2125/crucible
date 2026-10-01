"""Regression coverage for the skill-handoff gap found live (2 independent kafka-clone-4
dogfood runs, see docs/superpowers/... skill-handoff write-up): a plan document the model
itself writes can name "REQUIRED SUB-SKILL: superpowers:<name>" for its own execution, but
the model reliably never connects that text to a read_skill call later in the same turn.
`_extract_required_subskills`/`_pick_executable_required_subskill` are the pure detection
primitives; `ControllerLoop._maybe_force_required_subskill` is the integration that force-
loads the skill deterministically (mirrors the /skill forced_skills seeding path) instead of
relying on the model's own judgment a second time.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import (
    ControllerLoop,
    _extract_required_subskills,
    _pick_executable_required_subskill,
)
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.skills.loader import SkillCatalogLoader
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager

_PLAN_DOC = """\
# Kafka Commit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development \
(recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use \
checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the commit log.
"""


def test_extract_required_subskills_pulls_both_names_in_order() -> None:
    names = _extract_required_subskills(
        [{"op": "create_file", "file": "docs/plans/x.md", "content": _PLAN_DOC, "reason": "r"}])
    assert names == ["subagent-driven-development", "executing-plans"]


def test_extract_required_subskills_scans_every_op_text_field() -> None:
    # search_replace carries text in 'search'/'replace', not 'content'.
    names = _extract_required_subskills([{
        "op": "search_replace", "file": "docs/plans/x.md",
        "search": "old", "reason": "r",
        "replace": "REQUIRED SUB-SKILL: Use superpowers:executing-plans now.",
    }])
    assert names == ["executing-plans"]


def test_extract_required_subskills_empty_when_no_directive() -> None:
    assert _extract_required_subskills(
        [{"op": "create_file", "file": "a.py", "content": "x = 1\n", "reason": "r"}]) == []


def test_pick_executable_required_subskill_skips_subagent_driven_development() -> None:
    # Crucible's controller has no subagent-dispatch tool — only executing-plans is
    # ever actually runnable inline, regardless of which name the directive lists first.
    assert _pick_executable_required_subskill(
        ["subagent-driven-development", "executing-plans"]) == "executing-plans"


def test_pick_executable_required_subskill_none_when_only_non_executable_named() -> None:
    assert _pick_executable_required_subskill(["subagent-driven-development"]) is None


def test_subagent_driven_development_is_executable_when_subagents_are_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    assert _pick_executable_required_subskill(
        ["subagent-driven-development", "executing-plans"]) == "subagent-driven-development"


def _install_skill(root: Path, name: str, description: str, body: str) -> None:
    skill_dir = root / ".crucible" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8")


def _loop(tmp_path: Path, steps: list[dict[str, object]], active_skills: dict[str, str]):
    wm = ShadowWorkspaceManager(tmp_path / "shadows")
    patch_engine = PatchEngine()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: TurnEditSession(
            turn_id="t1", real_path=tmp_path, workspace_manager=wm, patch_engine=patch_engine),
        active_skills=active_skills,
        skill_catalog_loader=SkillCatalogLoader(tmp_path),
    )


@pytest.mark.asyncio
async def test_saving_a_plan_doc_auto_loads_its_required_subskill(tmp_path: Path) -> None:
    _install_skill(
        tmp_path, "executing-plans",
        "Use when you have a written implementation plan to execute",
        "# Executing Plans\n\nStep 1: Load and review.\nStep 2: Execute tasks.")
    active_skills: dict[str, str] = {"writing-plans": "some prior body"}
    steps = [
        {"type": "edit", "thought": "save plan", "patch_ops": [
            {"op": "create_file", "file": "docs/plans/x.md",
             "content": _PLAN_DOC, "reason": "save plan"}]},
        {"type": "submit_changes", "thought": "done", "summary": "saved plan"},
    ]
    out = await _loop(tmp_path, steps, active_skills).run(
        {"goal": "write a plan", "workspace_path": str(tmp_path)},
        max_iters=6, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    # The prior skill (writing-plans) was replaced, not accumulated — mirrors
    # SkillToolSource's "exactly one skill active" semantics.
    assert set(active_skills) == {"executing-plans"}
    assert "Step 1: Load and review." in active_skills["executing-plans"]
    # A synthetic tool_result explains the auto-load to the model (no real read_skill
    # call happened) so it isn't left wondering why active_skills changed underneath it.
    joined = " ".join(str(m.get("content", "")) for m in out.history or [])
    assert "auto-loaded" in joined.lower() or "auto-loaded" in joined


@pytest.mark.asyncio
async def test_no_autoload_when_skill_catalog_loader_not_wired(tmp_path: Path) -> None:
    # Without a skill_catalog_loader (skills disabled), the plan doc's directive text
    # is inert — same behavior as before this fix, no crash, no forced load.
    wm = ShadowWorkspaceManager(tmp_path / "shadows")
    patch_engine = PatchEngine()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    active_skills: dict[str, str] = {}
    steps = [
        {"type": "edit", "thought": "save plan", "patch_ops": [
            {"op": "create_file", "file": "docs/plans/x.md",
             "content": _PLAN_DOC, "reason": "save plan"}]},
        {"type": "submit_changes", "thought": "done", "summary": "saved plan"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: TurnEditSession(
            turn_id="t1", real_path=tmp_path, workspace_manager=wm, patch_engine=patch_engine),
        active_skills=active_skills,
    )
    out = await loop.run(
        {"goal": "write a plan", "workspace_path": str(tmp_path)},
        max_iters=6, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert active_skills == {}


@pytest.mark.asyncio
async def test_autoload_skipped_when_target_skill_already_active(tmp_path: Path) -> None:
    _install_skill(
        tmp_path, "executing-plans", "Use when executing a plan", "# Executing Plans")
    active_skills: dict[str, str] = {"executing-plans": "already loaded body — unchanged"}
    steps = [
        {"type": "edit", "thought": "save plan", "patch_ops": [
            {"op": "create_file", "file": "docs/plans/x.md",
             "content": _PLAN_DOC, "reason": "save plan"}]},
        {"type": "submit_changes", "thought": "done", "summary": "saved plan"},
    ]
    out = await _loop(tmp_path, steps, active_skills).run(
        {"goal": "write a plan", "workspace_path": str(tmp_path)},
        max_iters=6, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    # Untouched — no redundant reload/replace when the target is already active.
    assert active_skills == {"executing-plans": "already loaded body — unchanged"}
