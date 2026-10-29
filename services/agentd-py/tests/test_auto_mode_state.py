"""Auto approval mode in backend state, routes and denial text (spec §6.1)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agentd.domain.models import ApprovalOutcome, ShellPolicy
from agentd.tools.approvals import denial_text
from tests.test_approval_mode import app_for, write
from tests.test_background_dispatch import _setup


def test_controller_accepts_auto_and_reports_capability(tmp_path, monkeypatch):
    ctrl, _, _ = _setup(tmp_path, monkeypatch, [], {})
    state = write(ctrl, auto=True, shell="auto")
    assert state.shell_policy == "auto"
    assert ctrl._shell_policy == ShellPolicy.AUTO
    assert state.shell_policies == ["ask", "allow_all", "auto"]


@pytest.mark.asyncio
async def test_put_approval_mode_accepts_auto(tmp_path, monkeypatch):
    ctrl, _, tid = _setup(tmp_path, monkeypatch, [], {})
    app = app_for(ctrl)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial = (await client.get("/v1/config")).json()["approval"]
        assert "auto" in initial["shell_policies"]
        resp = await client.put("/v1/chat/approval-mode", json={
            "auto_accept": True, "shell_policy": "auto", "resolve_waiting": False,
            "expected_process_id": initial["process_id"],
            "expected_version": initial["version"]})
        assert resp.status_code == 200 and resp.json()["shell_policy"] == "auto"
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()["approval"]
        assert live["shell_policy"] == "auto"


def test_policy_denial_text_carries_reason():
    text = denial_text(
        ApprovalOutcome.deny("policy", reason="recursive delete of your home folder"),
        subject="command `rm -rf ~`", user_text="unused")
    assert "recursive delete of your home folder" in text and "another way" in text


def test_policy_denial_without_reason_is_unchanged():
    text = denial_text(ApprovalOutcome.deny("policy"), subject="command `x`", user_text="unused")
    assert "Auto blocked" not in text


def test_allow_carries_note():
    assert ApprovalOutcome.allow(note="Auto: you asked").note == "Auto: you asked"
    assert ApprovalOutcome.allow().note is None
