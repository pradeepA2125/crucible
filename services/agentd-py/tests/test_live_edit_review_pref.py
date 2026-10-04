"""Live-mutable "Review each edit" preference for an in-flight controller turn.

The preference used to be frozen at turn start: ChatController._run_loop computed
`is_review` once and handed the loop a bool, so flipping the composer checkbox
mid-turn changed nothing until the next user message. These tests lock in the
task-side parity (orchestrator/task_control.py + POST /tasks/{id}/review-pref):
a mutable per-turn control the loop re-reads before EVERY edit, in both
directions, plus the pending-gate resolution when it flips to auto-accept.
"""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.chat.turn_control import ChatTurnControl
from agentd.domain.models import DiffEntry
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


def _two_edit_steps() -> list[dict]:
    """Two edits then a terminal, so a mid-turn flip has a second edit to govern."""
    return [
        {"type": "edit", "thought": "first", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        {"type": "edit", "thought": "second", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 2", "replace": "x = 3", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "bumped x twice"},
    ]


def _loop(tmp_path: Path, steps: list[dict]) -> tuple[ControllerLoop, Path]:
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    session = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    registry = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        registry, EventBroadcaster(), channel_id="c",
        phase_sm=ControllerPhaseSM(), edit_session_factory=lambda: session)
    return loop, real


# ── The loop re-reads the control before every edit ───────────────────────────

@pytest.mark.asyncio
async def test_flipping_to_auto_accept_mid_turn_skips_the_next_edits_gate(tmp_path: Path):
    """Review ON, user flips it OFF while the first edit is gated → the SECOND edit
    must promote without ever calling the decision cb (task /review-pref parity)."""
    loop, real = _loop(tmp_path, _two_edit_steps())
    control = ChatTurnControl(auto_accept_edits=False)
    gated: list[list[str]] = []

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        gated.append([d.path for d in diff])
        control.auto_accept_edits = True  # the user flips the composer checkbox
        return {"decision": "accept", "reason": ""}

    await loop.run(
        {"goal": "bump x", "workspace_path": str(real)}, max_iters=8,
        turn_control=control, edit_decision_cb=decide)

    assert gated == [["f.py"]], "only the first edit should have gated"
    assert (real / "f.py").read_text() == "x = 3\n", "both edits must have promoted"


@pytest.mark.asyncio
async def test_flipping_to_review_mid_turn_gates_the_next_edit(tmp_path: Path):
    """Auto-accept ON, user flips review back ON after the first edit lands → the
    SECOND edit must stop at the gate. Requires edit_decision_cb to be wired even
    when the turn started in auto-accept."""
    loop, real = _loop(tmp_path, _two_edit_steps())
    control = ChatTurnControl(auto_accept_edits=True)
    gated: list[list[str]] = []

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        gated.append([d.path for d in diff])
        return {"decision": "accept", "reason": ""}

    async def record(diff, decision, reason, was_gated) -> None:
        control.auto_accept_edits = False  # flipped after the first edit is recorded

    await loop.run(
        {"goal": "bump x", "workspace_path": str(real)}, max_iters=8,
        turn_control=control, edit_decision_cb=decide, edit_record_cb=record)

    assert gated == [["f.py"]], "the second edit should have gated after the flip"


@pytest.mark.asyncio
async def test_record_cb_receives_whether_each_edit_was_actually_gated(tmp_path: Path):
    """`was_gated` is per-edit, not frozen at turn start — otherwise a mid-turn-gated
    edit gets no "✓ Edit accepted" breadcrumb (and an auto-accepted one gets a false)."""
    loop, real = _loop(tmp_path, _two_edit_steps())
    control = ChatTurnControl(auto_accept_edits=True)
    recorded: list[bool] = []

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        return {"decision": "accept", "reason": ""}

    async def record(diff, decision, reason, was_gated) -> None:
        recorded.append(was_gated)
        control.auto_accept_edits = False

    await loop.run(
        {"goal": "bump x", "workspace_path": str(real)}, max_iters=8,
        turn_control=control, edit_decision_cb=decide, edit_record_cb=record)

    assert recorded == [False, True]


@pytest.mark.asyncio
async def test_static_auto_accept_edits_bool_still_governs_without_a_control(tmp_path: Path):
    """Back-compat: callers that pass no control keep the frozen-bool behavior."""
    loop, real = _loop(tmp_path, _two_edit_steps())
    gated: list[object] = []

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        gated.append(diff)
        return {"decision": "accept", "reason": ""}

    await loop.run(
        {"goal": "bump x", "workspace_path": str(real)}, max_iters=8,
        auto_accept_edits=True, edit_decision_cb=decide)

    assert gated == []
    assert (real / "f.py").read_text() == "x = 3\n"


# ── ChatController.set_review_pref ────────────────────────────────────────────

def _controller(tmp_path: Path, store: ChatThreadStore) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None)


