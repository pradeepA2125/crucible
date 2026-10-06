"""Implementation reports are checked before they are accepted (spec v2 §8.6 step 4)."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from agentd.chat.controller_loop import ReportVerdict
from agentd.teams.implementation import ImplementationReport, chain_checks
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import ActivationCounters
from agentd.teams.store import TeamStore

COMPLETED = {"type": "report", "summary": "s", "status": "completed"}
WAITING = {"type": "report", "summary": "s", "status": "awaiting_peer"}


def _store() -> TeamStore:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=3, budget=100, created_turn_id="u",
                                 created_at=datetime.now(UTC), phase="IMPLEMENTING"))
    for label in ("alice", "bob"):
        store.add_member(TeamMember(team_id="team-1", agent_id=f"a-{label}", label=label))
    store.set_assignment("team-1", "alice", {"member": "alice", "part": "api",
                                             "files": ["a.py"]})
    store.set_assignment("team-1", "bob", {"member": "bob", "part": "tests", "files": ["t.py"]})
    return store


def test_completed_with_unchanged_files_is_redirected_once() -> None:
    store = _store()
    check = ImplementationReport(store, "team-1", "alice", ActivationCounters(), lambda: set())
    first = check(COMPLETED, False)
    assert first.message is not None and "Your assignment's files are unchanged" in first.message
    assert check(COMPLETED, False) == ReportVerdict()          # second attempt accepted


def test_completed_with_changes_is_accepted() -> None:
    check = ImplementationReport(_store(), "team-1", "alice", ActivationCounters(),
                                 lambda: {"a.py"})
    assert check(COMPLETED, False) == ReportVerdict()


def test_completed_while_waiting_on_a_reply_is_redirected() -> None:
    store = _store()
    counters = ActivationCounters()
    sent = store.append_post("team-1", author="alice", kind="post", text="?",
                             recipient="bob", mentions=["bob"])
    counters.messaged.append(("bob", sent.seq))
    check = ImplementationReport(store, "team-1", "alice", counters, lambda: {"a.py"})
    verdict = check(COMPLETED, False)
    assert verdict.message is not None and "You are waiting on bob" in verdict.message


def test_a_reply_clears_the_wait() -> None:
    store = _store()
    counters = ActivationCounters()
    sent = store.append_post("team-1", author="alice", kind="post", text="?",
                             recipient="bob", mentions=["bob"])
    counters.messaged.append(("bob", sent.seq))
    store.append_post("team-1", author="bob", kind="post", text="done", recipient="alice",
                      mentions=["alice"])
    check = ImplementationReport(store, "team-1", "alice", counters, lambda: {"a.py"})
    assert check(COMPLETED, False) == ReportVerdict()


def test_awaiting_peer_with_nobody_to_wait_on_is_redirected() -> None:
    store = _store()
    store.set_assignment_done("team-1", "bob")
    check = ImplementationReport(store, "team-1", "alice", ActivationCounters(), lambda: set())
    verdict = check(WAITING, False)
    assert verdict.message is not None and "You are not waiting on anyone" in verdict.message


def test_awaiting_peer_while_another_member_works_is_accepted() -> None:
    check = ImplementationReport(_store(), "team-1", "alice", ActivationCounters(),
                                 lambda: set())
    assert check(WAITING, False) == ReportVerdict()


def test_final_iteration_and_other_phases_pass() -> None:
    store = _store()
    check = ImplementationReport(store, "team-1", "alice", ActivationCounters(), lambda: set())
    assert check(COMPLETED, True) == ReportVerdict()
    store.update_team("team-1", phase="DELIBERATING")
    assert check(COMPLETED, False) == ReportVerdict()


def test_chain_takes_the_first_refusal() -> None:
    calls: list[str] = []

    def no(_r, _f):  # type: ignore[no-untyped-def]
        calls.append("no")
        return ReportVerdict(message="no")

    def yes(_r, _f):  # type: ignore[no-untyped-def]
        calls.append("yes")
        return ReportVerdict()

    assert chain_checks(yes, no)(COMPLETED, False).message == "no"
    assert chain_checks(no, yes)(COMPLETED, False).message == "no"
    assert calls == ["yes", "no", "no"]
