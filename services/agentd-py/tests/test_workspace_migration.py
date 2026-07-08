"""One-time legacy-dir migration: .ai-editor -> .crucible, .agentd -> .crucible/state."""
from pathlib import Path

from agentd.workspace_migration import migrate_legacy_dirs


def test_migrates_both_legacy_dirs(tmp_path: Path) -> None:
    (tmp_path / ".ai-editor" / "skills").mkdir(parents=True)
    (tmp_path / ".ai-editor" / "mcp.json").write_text("{}")
    (tmp_path / ".agentd" / "shadows").mkdir(parents=True)
    (tmp_path / ".agentd" / "chat.sqlite3").write_text("db")

    migrate_legacy_dirs(tmp_path)

    assert (tmp_path / ".crucible" / "mcp.json").read_text() == "{}"
    assert (tmp_path / ".crucible" / "skills").is_dir()
    assert (tmp_path / ".crucible" / "state" / "chat.sqlite3").read_text() == "db"
    assert not (tmp_path / ".ai-editor").exists()
    assert not (tmp_path / ".agentd").exists()


def test_noop_when_new_dirs_already_exist(tmp_path: Path) -> None:
    (tmp_path / ".crucible" / "state").mkdir(parents=True)
    (tmp_path / ".crucible" / "mcp.json").write_text("new")
    (tmp_path / ".ai-editor").mkdir()
    (tmp_path / ".ai-editor" / "mcp.json").write_text("old")
    (tmp_path / ".agentd").mkdir()

    migrate_legacy_dirs(tmp_path)

    # Existing new dirs win; legacy left in place, nothing clobbered.
    assert (tmp_path / ".crucible" / "mcp.json").read_text() == "new"
    assert (tmp_path / ".ai-editor" / "mcp.json").read_text() == "old"


def test_state_only_migration_creates_root(tmp_path: Path) -> None:
    (tmp_path / ".agentd").mkdir()
    (tmp_path / ".agentd" / "agentd.sqlite3").write_text("db")

    migrate_legacy_dirs(tmp_path)

    assert (tmp_path / ".crucible" / "state" / "agentd.sqlite3").read_text() == "db"


def test_never_raises_on_missing_workspace(tmp_path: Path) -> None:
    migrate_legacy_dirs(tmp_path / "does-not-exist")  # must not raise