@pytest.mark.asyncio
async def test_set_review_pref_sets_the_process_control(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    controller = _controller(tmp_path, store)
    controller._review_control.auto_accept_edits = False

    controller.set_review_pref(auto_accept=True)
    assert controller._review_control.auto_accept_edits is True


def _pending_edit(controller: ChatController, store: ChatThreadStore, thread_id: str,
                  payload: dict[str, object]) -> asyncio.Future[dict[str, object]]:
    gate = store.add_controller_gate(thread_id, PendingGate(kind="edit", payload=payload))
    future: asyncio.Future[dict[str, object]] = asyncio.get_event_loop().create_future()
    controller._pending_edit[gate.gate_id] = future
    controller._pending_edit_gates[gate.gate_id] = (thread_id, gate)
    return future


@pytest.mark.asyncio
async def test_flipping_to_auto_accept_resolves_a_pending_edit_gate(tmp_path: Path):
    """Mirrors /tasks/{id}/review-pref → resolve_pending_step_review: a diff already
    on screen is accepted, so the open gate can't contradict the switch just flipped."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    controller = _controller(tmp_path, store)
    controller._review_control.auto_accept_edits = False
    future = _pending_edit(controller, store, thread.thread_id, {})
    protected = _pending_edit(controller, store, thread.thread_id, {"protected": True})

    result = controller.set_review_pref(auto_accept=True)
    assert (result.auto_resolved, result.background) == (1, 0)
    assert future.done() and future.result()["decision"] == "accept"
    assert not protected.done()   # a protected path takes an explicit decision (§3.9)


@pytest.mark.asyncio
async def test_flipping_to_review_leaves_a_pending_gate_alone(tmp_path: Path):
    """The other direction only governs future edits — never resolves an open gate."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    controller = _controller(tmp_path, store)
    future = _pending_edit(controller, store, thread.thread_id, {})

    controller.set_review_pref(auto_accept=False)
    assert not future.done()


# ── PUT /v1/chat/review-pref ─────────────────────────────────────────────────

def _app(tmp_path: Path):
    """Minimal router over a real ChatController (mirrors test_rewind_routes._build)."""
    from fastapi import FastAPI

    from agentd.api.routes import build_router
    from agentd.domain.models import ValidationResult
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.storage.in_memory import InMemoryTaskStore

    class _Validator:
        async def run(self, workspace_path) -> ValidationResult:
            return ValidationResult(success=True, diagnostics=[], duration_ms=1)

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    task_store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    engine = ScriptedReasoningEngine(None, [])
    orch = AgentOrchestrator(
        store=task_store, reasoning_engine=engine, validator=_Validator(),
        patch_engine=PatchEngine(), workspace_manager=ws_manager, chat_store=chat_store)
    controller = ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=chat_store,
        orchestrator=orch, broadcaster=orch.broadcaster, retrieval_client=None)
    app = FastAPI()
    app.include_router(build_router(task_store, orch, ws_manager, None, controller))
    thread = chat_store.create_thread(str(ws), title="t")
    return app, controller, thread.thread_id


def _http(app):
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_review_pref_route_flips_the_process_control(tmp_path: Path):
    app, controller, _thread_id = _app(tmp_path)
    controller._review_control.auto_accept_edits = False
    async with _http(app) as client:
        r = await client.put("/v1/chat/review-pref", json={"auto_accept": True})
    assert r.status_code == 200
    assert r.json() == {"auto_resolved": 0, "background": 0}
    assert controller._review_control.auto_accept_edits is True


@pytest.mark.asyncio
async def test_the_per_thread_route_is_gone(tmp_path: Path):
    app, _controller, thread_id = _app(tmp_path)
    async with _http(app) as client:
        r = await client.post(f"/v1/chat/threads/{thread_id}/review-pref",
                              json={"auto_accept": True})
    assert r.status_code in (404, 405)


@pytest.mark.asyncio
async def test_pref_set_at_a_gate_is_honored_by_the_resumed_turn(tmp_path: Path):
    """A ModeGate/ClarifyGate pause ends the loop — yet it is exactly where a user reaches
    for "stop asking me about each edit". The resumed turn reads the process control, so
    a flip made with no turn running still governs it."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    controller = _controller(tmp_path, store)
    controller._review_control.auto_accept_edits = False

    controller.set_review_pref(auto_accept=True)
    assert controller._review_control.auto_accept_edits is True
