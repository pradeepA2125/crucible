"""The activation seams of ControllerLoop (spec §3.2, §3.6, §4.2)."""
from pathlib import Path

import pytest

from agentd.subagents.inbox import InboxItem
from tests.loop_harness import run_child_loop

READ = {"type": "tool_call", "thought": "t", "tool": "list_directory", "args": {"path": "."}}
REPORT = {"type": "report", "thought": "t", "summary": "done"}


@pytest.mark.asyncio
async def test_iteration_cb_sees_each_iteration_and_the_end(tmp_path: Path) -> None:
    saved: list[int] = []
    await run_child_loop(tmp_path, [READ, REPORT],
                         iteration_cb=lambda history: saved.append(len(history)))
    assert len(saved) == 2 and saved[0] < saved[1]   # once after iteration 0, once at the end


@pytest.mark.asyncio
async def test_inbox_items_are_appended_at_the_iteration_top(tmp_path: Path) -> None:
    pending = [InboxItem(kind="report", text="child finished: X", wakes=True)]

    def drain() -> list[InboxItem]:
        items, pending[:] = list(pending), []
        return items

    outcome = await run_child_loop(tmp_path, [READ, REPORT], inbox_drain=drain)
    users = [m["content"] for m in outcome.history or [] if m.get("role") == "user"]
    assert "New message:\nchild finished: X" in users


@pytest.mark.asyncio
async def test_report_guard_redirects_until_clear(tmp_path: Path) -> None:
    answers = ["New reports arrived from your agents — read them before reporting.", None]
    outcome = await run_child_loop(
        tmp_path, [REPORT, REPORT], report_guard=lambda: answers.pop(0))
    assert outcome.kind == "report"
    assert any("read them before reporting" in str(m.get("content"))
               for m in outcome.history or [])


@pytest.mark.asyncio
async def test_report_guard_is_skipped_on_the_final_iteration(tmp_path: Path) -> None:
    outcome = await run_child_loop(tmp_path, [REPORT], max_iters=0,
                                   report_guard=lambda: "blocked")
    assert outcome.kind == "report" and outcome.payload == {"status": "partial"}
