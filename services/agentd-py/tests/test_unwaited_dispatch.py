"""The main agent does not end its turn on unwaited agents by accident (spec §4.1)."""
import json
from pathlib import Path

import pytest

from tests.test_background_dispatch import _setup, _tool

ANSWER = {"type": "answer", "thought": "t", "answer": "They are working on it."}


@pytest.mark.asyncio
async def test_redirect_once_then_upgrade_to_wake(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "general-purpose", "label": "slow",
                                          "prompt": "run"}]),
        ANSWER, ANSWER],
        {"slow": [_tool("run_command", command="sleep", args=["30"])]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    redirects = [m for m in history if "are still running" in str(m.get("content", ""))]
    assert len(redirects) == 1
    [row] = store.list_agents(tid)
    assert row.on_finish == "wake"
    await ctrl.stop_all_agents(tid)


@pytest.mark.asyncio
async def test_no_redirect_after_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"), ANSWER],
        {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert not [m for m in history if "are still running" in str(m.get("content", ""))]
    assert json.loads(str([m for m in history if m.get("tool") == "wait_agents"][0]["content"]))
