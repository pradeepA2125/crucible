"""ApprovalOutcome (spec §4.6.4): who denied a command/MCP call, worded truthfully."""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ApprovalOutcome, CommandDecision, ShellPolicy
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource
from agentd.mcp.tool_source import McpToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.tools.approvals import denial_text
from agentd.tools.registry import ToolRegistry
from agentd.workspace.shadow import ShadowWorkspaceManager

_POLICY_TAIL = ("is not permitted for this agent (no remembered rule allows it); nobody was "
                "asked. Work without it or note the need in your report.")


def test_from_command_maps_approval_and_denier() -> None:
    ok = ApprovalOutcome.from_command(CommandDecision(approve=True))
    assert ok.approved is True and ok.denied_by is None and ok.decision is not None
    no = ApprovalOutcome.from_command(CommandDecision(approve=False), denied_by="timeout")
    assert no.approved is False and no.denied_by == "timeout"
    assert ApprovalOutcome.from_command(CommandDecision(approve=False)).denied_by == "user"
    assert ApprovalOutcome.allow().approved is True
    assert ApprovalOutcome.deny("policy").denied_by == "policy"


def test_denial_text_words_each_denier() -> None:
    user = denial_text(ApprovalOutcome.deny("user"), subject="command `ls`", user_text="U")
    timeout = denial_text(ApprovalOutcome.deny("timeout"), subject="command `ls`", user_text="U")
    policy = denial_text(ApprovalOutcome.deny("policy"), subject="command `ls`", user_text="U")
    assert user == "U"
    assert timeout == "No decision arrived in time; command `ls` was not run."
    assert policy == f"Command `ls` {_POLICY_TAIL}"


def _registry(tmp_path: Path, outcome: ApprovalOutcome) -> ToolRegistry:
    async def cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        return outcome
    return ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path,
                        command_approval_callback=cb)


@pytest.mark.asyncio
async def test_run_command_user_rejection_wording_is_unchanged(tmp_path: Path) -> None:
    out = await _registry(tmp_path, ApprovalOutcome.deny("user")).execute(
        "run_command", {"command": "pytest", "args": ["-x"]})
    assert out.is_error and out.output == (
        "Command rejected by user: pytest -x. Try a different approach (e.g. a static check).")


@pytest.mark.asyncio
async def test_run_command_timeout_and_policy_never_blame_the_user(tmp_path: Path) -> None:
    timeout = await _registry(tmp_path, ApprovalOutcome.deny("timeout")).execute(
        "run_command", {"command": "pytest", "args": ["-x"]})
    policy = await _registry(tmp_path, ApprovalOutcome.deny("policy")).execute(
        "run_command", {"command": "pytest", "args": ["-x"]})
    assert timeout.output == "No decision arrived in time; command `pytest -x` was not run."
    assert policy.output == f"Command `pytest -x` {_POLICY_TAIL}"
    assert "by user" not in timeout.output + policy.output


@pytest.mark.asyncio
async def test_start_session_is_approved_by_an_outcome_not_silently_denied(tmp_path: Path) -> None:
    async def cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        return ApprovalOutcome.from_command(CommandDecision(approve=True))
    src = ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)
    out = await src.execute("start_session", {"command": "echo", "args": ["hi"],
                                              "yield_time_ms": 2000})
    assert not out.is_error and "hi" in out.output


@pytest.mark.asyncio
async def test_start_session_timeout_wording(tmp_path: Path) -> None:
    async def cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        return ApprovalOutcome.deny("timeout")
    src = ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)
    out = await src.execute("start_session", {"command": "echo", "args": ["hi"]})
    assert out.is_error
    assert out.output == "No decision arrived in time; command `echo hi` was not run."


@pytest.mark.asyncio
async def test_mcp_timeout_and_policy_wording() -> None:
    async def timeout(server: str, tool: str, args: dict[str, object]) -> ApprovalOutcome:
        return ApprovalOutcome.deny("timeout")

    async def policy(server: str, tool: str, args: dict[str, object]) -> ApprovalOutcome:
        return ApprovalOutcome.deny("policy")

    t = await McpToolSource(object(), timeout).execute("mcp__gh__create_issue", {})
    p = await McpToolSource(object(), policy).execute("mcp__gh__create_issue", {})
    assert t.output == "No decision arrived in time; MCP tool `gh.create_issue` was not run."
    assert p.output == f"MCP tool `gh.create_issue` {_POLICY_TAIL}"


def _controller(tmp_path: Path, store: ChatThreadStore, **kw: object) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK, **kw)


@pytest.mark.asyncio
async def test_chat_command_timeout_is_marked_timeout(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, command_decision_timeout_sec=0.05)
    outcome = await ctrl._command_approval_cb(tid, f"chat:{tid}", "rm", ["-rf"], "")
    assert outcome.approved is False and outcome.denied_by == "timeout"


@pytest.mark.asyncio
async def test_chat_mcp_timeout_is_marked_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", "0.05")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    outcome = await _controller(tmp_path, store)._mcp_approval_cb(
        tid, f"chat:{tid}", "gh", "t", {})
    assert outcome.approved is False and outcome.denied_by == "timeout"


@pytest.mark.asyncio
async def test_a_client_cannot_post_denied_by(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    gate = asyncio.create_task(ctrl._command_approval_cb(tid, f"chat:{tid}", "ls", [], ""))
    for _ in range(100):
        await asyncio.sleep(0)
        if ctrl._pending_command:
            break
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(f"/v1/chat/threads/{tid}/command-decision",
                                 json={"approve": False, "denied_by": "policy"})
    assert resp.status_code == 200
    outcome = await gate
    assert outcome.approved is False and outcome.denied_by == "user"
