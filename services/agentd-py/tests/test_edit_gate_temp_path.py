"""A live edit gate carries each file's shadow path, so the card's view-diff button can
open the native diff (FileRow posts viewDiffFile with temp_path; without it the
extension can only warn "Diff is unavailable — shadow path missing")."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import DiffEntry
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_dispatch_integration import _controller


@pytest.mark.asyncio
async def test_the_edit_gate_payload_carries_the_shadow_path(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, tmp_path, store, ScriptedReasoningEngine(None, []))
    diff = [DiffEntry(path="shop/a.py", additions=2, deletions=0,
                      temp_path="/shadows/chatturn-x/shop/a.py", unified_diff="+x")]

    waiting = asyncio.create_task(ctrl._edit_decision_cb(tid, f"chat:{tid}", diff))
    for _ in range(3):
        await asyncio.sleep(0)
    thread = store.get_thread(tid)
    assert thread is not None
    [gate] = thread.pending_controller_gates
    [entry] = gate.payload["diff_entries"]
    waiting.cancel()
    assert entry["temp_path"] == "/shadows/chatturn-x/shop/a.py"
    assert entry["unified_diff"] == "+x"
