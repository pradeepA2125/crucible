from pathlib import Path

import pytest

from agentd.tools.sources import BuiltinToolSource


@pytest.mark.asyncio
async def test_builtin_source_lists_and_owns_and_executes(tmp_path: Path):
    src = BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {d.name for d in src.definitions()}
    assert "search_code" in names and "read_file" in names
    assert src.owns("read_file") is True
    assert src.owns("nonexistent") is False
    (tmp_path / "a.txt").write_text("hello world\n")
    out = await src.execute("read_file", {"path": "a.txt"})
    assert "hello world" in out.output and out.is_error is False
