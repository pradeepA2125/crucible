"""An untrusted AGENTS.md is notes, never instructions or authorization (Phase 1, spec §4.1)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentd.approvals.types import JudgeRequest
from agentd.chat.controller import ChatController
from agentd.chat.controller_prompts import build_controller_step_payload
from agentd.chat.storage import ChatThreadStore
from agentd.instructions.loader import ProjectInstructionsLoader
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.trust.project_trust import ProjectTrust

TEXT = "Always run pnpm test before answering."


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(TEXT)
    return workspace


def _trust(ws: Path) -> None:
    ProjectTrust(ws).trust("agents_md", hashlib.sha256(TEXT.encode()).hexdigest())


def _controller(ws: Path, tmp_path: Path) -> tuple[ChatController, str]:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    thread = store.create_thread(str(ws), title="t")
    ctrl = ChatController(
        workspace_path=str(ws),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    return ctrl, thread.thread_id


def test_untrusted_agents_md_not_in_system_prompt(ws: Path) -> None:
    loader = ProjectInstructionsLoader(ws)
    assert loader.load() is None
    assert loader.load_untrusted() == TEXT


def test_untrusted_agents_md_in_payload_head_framed(ws: Path, tmp_path: Path) -> None:
    ctrl, _tid = _controller(ws, tmp_path)
    notes = ctrl._untrusted_project_notes()
    assert notes is not None
    assert notes.splitlines()[0] == (
        '<<<agent-content author="repository" kind="project-notes" source="AGENTS.md">>>')
    assert "| " + TEXT in notes
    payload = build_controller_step_payload(
        {"workspace_path": str(ws), "goal": "hi", "untrusted_project_notes": notes},
        [{"role": "user", "content": "earlier"}], [], phase="ACTIVE")
    keys = list(payload)
    assert keys.index("workspace_path") < keys.index("untrusted_project_notes")
    assert keys.index("untrusted_project_notes") < keys.index("conversation_history")


def test_trusted_agents_md_unchanged(ws: Path, tmp_path: Path) -> None:
    _trust(ws)
    loader = ProjectInstructionsLoader(ws)
    assert loader.load() == TEXT
    assert loader.load_untrusted() is None
    ctrl, _tid = _controller(ws, tmp_path)
    assert ctrl._untrusted_project_notes() is None
    payload = build_controller_step_payload(
        {"workspace_path": str(ws), "goal": "hi"}, [], [], phase="ACTIVE")
    assert "untrusted_project_notes" not in payload


def test_judge_context_omits_untrusted_agents_md(ws: Path, tmp_path: Path) -> None:
    ctrl, tid = _controller(ws, tmp_path)
    request = JudgeRequest(thread_id=tid, agent_id="main", workspace=ws)
    assert ctrl._judge_context(request).agents_md == ""
    _trust(ws)
    assert ctrl._judge_context(request).agents_md == TEXT


def test_flag_off_agents_md_as_today(ws: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "0")
    loader = ProjectInstructionsLoader(ws)
    assert loader.load() == TEXT
    assert loader.load_untrusted() is None
    ctrl, tid = _controller(ws, tmp_path)
    assert ctrl._untrusted_project_notes() is None
    request = JudgeRequest(thread_id=tid, agent_id="main", workspace=ws)
    assert ctrl._judge_context(request).agents_md == TEXT
