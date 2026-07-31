"""Live-smoke finding: on reload, a controller turn's tool pills all pile up at the TOP
of the turn instead of sitting in the chronological slots they rendered in live — only
the gate breadcrumbs keep their position.

Root cause (same family as the `progress`-note ordering bug covered by
test_controller_progress_transcript_order.py, but at a MUCH more common boundary):

* Pills accumulate in ONE in-flight message. `ChatThreadStore.upsert_inflight_pills`
  creates it at the FIRST tool result of the turn and updates it IN PLACE on every
  subsequent tool result; `finalize_inflight_pills` sets its final content at turn end.
* Mid-turn durable messages are plain appends. A command/MCP approval breadcrumb
  (`ChatController._write_breadcrumb`) and an inert edit `diff_card`
  (`_edit_record_cb`) both go through `ChatThreadStore.append_message`, which appends
  at the END of the message list.
* Only `_progress_note_cb` sealed the in-flight message. So for every OTHER mid-turn
  message the pills message stays open at its original early position and keeps
  absorbing the whole rest of the turn's pills — every later pill (and the closing
  answer text, which finalizes that same message object) is persisted BEHIND a
  breadcrumb/diff_card that happened before it.

The live webview has no such divergence because its `appendMessage` reducer
(webview-ui/src/hooks/useAppState.ts) calls `sealStreaming` for EVERY appended message,
splitting the streaming bubble at each one. These tests pin the durable half to that
same universal rule: any mid-turn durable message closes the current pills segment.
"""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.workspace.shadow import ShadowWorkspaceManager


class _FakeOrch:
    """Minimum ChatController._run_loop needs to build a real edit_session_factory."""

    def __init__(self, tmp_path: Path) -> None:
        self._workspace_manager = ShadowWorkspaceManager(tmp_path / "sh")
        self._patch_engine = PatchEngine()


def _ctrl(
    tmp_path: Path, store: ChatThreadStore, responses: list[dict[str, object]],
    *, orchestrator: object | None = None,
    shell_policy: ShellPolicy = ShellPolicy.ASK,
) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=responses),
        thread_store=store, orchestrator=orchestrator, broadcaster=EventBroadcaster(),
        retrieval_client=None, shell_policy=shell_policy)


def _index_of(msgs: list, predicate) -> int:
    return next(i for i, m in enumerate(msgs) if predicate(m))


def _pill_tools(msg) -> list[str]:
    return [e.get("tool") for e in (msg.metadata or {}).get("tool_events", [])]


def _pill_paths(msg) -> list[str]:
    return [e.get("args", {}).get("path") for e in (msg.metadata or {}).get("tool_events", [])]


