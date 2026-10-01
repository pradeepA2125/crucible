"""Approval-callback types and truthful denial wording (spec §4.6.4).

Today's user-rejection strings stay byte-identical (each consumer passes its own as
user_text); timeouts and policy denials say what actually happened instead of blaming
a user who was never asked.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentd.domain.models import ApprovalOutcome

CommandApprovalCallback = Callable[[str, list[str], str], Awaitable[ApprovalOutcome]]
McpApprovalCallback = Callable[[str, str, dict[str, object]], Awaitable[ApprovalOutcome]]

_POLICY_TAIL = (
    " is not permitted for this agent (no remembered rule allows it); nobody was asked. "
    "Work without it or note the need in your report."
)


def denial_text(outcome: ApprovalOutcome, *, subject: str, user_text: str) -> str:
    """The tool-result text for a denied approval. `subject` is lower-case prose naming
    the call ("command `pytest -x`"); `user_text` is the consumer's existing wording."""
    if outcome.denied_by == "timeout":
        return f"No decision arrived in time; {subject} was not run."
    if outcome.denied_by == "policy":
        return subject[:1].upper() + subject[1:] + _POLICY_TAIL
    return user_text
