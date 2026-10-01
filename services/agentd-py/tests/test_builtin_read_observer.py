"""BuiltinToolSource read tracking (spec §7.3): only a SUCCESSFUL read_file counts."""
from pathlib import Path

import pytest

from agentd.tools.sources import BuiltinToolSource


@pytest.mark.asyncio
async def test_only_successful_reads_are_observed(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    seen: list[str] = []
    src = BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path,
                            read_observer=seen.append)
    ok = await src.execute("read_file", {"path": "./f.py"})
    alias = await src.execute("read_file", {"file_path": "f.py"})
    missing = await src.execute("read_file", {"path": "nope.py"})
    await src.execute("search_code", {"pattern": "x"})
    assert not ok.is_error and not alias.is_error and missing.is_error
    assert seen == ["./f.py", "f.py"]


@pytest.mark.asyncio
async def test_no_observer_is_the_default(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    src = BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)
    assert not (await src.execute("read_file", {"path": "f.py"})).is_error
