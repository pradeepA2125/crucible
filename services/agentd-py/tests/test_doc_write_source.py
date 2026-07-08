"""DocWriteToolSource: allowlisted, per-write-gated writes of non-executable files
(docs/diagrams/data) to the REAL workspace — the lightweight alternative to EDIT mode."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.chat.doc_write_source import (
    DOC_WRITE_ALLOWED_EXTENSIONS,
    DocWriteToolSource,
    doc_write_decision_timeout_sec,
)


class _Recorder:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[str, bool, str]] = []

    async def __call__(self, path: str, exists: bool, preview: str) -> bool:
        self.calls.append((path, exists, preview))
        return self.result


def _src(tmp_path: Path, cb) -> DocWriteToolSource:
    return DocWriteToolSource(tmp_path, cb)


def test_definitions_and_owns(tmp_path: Path):
    src = _src(tmp_path, _Recorder())
    defs = src.definitions()
    assert [d.name for d in defs] == ["write_doc"]
    assert set(defs[0].parameters["required"]) == {"path", "content"}
    assert src.owns("write_doc") is True
    assert src.owns("read_file") is False


@pytest.mark.asyncio
async def test_approved_write_lands_in_real_workspace(tmp_path: Path):
    cb = _Recorder(result=True)
    out = await _src(tmp_path, cb).execute(
        "write_doc", {"path": "docs/notes.md", "content": "# hi\n"})
    assert out.is_error is False and "docs/notes.md" in out.output
    assert (tmp_path / "docs" / "notes.md").read_text(encoding="utf-8") == "# hi\n"
    (path, exists, preview) = cb.calls[0]
    assert path == "docs/notes.md" and exists is False and "# hi" in preview


@pytest.mark.asyncio
async def test_rejected_write_leaves_no_file(tmp_path: Path):
    out = await _src(tmp_path, _Recorder(result=False)).execute(
        "write_doc", {"path": "a.md", "content": "x"})
    assert out.is_error is True and "rejected" in out.output
    assert not (tmp_path / "a.md").exists()


@pytest.mark.asyncio
async def test_existing_file_gets_unified_diff_preview(tmp_path: Path):
    (tmp_path / "a.md").write_text("old line\n", encoding="utf-8")
    cb = _Recorder(result=True)
    await _src(tmp_path, cb).execute("write_doc", {"path": "a.md", "content": "new line\n"})
    (_, exists, preview) = cb.calls[0]
    assert exists is True
    assert "-old line" in preview and "+new line" in preview
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "new line\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["main.py", "run.sh", "x.tar.gz", "Makefile", "a.md.exe"])
async def test_disallowed_extensions_error_without_gate(tmp_path: Path, bad):
    cb = _Recorder()
    out = await _src(tmp_path, cb).execute("write_doc", {"path": bad, "content": "x"})
    assert out.is_error is True and "extension" in out.output.lower()
    assert cb.calls == []  # no gate raised


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", ["a.MD", "d/e.mermaid", "x.yaml", "x.yml", "x.csv", "x.svg"])
async def test_allowlist_is_case_insensitive_and_covers_data(tmp_path: Path, ok):
    out = await _src(tmp_path, _Recorder()).execute("write_doc", {"path": ok, "content": "x"})
    assert out.is_error is False


@pytest.mark.asyncio
@pytest.mark.parametrize("evil", ["../escape.md", "/etc/pwn.md"])
async def test_traversal_and_absolute_paths_rejected(tmp_path: Path, evil):
    cb = _Recorder()
    out = await _src(tmp_path, cb).execute("write_doc", {"path": evil, "content": "x"})
    assert out.is_error is True
    assert cb.calls == []
    assert not (tmp_path.parent / "escape.md").exists()


@pytest.mark.asyncio
async def test_oversize_content_rejected(tmp_path: Path):
    cb = _Recorder()
    out = await _src(tmp_path, cb).execute(
        "write_doc", {"path": "big.md", "content": "x" * (1_048_576 + 1)})
    assert out.is_error is True and "1 MB" in out.output
    assert cb.calls == []


def test_timeout_env_default_and_override(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", raising=False)
    assert doc_write_decision_timeout_sec() == 0.0
    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", "3.5")
    assert doc_write_decision_timeout_sec() == 3.5


def test_allowlist_constant_matches_spec():
    assert DOC_WRITE_ALLOWED_EXTENSIONS == frozenset({
        ".md", ".mmd", ".mermaid", ".txt", ".rst", ".adoc",
        ".svg", ".json", ".yaml", ".yml", ".csv"})
