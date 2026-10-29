"""Auto mode's decision order for one action (spec §6.2): deny list → protected write →
remembered rules → safe list → judge. Only the deny list denies."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agentd.approvals.auto_rules import deny_reason, is_safe, rules_cover, writes_protected
from agentd.approvals.command_parse import ASSIGNMENT, parse_command
from agentd.approvals.types import (
    AutoDecision,
    CommandJudge,
    JudgeRequest,
    PendingAction,
    PendingActionKind,
)
from agentd.tools.command_rules import CommandRuleStore

PROTECTED_WRITE_REASON = "writes a protected file"
NO_JUDGE_REASON = "Needs your approval in Auto"
# Enter, Ctrl-C, Ctrl-D, Ctrl-Z, Escape, Tab and the arrow keys a prompt menu uses.
_CONTROL_KEYS = frozenset({"\n", "\r", "\r\n", "\x03", "\x04", "\x1a", "\x1b", "\t",
                           "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"})
# One word or number answering a prompt (y, n, 2, yes): no spaces, no punctuation.
_SIMPLE_ANSWER = re.compile(r"[A-Za-z0-9]{1,12}(\r?\n)?")
# Sessions whose input is itself a command line: every typed line goes to the judge.
_COMMAND_SESSIONS = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish", "tcsh", "csh", "pwsh", "powershell",
    "python", "python3", "ipython", "node", "deno", "bun", "irb", "ruby", "perl", "php",
    "lua", "R", "julia", "ghci", "scala", "sbt", "psql", "mysql", "sqlite3", "redis-cli",
    "mongosh", "ssh", "telnet", "nc", "docker", "kubectl", "su", "sudo",
})


def _runs_commands(session_command: str) -> bool:
    words = [w for w in session_command.split() if not ASSIGNMENT.match(w)]
    if words and Path(words[0]).name == "env":
        words = words[1:]
    if not words:
        return True  # unknown: treat as a shell
    name = Path(words[0]).name
    return name in _COMMAND_SESSIONS or name.startswith("python")


class AutoGate:
    def __init__(self, workspace: Path, home: Path, judge: CommandJudge | None) -> None:
        self._workspace = workspace
        self._home = home
        self.judge = judge

    async def decide_command(self, command: str, args: list[str], request: JudgeRequest,
                             agent_reason: str = "", cwd: str = "") -> AutoDecision:
        parsed = parse_command(command, args, self._workspace)
        reason = deny_reason(parsed, workspace=self._workspace, home=self._home)
        if reason is not None:
            return AutoDecision("deny", reason, None)
        if writes_protected(parsed, workspace=self._workspace, cwd=cwd):
            # These files feed the judge and the remembered rules: never self-approved.
            return AutoDecision("ask", PROTECTED_WRITE_REASON, None)
        if rules_cover(CommandRuleStore(self._workspace).load(), command, args, self._workspace):
            return AutoDecision("run", "", None)
        if is_safe(parsed, workspace=self._workspace, cwd=cwd):
            return AutoDecision("run", "", None)
        if self.judge is None:
            return AutoDecision("ask", NO_JUDGE_REASON, None)
        action = PendingAction.build("command", f"run_command: {parsed.line}", agent_reason)
        return await self.judge.judge(request, action)

    async def decide_stdin(self, session_command: str, chars: str,
                           request: JudgeRequest) -> AutoDecision:
        """Input to a running session. Control keys always run. In a shell or REPL every
        line is a command, so it is judged; elsewhere a one-word answer to a prompt (y, a
        number, a choice) runs and anything longer is judged."""
        if chars in _CONTROL_KEYS:
            return AutoDecision("run", "", None)
        if not _runs_commands(session_command) and _SIMPLE_ANSWER.fullmatch(chars):
            return AutoDecision("run", "", None)
        if self.judge is None:
            return AutoDecision("ask", NO_JUDGE_REASON, None)
        action = PendingAction.build("stdin", f"stdin to `{session_command}`: {chars!r}", "")
        return await self.judge.judge(request, action)

    async def decide_tool_call(self, kind: PendingActionKind, server: str, tool: str,
                               args: dict[str, object], request: JudgeRequest) -> AutoDecision:
        """MCP and web calls: no deny or safe list applies; the judge decides (a
        remembered rule is checked by the caller first)."""
        if self.judge is None:
            return AutoDecision("ask", NO_JUDGE_REASON, None)
        summary = f"{server}.{tool}({json.dumps(args, sort_keys=True, default=str)})"
        return await self.judge.judge(request, PendingAction.build(kind, summary, ""))
