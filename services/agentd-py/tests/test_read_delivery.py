
import pytest

from agentd.context_compaction import bounded_tree
from agentd.context_metrics import serialized_size
from agentd.tools.files import read_file
from agentd.tools.read_policy import ResultBudgetObserver, result_budget
from agentd.tools.registry import ToolRegistry
from agentd.tools.sources import BuiltinToolSource


@pytest.mark.asyncio
async def test_900_line_read_has_no_second_cap_and_complete_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_ADAPTIVE_READS_ENABLED", "1")
    (tmp_path / "file.py").write_text("\n".join(f"line {i}" for i in range(1000)))
    result = await ToolRegistry(tmp_path, tmp_path).execute(
        "read_file", {"path": "file.py", "start_line": 51, "end_line": 950}
    )
    assert len(result.output.splitlines()) == 900
    meta = result.delivered_read
    assert (meta.delivered_start, meta.delivered_end, meta.total_lines) == (51, 950, 1000)
    assert meta.truncated_by is None


@pytest.mark.asyncio
async def test_character_limit_does_not_deliver_partial_lines(tmp_path):
    (tmp_path / "file").write_text("first\n" + "x" * 5000 + "\nlast\n")
    result = await read_file(
        path="file", start_line=None, end_line=None, shadow_root=tmp_path, max_chars=600
    )
    assert "first" in result.output and "xxxxx" not in result.output
    assert len(result.output) <= 600
    assert result.delivered_read.delivered_end == 1
    assert result.delivered_read.truncated_by == "characters"


@pytest.mark.asyncio
async def test_empty_out_of_range_does_not_mark_read(tmp_path):
    (tmp_path / "file").write_text("first\n")
    observed = []
    source = BuiltinToolSource(
        shadow_root=tmp_path, real_workspace_path=tmp_path, read_observer=observed.append
    )
    result = await source.execute("read_file", {"path": "file", "start_line": 500})
    assert not result.is_error
    assert not result.delivered_read.has_content
    assert observed == []


@pytest.mark.asyncio
async def test_pathological_input_is_bounded_and_total_not_fabricated(tmp_path):
    (tmp_path / "file").write_text("x" * 3_000_000)
    result = await read_file(path="file", start_line=None, end_line=None, shadow_root=tmp_path)
    assert result.delivered_read.total_lines is None
    assert result.delivered_read.revision is None
    assert not result.delivered_read.has_content
    assert "input byte limit" in result.output


def test_tree_caps_are_deterministic_and_mark_expansion():
    tree = [f"directory-{i}/" for i in range(2000)]
    clipped = bounded_tree(tree)
    assert len(clipped) == 301
    assert serialized_size(clipped) <= 16_000
    assert "list_directory" in clipped[-1]
    assert bounded_tree(tree) == clipped
    assert bounded_tree(["a/"]) == ["a/"]


def test_budget_uses_this_physical_request_and_model_specific_capacity():
    context = {"context_capacity": 8000, "capacity_model": "small"}
    observer = ResultBudgetObserver(context)
    observer.begin("small", {"input": ["x" * 20000], "tools": [{"name": "foo"}]})
    assert 0 < context["tool_result_budget"] < 8000
    observer.reported(7000)
    assert context["tool_result_budget"] == 0
    assert result_budget(None, 100000) == 16000
    observer.begin("different-child", {})
    assert context["context_capacity"] is None
    assert context["tool_result_budget"] == 16000
