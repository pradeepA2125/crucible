"""search_code never returns Crucible's own state (its log, DBs, shadows, artifacts):
live run 4 got hits from .crucible/state/agentd.log, noise in the agent's context.

rg skips hidden directories on its own, but a positive -g glob overrides that: the
run's path_filter "*.*" matches the directory name ".crucible" itself."""
from pathlib import Path

import pytest

from agentd.tools.search import search_code


async def _search(root: Path, path_filter: str | None = None) -> str:
    out = await search_code(pattern="needle", path_filter=path_filter, context_lines=0,
                            fixed_strings=True, shadow_root=root)
    return out.output


@pytest.mark.asyncio
async def test_crucible_state_is_not_searched(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("needle = 1\n")
    state = tmp_path / ".crucible" / "state"
    state.mkdir(parents=True)
    (state / "agentd.log").write_text("needle in the log\n")
    output = await _search(tmp_path)
    assert "app.py" in output
    assert "agentd.log" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("path_filter", ["*.*", "*.log", ".crucible/**"])
async def test_crucible_state_stays_excluded_with_a_path_filter(
    tmp_path: Path, path_filter: str,
) -> None:
    state = tmp_path / ".crucible" / "state"
    state.mkdir(parents=True)
    (state / "agentd.log").write_text("needle\n")
    output = await _search(tmp_path, path_filter)
    assert "agentd.log" not in output


@pytest.mark.asyncio
async def test_a_path_filter_still_finds_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "notes.log").write_text("needle\n")
    assert "notes.log" in await _search(tmp_path, "*.*")

