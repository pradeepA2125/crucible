from pathlib import Path

import pytest

from agentd.images import ImageAssetStore
from agentd.tools.images import read_image
from agentd.tools.registry import ToolOutput, ToolRegistry
from tests.test_image_assets import image_bytes


@pytest.mark.asyncio
async def test_read_image_native_refs_and_asset_reread(tmp_path: Path) -> None:
    (tmp_path / "sample.png").write_bytes(image_bytes())
    result = await read_image(path="sample.png", root=tmp_path)
    assert not result.is_error
    assert "MIME type: image/png" in result.output
    assert "data:" not in result.output
    assert "base64" not in result.output
    assert len(result.image_refs) == 1
    ref = result.image_refs[0]
    reread = await read_image(asset_id=ref["asset_id"], root=tmp_path)
    assert not reread.is_error
    assert reread.image_refs[0]["asset_id"] == ref["asset_id"]
    assert ImageAssetStore(tmp_path).resolve(ref)["width"] == 12


@pytest.mark.asyncio
async def test_read_image_rejects_traversal_spoofing_and_symlinks(tmp_path: Path) -> None:
    (tmp_path / "note.png").write_text("not an image")
    outside = tmp_path.parent / (tmp_path.name + "outside.png")
    outside.write_bytes(image_bytes())
    (tmp_path / "escape.png").symlink_to(outside)
    for path in ("../outside.png", "escape.png", "note.png"):
        result = await read_image(path=path, root=tmp_path)
        assert result.is_error
        assert result.image_refs == []
    assert "path traversal rejected" in (await read_image(
        path="escape.png", root=tmp_path,
    )).output


@pytest.mark.asyncio
async def test_read_image_rejects_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "large.jpg").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    result = await read_image(path="large.jpg", root=tmp_path)
    assert result.is_error
    assert "maximum supported size" in result.output


@pytest.mark.asyncio
async def test_exactly_one_selector(tmp_path):
    for args in ({}, {"path": "x", "asset_id": "x"}, {"asset_id": "../x"}):
        assert (await read_image(root=tmp_path, **args)).is_error


@pytest.mark.asyncio
async def test_shadow_read_stores_real_workspace_asset(tmp_path):
    real, shadow = tmp_path / "real", tmp_path / "shadow"
    real.mkdir()
    shadow.mkdir()
    (shadow / "sample.png").write_bytes(image_bytes())
    registry = ToolRegistry(shadow_root=shadow, real_workspace_path=real)
    registry.use_shadow_for_reads()
    result = await registry.execute("read_image", {"path": "sample.png"})
    assert not result.is_error
    asset_id = result.image_refs[0]["asset_id"]
    reread = await registry.execute("read_image", {"asset_id": asset_id})
    assert not reread.is_error
    assert not (shadow / ".crucible").exists()
    definition = next(item for item in registry.definitions() if item.name == "read_image")
    assert "asset_id" in definition.parameters["properties"]


def test_tooloutput_positional_fields_unchanged():
    result = ToolOutput("text", False, ["file"], "status", 2, {"read": True})
    assert result.delivered_read == {"read": True}
    assert result.image_refs == []
    result.image_refs.append({"asset_id": "x"})
    assert ToolOutput("other").image_refs == []