"""AI_EDITOR_DOC_WRITE_ENABLED parsing; POST /doc-decision routes to resolve_doc_write;
the write_doc teaching block appends iff the tool is present in tool_definitions."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller_factory import is_doc_write_enabled
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("AI_EDITOR_DOC_WRITE_ENABLED", raising=False)
    assert is_doc_write_enabled() is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False),
])
def test_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("AI_EDITOR_DOC_WRITE_ENABLED", raw)
    assert is_doc_write_enabled() is expected


class _StubChatHandler:
    def __init__(self):
        self.calls = []
        self._store = None
        self._broadcaster = None

    async def resolve_doc_write(self, thread_id, decision):
        self.calls.append((thread_id, decision))
        return True


@pytest.mark.asyncio
async def test_doc_decision_route(tmp_path: Path):
    stub = _StubChatHandler()
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=stub))
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post("/v1/chat/threads/th1/doc-decision",
                                 json={"approve": True})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    (thread_id, decision), = stub.calls
    assert thread_id == "th1" and decision.approve is True


_BASE = [{"name": "read_file", "description": "d", "parameters": {}}]
_DOC = [{"name": "write_doc", "description": "d", "parameters": {}}]


def _prompt(defs):
    return format_controller_system_prompt(
        defs, task_subsystem_enabled=False, memory_enabled=False)


def test_block_absent_without_write_doc():
    assert "WRITING DOCS" not in _prompt(_BASE)


def test_block_present_with_write_doc():
    text = _prompt(_BASE + _DOC)
    assert "WRITING DOCS" in text
    assert "approval" in text
    # No superiority framing after the block header.
    assert "instead of" not in text.split("WRITING DOCS")[1].lower()


def test_block_has_worked_examples_and_unconditional_clause():
    """Weak-model action-selection gap (seen live on TQP 2026-07-02): the model
    thought 'I should use write_doc' then emitted answer/propose_mode anyway.
    Fix = worked few-shot examples + an 'IS the complete workflow' clause
    (same recipe as the P2 read_skill fix), not more abstract rewording."""
    block = _prompt(_BASE + _DOC).split("WRITING DOCS")[1]
    # A literal tool_call shape the model can pattern-match.
    assert '"tool":"write_doc"' in block.replace(" ", "").replace("\n", "")
    # The unconditional clause: one standalone file => write_doc is the whole job.
    assert "complete workflow" in block
    assert "even when the request seems small" in block
    # Updating an existing doc = read then ONE write_doc with the full content.
    assert "read_file" in block
    # Anti-hallucination guard: never describe a write that did not happen.
    assert "Never claim" in block
