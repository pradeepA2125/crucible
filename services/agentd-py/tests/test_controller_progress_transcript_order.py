"""Finding 1 (final whole-branch review): a `progress` note persisted mid-turn must
land in the durable transcript in the same chronological slot it renders live — i.e.
AFTER whatever pills accumulated before it, and BEFORE whatever the turn does next.

Root cause (traced via ChatThreadStore.upsert_inflight_pills/finalize_inflight_pills):
`upsert_inflight_pills` creates ONE pills-only message at the position of the FIRST
tool result each turn and updates it in place on every subsequent tool result;
`finalize_inflight_pills` (called from `_write_turn_message` at turn end) finds that
SAME message (matched by turn_id) and sets its final content. A `progress` note is
appended as a brand-new message via plain `append_message` in between — so without a
fix, the closing answer ends up BEHIND the note in the message list (it overwrites the
message that was created ahead of the note), even though live it renders after it.

The fix: `ChatController._progress_note_cb` calls `ChatThreadStore.seal_inflight_pills`
(drops the `inflight_turn_id` marker, keeps content/metadata as-is) BEFORE appending the
note, so the accumulated-so-far pills message is frozen exactly where it is and the next
tool result starts a FRESH in-flight message positioned after the note. `ControllerLoop`
mirrors this with its own `_pill_segment_start`/`_thinking_segment_start` indices so the
pills CONTENT is also segment-scoped (no earlier segment's pills re-shown in a later
message) — this is the loop-side half that keeps live `call_index` and the durable
`tool_events` arrays consistent across a note boundary.
"""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.workspace.shadow import ShadowWorkspaceManager


class _FakeOrch:
    """Bare minimum ChatController._run_loop needs to build a real edit_session_factory
    (workspace_manager + patch_engine) — mirrors test_controller_durable_edit.py's
    inline TurnEditSession construction, just wrapped for a real handle_message() run."""

    def __init__(self, tmp_path: Path) -> None:
        self._workspace_manager = ShadowWorkspaceManager(tmp_path / "sh")
        self._patch_engine = PatchEngine()


def _ctrl(
    tmp_path: Path, store: ChatThreadStore, responses: list[dict[str, object]],
    *, orchestrator: object | None = None,
) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=responses),
        thread_store=store, orchestrator=orchestrator, broadcaster=EventBroadcaster(),
        retrieval_client=None)


