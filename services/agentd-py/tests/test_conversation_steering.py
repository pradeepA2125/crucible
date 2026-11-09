"""Talking to a running turn: status replies, scoped corrections and their receipts."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_prompts import build_controller_step_payload
from agentd.chat.conversation import (
    CLARIFY_TEXT,
    STEER_EXPIRED_TEXT,
    STEER_INCORPORATED_TEXT,
    STEER_QUEUED_GATED_TEXT,
    STEER_QUEUED_TEXT,
    ConversationError,
    ConversationService,
    fallback_reply,
)
from agentd.domain.models import CommandDecision, ShellPolicy
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_background_dispatch import _setup, _tool

ANSWER = {"type": "answer", "thought": "t", "answer": "done"}


class FakeText:
    """A text transport whose reply (or delay) the test chooses."""

    def __init__(self, reply: str = "", *, delay: float = 0.0,
                 gate: asyncio.Event | None = None) -> None:
        self.reply, self.delay, self.gate = reply, delay, gate
        self.calls: list[dict[str, object]] = []

    async def generate_text(self, *, model: str, system_instructions: str,
                            user_payload: dict[str, object]) -> str:
        self.calls.append({"system": system_instructions, "payload": user_payload})
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


def _service(transport: FakeText | None = None) -> ConversationService:
    return ConversationService(
        transport_source=(lambda: (transport, "m")) if transport is not None else None)


def _snapshot() -> dict[str, object]:
    return {"phase": "ACTIVE", "recent_tools": [{"tool": "read_file", "path": "a.py"}],
            "todos": [], "waiting_on": [], "agents_running": 0, "tool_calls_so_far": 1}


async def _until(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


# --- service: scoping, receipts, intent --------------------------------------------------

@pytest.mark.asyncio
async def test_idle_and_wrong_turn_are_refused() -> None:
    svc = _service()
    with pytest.raises(ConversationError) as idle:
        await svc.converse("t1", "m1", "use tabs", "steer", "turn-a")
    assert idle.value.status_code == 409
    assert idle.value.detail == {"code": "turn_not_active", "active_turn_id": None}
    svc.open_turn("t1", "turn-a", _snapshot)
    with pytest.raises(ConversationError) as wrong:
        await svc.converse("t1", "m2", "use tabs", "steer", "turn-b")
    assert wrong.value.detail == {"code": "turn_not_active", "active_turn_id": "turn-a"}
    # A turn in another thread is not this thread's turn.
    with pytest.raises(ConversationError):
        await svc.converse("t2", "m3", "use tabs", "steer", "turn-a")
    assert svc.receipt("t1", "m2") is None


@pytest.mark.asyncio
async def test_retry_id_returns_the_same_receipt_and_conflicts_on_new_text() -> None:
    svc = _service()
    svc.open_turn("t1", "turn-a", _snapshot)
    first = await svc.converse("t1", "m1", "use tabs", "steer", "turn-a")
    again = await svc.converse("t1", "m1", "use tabs", "steer", "turn-a")
    assert again is first
    assert first.view() == {"message_id": "m1", "turn_id": "turn-a", "kind": "steer",
                            "text": STEER_QUEUED_TEXT, "status": "queued"}
    steering = svc.open_turn("t1", "turn-a", _snapshot)  # re-registering clears pending
    assert steering.drain() == []
    with pytest.raises(ConversationError) as conflict:
        await svc.converse("t1", "m1", "use spaces", "steer", "turn-a")
    assert conflict.value.detail["code"] == "message_id_conflict"  # type: ignore[index]
    # A retry after the turn ended still gets its original receipt, not a 409.
    svc.close_turn("t1", "turn-a")
    assert (await svc.converse("t1", "m1", "use tabs", "steer", "turn-a")) is first


@pytest.mark.asyncio
async def test_concurrent_retry_waits_for_the_in_flight_classification() -> None:
    gate = asyncio.Event()
    transport = FakeText("INTENT: question\nREPLY: Reading a.py.", gate=gate)
    svc = _service(transport)
    svc.open_turn("t1", "turn-a", _snapshot)
    one = asyncio.create_task(svc.converse("t1", "m1", "where are we?", "auto", "turn-a"))
    two = asyncio.create_task(svc.converse("t1", "m1", "where are we?", "auto", "turn-a"))
    await asyncio.sleep(0.01)
    gate.set()
    first, second = await asyncio.gather(one, two)
    assert first is second and len(transport.calls) == 1
    assert first.view()["kind"] == "reply" and first.view()["text"] == "Reading a.py."


@pytest.mark.asyncio
async def test_an_explicit_question_never_steers() -> None:
    transport = FakeText("INTENT: steer")
    svc = _service(transport)
    steering = svc.open_turn("t1", "turn-a", _snapshot)
    receipt = await svc.converse("t1", "m1", "switch to tabs?", "question", "turn-a")
    assert receipt.kind == "reply" and receipt.status == "answered"
    assert steering.drain() == [] and not steering.has_pending()
    # The reply call is given the snapshot as data, never tool outputs.
    payload = transport.calls[0]["payload"]
    assert payload == {"message": "switch to tabs?", "progress": _snapshot()}


@pytest.mark.asyncio
async def test_auto_steer_is_queued_and_unclear_asks_which() -> None:
    svc = _service(FakeText("INTENT: steer\nREPLY:"))
    steering = svc.open_turn("t1", "turn-a", _snapshot)
    steer = await svc.converse("t1", "m1", "use the v2 api instead", "auto", "turn-a")
    assert (steer.kind, steer.status) == ("steer", "queued")
    assert steering.has_pending()
    svc.transport_source = lambda: (FakeText("INTENT: unclear"), "m")
    clarify = await svc.converse("t1", "m2", "the api", "auto", "turn-a")
    assert clarify.view() == {"message_id": "m2", "turn_id": "turn-a", "kind": "clarify",
                              "text": CLARIFY_TEXT, "status": "answered"}


@pytest.mark.asyncio
async def test_explicit_spoken_correction_does_not_wait_for_the_provider() -> None:
    transport = FakeText("INTENT: unclear")
    svc = _service(transport)
    steering = svc.open_turn("t1", "turn-a", _snapshot)
    receipt = await svc.converse(
        "t1", "m1", "Crucible, correction. Only discuss the empty name.", "auto", "turn-a")
    assert (receipt.kind, receipt.status) == ("steer", "queued")
    assert steering.has_pending() and transport.calls == []
    question = await svc.converse("t1", "m2", "Is a correction needed?", "auto", "turn-a")
    assert question.kind == "clarify" and len(transport.calls) == 1


@pytest.mark.asyncio
async def test_turn_rechecked_after_async_classification() -> None:
    gate = asyncio.Event()
    svc = _service(FakeText("INTENT: steer", gate=gate))
    svc.open_turn("t1", "turn-a", _snapshot)
    pending = asyncio.create_task(svc.converse("t1", "m1", "stop that", "auto", "turn-a"))
    await asyncio.sleep(0.01)
    svc.close_turn("t1", "turn-a")
    svc.open_turn("t1", "turn-b", _snapshot)   # a new turn must not receive it
    gate.set()
    receipt = await pending
    assert (receipt.kind, receipt.status, receipt.reply) == ("steer", "expired",
                                                             STEER_EXPIRED_TEXT)
    assert svc._active["t1"].pending == []


@pytest.mark.asyncio
async def test_no_provider_falls_back_without_ever_steering(monkeypatch) -> None:
    svc = _service()
    steering = svc.open_turn("t1", "turn-a", _snapshot)
    status = await svc.converse("t1", "m1", "what's the status?", "auto", "turn-a")
    assert status.kind == "reply" and status.reply == fallback_reply(_snapshot())
    other = await svc.converse("t1", "m2", "use tabs", "auto", "turn-a")
    assert other.kind == "clarify" and not steering.has_pending()
    # A provider that times out behaves the same way.
    monkeypatch.setenv("CRUCIBLE_CONVERSATION_TIMEOUT_SEC", "0.05")
    svc.transport_source = lambda: (FakeText("INTENT: steer", delay=1.0), "m")
    slow = await svc.converse("t1", "m3", "use tabs", "auto", "turn-a")
    assert slow.kind == "clarify" and not steering.has_pending()
    asked = await svc.converse("t1", "m4", "how far along?", "question", "turn-a")
    assert asked.kind == "reply" and asked.reply == fallback_reply(_snapshot())


@pytest.mark.asyncio
async def test_turn_end_expires_pending_and_delivered_corrections() -> None:
    svc = _service()
    steering = svc.open_turn("t1", "turn-a", _snapshot)
    first = await svc.converse("t1", "m1", "use tabs", "steer", "turn-a")
    history: list[dict[str, object]] = [{"role": "user", "content": "go"}]
    history.extend(steering.drain())
    second = await svc.converse("t1", "m2", "and spaces", "steer", "turn-a")
    steering.release(history)            # loop exit with no decision after the drain
    svc.close_turn("t1", "turn-a")
    assert history == [{"role": "user", "content": "go"}]
    assert [first.status, second.status] == ["expired", "expired"]
    assert svc.active_turn_id("t1") is None


# --- controller: the loop's pre-dispatch handling -----------------------------------------

def _gated_steps(monkeypatch, on_call: Callable[[int, list[dict[str, object]]], object],
                 *, payloads: list[dict[str, object]] | None = None):
    """Wrap the scripted step so each call can block or inspect its input history."""
    scripted = ScriptedReasoningEngine.create_controller_step
    calls: list[list[dict[str, object]]] = []

    async def step(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        history = list(kwargs.get("history") or [])
        calls.append(history)
        if payloads is not None:
            payloads.append(build_controller_step_payload(
                kwargs["plan_context"], history, kwargs["tool_definitions"], phase=kwargs["phase"]))
        waited = on_call(len(calls), history)
        if asyncio.iscoroutine(waited):
            await waited
        return await scripted(self, *args, **kwargs)

    monkeypatch.setattr(ScriptedReasoningEngine, "create_controller_step", step)
    return calls


def _corrections(history: list[dict[str, object]]) -> list[str]:
    return [str(m["content"]) for m in history
            if m.get("role") == "user" and "Correction from the user" in str(m["content"])]


@pytest.mark.asyncio
async def test_correction_during_a_model_call_discards_its_unstarted_action(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("list_directory", path="."), ANSWER], {})
    hold, receipts = asyncio.Event(), {}

    async def on_call(n: int, history):  # type: ignore[no-untyped-def]
        if n == 1:
            await hold.wait()
        if n == 2:
            # The decision receives the correction; until it returns it is only queued.
            assert receipts["m1"].status == "queued"
            assert len(_corrections(history)) == 1

    payloads: list[dict[str, object]] = []
    calls = _gated_steps(monkeypatch, on_call, payloads=payloads)
    turn = asyncio.create_task(ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"))
    await _until(lambda: ctrl.active_turn_id(tid) is not None and len(calls) == 1)
    turn_id = ctrl.active_turn_id(tid)
    receipts["m1"] = await ctrl.converse(tid, "m1", "skip the listing", "steer", str(turn_id))
    hold.set()
    await turn
    assert len(calls) == 2
    assert "goal_corrections" not in payloads[0]
    assert payloads[1]["goal"] == "go"
    corrections = payloads[1]["goal_corrections"]
    assert isinstance(corrections, dict)
    assert corrections["messages"] == ["skip the listing"]
    assert "omit the superseded work and examples" in corrections["instruction"]
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert not any(m.get("tool") == "list_directory" for m in history)
    [entry] = _corrections(history)
    assert "skip the listing" in entry and "`tool_calls`" in entry
    receipt = ctrl.conversation_receipt(tid, "m1")
    assert receipt is not None and receipt.view() == {
        "message_id": "m1", "turn_id": turn_id, "kind": "steer",
        "text": STEER_INCORPORATED_TEXT, "status": "incorporated"}
    assert ctrl.active_turn_id(tid) is None


@pytest.mark.asyncio
async def test_correction_waits_for_an_approval_gate_and_never_resolves_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("run_command", command="echo hi"), ANSWER], {})
    ctrl._shell_policy = ShellPolicy.ASK
    seen: list[list[dict[str, object]]] = []
    _gated_steps(monkeypatch, lambda n, h: seen.append(h))
    turn = asyncio.create_task(ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"))

    def gate_pending() -> bool:
        thread = store.get_thread(tid)
        return thread is not None and any(
            g.kind == "command" for g in thread.pending_controller_gates)

    await _until(gate_pending)
    receipt = await ctrl.converse(tid, "m1", "also run the tests", "steer",
                                  str(ctrl.active_turn_id(tid)))
    assert (receipt.status, receipt.reply) == ("queued", STEER_QUEUED_GATED_TEXT)
    await asyncio.sleep(0.02)
    assert gate_pending() and not turn.done() and len(seen) == 1
    assert await ctrl.resolve_command(tid, CommandDecision(approve=True))
    await turn
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    # The command ran (its result precedes the correction), then the next decision saw it.
    tool_index = next(i for i, m in enumerate(history) if m.get("tool") == "run_command")
    correction_index = next(i for i, m in enumerate(history)
                            if "Correction from the user" in str(m.get("content")))
    assert tool_index < correction_index
    assert len(seen) == 2 and _corrections(seen[1])
    assert receipt.status == "incorporated"


@pytest.mark.asyncio
async def test_correction_at_turn_end_expires_and_starts_no_turn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    receipts = []
    original_run = ControllerLoop.run

    async def run_then_steer(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        outcome = await original_run(self, *args, **kwargs)
        # The loop has decided its last action; the turn is still registered.
        if self._agent is None:
            receipts.append(await ctrl.converse(
                tid, "late", "one more thing", "steer", str(ctrl.active_turn_id(tid))))
        return outcome

    monkeypatch.setattr(ControllerLoop, "run", run_then_steer)
    calls = _gated_steps(monkeypatch, lambda n, h: None)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [receipt] = receipts
    assert (receipt.status, receipt.reply) == ("expired", STEER_EXPIRED_TEXT)
    assert len(calls) == 1 and not ctrl._active_turns
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert not _corrections(history)
    assert [m.content for m in store.get_thread(tid).messages  # type: ignore[union-attr]
            if m.role == "user"] == ["go"]


@pytest.mark.asyncio
async def test_stopped_turn_drops_an_undecided_correction_from_history(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("list_directory", path="."), ANSWER, ANSWER], {})
    first, never = asyncio.Event(), asyncio.Event()

    async def on_call(n: int, _history):  # type: ignore[no-untyped-def]
        await (first.wait() if n == 1 else never.wait())

    calls = _gated_steps(monkeypatch, on_call)
    ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"),
                     channel_id=f"chat:{tid}")
    await _until(lambda: len(calls) == 1)
    receipt = await ctrl.converse(tid, "m1", "different plan", "steer",
                                  str(ctrl.active_turn_id(tid)))
    first.set()
    await _until(lambda: len(calls) == 2)   # the second call carries the correction
    assert receipt.status == "queued"
    assert await ctrl.stop_turn(tid)
    await _until(lambda: not ctrl._active_turns)
    assert receipt.status == "expired"
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert not _corrections(history)


@pytest.mark.asyncio
async def test_question_during_a_turn_uses_the_progress_snapshot(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _store, tid = _setup(tmp_path, monkeypatch, [
        _tool("list_directory", path="."), ANSWER], {})
    transport = FakeText("Listing the workspace.")
    ctrl._conversation.transport_source = lambda: (transport, "m")
    hold = asyncio.Event()

    async def on_call(n: int, _history):  # type: ignore[no-untyped-def]
        if n == 2:
            await hold.wait()

    calls = _gated_steps(monkeypatch, on_call)
    turn = asyncio.create_task(ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"))
    await _until(lambda: len(calls) == 2)
    receipt = await ctrl.converse(tid, "q1", "what are you doing?", "question",
                                  str(ctrl.active_turn_id(tid)))
    assert receipt.view()["text"] == "Listing the workspace."
    progress = transport.calls[0]["payload"]["progress"]  # type: ignore[index]
    assert progress["recent_tools"] == [{"tool": "list_directory", "path": "."}]
    assert progress["phase"] == "ACTIVE" and "output" not in str(progress)
    hold.set()
    await turn
    # A question never reaches the coding model's history.
    assert not any("what are you doing?" in str(m.get("content")) for m in calls[1])


# --- routes ---------------------------------------------------------------------------

def _app(ctrl) -> FastAPI:  # type: ignore[no-untyped-def]
    orch = ctrl._orchestrator
    app = FastAPI()
    app.include_router(build_router(orch._store, orch, orch._workspace_manager, None, ctrl))
    return app


def _own_approval(ctrl) -> str:  # type: ignore[no-untyped-def]
    state = ctrl.approval_state()
    ctrl.set_approval_mode(auto_accept=False, shell_policy=ShellPolicy.ASK,
                           resolve_waiting=False, expected_process_id=state.process_id,
                           expected_version=state.version)
    return str(ctrl.context_process_id())


@pytest.mark.asyncio
async def test_routes_validate_guard_and_serve_receipts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _store, tid = _setup(tmp_path, monkeypatch, [], {})
    url = f"/v1/chat/threads/{tid}/conversation"
    body = {"message_id": "m1", "text": "use tabs", "intent": "steer",
            "expected_turn_id": "turn-a", "expected_process_id": "p"}
    async with AsyncClient(transport=ASGITransport(app=_app(ctrl)),
                           base_url="http://test") as client:
        for broken in ({"intent": "maybe"}, {"text": ""}, {"message_id": 7},
                       {"expected_turn_id": None}, {"expected_process_id": ""}):
            assert (await client.post(url, json={**body, **broken})).status_code == 422
        refused = await client.post(url, json=body)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "approval_not_confirmed"
        process = _own_approval(ctrl)
        body["expected_process_id"] = process
        idle = await client.post(url, json=body)
        assert idle.status_code == 409
        assert idle.json()["detail"] == {"code": "turn_not_active", "active_turn_id": None}
        assert (await client.get(f"{url}/m1")).status_code == 404
        live = await client.get(f"/v1/chat/threads/{tid}/live")
        assert live.json()["active_turn_id"] is None
        ctrl._conversation.open_turn(tid, "turn-a", _snapshot)
        assert (await client.get(f"/v1/chat/threads/{tid}/live")).json()[
            "active_turn_id"] == "turn-a"
        queued = await client.post(url, json=body)
        assert queued.status_code == 200
        assert queued.json() == {"message_id": "m1", "turn_id": "turn-a", "kind": "steer",
                                 "text": STEER_QUEUED_TEXT, "status": "queued"}
        assert (await client.post(url, json=body)).json() == queued.json()
        ctrl._conversation.close_turn(tid, "turn-a")
        got = await client.get(f"{url}/m1")
        assert got.status_code == 200 and got.json()["status"] == "expired"
        assert (await client.get("/v1/chat/threads/other/conversation/m1")).status_code == 404
        missing = await client.post("/v1/chat/threads/nope/conversation", json=body)
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_voice_requires_idle_refuses_before_notice_queue_admission(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    url = f"/v1/chat/threads/{tid}/message"
    # A notice turn is running and would queue an ordinary message.
    ctrl._active_turns[tid] = object()
    ctrl._turn_kinds[tid] = "notice"
    ctrl._accepting_queued.add(tid)
    ctrl._conversation.open_turn(tid, "turn-n", _snapshot)
    assert ctrl.accepts_queued(tid)
    async with AsyncClient(transport=ASGITransport(app=_app(ctrl)),
                           base_url="http://test") as client:
        refused = await client.post(url, json={
            "content": "spoken", "message_id": "v1", "voice_requires_idle": True})
        assert refused.status_code == 409
        assert refused.json()["detail"] == {"code": "turn_active",
                                            "active_turn_id": "turn-n"}
        assert not any(m.id == "v1" for m in store.get_thread(tid).messages)  # type: ignore[union-attr]
        # Omitted (or not literally true): the notice queue behaves as before.
        for extra in ({}, {"voice_requires_idle": "true"}):
            queued = await client.post(url, json={
                "content": "typed", "message_id": f"q{len(extra)}", **extra})
            assert queued.status_code == 202 and queued.json()["queued"] is True
    ctrl._active_turns.pop(tid)
    ctrl._conversation.close_turn(tid, "turn-n")
