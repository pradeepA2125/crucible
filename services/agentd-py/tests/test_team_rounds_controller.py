"""Teams run in rounds inside the controller (spec v2 §8.3)."""
from __future__ import annotations

import asyncio

import pytest

from tests.test_team_controller import REPORT, _make, _request, _settle

AGREE = {"type": "report", "thought": "checked", "summary": "P1 holds", "status": "completed",
         "stances": [{"proposal_id": "P1", "stance": "agree", "note": "checked the plan"}]}
POST_TO_BOB = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
               "args": {"text": "@bob the login route needs a test"}}


def _no_notice_turns(ctrl, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A milestone arms a main-agent notice turn; these tests read the notices directly.
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)


@pytest.mark.asyncio
async def test_agreement_on_the_report_adopts(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.adopted_proposal_id) == ("DONE", "P1")
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["adopted"]
    assert team_id not in ctrl._coordinators


@pytest.mark.asyncio
async def test_round_two_restarts_the_last_reporter(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    rounds = [e.payload["round"] for e in store.teams.activity(team_id)
              if e.kind == "round_started"]
    assert rounds == [1, 2, 3]
    for label in ("alice", "bob"):
        agent = store.get_agent(store.teams.member(team_id, label).agent_id)
        assert agent.activation_count == 3
    assert store.teams.get_team(team_id).phase == "DEADLOCKED"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["deadlock"]


@pytest.mark.asyncio
async def test_mid_round_post_is_held_not_delivered(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch,
                                     {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    activity = store.teams.activity(team_id)
    held = [e for e in activity if e.kind == "held"]
    assert held and held[0].payload["for"] == ["bob"] and held[0].payload["until_round"] == 2
    assert not [e for e in activity if e.kind in ("notified", "picked_up", "woke")]
    bob_inputs = [h[-1]["content"] for label, h, _, _ in engine.seen
                  if label == "bob" and h and h[-1].get("role") == "user"]
    assert "the login route needs a test" not in str(bob_inputs[0])     # round 1
    assert any("the login route needs a test" in str(c) for c in bob_inputs[1:])   # round 2


@pytest.mark.asyncio
async def test_deliberation_members_cannot_edit(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _no_notice_turns(ctrl, monkeypatch)
    seen_types: list[list[str]] = []
    original = engine.create_controller_step

    async def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen_types.append(list(kwargs.get("allowed_types") or []))
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", spy)
    await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    assert seen_types and all("edit" not in t for t in seen_types)


@pytest.mark.asyncio
async def test_user_stop_loses_quorum(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", slow)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await asyncio.sleep(0.05)
    alice = store.teams.member(team_id, "alice")
    await ctrl.stop_agent(tid, alice.agent_id)
    await _settle(ctrl)
    assert store.teams.get_team(team_id).phase == "FAILED"
    assert store.teams.member(team_id, "alice").in_quorum is False
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["member_lost", "ended"]
    wraps = {e.label: e.payload["status"] for e in store.teams.activity(team_id)
             if e.kind == "wrapped_up"}
    assert wraps == {"alice": "stopped", "bob": "stopped"}


@pytest.mark.asyncio
async def test_deadline_forces_a_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC", "1")
    calls = {"n": 0}

    def looking() -> dict[str, object]:   # a fresh path each time: no duplicate-call guard
        calls["n"] += 1
        return {"type": "tool_call", "thought": "keep looking", "tool": "read_file",
                "args": {"path": f"notes-{calls['n']}.txt"}}
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _no_notice_turns(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def stubborn(*args, **kwargs):  # type: ignore[no-untyped-def]
        label = getattr(kwargs.get("render_ctx"), "agent_label", "")
        if label == "alice" and kwargs.get("allowed_types") != ["report"]:
            await asyncio.sleep(0.2)
            return looking()
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", stubborn)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    for _ in range(40):
        await asyncio.sleep(0.1)
        if any(e.kind == "deadline" for e in store.teams.activity(team_id)):
            break
    await _settle(ctrl)
    assert any(e.kind == "deadline" and e.label == "alice"
               for e in store.teams.activity(team_id))
    alice_wrap = next(e for e in store.teams.activity(team_id)
                      if e.kind == "wrapped_up" and e.label == "alice")
    assert alice_wrap.payload["status"] == "partial"     # the forced-final report
