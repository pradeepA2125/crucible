"""Shared value types for Auto approval (spec §6.2–6.3)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

AutoDecisionKind = Literal["run", "ask", "deny"]
PendingActionKind = Literal["command", "session_start", "stdin", "mcp", "web_search", "web_fetch"]
ACTION_SUMMARY_MAX_CHARS = 2000
AGENT_REASON_MAX_CHARS = 500


@dataclass(frozen=True)
class AutoDecision:
    kind: AutoDecisionKind
    reason: str        # user-facing one line; "" for deterministic runs
    note: str | None   # pill note when the judge let the action run


@dataclass(frozen=True)
class PendingAction:
    kind: PendingActionKind
    summary: str        # e.g. "run_command: git push origin main"
    agent_reason: str   # the agent's thought for this call

    @classmethod
    def build(cls, kind: PendingActionKind, summary: str, agent_reason: str) -> PendingAction:
        return cls(kind, summary[:ACTION_SUMMARY_MAX_CHARS], agent_reason[:AGENT_REASON_MAX_CHARS])


@dataclass(frozen=True)
class JudgeRequest:
    thread_id: str
    agent_id: str       # "main" or the child's agent_id
    workspace: Path


class CommandJudge(Protocol):
    async def judge(self, request: JudgeRequest, action: PendingAction) -> AutoDecision: ...
