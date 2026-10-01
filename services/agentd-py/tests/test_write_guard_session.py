"""TurnEditSession + WriteGuard (spec §7.4): Check 1 at apply, Check 2 at accept."""
from pathlib import Path

import pytest

from agentd.chat.edit_session import StaleWriteError, TurnEditSession
from agentd.patch.engine import PatchEngine
from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, WriteGuard
from agentd.workspace.shadow import ShadowWorkspaceManager


def _sr(file: str, search: str, replace: str) -> list[dict[str, object]]:
    return [{"op": "search_replace", "file": file, "search": search, "replace": replace,
             "reason": "r"}]


def _session(tmp_path: Path, real: Path, log: WorkspaceWriteLog) -> TurnEditSession:
    return TurnEditSession(
        turn_id="guarded", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
        write_guard=WriteGuard(log, MAIN_AGENT_ID, "main", "main"))


def _workspace(tmp_path: Path) -> Path:
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    return real


@pytest.mark.asyncio
async def test_apply_refuses_a_file_a_sibling_changed_after_the_last_read(tmp_path: Path) -> None:
    real = _workspace(tmp_path)
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])
    sess = _session(tmp_path, real, log)
    with pytest.raises(StaleWriteError) as caught:
        await sess.apply(_sr("./f.py", "x = 1", "x = 2"))
    assert caught.value.path == "f.py"
    assert caught.value.writer_label == "impl"
    assert str(caught.value) == ("`f.py` was modified by agent `impl` (general-purpose) "
                                 "after your last read — read it again before editing.")
    assert log.stale_refusals(MAIN_AGENT_ID) == 1
    assert (real / "f.py").read_text() == "x = 1\n"
    await sess.close()


@pytest.mark.asyncio
async def test_after_a_reread_the_edit_lands_and_is_recorded(tmp_path: Path) -> None:
    real = _workspace(tmp_path)
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])
    log.note_read(MAIN_AGENT_ID, "f.py")
    sess = _session(tmp_path, real, log)
    await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 2\n"
    # main's promote is now unseen by a1: a1 editing f.py would be refused.
    record = log.stale_writer("a1", "f.py")
    assert record is not None and record.agent_id == MAIN_AGENT_ID
    await sess.close()


@pytest.mark.asyncio
async def test_accept_refuses_when_a_sibling_promoted_while_the_edit_was_held(
    tmp_path: Path,
) -> None:
    real = _workspace(tmp_path)
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    sess = _session(tmp_path, real, log)
    await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    # A sibling promotes f.py while this edit sits at a review gate.
    (real / "f.py").write_text("x = 1\ny = 9\n")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])
    with pytest.raises(StaleWriteError) as caught:
        await sess.accept()
    assert caught.value.writer_label == "impl"
    assert (real / "f.py").read_text() == "x = 1\ny = 9\n"  # the sibling's work survives
    # accept() restored the shadow; after a re-read the next edit builds on the sibling's.
    log.note_read(MAIN_AGENT_ID, "f.py")
    await sess.apply(_sr("f.py", "y = 9", "y = 10"))
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 1\ny = 10\n"
    await sess.close()


@pytest.mark.asyncio
async def test_an_unguarded_session_never_checks(tmp_path: Path) -> None:
    real = _workspace(tmp_path)
    sess = TurnEditSession(
        turn_id="plain", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine())
    await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 2\n"
    await sess.close()
