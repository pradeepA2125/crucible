"""Auto mode's decision order for one action (spec §6.2): deny list → protected write →
remembered rules → safe list → judge. Only the deny list denies."""
from __future__ import annotations

from pathlib import Path

from agentd.approvals.auto_rules import deny_reason, is_safe, rules_cover, writes_protected
from agentd.approvals.command_parse import parse_command
from agentd.approvals.types import AutoDecision, CommandJudge, JudgeRequest, PendingAction
from agentd.tools.command_rules import CommandRuleStore

PROTECTED_WRITE_REASON = "writes a protected file"
NO_JUDGE_REASON = "Needs your approval in Auto"


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