@pytest.mark.asyncio
async def test_note_after_tool_calls_lands_after_the_pills_message(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f.py"}},
        {"type": "progress", "thought": "t", "note": "Read the file. Now answering."},
        {"type": "answer", "thought": "done", "answer": "x is 1"},
    ]
    await _ctrl(tmp_path, store, responses).handle_message(
        thread.thread_id, "what is x", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    roles_contents = [(m.role, m.content, m.metadata) for m in msgs]
    assert [m.role for m in msgs] == ["user", "agent", "agent", "agent"]
    pills_msg, note_msg, answer_msg = msgs[1], msgs[2], msgs[3]

    # Pills message: sealed with the ONE tool call, no inflight marker left, no content
    # (the closing message carries the answer text, not this one).
    assert pills_msg.content == ""
    assert pills_msg.metadata.get("inflight_turn_id") is None
    assert any(e.get("tool") == "read_file" for e in pills_msg.metadata.get("tool_events", []))

    # Note message: exactly the posted note, tagged progress.
    assert note_msg.metadata.get("progress") is True
    assert note_msg.content == "Read the file. Now answering."

    # Closing answer message: the final text, AFTER the note — and it must NOT re-carry
    # the read_file pill (that already rendered in pills_msg; duplicating it here would
    # be the live/durable divergence this fix closes).
    assert answer_msg.content == "x is 1"
    assert not answer_msg.metadata.get("tool_events"), (
        "closing message must not duplicate pills already sealed before the note")

    assert roles_contents  # (kept — documents the full tuple shape for debugging)


@pytest.mark.asyncio
async def test_note_before_any_tool_call_is_a_noop_seal(tmp_path: Path) -> None:
    # No in-flight pills message exists yet when the note fires — seal_inflight_pills
    # must be a harmless no-op, and the note simply lands first.
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "progress", "thought": "t", "note": "Starting now."},
        {"type": "answer", "thought": "done", "answer": "Done."},
    ]
    await _ctrl(tmp_path, store, responses).handle_message(
        thread.thread_id, "go", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    assert [m.role for m in msgs] == ["user", "agent", "agent"]
    assert msgs[1].metadata.get("progress") is True
    assert msgs[1].content == "Starting now."
    assert msgs[2].content == "Done."


@pytest.mark.asyncio
async def test_n_notes_interleaved_with_tool_calls_segment_pills_without_duplication(
    tmp_path: Path,
) -> None:
    (tmp_path / "f1.py").write_text("a = 1\n")
    (tmp_path / "f2.py").write_text("b = 2\n")
    (tmp_path / "f3.py").write_text("c = 3\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f1.py"}},
        {"type": "progress", "thought": "t", "note": "note one"},
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f2.py"}},
        {"type": "progress", "thought": "t", "note": "note two"},
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f3.py"}},
        {"type": "answer", "thought": "done", "answer": "a=1, b=2, c=3"},
    ]
    await _ctrl(tmp_path, store, responses).handle_message(
        thread.thread_id, "what are a b c", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    # user, pillsA, note1, pillsB, note2, pillsC(=finalized closing message)
    assert [m.role for m in msgs] == ["user", "agent", "agent", "agent", "agent", "agent"]
    pills_a, note1, pills_b, note2, closing = msgs[1], msgs[2], msgs[3], msgs[4], msgs[5]

    assert note1.content == "note one"
    assert note2.content == "note two"

    def _args_paths(msg) -> list[str]:
        return [e.get("args", {}).get("path") for e in msg.metadata.get("tool_events", [])]

    # Each segment carries ONLY its own call — no earlier segment's pill re-appears in a
    # later message (the duplication finding 1's fix specifically has to prevent).
    assert _args_paths(pills_a) == ["f1.py"]
    assert _args_paths(pills_b) == ["f2.py"]
    assert _args_paths(closing) == ["f3.py"]
    assert closing.content == "a=1, b=2, c=3"

    # No dangling inflight markers anywhere once the turn is done.
    assert all(not (m.metadata or {}).get("inflight_turn_id") for m in msgs)


@pytest.mark.asyncio
async def test_zero_notes_turn_is_unaffected_single_finalized_pills_message(
    tmp_path: Path,
) -> None:
    # Regression guard: a turn that never emits `progress` must behave EXACTLY as
    # before this fix — one pills message, finalized in place with the answer.
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f.py"}},
        {"type": "answer", "thought": "done", "answer": "x is 1"},
    ]
    await _ctrl(tmp_path, store, responses).handle_message(
        thread.thread_id, "what is x", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    pill_msgs = [m for m in msgs if m.role == "agent" and (m.metadata or {}).get("tool_events")]
    assert len(pill_msgs) == 1
    assert pill_msgs[0].content == "x is 1"
    assert any(e.get("tool") == "read_file" for e in pill_msgs[0].metadata["tool_events"])


@pytest.mark.asyncio
async def test_note_in_a_turn_ending_via_submit_changes(tmp_path: Path) -> None:
    # Dry-run scenario from the review: a note followed by an edit that ends the turn
    # via submit_changes (not answer) — the note must still precede the closing message.
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    responses = [
        {"type": "progress", "thought": "t", "note": "Writing the file now."},
        {"type": "edit", "thought": "write", "patch_ops": [
            {"op": "create_file", "file": "new.py", "content": "y = 2\n", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "Created new.py"},
    ]
    ctrl = _ctrl(tmp_path, store, responses, orchestrator=_FakeOrch(tmp_path))
    await ctrl.handle_message(thread.thread_id, "create new.py", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    agent_msgs = [m for m in msgs if m.role == "agent"]
    note_idx = next(i for i, m in enumerate(agent_msgs) if m.metadata.get("progress") is True)
    summary_idx = next(i for i, m in enumerate(agent_msgs) if m.content == "Created new.py")
    assert note_idx < summary_idx
    assert (tmp_path / "new.py").read_text() == "y = 2\n"
