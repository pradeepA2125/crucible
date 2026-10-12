"""A path_filter that names a directory and uses wildcards finds files under it.

search_code passes an absolute search root, so ripgrep matches a glob containing "/"
against absolute paths: "services/agentd-py/**/*.py" matched nothing. Live (2026-10-12),
"who calls interrupted_history?" got "no matches" for that filter; in a replay, 3 of 6
searches for every caller used that form."""
import shutil
from pathlib import Path

import pytest

from agentd.tools.search import search_code

# search_code shells out to ripgrep; the GitHub runner has none installed.
pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep (rg) not installed")


async def _search(root: Path, path_filter: str) -> str:
    out = await search_code(pattern="needle", path_filter=path_filter, context_lines=0,
                            fixed_strings=True, shadow_root=root)
    return out.output


def _tree(root: Path) -> None:
    for rel in ("pkg/a.py", "pkg/sub/b.py", "other/c.py"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle = 1\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("path_filter", ["pkg/**/*.py", "./pkg/**/*.py", "**/pkg/**/*.py"])
async def test_a_directory_glob_finds_files_under_that_directory(
    tmp_path: Path, path_filter: str,
) -> None:
    _tree(tmp_path)
    output = await _search(tmp_path, path_filter)
    assert "pkg/a.py" in output and "pkg/sub/b.py" in output
    assert "other/c.py" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("path_filter", ["pkg/**/*.py", "pkg/sub/b.py", "*.py"])
async def test_globs_do_not_depend_on_the_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_filter: str,
) -> None:
    """ripgrep matches globs against paths relative to its cwd. The backend runs from
    services/agentd-py, so "services/agentd-py/**/*.py" found 0 of 8 matches live: relative
    to that cwd, no path contains services/agentd-py."""
    _tree(tmp_path)
    monkeypatch.chdir(tmp_path / "pkg")
    assert "pkg/sub/b.py" in await _search(tmp_path, path_filter)


@pytest.mark.asyncio
async def test_a_missing_root_is_reported_as_missing_not_as_a_missing_ripgrep(
    tmp_path: Path,
) -> None:
    """ripgrep runs from the search root; starting a process in a directory that does not
    exist raises the same FileNotFoundError as a missing rg binary."""
    out = await search_code(pattern="needle", path_filter=None, context_lines=0,
                            fixed_strings=True, shadow_root=tmp_path / "gone")
    assert out.is_error
    assert "does not exist" in out.output and "ripgrep not found" not in out.output


@pytest.mark.asyncio
async def test_a_single_level_directory_glob_stays_at_that_level(tmp_path: Path) -> None:
    _tree(tmp_path)
    output = await _search(tmp_path, "pkg/*.py")
    assert "pkg/a.py" in output
    assert "other/c.py" not in output
