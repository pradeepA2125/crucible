"""Exact reactive decisions: real SQLite/router/controller, no provider execution."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.gate_claims import GateClaim, GateClaimError
from agentd.chat.models import ChatMessage, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "0")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path))
    store.append_message(thread.thread_id, ChatMessage(role="user", content="original goal"))
    handler = ChatController(
        workspace_path=str(tmp_path),
        thread_store=store,
        orchestrator=None,
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[]),
        broadcaster=EventBroadcaster(),
        retrieval_client=None,
    )
    handler._orchestrator = SimpleNamespace(_running_tasks=set())
    handler._approval_owned = True
    handler._plan_mode = True
    calls = []

    async def run_loop(*args, **kwargs):
        calls.append((args, kwargs))
        await asyncio.sleep(0)
        return None

    async def finish(*args, **kwargs):
        handler._broadcaster.broadcast(args[1], {"type": "chat_done", "payload": {}})

    async def run_user(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(handler, "_run_loop", run_loop)
    monkeypatch.setattr(handler, "_finish", finish)
    monkeypatch.setattr(handler, "_run_user_turn", run_user)
    app = FastAPI()
    app.include_router(
        build_router(InMemoryTaskStore(), handler._orchestrator, None, None, handler)
    )
    yield app, handler, store, thread.thread_id, tmp_path, calls
    store._conn.close()


def make_claim(f, kind="mode", value="implement", request_id="request-1"):
    _, handler, store, tid, root, _ = f
    gate = store.add_controller_gate(
        tid,
        PendingGate.new(
            "mode" if kind == "feedback" else kind,
            {
                "options": [{"mode": "implement", "label": "Implement"}],
                "question": "Which?",
                "plan_sketch": "agreed plan",
                "resume_phase": "PLAN",
            },
        ),
    )
    return GateClaim(
        version=1,
        request_id=request_id,
        process_id=handler.context_process_id(),
        workspace_path=str(root.resolve()),
        gate_id=gate.gate_id,
        kind=kind,
        value=value,
    )


async def post(f, claim, changes=None):
    app, _, _, tid, _, _ = f
    body = claim.model_dump() | (changes or {})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/v1/chat/threads/{tid}/gate-decision", json=body)


async def lookup(f, claim, changes=None):
    app, _, _, tid, _, _ = f
    query = claim.identity(tid)
    query.pop("version")
    query.pop("thread_id")
    query["nonce"] = "lookup-1"
    query.update(changes or {})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/v1/chat/threads/{tid}/gate-decision", params=query)


async def drain(f):
    tasks = list(f[1]._active_turns.values())
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,value",
    [("mode", "implement"), ("clarify", " answer 😀\n"), ("feedback", ' revise this é\\"\n')],
)
async def test_exact_claim_receipt_worker_and_duplicate(fixture, kind, value):
    f = fixture
    _, handler, store, tid, _, calls = f
    claim = make_claim(f, kind, value)
    response = await post(f, claim)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {**claim.identity(tid), "status": "accepted"}
    assert not any(
        g.gate_id == claim.gate_id for g in store.get_thread(tid).pending_controller_gates
    )
    # Exact retries retain acceptance, including while the original worker is active.
    assert (await post(f, claim)).json() == response.json()
    await drain(f)
    assert len(calls) == 1
    assert (await post(f, claim)).json() == response.json()
    assert len(calls) == 1
    assert (await lookup(f, claim)).json()["receipt"] == response.json()
    if kind == "mode":
        assert handler._plan_mode is False
        assert calls[0][0][2] == "agreed plan"
    if kind == "clarify":
        assert calls[0][1]["seed_history"][-1]["content"].endswith(value.strip())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"version": True},
        {"extra": True},
        {"value": 1},
        {"request_id": "bad request"},
        {"process_id": "old-process"},
        {"workspace_path": "/other"},
        {"gate_id": "old"},
        {"kind": "clarify"},
        {"value": " "},
        {"value": "create_task"},
        {"value": "edit"},
    ],
)
async def test_refusals_never_consume_or_launch_or_exit_plan(fixture, change):
    claim = make_claim(fixture)
    before = list(fixture[2]._conn.iterdump())
    response = await post(fixture, claim, change)
    assert response.status_code in (409, 422)
    assert list(fixture[2]._conn.iterdump()) == before
    assert not fixture[1]._active_turns and not fixture[5]
    assert fixture[1]._plan_mode is True


@pytest.mark.asyncio
async def test_changed_payload_and_replacement_gate_are_not_accepted(fixture):
    claim = make_claim(fixture, "feedback", "first")
    assert (await post(fixture, claim)).status_code == 200
    await drain(fixture)
    replacement = fixture[2].add_controller_gate(fixture[3], PendingGate.new("mode", {}))
    assert (await post(fixture, claim, {"value": "second"})).status_code == 409
    assert (await post(fixture, claim, {"gate_id": replacement.gate_id})).status_code == 409
    assert (await post(fixture, claim, {"request_id": "new-request"})).status_code == 409
    assert [g.gate_id for g in fixture[2].get_thread(fixture[3]).pending_controller_gates] == [
        replacement.gate_id
    ]
    assert len(fixture[5]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    ["busy", "unowned", "task-mode", "unoffered", "foreign-thread", "empty-project", "subagent"],
)
async def test_authority_refusals(fixture, condition):
    _, handler, store, tid, _, _ = fixture
    claim = make_claim(fixture)
    if condition == "busy":
        handler._active_turns[tid] = asyncio.current_task()
    elif condition == "unowned":
        handler._approval_owned = False
    elif condition == "task-mode":
        handler._task_subsystem_enabled = True
    elif condition == "unoffered":
        store._conn.execute(
            "UPDATE chat_threads SET controller_gate_json = ? WHERE thread_id = ?",
            (json.dumps([{"gate_id": claim.gate_id, "kind": "mode", "payload": {}}]), tid),
        )
        store._conn.commit()
    elif condition == "foreign-thread":
        store._conn.execute(
            "UPDATE chat_threads SET workspace_path = '/other' WHERE thread_id = ?", (tid,)
        )
        store._conn.commit()
    elif condition == "empty-project":
        store._conn.execute(
            "UPDATE chat_threads SET workspace_path = '' WHERE thread_id = ?", (tid,)
        )
        store._conn.commit()
    else:
        raw = store.get_thread(tid).pending_controller_gates[0].model_dump()
        raw["agent"] = {"id": "a", "label": "a", "name": "a"}
        store._conn.execute(
            "UPDATE chat_threads SET controller_gate_json = ? WHERE thread_id = ?",
            (json.dumps([raw]), tid),
        )
        store._conn.commit()
    before = list(store._conn.iterdump())
    try:
        assert (await post(fixture, claim)).status_code == 409
        assert list(store._conn.iterdump()) == before
    finally:
        handler._active_turns.clear()


@pytest.mark.asyncio
async def test_feedback_preserves_replacement_and_unrelated_gates_before_worker(fixture):
    _, handler, store, tid, _, _ = fixture
    claim = make_claim(fixture, "feedback", "please revise")
    # No await: consume gate then insert replacement before detached worker starts.
    handler.claim_gate_decision(tid, claim)
    replacement = store.add_controller_gate(tid, PendingGate.new("mode", {}))
    unrelated = store.add_controller_gate(
        tid, PendingGate.new("clarify", {"question": "unrelated"})
    )
    await drain(fixture)
    assert {g.gate_id for g in store.get_thread(tid).pending_controller_gates} == {
        replacement.gate_id,
        unrelated.gate_id,
    }
    assert store.get_thread(tid).messages[-1].content == "please revise"


@pytest.mark.asyncio
async def test_claimed_mode_worker_cannot_reselect_or_remove_replacement(fixture):
    claim = make_claim(fixture)
    fixture[1].claim_gate_decision(fixture[3], claim)
    replacement = fixture[2].add_controller_gate(
        fixture[3], PendingGate.new("mode", {"plan_sketch": "wrong"})
    )
    await drain(fixture)
    assert fixture[5][0][0][2] == "agreed plan"
    assert (
        fixture[2].get_thread(fixture[3]).pending_controller_gates[0].gate_id == replacement.gate_id
    )


@pytest.mark.asyncio
async def test_launch_failure_is_durable_and_never_retried(fixture, monkeypatch):
    claim = make_claim(fixture)
    launches = []

    def fail(*args, **kwargs):
        launches.append(1)
        raise RuntimeError("test launch registration failure")

    monkeypatch.setattr(fixture[1], "launch_turn", fail)
    result = (await post(fixture, claim)).json()
    assert result["status"] == "launch_failed"
    assert (await lookup(fixture, claim)).json()["receipt"] == result
    assert (await post(fixture, claim)).json() == result
    assert launches == [1] and not fixture[2].get_thread(fixture[3]).pending_controller_gates


@pytest.mark.asyncio
async def test_read_only_lookup_mismatches_restart_eviction_and_no_leak(fixture):
    claim = make_claim(fixture)
    assert (await post(fixture, claim)).status_code == 200
    await drain(fixture)
    fixture[1]._context_usage.process_id = "new-process"
    assert (await post(fixture, claim)).status_code == 409
    before = list(fixture[2]._conn.iterdump())
    assert (await lookup(fixture, claim)).json()["found"] is True
    for change in (
        {"process_id": "new-process"},
        {"gate_id": "replacement"},
        {"kind": "feedback"},
        {"workspace_path": "/other"},
        {"payload_sha256": "0" * 64},
    ):
        result = (await lookup(fixture, claim, change)).json()
        assert result["found"] is False and result["receipt"] is None
    assert list(fixture[2]._conn.iterdump()) == before
    for index in range(64):
        other = make_claim(fixture, request_id=f"later-{index}")
        gate = next(
            g
            for g in fixture[2].get_thread(fixture[3]).pending_controller_gates
            if g.gate_id == other.gate_id
        )
        fixture[2].claim_controller_gate(fixture[3], gate, other.identity(fixture[3]))
    assert (await lookup(fixture, claim)).json()["found"] is False  # unknown, not rejected
    app = fixture[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for url in ("/v1/chat/threads", f"/v1/chat/threads/{fixture[3]}"):
            body = (await client.get(url)).text
            assert "gate_receipts" not in body and "payload_sha256" not in body
        live = (await client.get(f"/v1/chat/threads/{fixture[3]}/live")).json()
        assert live["gate_claim_version"] == 1


def test_atomic_compare_and_swap_across_connections(fixture, monkeypatch):
    _, _, store, tid, root, _ = fixture
    claim = make_claim(fixture)
    gate = store.get_thread(tid).pending_controller_gates[0]
    second = ChatThreadStore(root / "chat.sqlite3")
    decode = store._gates_from_row

    def replace_after_read(row):
        old = decode(row)
        second.remove_controller_gate(tid, gate.gate_id)
        second.add_controller_gate(tid, PendingGate.new("mode", {}))
        return old

    monkeypatch.setattr(store, "_gates_from_row", replace_after_read)
    with pytest.raises(GateClaimError, match="gate_changed"):
        store.claim_controller_gate(tid, gate, claim.identity(tid))
    assert store.gate_receipt(tid, claim.request_id) is None
    assert len(second.get_thread(tid).pending_controller_gates) == 1
    second._conn.close()


def test_canonical_hash_keeps_original_unicode_controls_and_surrogates():
    value = 'é😀\x7f\u2028\\"\n\b'
    claim = GateClaim(
        version=1,
        request_id="r",
        process_id="p",
        workspace_path="/p",
        gate_id="g",
        kind="feedback",
        value=value,
    )
    expected = hashlib.sha256(
        json.dumps(["feedback", value], ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert claim.identity("t")["payload_sha256"] == expected
    assert expected == "bf44292d6695e02a5b5521a41985871c032809411e7abe91bbde4bea5a6f63b5"


@pytest.mark.asyncio
async def test_concurrent_exact_retries_launch_once(fixture):
    claim = make_claim(fixture)
    responses = await asyncio.gather(post(fixture, claim), post(fixture, claim))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    await drain(fixture)
    assert len(fixture[5]) == 1


@pytest.mark.asyncio
async def test_legacy_routes_still_dispatch_without_claim_fields(fixture):
    app, _, _, tid, _, calls = fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for kind, value in (("mode", "implement"), ("clarify", "answer")):
            make_claim(fixture, kind, value)
            response = await client.post(
                f"/v1/chat/threads/{tid}/{kind}-decision",
                json={"mode" if kind == "mode" else "answer": value},
            )
            assert response.status_code == 200 and "chat_done" in response.text
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_receipt_survives_actual_store_reopen_and_lookup_is_select_only(fixture):
    claim = make_claim(fixture)
    await post(fixture, claim)
    await drain(fixture)
    original = fixture[2]
    reopened = ChatThreadStore(fixture[4] / "chat.sqlite3")
    fixture[1]._store = reopened
    traces = []
    reopened._conn.set_trace_callback(traces.append)
    try:
        result = await lookup(fixture, claim)
        assert result.json()["found"] is True
        assert traces and all(sql.lstrip().startswith("SELECT") for sql in traces)
    finally:
        fixture[1]._store = original
        reopened._conn.close()


def test_cas_duplicate_from_other_connection_does_not_claim_new_work(fixture):
    _, _, store, tid, root, _ = fixture
    claim = make_claim(fixture)
    gate = store.get_thread(tid).pending_controller_gates[0]
    second = ChatThreadStore(root / "chat.sqlite3")
    try:
        first, created = second.claim_controller_gate(tid, gate, claim.identity(tid))
        assert created is True
        duplicate, created = store.claim_controller_gate(tid, gate, claim.identity(tid))
        assert created is False and duplicate == first
    finally:
        second._conn.close()


@pytest.mark.asyncio
async def test_malformed_offered_container_definitely_refuses_before_claim(fixture):
    claim = make_claim(fixture)
    store, tid = fixture[2], fixture[3]
    gate = store.get_thread(tid).pending_controller_gates[0]
    gate.payload["options"] = 1
    store._write_gates(tid, [gate])
    before = list(store._conn.iterdump())
    assert (await post(fixture, claim)).status_code == 409
    assert list(store._conn.iterdump()) == before
    assert not fixture[1]._active_turns
