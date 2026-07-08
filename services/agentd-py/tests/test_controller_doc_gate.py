"""doc_write gate: write_doc calls pause for live approval — mirror of the mcp_tool
gate on the same thread-gate machinery, minus the remember option (spec §3.3)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import DocWriteDecision
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _controller(tmp_path, store, broadcaster=None):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=broadcaster or EventBroadcaster(), retrieval_client=None)


@pytest.mark.asyncio
async def test_gate_raised_then_approve_resolves(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._doc_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "docs/a.md", False, "# preview"))
    await asyncio.sleep(0)
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "doc_write"
    assert gate.payload == {"path": "docs/a.md", "exists": False, "preview": "# preview"}

    assert await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=True)) is True
    assert await cb_task is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None  # cleared in place


@pytest.mark.asyncio
async def test_reject_returns_false_with_breadcrumb(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._doc_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "a.md", True, "diff"))
    await asyncio.sleep(0)
    await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=False))
    assert await cb_task is False
    texts = [m.content for m in store.get_thread(th.thread_id).messages]
    assert any("✗ Doc write rejected: a.md" in t for t in texts)


@pytest.mark.asyncio
async def test_broadcasts_doc_write_requested_poke(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    bc = EventBroadcaster()
    ctrl = _controller(tmp_path, store, broadcaster=bc)
    cid = f"chat:{th.thread_id}"
    q = bc.subscribe(cid)
    cb_task = asyncio.create_task(ctrl._doc_approval_cb(th.thread_id, cid, "a.md", False, "p"))
    await asyncio.sleep(0)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    poke = [e for e in events if e["type"] == "doc_write_requested"]
    assert poke and poke[0]["payload"] == {"path": "a.md", "exists": False}
    await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=False))
    await cb_task


@pytest.mark.asyncio
async def test_timeout_rejects(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", "0.05")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl._doc_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "a.md", False, "p") is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_resolve_no_pending_returns_false_and_clears_orphan(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=True)) is False
    store.set_controller_gate(
        th.thread_id, PendingGate(kind="doc_write", payload={"path": "x.md"}))
    assert await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=True)) is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_registry_includes_write_doc_only_when_flag_on(tmp_path: Path, monkeypatch):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)

    async def _cb(path, exists, preview):
        return True

    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_ENABLED", "1")
    names = [d.name for d in ctrl._build_registry(doc_approval_cb=_cb).definitions()]
    assert "write_doc" in names
    monkeypatch.delenv("CRUCIBLE_DOC_WRITE_ENABLED", raising=False)
    names_off = [d.name for d in ctrl._build_registry(doc_approval_cb=_cb).definitions()]
    assert "write_doc" not in names_off
