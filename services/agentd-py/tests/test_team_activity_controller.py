"""The controller writes the activity record (spec 2026-10-05 §4.3)."""
from __future__ import annotations

import asyncio

import pytest

from tests.test_team_controller import REPORT, _make, _request, _settle

POST_TO_BOB = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
               "args": {"text": "@bob the login route needs a test"}}
DM_TO_BOB = {"type": "tool_call", "thought": "ask bob", "tool": "team_message",
             "args": {"member": "bob", "text": "which file?"}}


def _kinds(store, team_id: str, label: str | None = None) -> list[str]:
    return [e.kind for e in store.teams.activity(team_id) if label in (None, e.label)]








@pytest.mark.asyncio
async def test_disband_closes_open_chapters(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    original = engine.create_controller_step

    async def slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", slow)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await asyncio.sleep(0.05)                      # both activations are now running
    await ctrl.disband_team(tid, team_id)
    await _settle(ctrl)
    wraps = {e.label: e.payload["status"] for e in store.teams.activity(team_id)
             if e.kind == "wrapped_up"}
    assert wraps == {"alice": "stopped", "bob": "stopped"}
    assert any(e.kind == "phase" and e.payload["phase"] == "DISBANDED"
               for e in store.teams.activity(team_id))


@pytest.mark.asyncio
async def test_reap_closes_live_members_and_fails_the_team(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    ctrl.reap_subagents()
    tail = store.teams.activity(team_id)
    assert tail[-1].kind == "phase" and tail[-1].payload["phase"] == "FAILED"


@pytest.mark.asyncio
async def test_activity_is_streamed_on_the_team_channel(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    sent: list[dict] = []
    original = ctrl._broadcaster.broadcast
    monkeypatch.setattr(ctrl._broadcaster, "broadcast",
                        lambda ch, ev: (sent.append(ev), original(ch, ev))[1])
    await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    acts = [e for e in sent if e.get("type") == "team_activity"]
    assert acts and [e["aseq"] for e in acts] == sorted(e["aseq"] for e in acts)
    assert acts[0]["payload"]["event"]["kind"] == "phase"


@pytest.mark.asyncio
async def test_kickoff_records_phase_round_handoffs_and_wrapups(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    events = store.teams.activity(team_id)
    assert events[0].kind == "phase" and events[0].payload["phase"] == "DELIBERATING"
    assert events[1].kind == "round_started" and events[1].payload["round"] == 1
    assert "woke" not in _kinds(store, team_id)            # round strips replace wakes
    took = next(e for e in events if e.kind == "took_up" and e.label == "alice")
    assert took.payload["posts"] == [1] and took.payload["from"] == ["main"]
    assert took.payload["round"] == 1 and took.activation == 1
    wrap = next(e for e in events if e.kind == "wrapped_up" and e.label == "alice")
    assert wrap.payload["status"] == "completed" and wrap.payload["report"] == "done here"
    assert wrap.payload["round"] == 1
    assert {"duration_ms", "tools", "posts", "messages", "stances"} <= set(wrap.payload)
    assert [p.kind for p in store.teams.posts(team_id)] == ["proposal"]


@pytest.mark.asyncio
async def test_mention_mid_round_is_held(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    held = next(e for e in store.teams.activity(team_id) if e.kind == "held")
    assert held.label == "alice" and held.cause_seq == 2
    assert held.payload == {"post_seq": 2, "for": ["bob"], "everyone": False, "until_round": 2}


@pytest.mark.asyncio
async def test_direct_message_is_held_for_its_recipient(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [DM_TO_BOB, REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    held = next(e for e in store.teams.activity(team_id) if e.kind == "held")
    assert held.payload["for"] == ["bob"] and held.payload["everyone"] is False


@pytest.mark.asyncio
async def test_running_member_is_notified_then_picks_up(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    post = store.teams.append_post(team_id, author="alice", kind="post", text="@bob new finding",
                                   mentions=["bob"])
    ctrl.wake_for_post(store.teams.get_team(team_id), post)   # outside deliberation
    ctrl._drain_member(bob.agent_id, team_id, "bob")
    kinds = _kinds(store, team_id, "bob")
    assert kinds[-2:] == ["notified", "picked_up"]
    picked = store.teams.activity(team_id)[-1]
    assert picked.payload["posts"] == [post.seq]


@pytest.mark.asyncio
async def test_wake_cap_is_an_event_not_a_post(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    post = store.teams.append_post(team_id, author="bob", kind="post", text="@alice again",
                                   mentions=["alice"])
    for _ in range(2):
        ctrl.wake_for_post(store.teams.get_team(team_id), post)
        await _settle(ctrl)
    capped = [e for e in store.teams.activity(team_id) if e.kind == "capped"]
    assert len(capped) == 1 and capped[0].payload == {"wakes": 2, "cap": 1}
    assert not [p for p in store.teams.posts(team_id) if p.kind == "system"]


@pytest.mark.asyncio
async def test_divider_carries_activation(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)                                 # three rounds, three activations
    bob = store.get_agent(store.teams.member(team_id, "bob").agent_id)
    dividers = [m.metadata.get("activation") for m in bob.transcript if m.metadata.get("divider")]
    assert dividers == [2, 3]
