"""run_command executes via a real shell (not argv-exec) so the model can compose
pipes/redirects/chaining — user-directed 2026-07-13: the human-approval gate
(CRUCIBLE_SHELL_POLICY=ask) is the actual safety boundary, so the tool shouldn't
silently refuse valid shell syntax. Live-observed failure this fixes: a model
tried `xxd file | head -20` (args: ["file", "|", "head", "-20"]) and got a
literal-argument usage error from xxd instead of a real pipe.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentd.tools.shell import _build_shell_command_line, run_command


@pytest.mark.asyncio
async def test_pipe_operator_is_interpreted(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    out = await run_command(
        command="printf",
        args=["a\\nb\\nc\\n", "|", "wc", "-l"],
        shadow_root=tmp_path, real_workspace_path=real,
    )
    assert not out.is_error, out.output
    assert "3" in out.output


@pytest.mark.asyncio
async def test_redirect_operator_is_interpreted(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    out = await run_command(
        command="echo", args=["hello", ">", "out.txt"],
        shadow_root=tmp_path, real_workspace_path=real,
    )
    assert not out.is_error, out.output
    assert (tmp_path / "out.txt").read_text().strip() == "hello"


@pytest.mark.asyncio
async def test_chaining_operator_is_interpreted(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    out = await run_command(
        command="echo", args=["first", "&&", "echo", "second"],
        shadow_root=tmp_path, real_workspace_path=real,
    )
    assert not out.is_error, out.output
    assert "first" in out.output and "second" in out.output


@pytest.mark.asyncio
async def test_argument_with_spaces_stays_literal_no_operators(tmp_path: Path) -> None:
    """The common case (no shell operators) must behave exactly like before:
    one array element with a space is ONE argument, not shell-split."""
    real = tmp_path / "real"
    real.mkdir()
    out = await run_command(
        command="echo", args=["hello world"],
        shadow_root=tmp_path, real_workspace_path=real,
    )
    assert not out.is_error, out.output
    assert "hello world" in out.output


@pytest.mark.asyncio
async def test_argument_containing_shell_metacharacters_is_quoted_not_executed(
    tmp_path: Path,
) -> None:
    """A non-operator argument that merely CONTAINS a shell metacharacter (not
    equal to a recognized operator token) must stay a literal, safely-quoted
    string — only exact operator tokens as their OWN args entry get raw treatment."""
    real = tmp_path / "real"
    real.mkdir()
    out = await run_command(
        command="echo", args=["a;b|c"],
        shadow_root=tmp_path, real_workspace_path=real,
    )
    assert not out.is_error, out.output
    assert "a;b|c" in out.output


def test_build_shell_command_line_quotes_normal_args() -> None:
    assert _build_shell_command_line("echo", ["hi there"]) == "echo 'hi there'"


def test_build_shell_command_line_leaves_operators_raw() -> None:
    line = _build_shell_command_line("xxd", ["file.go", "|", "head", "-20"])
    assert line == "xxd file.go | head -20"


@pytest.mark.asyncio
async def test_timeout_kills_whole_pipeline_not_just_shell_wrapper(tmp_path: Path) -> None:
    """A pipeline forks a child per stage; the timeout kill must reach all of
    them (process-group kill), not just the shell wrapper's own PID — otherwise
    a hung pipeline stage leaks exactly like the pre-Fix#7 single-command case."""
    real = tmp_path / "real"
    real.mkdir()
    marker = tmp_path / "still-running-marker"
    # The second pipeline stage sleeps well past the timeout; if only the shell
    # wrapper got killed, `sleep 30` would survive and eventually touch marker.
    out = await run_command(
        command="echo", args=["x", "|", "sh", "-c", f"sleep 30; touch {marker}"],
        shadow_root=tmp_path, real_workspace_path=real,
        timeout_sec=1,
    )
    assert out.is_error
    assert "timed out" in out.output
    time.sleep(2)  # would-be marker-creation window if the child survived
    assert not marker.exists(), "pipeline stage survived the timeout kill"
