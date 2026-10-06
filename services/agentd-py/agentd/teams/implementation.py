"""Implementation reports (spec v2 §8.6 step 4): a `completed` that changed none of the
member's files, or that leaves a direct message unanswered, and an `awaiting_peer` that does
not name a working member to wait on, are sent back once. Redirects are not malformed
actions. An accepted wait is kept in `waiting_on`: the coordinator wakes the member when
the one it waits on reports (found live, 2026-10-06: an unnamed wait woke nobody)."""
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
        self.waiting_on: list[str] = []   # the accepted awaiting_peer report's named wait

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
        if status == "awaiting_peer":
            refusal = self._named_wait(resp, waiting)
            if refusal is not None:
                self._redirected = True
                return ReportVerdict(message=refusal)
        return ReportVerdict()

    def _named_wait(self, resp: dict[str, object], waiting: list[str]) -> str | None:
        """Accept a wait only when it names members of this team that still have work."""
        members = {m.label: m for m in self._store.members(self._team_id)}
        raw = resp.get("waiting_on")
        named = [str(x).strip().lstrip("@").casefold()
                 for x in (raw if isinstance(raw, list) else [])]
        bad = [n for n in named if n not in members or n == self._label]
        if bad:
            others = ", ".join(lb for lb in members if lb != self._label)
            return (f"waiting_on names {', '.join(bad)}, which is not another member of this "
                    f"team ({others}). Name whom you wait on, then report again.")
        wait = [*named, *[w for w in waiting if w not in named]]
        if not wait:
            example = next((lb for lb in members if lb != self._label), "bob")
            return ("Name whom you wait on: add \"waiting_on\": [\"" + example + "\"] to this "
                    "report (they wake you when they report), or finish your part or report "
                    "partial.")
        finished = [n for n in named if (a := members[n]).assignment and a.assignment_done]
        if named and len(finished) == len(named) and not waiting:
            return (f"{', '.join(finished)} has already finished its part — read its changes, "
                    "then finish yours or report partial.")
        self.waiting_on = wait
        return None
