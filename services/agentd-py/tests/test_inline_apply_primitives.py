from pathlib import Path

import pytest

from agentd.patch.diffing import compute_diff_entries
from agentd.patch.engine import PatchEngine
from agentd.patch.inline_apply import apply_ops
from agentd.workspace.promote import promote_files


@pytest.mark.asyncio
async def test_apply_ops_diff_and_promote(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    shadow = tmp_path / "sh"
    shadow.mkdir()
    (shadow / "f.py").write_text("x = 1\n")
    touched = await apply_ops(
        PatchEngine(), shadow,
        [{"op": "search_replace", "file": "f.py", "search": "x = 1",
          "replace": "x = 2", "reason": "r"}],
        allowed_files={"f.py"},
    )
    assert touched == ["f.py"]
    entries = compute_diff_entries(real, shadow, touched, "k1")
    assert entries[0].path == "f.py" and entries[0].additions >= 1
    promote_files(shadow, real, touched)
    assert (real / "f.py").read_text() == "x = 2\n"
