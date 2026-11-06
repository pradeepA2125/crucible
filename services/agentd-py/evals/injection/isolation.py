"""A trial's isolated process environment and the command recorder (spec §3, §5).

Nothing a trial does may reach the user's real home, databases or trust records, and no
command an attack asks for may run. The shared pieces live in `evals.common`; this module
keeps the injection harness's own rules and its public names."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from evals.common import commands as _commands
from evals.common.commands import (
    ATTACKER_DOMAIN,
    NOT_RUN_OUTPUT,
    CommandRecord,
    workspace_fingerprint,
)
from evals.common.environment import applied_environment, isolated_environment
from evals.injection.models import Allow, Trial

__all__ = [
    "ATTACKER_DOMAIN", "NOT_RUN_OUTPUT", "CommandRecord", "CommandRecorder",
    "applied_environment", "command_may_run", "eval_environment", "workspace_fingerprint",
]


def eval_environment(
    real_env: Mapping[str, str], home: Path, trial: Trial, state_dir: Path,
) -> dict[str, str]:
    """The whole process environment for one trial."""
    scenario = trial.scenario
    return isolated_environment(real_env, home, state_dir, flags={
        # The native web tools reach the real internet from the unsandboxed eval process,
        # and are out of this eval's scope (spec §2); the fixture MCP server stands in.
        "CRUCIBLE_WEB_SEARCH_ENABLED": "0",
        "CRUCIBLE_WEB_FETCH_ENABLED": "0",
        "CRUCIBLE_CHAT_CONTROLLER": "1",
        "CRUCIBLE_MEMORY_ENABLED": "1",
        "CRUCIBLE_SUBAGENTS_ENABLED": "0",
        "CRUCIBLE_TEAMS_ENABLED": "0",
        "CRUCIBLE_EXEC_SESSIONS_ENABLED": "0",
        "CRUCIBLE_CONTROLLER_MAX_ITERS": "60",
        "CRUCIBLE_MCP_ENABLED": "1" if scenario.uses_mcp else "0",
        "CRUCIBLE_SKILLS_ENABLED": "1" if scenario.uses_skills else "0",
        "CRUCIBLE_INJECTION_DEFENSE": "1" if trial.arm.defense else "0",
    })


def command_may_run(line: str, allow: Allow, marker: str) -> bool:
    """Whether a careful user would let this exact line run (see evals.common.commands)."""
    return _commands.command_may_run(line, allow.commands, marker)


class CommandRecorder(_commands.CommandRecorder):
    def __init__(self, allow: Allow, marker: str = "", workspace: Path | None = None) -> None:
        super().__init__(allow.commands, marker, workspace)
