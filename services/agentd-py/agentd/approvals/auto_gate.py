"""Auto mode's decision order for one action (spec §6.2): deny list → protected write →
remembered rules → safe list → judge. Only the deny list denies."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agentd.approvals.auto_rules import deny_reason, is_safe, rules_cover, writes_protected
from agentd.approvals.command_parse import parse_command
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
# One short line of plain words or numbers, as a prompt's answer: no shell punctuation.
_SIMPLE_ANSWER = re.compile(r"[A-Za-z0-9 _.,:+\-]{1,40}(\r?\n)?")


class AutoGate:
    def __init__(self, workspace: Path, home: Path, judge: CommandJudge | None) -> None:
        self._workspace = workspace
        self._home = home
        self.judge = judge

    async def decide_command(self, command: str, args: list[str], request: JudgeRequest,
                             agent_reason: str = "") -> AutoDecision:
        parsed = parse_command(command, args, self._workspace)
        reason = deny_reason(parsed, workspace=self._workspace, home=self._home)
        if reason is not None:
            return AutoDecision("deny", reason, None)
        if writes_protected(parsed, workspace=self._workspace):
            # These files feed the judge and the remembered rules: never self-approved.
            return AutoDecision("ask", PROTECTED_WRITE_REASON, None)
        if rules_cover(CommandRuleStore(self._workspace).load(), command, args, self._workspace):
            return AutoDecision("run", "", None)
        if is_safe(parsed, workspace=self._workspace):
            return AutoDecision("run", "", None)
        if self.judge is None:
            return AutoDecision("ask", NO_JUDGE_REASON, None)
        action = PendingAction.build("command", f"run_command: {parsed.line}", agent_reason)
        return await self.judge.judge(request, action)

    async def decide_stdin(self, session_command: str, chars: str,
                           request: JudgeRequest) -> AutoDecision:
        """Input to a running session. Its start was already approved, so a short plain
        answer (y, a number, Enter, Ctrl-C) runs; anything shell-like is judged."""
        if chars in _CONTROL_KEYS or _SIMPLE_ANSWER.fullmatch(chars):
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
