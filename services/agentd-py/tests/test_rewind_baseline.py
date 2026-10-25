"""Review baselines: read-only views of rewind checkpoints for the desktop Changes tool."""
from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ValidationResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _two_turns(tmp_path: Path):
    """Turn 0 edits a.py and creates new.py; turn 1 edits a.py again and b.py."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("a original\n")
    (ws / "b.py").write_text("b original\n")
    (ws / "big.bin").write_bytes(b"x" * 64)
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    rewind = RewindStore(store, ws, max_file_bytes=32)
    thread = store.create_thread(str(ws), "t")
    tid = thread.thread_id
    first = store.append_message(tid, ChatMessage(role="user", content="one"))
    rewind.open_checkpoint(tid, first, "turn0", thread=store.get_thread(tid),
                           memory_anchor_md=None)
    rewind.capture(tid, ["a.py", "new.py", "big.bin"])
    (ws / "a.py").write_text("a after turn 0\n")
    (ws / "new.py").write_text("created\n")
    second = store.append_message(tid, ChatMessage(role="user", content="two"))
    rewind.open_checkpoint(tid, second, "turn1", thread=store.get_thread(tid),
                           memory_anchor_md=None)
    rewind.capture(tid, ["a.py", "b.py"])
    (ws / "a.py").write_text("a after turn 1\n")
    return ws, store, rewind, tid


def test_baseline_is_the_rewind_fold(tmp_path: Path):
    _ws, _store, rewind, tid = _two_turns(tmp_path)
    assert [(e.path, e.existed, e.oversize, e.from_seq) for e in rewind.baseline(tid, 0)] == [
        ("a.py", True, False, 0), ("b.py", True, False, 1),
        ("big.bin", True, True, 0), ("new.py", False, False, 0)]
    assert [(e.path, e.from_seq) for e in rewind.baseline(tid, 1)] == [("a.py", 1), ("b.py", 1)]
    assert rewind.baseline(tid, 7) is None


def test_baseline_file_states(tmp_path: Path):
    ws, _store, rewind, tid = _two_turns(tmp_path)
    oldest = rewind.baseline_file(tid, 0, "a.py", max_bytes=1024)
    assert (oldest.state, oldest.content, oldest.from_seq) == ("captured", b"a original\n", 0)
    assert rewind.baseline_file(tid, 1, "a.py", max_bytes=1024).content == b"a after turn 0\n"
    assert rewind.baseline_file(tid, 0, "new.py", max_bytes=1024).state == "absent"
    assert rewind.baseline_file(tid, 0, "big.bin", max_bytes=1024).state == "oversize"
    small = rewind.baseline_file(tid, 0, "a.py", max_bytes=4)
    assert (small.state, small.size, small.content) == ("too_large", 11, None)
    # Only paths the checkpoint recorded are readable, however the caller spells them.
    for path in ["keep.py", "../chat.sqlite3", "/etc/hosts", "a.py/../b.py"]:
        assert rewind.baseline_file(tid, 0, path, max_bytes=1024) is None
    (ws / ".crucible" / "state" / "rewind" / tid / "1" / "files" / "b.py").unlink()
    assert rewind.baseline_file(tid, 1, "b.py", max_bytes=1024).state == "missing"


def test_links_planted_in_the_rewind_store_are_refused(tmp_path: Path):
    ws, _store, rewind, tid = _two_turns(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("outside the project\n")
    stored = ws / ".crucible" / "state" / "rewind" / tid / "0" / "files"
    (stored / "a.py").unlink()
    (stored / "a.py").symlink_to(secret)
    linked = rewind.baseline_file(tid, 0, "a.py", max_bytes=1024)
    assert (linked.state, linked.content) == ("missing", None)
    # A link one folder up is refused too.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.copy(secret, elsewhere / "a.py")
    shutil.rmtree(stored)
    stored.symlink_to(elsewhere, target_is_directory=True)
    assert rewind.baseline_file(tid, 0, "a.py", max_bytes=1024).state == "missing"


@pytest.mark.parametrize("missing", ["dir_fd", "nofollow"])
def test_platforms_without_secure_opens_fail_closed(tmp_path: Path, monkeypatch, missing: str):
    ws, _store, rewind, tid = _two_turns(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("outside the project\n")
    stored = ws / ".crucible" / "state" / "rewind" / tid / "0" / "files" / "a.py"
    stored.unlink()
    stored.symlink_to(secret)
    if missing == "dir_fd":
        monkeypatch.setattr(os, "supports_dir_fd", set())
    else:
        monkeypatch.delattr(os, "O_NOFOLLOW")
    linked = rewind.baseline_file(tid, 0, "a.py", max_bytes=1024)
    plain = rewind.baseline_file(tid, 1, "b.py", max_bytes=1024)
    assert [(r.state, r.content) for r in (linked, plain)] == [("unavailable", None)] * 2


def _app(tmp_path: Path):
    ws, store, rewind, tid = _two_turns(tmp_path)
    task_store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "sh")
    orch = AgentOrchestrator(
        store=task_store, reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(), workspace_manager=ws_manager, chat_store=store,
    )
    controller = ChatController(
        workspace_path=str(ws), reasoning_engine=_NoopReasoning(), thread_store=store,
        orchestrator=orch, broadcaster=orch.broadcaster, rewind_store=rewind,
    )
    app = FastAPI()
    app.include_router(build_router(task_store, orch, ws_manager, None, controller))
    return app, tid


@pytest.mark.asyncio
async def test_checkpoint_routes(tmp_path: Path):
    app, tid = _app(tmp_path)
    base = f"/v1/chat/threads/{tid}/checkpoints"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = (await client.get(base)).json()["checkpoints"]
        assert [(c["seq"], c["turn_id"], [f["path"] for f in c["files"]]) for c in listed] == [
            (0, "turn0", ["a.py", "new.py", "big.bin"]), (1, "turn1", ["a.py", "b.py"])]
        assert set(listed[0]) == {"seq", "anchor_message_id", "turn_id", "created_at", "files"}
        baseline = (await client.get(f"{base}/0/baseline")).json()
        assert baseline["files"][0] == {
            "path": "a.py", "existed": True, "oversize": False, "from_seq": 0}
        file = (await client.get(f"{base}/0/baseline/file", params={"path": "a.py"})).json()
        assert base64.b64decode(file["content_base64"]) == b"a original\n"
        assert (file["state"], file["size"], "content" in file) == ("captured", 11, False)
        absent = (await client.get(f"{base}/0/baseline/file", params={"path": "new.py"})).json()
        assert absent["state"] == "absent" and "content_base64" not in absent
        outside = await client.get(f"{base}/0/baseline/file", params={"path": "../x"})
        assert outside.status_code == 404
        assert (await client.get(f"{base}/9/baseline")).status_code == 404
        assert (await client.get("/v1/chat/threads/nope/checkpoints")).status_code == 404
