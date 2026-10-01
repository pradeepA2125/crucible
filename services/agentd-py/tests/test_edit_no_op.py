"""An edit that leaves every file byte-for-byte unchanged is rejected, not promoted.

Found live (sub-agent smoke, 2026-10-01): a model emitted search_replace with replace ==
search. It sailed through as "✓ Edit accepted", was reported in files_changed, and its
recorded promote made a sibling's edit of the same file refused as stale.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import _edit_failure_guidance
from agentd.chat.edit_session import TurnEditSession
from agentd.domain.models import PatchFailureCode, PatchPreflightIssue
from agentd.patch.engine import PatchEngine, PatchPreflightFailed
from agentd.subagents.write_log import WorkspaceWriteLog, WriteGuard
from agentd.workspace.shadow import ShadowWorkspaceManager


def _sr(file: str, search: str, replace: str) -> dict[str, object]:
    return {"op": "search_replace", "file": file, "search": search, "replace": replace,
            "reason": "r"}


def _session(tmp_path: Path, guard: WriteGuard | None = None) -> tuple[Path, TurnEditSession]:
    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("A = 1\n")
    (real / "b.py").write_text("B = 1\n")
    return real, TurnEditSession(
        turn_id="t", real_path=real, workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(), write_guard=guard)


@pytest.mark.asyncio
async def test_a_no_op_edit_is_rejected_and_the_session_stays_usable(tmp_path: Path) -> None:
    real, sess = _session(tmp_path)
    with pytest.raises(PatchPreflightFailed) as info:
        await sess.apply([_sr("a.py", "A = 1", "A = 1")])
    assert [i.code for i in info.value.issues] == [PatchFailureCode.NO_OP]
    assert "a.py" in str(info.value)
    # shadow == real still holds: the next real edit applies and promotes normally.
    diff = await sess.apply([_sr("a.py", "A = 1", "A = 2")])
    assert [d.path for d in diff] == ["a.py"]
    await sess.accept()
    assert (real / "a.py").read_text() == "A = 2\n"
    await sess.close()


@pytest.mark.asyncio
async def test_unchanged_files_in_a_mixed_batch_are_not_promoted_or_recorded(
    tmp_path: Path,
) -> None:
    log = WorkspaceWriteLog()
    log.register_agent("agent-x")
    real, sess = _session(tmp_path, WriteGuard(log, "agent-x", "x", "general-purpose"))
    diff = await sess.apply([_sr("a.py", "A = 1", "A = 2"), _sr("b.py", "B = 1", "B = 1")])
    assert [d.path for d in diff] == ["a.py"]
    await sess.accept()
    assert log.files_changed_by({"agent-x"}) == ["a.py"]
    # b.py was never written, so a sibling that read b.py is not refused on it.
    log.register_agent("agent-y")
    assert log.stale_writer("agent-y", "b.py") is None
    await sess.close()


def test_the_model_is_told_its_edit_changes_nothing() -> None:
    exc = PatchPreflightFailed(
        "x", [PatchPreflightIssue(code=PatchFailureCode.NO_OP, file="a.py", message="m")])
    assert "changes nothing" in _edit_failure_guidance(exc)
