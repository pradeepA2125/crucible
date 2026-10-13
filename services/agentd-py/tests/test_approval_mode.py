"""Versioned approval ownership, gate resolution and turn admission."""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.approval import ApprovalConflict, ApprovalManaged
from agentd.chat.models import GateAgent, PendingGate
from agentd.domain.models import ShellPolicy
from tests.test_background_dispatch import _setup


def write(ctrl, *, auto=False, shell="ask", resolve=False, process=None, version=None):
    state = ctrl.approval_state()
    return ctrl.set_approval_mode(
        auto_accept=auto,
        shell_policy=ShellPolicy(shell),
        resolve_waiting=resolve,
        expected_process_id=process or state.process_id,
        expected_version=state.version if version is None else version,
    )


def app_for(ctrl):
    orch = ctrl._orchestrator
    app = FastAPI()
    app.include_router(build_router(orch._store, orch, orch._workspace_manager, None, ctrl))
    return app


def test_ownership_version_and_conflicts(tmp_path, monkeypatch):
    ctrl, _, _ = _setup(tmp_path, monkeypatch, [], {})
    initial = ctrl.approval_state()
    assert initial.process_id == ctrl.context_process_id()
    assert not initial.owned and initial.version == 0
    assert write(ctrl).version == 1
    assert write(ctrl).version == 1
    assert write(ctrl, auto=True).version == 2
    for kw in ({"version": 1}, {"process": "another-process"}):
        with pytest.raises(ApprovalConflict) as err:
            write(ctrl, **kw)
        assert err.value.state == ctrl.approval_state()
    assert ctrl.approval_state().version == 2
    with pytest.raises(ApprovalManaged):
        ctrl.set_review_pref(auto_accept=False)
    assert ctrl.approval_state().auto_accept


@pytest.mark.asyncio
async def test_only_explicit_false_to_true_resolves_eligible_edits(tmp_path, monkeypatch):
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    write(ctrl)
    futures = []
    other = store.create_thread(str(tmp_path), title="background").thread_id
    for thread, payload, agent in (
        (tid, {}, None),
        (other, {}, GateAgent(id="a", label="bg", name="explore")),
        (other, {"protected": True}, None),
        (other, {"review_required": True}, None),
    ):
        gate = PendingGate.new("edit", payload, agent=agent)
        future = asyncio.get_running_loop().create_future()
        ctrl._pending_edit[gate.gate_id] = future
        ctrl._pending_edit_gates[gate.gate_id] = (thread, gate)
        futures.append(future)
    command = asyncio.get_running_loop().create_future()
    ctrl._pending_command["command"] = command
    write(ctrl, auto=True, resolve=False)
    assert not any(f.done() for f in futures)
    write(ctrl, auto=True, shell="allow_all", resolve=True)
    assert not any(f.done() for f in futures)
    write(ctrl, auto=False)
    result = write(ctrl, auto=True, resolve=True)
    assert result.auto_resolved_edits == 2 and result.background == 1
    assert [f.done() for f in futures] == [True, True, False, False]
    assert not command.done()
    futures[2].cancel()
    write(ctrl, auto=False)
    assert write(ctrl, auto=True, resolve=True).auto_resolved_edits == 0


@pytest.mark.asyncio
async def test_owned_policy_ignores_legacy_message_preferences(tmp_path, monkeypatch):
    ctrl, _, tid = _setup(
        tmp_path, monkeypatch, [{"type": "answer", "thought": "t", "answer": "ok"}], {}
    )
    write(ctrl, auto=False)
    state = ctrl.approval_state()
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}", step_review=False)
    assert ctrl.approval_state() == state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,body",
    [
        ("message", {"content": "go"}),
        ("mode-decision", {"mode": "edit"}),
        ("clarify-decision", {"answer": "yes"}),
        ("team-plan-decision", {"gate_id": "g", "decision": "approve"}),
    ],
)
async def test_admission_before_work_or_gate_consumption(tmp_path, monkeypatch, route, body):
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    app = app_for(ctrl)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for process in (ctrl.context_process_id(), "restarted"):
            before = store.get_thread(tid).model_dump()
            resp = await client.post(
                f"/v1/chat/threads/{tid}/{route}", json={**body, "expected_process_id": process}
            )
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "approval_not_confirmed"
            assert store.get_thread(tid).model_dump() == before
            assert tid not in ctrl._active_turns
        write(ctrl)
        resp = await client.post(
            f"/v1/chat/threads/{tid}/{route}", json={**body, "expected_process_id": "old"}
        )
        assert resp.status_code == 409 and resp.json()["detail"]["code"] == "approval_not_confirmed"


