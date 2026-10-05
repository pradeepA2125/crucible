"""Teams inside the controller (spec v2 §7, Phase 4's interim activation policy)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition
from agentd.subagents.inbox import InboxItem
from agentd.teams.tools import CreateTeamRequest, TeamMemberSpec
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


class _Recording(ScriptedReasoningEngine):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.seen: list[tuple[str, list[dict[str, object]], list[str], dict[str, object]]] = []

    async def create_controller_step(self, plan_context, history, tool_definitions, **kwargs):  # type: ignore[no-untyped-def]
        label = getattr(kwargs.get("render_ctx"), "agent_label", "")
        names = [str(d.get("name")) for d in tool_definitions]
        self.seen.append((label, [dict(m) for m in history], names, dict(plan_context)))
        return await super().create_controller_step(
            plan_context, history, tool_definitions, **kwargs)


REPORT = {"type": "report", "thought": "t", "summary": "done here", "status": "completed"}
WAITING = {"type": "report", "thought": "t", "summary": "waiting on alice",
           "status": "awaiting_peer"}


def _make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
          scripts: dict[str, list[dict[str, object]]]):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = _Recording(None, [], controller_step_responses=[
        {"type": "submit_changes", "thought": "d", "summary": "done"}], agent_scripts=scripts)
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)
    return ctrl, store, tid, engine


def _request(kind: str = "proposal", agent: AgentDefinition | None = None,
             **over) -> CreateTeamRequest:
    gp = agent or BUILTIN_AGENTS["general-purpose"]
    base = dict(name="auth", goal="Add login",
                members=[TeamMemberSpec("alice", gp), TeamMemberSpec("bob", gp)],
                approval_gate=False, max_rounds=3, budget=160, kickoff_kind=kind,
                kickoff_text="Plan: alice does the API, bob the tests",
                kickoff_assignments=[], kickoff_shared_files=[],
                kickoff_mentions=[] if kind == "proposal" else ["alice"])
    base.update(over)
    return CreateTeamRequest(**base)


async def _settle(ctrl: ChatController) -> None:
    for _ in range(200):
        tasks = [h.task for h in ctrl._subagents.registry._handles.values()  # type: ignore[union-attr]
                 if h.task is not None and not h.task.done()]
        if not tasks:
            await asyncio.sleep(0)
            if not [h for h in ctrl._subagents.registry._handles.values()  # type: ignore[union-attr]
                    if h.task is not None and not h.task.done()]:
                return
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_create_team_rows_kickoff_and_activation(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [WAITING]})
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    team_id = str(result["team_id"])
    rows = {r.label: r for r in store.list_agents(tid)}
    assert set(rows) == {"alice", "bob"}
    assert all(r.team_id == team_id and r.dispatcher_id is None and r.depth == 1
               for r in rows.values())
    assert rows["bob"].status == "awaiting_peer"     # members keep it (no partial mapping)
    posts = store.teams.posts(team_id)
    first = posts[0]
    assert (first.seq, first.author, first.kind, first.round) == (1, "main", "proposal", 0)
    assert {p.text for p in posts if p.kind == "system"} == {
        "alice finished (completed)", "bob finished (awaiting_peer)"}
    assert store.teams.member(team_id, "alice").delivered_seq == 1
    alice_first = next(h for label, h, _, _ in engine.seen if label == "alice")
    assert "Plan: alice does the API" in str(alice_first[-1]["content"])
    assert any(m.type == "team_created" for m in store.get_thread(tid).messages)


@pytest.mark.asyncio
async def test_post_kickoff_wakes_only_mentions(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    await ctrl._create_team(tid, "turn1", _request(kind="post"))
    await _settle(ctrl)
    assert {label for label, *_ in engine.seen} == {"alice"}


@pytest.mark.asyncio
async def test_mention_wakes_an_idle_member_with_its_delta(tmp_path, monkeypatch) -> None:
    post_to_bob = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
                   "args": {"text": "@bob the login route needs a test"}}
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch,
                                     {"alice": [post_to_bob, REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request(kind="post"))
    await _settle(ctrl)
    bob_calls = [h for label, h, _, _ in engine.seen if label == "bob"]
    assert bob_calls, "bob was never woken by the mention"
    assert "the login route needs a test" in str(bob_calls[0][-1]["content"])
    assert "<<<agent-content author=\"alice (general-purpose)\"" in str(bob_calls[0][-1]["content"])
    team_id = str(result["team_id"])
    assert store.teams.member(team_id, "bob").delivered_seq >= 2


@pytest.mark.asyncio
async def test_post_to_running_member_lands_at_next_drain(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    team_id = str(result["team_id"])
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    ctrl._teams.post(team_id, "alice", "@bob new finding")   # type: ignore[union-attr]
    items = ctrl._drain_member(bob.agent_id, team_id, "bob")
    assert [i.kind for i in items] == ["team"]
    assert "new finding" in items[0].text
    assert store.teams.member(team_id, "bob").delivered_seq == store.teams.posts(team_id)[-1].seq
    # A second marker with nothing new behind it (woken twice) renders nothing.
    ctrl._subagents.deliver(bob.agent_id, InboxItem(kind="team", text="", wakes=True))
    assert ctrl._drain_member(bob.agent_id, team_id, "bob") == []


@pytest.mark.asyncio
async def test_wake_cap_stops_ping_pong(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request(kind="post"))  # wakes alice once
    await _settle(ctrl)
    team_id = str(result["team_id"])
    ctrl._teams.post(team_id, "bob", "@alice again")   # type: ignore[union-attr]
    await _settle(ctrl)
    system = [p.text for p in store.teams.posts(team_id) if p.kind == "system"]
    assert any("alice has been woken 1 time" in t for t in system)


@pytest.mark.asyncio
async def test_team_tools_survive_definition_filter(tmp_path, monkeypatch) -> None:
    narrow = AgentDefinition(name="narrow", description="reads only", permission="plan",
                             tools=frozenset({"read_file"}), persona="Read.")
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    await ctrl._create_team(tid, "turn1", _request(agent=narrow))
    await _settle(ctrl)
    _, _, names, _ = next(s for s in engine.seen if s[0] == "alice")
    assert {"team_post", "team_agree", "team_object", "team_read"} <= set(names)
    assert "read_file" in names
    assert "run_command" not in names and "search_code" not in names


@pytest.mark.asyncio
async def test_live_team_limit_and_disband(tmp_path, monkeypatch) -> None:
    from agentd.teams.validation import TeamInputError
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    first = await ctrl._create_team(tid, "turn1", _request())
    await ctrl._create_team(tid, "turn1", _request(name="two"))
    with pytest.raises(TeamInputError, match="2 live teams"):
        await ctrl._create_team(tid, "turn1", _request(name="three"))
    await _settle(ctrl)
    out = await ctrl.disband_team(tid, str(first["team_id"]))
    assert out["phase"] == "DISBANDED"
    assert store.teams.get_team(str(first["team_id"])).phase == "DISBANDED"


@pytest.mark.asyncio
async def test_reap_fails_live_teams(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    ctrl.reap_subagents()
    assert store.teams.get_team(str(result["team_id"])).phase == "FAILED"
