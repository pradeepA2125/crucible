"""One add-on-only judge conversation per (thread, agent) — plan Task 12.

The context (AGENTS.md, the user's authorizations, the agent's actions) lives in the
conversation history and only ever grows: the first judgment sends all of it, later ones
append just what is new. The pending action and any check results ride the payload's
tail, which the ChatGPT-plan route appends rather than replaces (`SessionInputs`), so each
request's input starts with the previous one and the provider's prompt cache keeps it.
Verdicts are not replayed: they are context, not precedent (the policy says so), and
leaving them out keeps the history free of action-shaped items.

A session restarts when the thread's rewind generation changes or when a count shrinks
(compaction evicted history), so a rewound message can never keep authorizing.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from agentd.approvals.judge_context import JudgeContext
from agentd.approvals.types import PendingAction

HISTORY_KEY = "conversation_history"
CODEX_REVIEW_MODEL = "codex-auto-review"
CLAUDE_REVIEW_MODEL = "claude-haiku-5-5"


@dataclass
class JudgeSession:
    anchor: str
    history: list[dict[str, str]] = field(default_factory=list)
    _authorizations_sent: int = 0
    _actions_sent: int = 0

    def _restart(self) -> None:
        self.history.clear()
        self._authorizations_sent = 0
        self._actions_sent = 0

    def _sync(self, ctx: JudgeContext) -> None:
        if (ctx.authorization_total < self._authorizations_sent
                or ctx.action_total < self._actions_sent):
            self._restart()
        if not self.history:
            self.history.append({"role": "user", "content": json.dumps({
                "agents_md": ctx.agents_md,
                "authorizations": list(ctx.authorizations),
                "action_log": list(ctx.action_log),
            })})
        else:
            new_auth = _newest(ctx.authorizations,
                               ctx.authorization_total - self._authorizations_sent)
            new_actions = _newest(ctx.action_log, ctx.action_total - self._actions_sent)
            if new_auth or new_actions:
                update: dict[str, list[str]] = {}
                if new_auth:
                    update["new_authorizations"] = new_auth
                if new_actions:
                    update["new_actions"] = new_actions
                self.history.append({"role": "user", "content": json.dumps(update)})
        self._authorizations_sent = ctx.authorization_total
        self._actions_sent = ctx.action_total

    def payload(self, ctx: JudgeContext, action: PendingAction,
                checks: list[dict[str, str]], instruction: str = "") -> dict[str, object]:
        self._sync(ctx)
        payload: dict[str, object] = {HISTORY_KEY: list(self.history),
                                      "pending_action": asdict(action)}
        if checks:
            payload["checks"] = list(checks)
        payload["instruction"] = instruction or "Judge the pending action."
        return payload


def _newest(items: tuple[str, ...], count: int) -> list[str]:
    if count <= 0:
        return []
    return list(items[-count:])


class JudgeSessions:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], JudgeSession] = {}

    def get(self, thread_id: str, agent_id: str, anchor: str) -> JudgeSession:
        key = (thread_id, agent_id)
        session = self._sessions.get(key)
        if session is None or session.anchor != anchor:
            session = JudgeSession(anchor=anchor)
            self._sessions[key] = session
        return session

    def forget_thread(self, thread_id: str) -> None:
        for key in [k for k in self._sessions if k[0] == thread_id]:
            del self._sessions[key]


def preferred_judge_model(transport: Any, agent_model: str) -> str:
    """A smaller model on the plan logins, the agent's own model everywhere else.
    `CRUCIBLE_AUTO_JUDGE_MODEL` overrides; a preferred model the provider rejects is
    remembered by the judge and the agent's model used instead."""
    override = os.environ.get("CRUCIBLE_AUTO_JUDGE_MODEL", "").strip()
    if override:
        return override
    from agentd.providers.claude_subscription import ClaudeSubscriptionTransport
    from agentd.providers.openai_transport import OpenAIJsonTransport

    inner = getattr(transport, "wrapped", transport)
    if isinstance(inner, ClaudeSubscriptionTransport):
        return CLAUDE_REVIEW_MODEL
    if isinstance(inner, OpenAIJsonTransport) and getattr(inner, "_plan_route", False):
        return CODEX_REVIEW_MODEL
    return agent_model