@pytest.mark.asyncio
async def test_pills_after_an_edit_diff_card_land_after_it(tmp_path: Path) -> None:
    """An auto-accepted edit persists an inert diff_card mid-turn. Pills from AFTER the
    edit must be persisted after that card, not folded back into the pre-edit pills
    message (which is what put the whole turn's pills at the top on reload)."""
    (tmp_path / "f1.py").write_text("a = 1\n")
    (tmp_path / "f2.py").write_text("b = 2\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f1.py"}},
        {"type": "edit", "thought": "write", "patch_ops": [
            {"op": "create_file", "file": "new.py", "content": "y = 2\n", "reason": "r"}]},
        {"type": "tool_call", "thought": "verify", "tool": "read_file", "args": {"path": "f2.py"}},
        {"type": "answer", "thought": "done", "answer": "did it"},
    ]
    await _ctrl(tmp_path, store, responses, orchestrator=_FakeOrch(tmp_path)).handle_message(
        thread.thread_id, "change things", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    card_idx = _index_of(msgs, lambda m: m.type == "diff_card")
    pre_idx = _index_of(msgs, lambda m: "read_file" in _pill_tools(m))
    answer_idx = _index_of(msgs, lambda m: m.content == "did it")

    # The pre-edit pill message stays where it was, ahead of the card.
    assert pre_idx < card_idx
    # The closing message (and the post-edit pill it carries) lands AFTER the card.
    assert answer_idx > card_idx, (
        "closing message persisted ahead of a diff_card that happened before it — "
        f"order was {[(m.type, m.content, _pill_tools(m)) for m in msgs]}")
    # Two distinct pill messages, one per segment: the post-edit read must NOT be
    # folded back into the pre-edit message.
    pill_msgs = [m for m in msgs if _pill_tools(m)]
    assert len(pill_msgs) == 2, [_pill_tools(m) for m in pill_msgs]
    assert _pill_paths(pill_msgs[0]) == ["f1.py"]
    assert _pill_paths(pill_msgs[1]) == ["f2.py"]
    assert all(not (m.metadata or {}).get("inflight_turn_id") for m in msgs)


@pytest.mark.asyncio
async def test_closing_message_lands_after_a_command_approval_breadcrumb(
    tmp_path: Path,
) -> None:
    """The reported scenario: the model reads a file, runs a gated command, then answers.
    The approval breadcrumb is appended mid-turn, so the closing message — which finalizes
    the in-flight pills message created before it — must not end up ahead of it."""
    (tmp_path / "f1.py").write_text("a = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f1.py"}},
        {"type": "tool_call", "thought": "run it", "tool": "run_command",
         "args": {"command": "touch", "args": ["s.txt"]}},
        {"type": "answer", "thought": "done", "answer": "ran the command"},
    ]
    ctrl = _ctrl(tmp_path, store, responses)
    turn = asyncio.create_task(
        ctrl.handle_message(thread.thread_id, "run it", channel_id="c1"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        gate = store.get_thread(thread.thread_id).pending_controller_gate
        if gate is not None and gate.kind == "command":
            break
    assert gate is not None and gate.kind == "command"
    assert await ctrl.resolve_command(thread.thread_id, CommandDecision(approve=True)) is True
    await turn

    msgs = store.get_thread(thread.thread_id).messages
    crumb_idx = _index_of(msgs, lambda m: (m.metadata or {}).get("breadcrumb") is True)
    answer_idx = _index_of(msgs, lambda m: m.content == "ran the command")
    assert answer_idx > crumb_idx, (
        "the turn's closing message persisted ahead of the approval breadcrumb it "
        f"followed live — order was {[(m.content, _pill_tools(m)) for m in msgs]}")
    # Both pills stay together in the pre-breadcrumb segment (mirrors the live bubble,
    # which is sealed with the pending run_command pill already in it).
    pill_msgs = [m for m in msgs if _pill_tools(m)]
    assert len(pill_msgs) == 1
    assert _pill_tools(pill_msgs[0]) == ["read_file", "run_command"]
    assert msgs.index(pill_msgs[0]) < crumb_idx
    assert all(not (m.metadata or {}).get("inflight_turn_id") for m in msgs)


@pytest.mark.asyncio
async def test_pills_after_a_breadcrumb_start_a_fresh_message(tmp_path: Path) -> None:
    """Two gated commands back to back: each command's pill gets its OWN message, and the
    second is NOT folded back into the first (the pile-up).

    Placement note — a gated command's pill lands AFTER its own approval breadcrumb here,
    where in test_closing_message_lands_after_a_command_approval_breadcrumb it lands
    before. Both are correct and follow from one rule: the boundary seals whatever pills
    are ALREADY persisted, and pills are only persisted once a call has a result. A
    command that is the first call of its segment has nothing persisted yet when its gate
    raises, so its pill opens the next segment (reading approval → command → output); a
    command later in a segment is folded in with the pills it was sealed alongside live.
    The alternative — persisting a result-less pill at request time so the pre-crumb
    message always contains the pending call — would put `done: true` pills with no output
    in the transcript and break the webview's "persisted pills are always done" dedup
    invariant (useAppState.ts appendToolResult), for a one-slot cosmetic gain."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "tool_call", "thought": "one", "tool": "run_command",
         "args": {"command": "touch", "args": ["one.txt"]}},
        {"type": "tool_call", "thought": "two", "tool": "run_command",
         "args": {"command": "touch", "args": ["two.txt"]}},
        {"type": "answer", "thought": "done", "answer": "both ran"},
    ]
    ctrl = _ctrl(tmp_path, store, responses)
    turn = asyncio.create_task(
        ctrl.handle_message(thread.thread_id, "run both", channel_id="c1"))
    for _ in range(2):
        for _ in range(200):
            await asyncio.sleep(0.01)
            gate = store.get_thread(thread.thread_id).pending_controller_gate
            if gate is not None and gate.kind == "command":
                break
        assert gate is not None and gate.kind == "command"
        assert await ctrl.resolve_command(
            thread.thread_id, CommandDecision(approve=True)) is True
    await turn

    msgs = store.get_thread(thread.thread_id).messages
    crumbs = [i for i, m in enumerate(msgs) if (m.metadata or {}).get("breadcrumb") is True]
    assert len(crumbs) == 2, [(m.content, _pill_tools(m)) for m in msgs]
    pill_idxs = [i for i, m in enumerate(msgs) if _pill_tools(m)]
    # One pill message per command — never one message carrying both.
    assert len(pill_idxs) == 2, [_pill_tools(msgs[i]) for i in pill_idxs]
    assert [_pill_tools(msgs[i]) for i in pill_idxs] == [["run_command"], ["run_command"]]
    answer_idx = _index_of(msgs, lambda m: m.content == "both ran")
    assert crumbs[0] < pill_idxs[0] < crumbs[1] < pill_idxs[1] < answer_idx, (
        f"order was {[(m.content, _pill_tools(m)) for m in msgs]}")
    assert (tmp_path / "one.txt").exists() and (tmp_path / "two.txt").exists()
