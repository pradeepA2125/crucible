from pathlib import Path

import pytest

from agentd.chat.edit_session import TurnEditSession, _looks_double_escaped
from agentd.patch.engine import PatchEngine
from agentd.workspace.shadow import ShadowWorkspaceManager


def test_looks_double_escaped_boundary_cases():
    assert _looks_double_escaped("a\\nb\\nc\\n") is True  # 3 literal escapes, no real newline
    assert _looks_double_escaped("a\\nb\\n") is False  # only 2, below threshold
    assert _looks_double_escaped("a\nb\\nc\\nd\\n") is False  # has a real newline
    assert _looks_double_escaped("") is False
    assert _looks_double_escaped("short one-liner") is False


def _sr(file: str, search: str, replace: str) -> list[dict]:
    return [
        {"op": "search_replace", "file": file, "search": search, "replace": replace, "reason": "r"}
    ]


@pytest.mark.asyncio
async def test_accept_promotes_to_real_and_reject_restores(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sess = TurnEditSession(
        turn_id="t1",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
    )
    diff = await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    assert any(e.path == "f.py" for e in diff)
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 2\n"  # instant-promoted to real
    # reject leaves real untouched (patch applied to shadow only, not yet promoted)
    await sess.apply(_sr("f.py", "x = 2", "x = 999"))
    await sess.reject()
    assert (real / "f.py").read_text() == "x = 2\n"
    await sess.close()


@pytest.mark.asyncio
async def test_code_in_file_field_is_rejected_not_promoted(tmp_path: Path):
    """A weak model may put the file *body* in the 'file' (path) field. POSIX allows
    newlines in filenames, so without validation this creates a garbage-named file and
    promotes it to real disk under a false success. apply() must reject it instead."""
    real = tmp_path / "ws"
    real.mkdir()
    sess = TurnEditSession(
        turn_id="bad1",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    code = '"""Tax helper."""\n\n\ndef with_tax(price, rate):\n    return price * (1 + rate)\n'
    with pytest.raises(ValueError, match="content"):
        await sess.apply([{"op": "create_file", "file": code, "content": "src/tax.py"}])
    # nothing landed anywhere — no garbage-named file in real or shadow
    assert list(real.iterdir()) == []
    await sess.close()


@pytest.mark.asyncio
async def test_double_escaped_content_is_rejected_not_promoted(tmp_path: Path):
    """A model can produce technically-valid JSON where a string value is
    double-escaped: literal backslash-n/backslash-t sequences instead of real
    newline/tab characters. This passes JSON parsing and schema validation with no
    exception anywhere — confirmed live 2026-07-13 (Ollama Cloud / Nemotron): the
    debug artifact already showed this shape straight out of the transport, before
    any Crucible code touched it. Left unvalidated, apply() would silently write a
    real file that's one giant line of escaped text (0 real newlines) with a false
    "Applied" success. apply() must reject it instead."""
    real = tmp_path / "ws"
    real.mkdir()
    sess = TurnEditSession(
        turn_id="bad-escape",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    # Real fixture: a trimmed slice of the actual corrupted content observed live.
    broken_content = 'package commitlog\\n\\nimport (\\n\\t"encoding/binary"\\n\\t"errors"\\n)\\n'
    with pytest.raises(ValueError, match="double-escaped"):
        await sess.apply([{"op": "create_file", "file": "commitlog/log.go", "content": broken_content}])
    assert list(real.iterdir()) == []
    await sess.close()


@pytest.mark.asyncio
async def test_short_single_line_content_is_not_flagged(tmp_path: Path):
    """The double-escape heuristic must not false-positive on legitimate short,
    single-line content (no real newline is expected/fine for a one-liner)."""
    real = tmp_path / "ws"
    real.mkdir()
    sess = TurnEditSession(
        turn_id="ok-oneliner",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    diff = await sess.apply(
        [{"op": "create_file", "file": "VERSION", "content": "1.0.0", "reason": "r"}])
    assert any(e.path == "VERSION" for e in diff)
    await sess.close()


@pytest.mark.asyncio
async def test_content_with_real_newlines_and_a_literal_backslash_n_comment_is_not_flagged(
    tmp_path: Path,
):
    """A real multi-line file that happens to mention \\n as text (e.g. inside a
    comment or string literal about newlines) must not be flagged — the heuristic
    only fires when there is NO real newline anywhere."""
    real = tmp_path / "ws"
    real.mkdir()
    sess = TurnEditSession(
        turn_id="ok-mixed",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    content = (
        "package main\n\n"
        '// splitLines splits on \\n, \\n, \\n markers\n'
        "func splitLines(s string) []string { return nil }\n"
    )
    diff = await sess.apply(
        [{"op": "create_file", "file": "main.go", "content": content, "reason": "r"}])
    assert any(e.path == "main.go" for e in diff)
    await sess.close()


@pytest.mark.asyncio
async def test_op_missing_file_is_rejected(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    sess = TurnEditSession(
        turn_id="bad2",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    with pytest.raises(ValueError, match="file"):
        await sess.apply([{"op": "create_file", "content": "x = 1\n"}])
    await sess.close()


@pytest.mark.asyncio
async def test_op_absolute_or_traversal_path_rejected(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    sess = TurnEditSession(
        turn_id="bad3",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    with pytest.raises(ValueError, match="workspace-relative"):
        await sess.apply([{"op": "create_file", "file": "../escape.py", "content": "x = 1\n"}])
    await sess.close()


@pytest.mark.asyncio
async def test_reject_then_edit_different_file_keeps_invariant(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("a = 1\n")
    (real / "b.py").write_text("b = 1\n")
    sess = TurnEditSession(
        turn_id="t2",
        real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine(),
    )
    await sess.apply(_sr("a.py", "a = 1", "a = 9"))
    await sess.reject()  # a.py rejected
    await sess.apply(_sr("b.py", "b = 1", "b = 2"))
    await sess.accept()  # b.py accepted
    assert (real / "a.py").read_text() == "a = 1\n"  # untouched
    assert (real / "b.py").read_text() == "b = 2\n"  # promoted
    await sess.close()
