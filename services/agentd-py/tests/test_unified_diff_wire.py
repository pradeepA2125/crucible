"""DiffEntry carries the unified diff text (capped) for in-card rendering."""
from __future__ import annotations

from pathlib import Path

from agentd.orchestrator.engine import AgentOrchestrator, _cap_unified_diff
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _orch(tmp_path: Path) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )


def test_diff_entries_carry_unified_diff(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (real / "a.py").write_text("x = 1\ny = 2\n")
    (shadow / "a.py").write_text("x = 1\ny = 3\n")

    [entry] = _orch(tmp_path)._compute_diff_entries(real, shadow, ["a.py"], "t1")

    assert entry.additions == 1 and entry.deletions == 1
    assert "-y = 2" in entry.unified_diff
    assert "+y = 3" in entry.unified_diff
    assert "@@" in entry.unified_diff


def test_unified_diff_is_capped() -> None:
    lines = [f"+line {i}" for i in range(1000)]
    capped = _cap_unified_diff("\n".join(lines))
    assert len(capped.splitlines()) <= 401  # 400 + truncation marker
    assert capped.endswith("… diff truncated — open in editor for the full diff")


def test_new_file_diff_renders(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "new.py").write_text("a = 1\n")

    [entry] = _orch(tmp_path)._compute_diff_entries(real, shadow, ["new.py"], "t1")
    assert "+a = 1" in entry.unified_diff


def test_unified_diff_has_no_spurious_blank_lines_between_rows(tmp_path: Path) -> None:
    """Regression: keepends=True content lines + "\\n".join(diff) doubled every
    newline, so the diff panel rendered a blank line between every single row —
    found live driving the chat UI. None of the source lines here are blank, so
    the rendered diff's content rows should be exactly the source lines, one per
    row, with no extra blank rows in between."""
    real = tmp_path / "real"
    real.mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "new.py").write_text("package foo\nfunc A() {}\nfunc B() {}\n")

    [entry] = _orch(tmp_path)._compute_diff_entries(real, shadow, ["new.py"], "t1")
    content_lines = [
        ln for ln in entry.unified_diff.split("\n")
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    assert content_lines == ["+package foo", "+func A() {}", "+func B() {}"]
