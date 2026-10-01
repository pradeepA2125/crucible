"""WorkspaceWriteLog (spec §7): attributed staleness for the shared workspace."""
from pathlib import Path

import pytest

from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, canonical_path


def test_canonical_path_normalizes_inside_and_rejects_outside(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    assert canonical_path(ws, "a/b.py") == "a/b.py"
    assert canonical_path(ws, "./a/b.py") == "a/b.py"
    assert canonical_path(ws, "a/../a/b.py") == "a/b.py"
    assert canonical_path(ws, str(ws / "a" / "b.py")) == "a/b.py"
    assert canonical_path(ws, "../outside.py") is None
    assert canonical_path(ws, str(tmp_path / "x.py")) is None
    assert canonical_path(ws, "") is None
    assert canonical_path(ws, ".") is None


def test_a_sibling_write_after_spawn_is_stale_until_reread() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.register_agent("a2")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    record = log.stale_writer("a2", "x.py")
    assert record is not None
    assert (record.agent_id, record.label, record.name) == ("a1", "impl", "general-purpose")
    log.note_read("a2", "x.py")
    assert log.stale_writer("a2", "x.py") is None


def test_own_writes_are_never_stale() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    assert log.stale_writer("a1", "x.py") is None


def test_writes_before_an_agent_spawned_are_not_stale_for_it() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    log.register_agent("late")
    assert log.stale_writer("late", "x.py") is None


def test_main_is_guarded_and_its_reads_persist_across_turns() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_read(MAIN_AGENT_ID, "x.py")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    assert log.stale_writer(MAIN_AGENT_ID, "x.py") is not None
    log.note_read(MAIN_AGENT_ID, "x.py")
    assert log.stale_writer(MAIN_AGENT_ID, "x.py") is None


def test_one_promote_is_one_sequence_step() -> None:
    log = WorkspaceWriteLog()
    before = log.seq
    assert log.note_promote(MAIN_AGENT_ID, "main", "main", ["a.py", "b.py"]) == before + 1
    assert log.seq == before + 1


def test_an_unregistered_agent_is_a_wiring_error() -> None:
    log = WorkspaceWriteLog()
    with pytest.raises(KeyError, match="ghost"):
        log.note_read("ghost", "x.py")
    with pytest.raises(KeyError, match="ghost"):
        log.note_promote("ghost", "g", "g", ["x.py"])


def test_stale_refusals_are_counted_per_agent() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    assert log.note_stale_refusal("a1") == 1
    assert log.note_stale_refusal("a1") == 2
    assert log.stale_refusals("a1") == 2
    assert log.stale_refusals(MAIN_AGENT_ID) == 0


def test_read_observer_canonicalizes_and_ignores_outside_paths(tmp_path: Path) -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    observe = log.read_observer(tmp_path, MAIN_AGENT_ID)
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    observe("./x.py")
    assert log.stale_writer(MAIN_AGENT_ID, "x.py") is None
    observe("../elsewhere.py")  # outside the workspace: silently not tracked
