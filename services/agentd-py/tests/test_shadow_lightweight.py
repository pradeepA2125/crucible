from __future__ import annotations
import pytest
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_prepare_lightweight_copies_only_target_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("a = 1\n")
    (ws / "b.py").write_text("b = 2\n")
    (ws / "c.py").write_text("c = 3\n")
    sub = ws / "sub"
    sub.mkdir()
    (sub / "d.py").write_text("d = 4\n")

    mgr = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    shadow = await mgr.prepare_lightweight("task-lw1", str(ws), ["a.py", "sub/d.py"])

    assert (shadow.shadow_path / "a.py").exists()
    assert (shadow.shadow_path / "sub" / "d.py").exists()
    assert not (shadow.shadow_path / "b.py").exists()
    assert not (shadow.shadow_path / "c.py").exists()
    assert (shadow.shadow_path / "a.py").read_text() == "a = 1\n"
    assert (shadow.shadow_path / "sub" / "d.py").read_text() == "d = 4\n"


@pytest.mark.asyncio
async def test_prepare_lightweight_missing_file_is_skipped(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "exists.py").write_text("x = 1\n")

    mgr = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    shadow = await mgr.prepare_lightweight(
        "task-lw2", str(ws), ["exists.py", "does_not_exist.py"]
    )

    assert (shadow.shadow_path / "exists.py").exists()
    assert not (shadow.shadow_path / "does_not_exist.py").exists()


@pytest.mark.asyncio
async def test_prepare_lightweight_replaces_existing_shadow(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("v1\n")

    mgr = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    await mgr.prepare_lightweight("task-lw3", str(ws), ["a.py"])

    (ws / "a.py").write_text("v2\n")
    shadow = await mgr.prepare_lightweight("task-lw3", str(ws), ["a.py"])

    assert (shadow.shadow_path / "a.py").read_text() == "v2\n"
