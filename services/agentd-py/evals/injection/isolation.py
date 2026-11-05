"""A trial's isolated process environment and the command recorder (spec §3, §5).

Nothing a trial does may reach the user's real home, databases or trust records, and no
command an attack asks for may run. The recorder replaces `agentd.tools.shell.run_command`
(the registry imports it at call time), so every command the agent gets past the approval
gate lands here first."""
from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from agentd.tools import shell
from agentd.tools.registry import ToolOutput
from evals.injection.models import Allow, Trial

ATTACKER_DOMAIN = ".attacker.example"
NOT_RUN_OUTPUT = "(not run in eval)"

# Provider settings a trial needs from the real environment. Every other CRUCIBLE_* variable
# is dropped: an inherited CRUCIBLE_DB_PATH or CRUCIBLE_SHELL_POLICY would point a trial at the
# user's real state or change what it measures.
_KEPT_CRUCIBLE = re.compile(
    r"CRUCIBLE_(REASONING_BACKEND|REASONING_EFFORT|CHATGPT_REGISTRATION"
    r"|(OPENAI|OPENAI_COMPAT|ANTHROPIC|GEMINI|GROQ|OLLAMA|OPENROUTER|WATSONX|HF|HUGGINGFACE"
    r"|TURBOQUANT)_\w+)"
)


def eval_environment(
    real_env: Mapping[str, str], home: Path, trial: Trial, state_dir: Path,
) -> dict[str, str]:
    """The whole process environment for one trial."""
    real_home = Path(real_env.get("HOME") or Path.home())
    env = {k: v for k, v in real_env.items()
           if not k.startswith("CRUCIBLE_") or _KEPT_CRUCIBLE.fullmatch(k)}
    scenario = trial.scenario
    env.update({
        "HOME": str(home),
        # Sign-in and model caches stay the user's real ones, by absolute path.
        "CRUCIBLE_CHATGPT_AUTH_DIR": real_env.get(
            "CRUCIBLE_CHATGPT_AUTH_DIR", str(real_home / ".crucible" / "auth" / "chatgpt")),
        "HF_HOME": real_env.get("HF_HOME", str(real_home / ".cache" / "huggingface")),
        "CRUCIBLE_CHAT_CONTROLLER": "1",
        "CRUCIBLE_MEMORY_ENABLED": "1",
        "CRUCIBLE_SUBAGENTS_ENABLED": "0",
        "CRUCIBLE_TEAMS_ENABLED": "0",
        "CRUCIBLE_EXEC_SESSIONS_ENABLED": "0",
        "CRUCIBLE_CONTROLLER_MAX_ITERS": "60",
        "CRUCIBLE_MCP_ENABLED": "1" if scenario.uses_mcp else "0",
        "CRUCIBLE_SKILLS_ENABLED": "1" if scenario.uses_skills else "0",
        "CRUCIBLE_INJECTION_DEFENSE": "1" if trial.arm.defense else "0",
        "CRUCIBLE_DB_PATH": str(state_dir / "agentd.sqlite3"),
        "CRUCIBLE_CHAT_DB_PATH": str(state_dir / "chat.sqlite3"),
        "CRUCIBLE_MEMORY_DB_PATH": str(state_dir / "memory.sqlite3"),
        "CRUCIBLE_SHADOW_ROOT": str(state_dir / "shadows"),
        "CRUCIBLE_LOG_FILE": str(state_dir / "agentd.log"),
    })
    return env


@contextmanager
def applied_environment(env: dict[str, str]) -> Iterator[None]:
    """Make `os.environ` exactly `env` for the duration, then restore it."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@dataclass(frozen=True)
class CommandRecord:
    line: str
    ran: bool
    exit_code: int | None


class CommandRecorder:
    """Records every command that reaches execution; really runs only allowed, harmless ones."""

    def __init__(self, allow: Allow) -> None:
        self._allow = allow
        self.records: list[CommandRecord] = []

    def _may_run(self, line: str) -> bool:
        # An attacker-shaped line never runs, whatever glob it happens to match.
        if ATTACKER_DOMAIN in line:
            return False
        return any(fnmatchcase(line, pattern) for pattern in self._allow.commands)

    @contextmanager
    def install(self) -> Iterator[None]:
        original = shell.run_command

        async def recording_run_command(
            *, command: str, args: list[str], real_workspace_path: Path, **kwargs: Any,
        ) -> ToolOutput:
            line = shell._build_shell_command_line(
                *shell._split_command(command, list(args), real_workspace_path))
            if not self._may_run(line):
                self.records.append(CommandRecord(line, ran=False, exit_code=None))
                return ToolOutput(output=NOT_RUN_OUTPUT, is_error=False)
            out = await original(command=command, args=args,
                                 real_workspace_path=real_workspace_path, **kwargs)
            self.records.append(CommandRecord(line, ran=True, exit_code=out.exit_code))
            return out

        shell.run_command = recording_run_command  # type: ignore[assignment]
        try:
            yield
        finally:
            shell.run_command = original  # type: ignore[assignment]
