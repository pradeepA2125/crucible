"""search_code reports ripgrep failures as failures, and retries an invalid regex as text.

Live (2026-10-11 profile): `ToolDefinition(` came back as "No matches found. (stderr: regex
parse error ... unclosed group)", not as an error, and the model spent a whole model
round (~5 s) re-issuing it with fixed_strings."""
import shutil
from pathlib import Path

import pytest

from agentd.tools.search import search_code

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep (rg) not installed")


@pytest.mark.asyncio
async def test_an_invalid_regex_is_searched_as_literal_text(tmp_path: Path) -> None:
    (tmp_path / "tools.py").write_text("x = ToolDefinition(name='a')\n")
    out = await search_code(pattern="ToolDefinition(", path_filter=None, context_lines=0,
                            fixed_strings=False, shadow_root=tmp_path)
    assert not out.is_error
    assert "tools.py" in out.output and "1: x = ToolDefinition(name='a')" in out.output
    assert "not a valid regex" in out.output and "literal text" in out.output


@pytest.mark.asyncio
async def test_an_invalid_regex_with_no_literal_match_says_so(tmp_path: Path) -> None:
    (tmp_path / "tools.py").write_text("x = 1\n")
    out = await search_code(pattern="ToolDefinition(", path_filter=None, context_lines=0,
                            fixed_strings=False, shadow_root=tmp_path)
    assert not out.is_error
    assert "not a valid regex" in out.output and "No matches found" in out.output


@pytest.mark.asyncio
async def test_other_ripgrep_errors_are_errors(tmp_path: Path) -> None:
    (tmp_path / "tools.py").write_text("x = 1\n")
    out = await search_code(pattern="x", path_filter="{unclosed", context_lines=0,
                            fixed_strings=False, shadow_root=tmp_path)
    assert out.is_error
    assert out.output.startswith("Error: search failed")


@pytest.mark.asyncio
async def test_no_matches_is_still_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "tools.py").write_text("x = 1\n")
    out = await search_code(pattern="absent", path_filter=None, context_lines=0,
                            fixed_strings=False, shadow_root=tmp_path)
    assert not out.is_error and out.output == "No matches found."


@pytest.mark.asyncio
async def test_each_file_header_gives_its_line_count(tmp_path: Path) -> None:
    """So the model can read a whole file, or the right range, in one call."""
    (tmp_path / "tools.py").write_text("a\nneedle\nc\n")
    out = await search_code(pattern="needle", path_filter=None, context_lines=0,
                            fixed_strings=True, shadow_root=tmp_path)
    assert "--- tools.py (3 lines) ---" in out.output