@pytest.mark.asyncio
async def test_api_readback_validation_and_legacy_writer_guard(tmp_path, monkeypatch):
    ctrl, _, tid = _setup(tmp_path, monkeypatch, [], {})
    app = app_for(ctrl)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial = (await client.get("/v1/config")).json()["approval"]
        body = {
            "auto_accept": False,
            "shell_policy": "ask",
            "resolve_waiting": False,
            "expected_version": initial["version"],
            "expected_process_id": initial["process_id"],
        }
        resp = await client.put("/v1/chat/approval-mode", json=body)
        assert resp.status_code == 200 and resp.json()["version"] == 1
        assert (await client.get(f"/v1/chat/threads/{tid}/live")).json()["approval"]["version"] == 1
        assert (await client.get("/v1/config")).json()["shell_policy"] == "ask"
        assert (await client.put("/v1/chat/approval-mode", json=body)).status_code == 409
        assert (
            await client.put("/v1/chat/review-pref", json={"auto_accept": True})
        ).status_code == 409
        for key, value in (
            ("shell_policy", "ignore"),
            ("auto_accept", "true"),
            ("expected_version", -1),
        ):
            assert (
                await client.put("/v1/chat/approval-mode", json={**body, key: value})
            ).status_code == 422


def test_legacy_version_and_owned_notice_queue(tmp_path, monkeypatch):
    ctrl, _, tid = _setup(tmp_path, monkeypatch, [], {})
    ctrl.set_review_pref(auto_accept=False)
    assert ctrl.approval_state().version == 1
    ctrl.set_review_pref(auto_accept=False)
    assert ctrl.approval_state().version == 1
    write(ctrl, auto=True)
    before = ctrl.approval_state()
    ctrl.queue_message(
        tid,
        "queued during a notice",
        message_id=None,
        step_review=True,
        plan_mode=None,
        mentioned_files=None,
        forced_skills=None,
    )
    assert ctrl.approval_state() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["default", "plan", "acceptEdits", "dontAsk"])
@pytest.mark.parametrize("shell", ["ask", "allow_all"])
async def test_child_command_permission_matrix_and_live_policy(
    tmp_path, monkeypatch, permission, shell
):
    from types import SimpleNamespace

    from agentd.domain.models import ApprovalOutcome
    from agentd.subagents.permissions import child_tool_names

    ctrl, _, tid = _setup(tmp_path, monkeypatch, [], {})
    calls = []

    async def gate(*args, **kwargs):
        calls.append(args)
        return ApprovalOutcome.allow()

    monkeypatch.setattr(ctrl, "_command_approval_cb", gate)
    child = SimpleNamespace(thread_id=tid, context=SimpleNamespace(no_ask=permission == "dontAsk"))
    tools = child_tool_names(
        ["run_command"], definition_tools=None, permission=permission, may_dispatch=False
    )
    write(ctrl, shell=shell)
    if permission == "plan":
        assert "run_command" not in tools
        assert not calls
        return
    result = await ctrl._child_command_approval_cb(child, "echo", ["ok"], str(tmp_path))
    assert result.approved == (shell == "allow_all" or permission != "dontAsk")
    assert len(calls) == (1 if shell == "ask" and permission != "dontAsk" else 0)
    # The same already-running child reads a newly applied policy on its next command.
    write(ctrl, shell="allow_all")
    assert (await ctrl._child_command_approval_cb(child, "echo", ["again"], str(tmp_path))).approved
    assert len(calls) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,body",
    [
        ("message", {"content": ""}),
        ("mode-decision", {"mode": "edit"}),
        ("clarify-decision", {"answer": "yes"}),
        ("team-plan-decision", {"gate_id": "g", "decision": "approve"}),
    ],
)
async def test_admission_accepts_owned_identity_and_keeps_older_clients(
    tmp_path, monkeypatch, route, body
):
    ctrl, _, _ = _setup(tmp_path, monkeypatch, [], {})
    if route in ("mode-decision", "clarify-decision"):
        ctrl._active_turns["missing"] = object()  # admission reaches the original busy-turn guard
    async with AsyncClient(
        transport=ASGITransport(app=app_for(ctrl)), base_url="http://test"
    ) as client:
        old = await client.post(f"/v1/chat/threads/missing/{route}", json=body)
        assert old.status_code in (400, 404, 409, 422)
        assert not isinstance(old.json().get("detail"), dict)
        write(ctrl)
        current = await client.post(
            f"/v1/chat/threads/missing/{route}",
            json={**body, "expected_process_id": ctrl.context_process_id()},
        )
        assert current.status_code == old.status_code
        assert current.json() == old.json()
