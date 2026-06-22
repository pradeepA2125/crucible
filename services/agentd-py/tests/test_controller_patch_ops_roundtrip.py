"""The controller edit path (TurnEditSession → apply_ops) must accept the richer
patch ops its schema now exposes — apply_diff and replace_range — not just
create_file/search_replace. apply_ops feeds dicts straight into PatchDocumentV2
(a pydantic discriminated union on `op`), so no conversion code is needed; these
tests guard that the controller's apply path actually applies them end-to-end.
"""
from pathlib import Path

import pytest

from agentd.patch.engine import PatchEngine
from agentd.patch.inline_apply import apply_ops


@pytest.mark.asyncio
async def test_replace_range_op_flows_through_controller_apply_ops(tmp_path: Path):
    f = tmp_path / "app.js"
    f.write_text("alpha\nbeta\ngamma\n")

    touched = await apply_ops(
        PatchEngine(),
        tmp_path,
        [
            {
                "op": "replace_range",
                "file": "app.js",
                "anchor": {"start_line": 2, "end_line": 2},
                "content": "BETA",
                "reason": "rewrite line 2",
            }
        ],
        allowed_files={"app.js"},
    )

    assert touched == ["app.js"]
    lines = f.read_text().splitlines()
    assert lines[0] == "alpha" and lines[-1] == "gamma"
    assert "BETA" in lines  # line 2 replaced; "beta" gone
    assert "beta" not in f.read_text()


@pytest.mark.asyncio
async def test_one_edit_combines_multiple_ops_across_types_and_files(tmp_path: Path):
    # A single edit's patch_ops list can combine multiple ops — mixed op types,
    # across multiple files — applied as one batch.
    a = tmp_path / "a.txt"
    a.write_text("one\ntwo\nthree\n")
    b = tmp_path / "b.txt"
    b.write_text("keep\nold\n")

    touched = await apply_ops(
        PatchEngine(),
        tmp_path,
        [
            {
                "op": "replace_range",
                "file": "a.txt",
                "anchor": {"start_line": 2, "end_line": 2},
                "content": "TWO",
                "reason": "rewrite a.txt line 2",
            },
            {
                "op": "search_replace",
                "file": "b.txt",
                "search": "old",
                "replace": "NEW",
                "reason": "swap in b.txt",
            },
        ],
        allowed_files={"a.txt", "b.txt"},
    )

    assert set(touched) == {"a.txt", "b.txt"}
    assert "TWO" in a.read_text() and "two" not in a.read_text()
    assert "NEW" in b.read_text() and "old" not in b.read_text()


@pytest.mark.asyncio
async def test_apply_diff_op_flows_through_controller_apply_ops(tmp_path: Path):
    f = tmp_path / "hello.py"
    f.write_text("def hello():\n    pass\n")

    touched = await apply_ops(
        PatchEngine(),
        tmp_path,
        [
            {
                "op": "apply_diff",
                "file": "hello.py",
                "diff": "@@ -1,2 +1,3 @@\n def hello():\n+    # TODO: implement\n     pass\n",
                "reason": "annotate",
            }
        ],
        allowed_files={"hello.py"},
    )

    assert touched == ["hello.py"]
    assert "# TODO: implement" in f.read_text()
