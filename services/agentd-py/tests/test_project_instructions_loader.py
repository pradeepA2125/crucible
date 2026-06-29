from pathlib import Path

from agentd.instructions.loader import ProjectInstructionsLoader


def test_absent_file_returns_none(tmp_path: Path) -> None:
    assert ProjectInstructionsLoader(tmp_path).load() is None


def test_reads_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Always use tabs.", encoding="utf-8")
    assert ProjectInstructionsLoader(tmp_path).load() == "Always use tabs."


def test_blank_file_returns_none(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("   \n\t\n", encoding="utf-8")
    assert ProjectInstructionsLoader(tmp_path).load() is None


def test_mtime_cache_serves_cached_until_changed(tmp_path: Path) -> None:
    f = tmp_path / "AGENTS.md"
    f.write_text("v1", encoding="utf-8")
    loader = ProjectInstructionsLoader(tmp_path)
    assert loader.load() == "v1"
    # Rewrite with a forced-newer mtime so the change is detected deterministically.
    import os
    import time

    f.write_text("v2", encoding="utf-8")
    future = time.time() + 5
    os.utime(f, (future, future))
    assert loader.load() == "v2"


def test_oversize_is_truncated_with_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_EDITOR_INSTRUCTIONS_MAX_CHARS", "10")
    (tmp_path / "AGENTS.md").write_text("0123456789ABCDEF", encoding="utf-8")
    out = ProjectInstructionsLoader(tmp_path).load()
    assert out is not None
    assert out.startswith("0123456789")
    assert "truncated at 10 chars" in out


def test_disappearing_file_after_load_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "AGENTS.md"
    f.write_text("hi", encoding="utf-8")
    loader = ProjectInstructionsLoader(tmp_path)
    assert loader.load() == "hi"
    f.unlink()
    assert loader.load() is None
