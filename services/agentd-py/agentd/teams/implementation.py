"""Implementation reports (spec v2 §8.6 step 4): a `completed` that changed none of the
member's files, or that leaves a direct message unanswered, and an `awaiting_peer` with
nobody to wait on, are sent back once. Redirects are not malformed actions."""
from __future__ import annotations

from collections.abc import Callable

from agentd.chat.controller_loop import ReportVerdict
from agentd.teams.service import ActivationCounters
from agentd.teams.store import TeamStore

Check = Callable[[dict[str, object], bool], ReportVerdict]


def chain_checks(*checks: Check) -> Check:
    def run(resp: dict[str, object], final: bool) -> ReportVerdict:
        for check in checks:
            verdict = check(resp, final)
            if verdict.message is not None:
                return verdict
        return ReportVerdict()
    return run


class ImplementationReport:
    """One per member activation; the redirect is used at most once."""

    def __init__(self, store: TeamStore, team_id: str, label: str,
                 counters: ActivationCounters, changed_files: Callable[[], set[str]]) -> None:
        self._store = store
        self._team_id = team_id
        self._label = label
        self._counters = counters
        self._changed = changed_files
        self._redirected = False

    def _waiting_on(self) -> list[str]:
        posts = self._store.posts(self._team_id)
        waiting: list[str] = []
        for to, seq in self._counters.messaged:
            replied = any(p.author == to and p.seq > seq and (
                p.recipient == self._label or self._label in p.mentions) for p in posts)
            if not replied and to not in waiting:
                waiting.append(to)
        return waiting

    def __call__(self, resp: dict[str, object], final: bool) -> ReportVerdict:
        team = self._store.get_team(self._team_id)
        if final or self._redirected or team is None or team.phase != "IMPLEMENTING":
            return ReportVerdict()
        member = self._store.member(self._team_id, self._label)
        if member is None:
            return ReportVerdict()
        status = str(resp.get("status") or "completed")
        waiting = self._waiting_on()
        if status == "completed" and member.assignment and not member.assignment_done:
            owned = {str(f) for f in member.assignment.get("files", [])}
            unchanged = bool(owned) and not (owned & self._changed())
            if waiting or unchanged:
                self._redirected = True
                reason = (f"You are waiting on {', '.join(waiting)}" if waiting
                          else "Your assignment's files are unchanged")
                return ReportVerdict(message=(
                    f"{reason} — report awaiting_peer or partial instead, or finish the "
                    "work, then report again."))
        if status == "awaiting_peer" and not waiting:
            others_open = any(m.assignment and not m.assignment_done
                              for m in self._store.members(self._team_id)
                              if m.label != self._label)
            if not others_open:
                self._redirected = True
                return ReportVerdict(message=(
                    "You are not waiting on anyone — finish your part or report partial."))
        return ReportVerdict()
