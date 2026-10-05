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
async def test_kickoff_records_phase_wakes_handoffs_and_wrapups(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    events = store.teams.activity(team_id)
    assert events[0].kind == "phase" and events[0].payload["phase"] == "DELIBERATING"
    for label in ("alice", "bob"):
        assert _kinds(store, team_id, label) == ["woke", "took_up", "wrapped_up"]
    woke = next(e for e in events if e.kind == "woke" and e.label == "alice")
    assert (woke.payload["cause"], woke.cause_seq, woke.activation) == ("kickoff", 1, 1)
    took = next(e for e in events if e.kind == "took_up" and e.label == "alice")
    assert took.payload["posts"] == [1] and took.payload["from"] == ["main"]
    wrap = next(e for e in events if e.kind == "wrapped_up" and e.label == "alice")
    assert wrap.payload["status"] == "completed" and wrap.payload["report"] == "done here"
    assert {"duration_ms", "tools", "posts", "messages", "stances"} <= set(wrap.payload)
    # The "finished" system posts are gone: the board holds only the kickoff.
    assert [p.kind for p in store.teams.posts(team_id)] == ["proposal"]


@pytest.mark.asyncio
async def test_mention_records_a_caused_wake(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    woke = [e for e in store.teams.activity(team_id) if e.kind == "woke" and e.label == "bob"]
    assert len(woke) == 1
    assert woke[0].payload["cause"] == "mention" and woke[0].payload["by"] == "alice"
    assert woke[0].cause_seq == 2 and woke[0].activation == 1


@pytest.mark.asyncio
async def test_direct_message_cause(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [DM_TO_BOB, REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    woke = next(e for e in store.teams.activity(team_id) if e.kind == "woke" and e.label == "bob")
    assert woke.payload["cause"] == "message"


@pytest.mark.asyncio
async def test_running_member_is_notified_then_picks_up(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    ctrl._teams.post(team_id, "alice", "@bob new finding")
    ctrl._drain_member(bob.agent_id, team_id, "bob")
    kinds = _kinds(store, team_id, "bob")
    assert kinds[-2:] == ["notified", "picked_up"]
    picked = store.teams.activity(team_id)[-1]
    assert picked.payload["posts"] == [store.teams.posts(team_id)[-1].seq]


@pytest.mark.asyncio
async def test_wake_cap_is_an_event_not_a_post(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    ctrl._teams.post(team_id, "bob", "@alice again")
    await _settle(ctrl)
    capped = [e for e in store.teams.activity(team_id) if e.kind == "capped"]
    assert len(capped) == 1 and capped[0].payload == {"wakes": 2, "cap": 1}
    assert not [p for p in store.teams.posts(team_id) if p.kind == "system"]


@pytest.mark.asyncio
async def test_divider_carries_activation(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)                                # both finished activation 1
    ctrl._teams.post(team_id, "alice", "@bob one more thing")
    await _settle(ctrl)                                # the mention woke bob: activation 2
    bob = store.get_agent(store.teams.member(team_id, "bob").agent_id)
    dividers = [m.metadata.get("activation") for m in bob.transcript if m.metadata.get("divider")]
    assert dividers == [2]


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
