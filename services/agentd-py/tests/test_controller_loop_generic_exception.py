"""A provider call (or any other loop step) can raise something with no specific
recovery path — e.g. a cloud model exhausting its whole output budget on thinking
and returning no text content (observed live: Ollama Cloud's Nemotron-3-Super burned
32768 tokens of <think> and never emitted a response). Before the fix, that exception
propagated straight out of the SSE route uncaught: the stream died mid-flight with no
chat_done, no breadcrumb, nothing visible — the composer re-enabled (turnActive cleanup
runs regardless) but the todo list stayed frozen and nothing in the transcript or live
UI ever indicated a failure happened. These tests pin the fix: a generic exception is
treated like a normal turn-ending "answer" with a visible error message instead of
propagating uncaught.
"""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster


class _RaisesThenRecovers:
    """Raises on the first `fail_count` calls, then returns a real response — proves
    that a create_controller_step exception is routed through _iterate's EXISTING
    consecutive_malformed correct-and-continue path (the same one already used for a
    parsed-but-semantically-invalid response) and recovers a transient provider
    failure WITHOUT ending the turn, distinct from _run_loop's except Exception
    (which only fires once _MAX_MALFORMED is exceeded)."""

    def __init__(self, fail_count, exc, final_response):
        self._fail_count = fail_count
        self._exc = exc
        self._final_response = final_response
        self.calls = 0

    async def create_controller_step(self, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_count:
            raise self._exc
        return self._final_response


@pytest.mark.asyncio
async def test_controller_loop_recovers_from_transient_failure_without_ending_turn(
    tmp_path: Path,
):
    """A failure that resolves within the retry budget (2 retries) must not surface
    as a turn failure — the transcript should show the real answer, not the ⚠️
    fallback message, and the retry correction should be visible in history."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    engine = _RaisesThenRecovers(
        fail_count=2,
        exc=RuntimeError("Ollama response contained no text content"),
        final_response={"type": "answer", "thought": "ok", "answer": "42"},
    )
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=engine,
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    await ctrl.handle_message(thread.thread_id, "what is the answer", channel_id="c1")

    assert engine.calls == 3  # 1 initial + 2 retries before landing the real response
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    last = reloaded.messages[-1]
    assert last.role == "agent"
    assert "42" in last.content
    assert "turn failed" not in last.content.lower()
    # The correction fed back to the model must include the ACTUAL error, not just a
    # generic "give me JSON" nudge — otherwise a retry after e.g. a truncated-output
    # failure just repeats the same oversized response and hits the same wall again.
    hist = reloaded.controller_conversation_history
    assert any(
        "Ollama response contained no text content" in str(m.get("content", ""))
        for m in hist
    ), hist


@pytest.mark.asyncio
async def test_exception_retry_broadcasts_retry_status_not_tool_thinking_chunk(tmp_path: Path):
    """User-directed: a retry cycle (transient error or malformed response) can run
    for a while with the turn otherwise showing nothing between 'Working…' and the
    eventual outcome — indistinguishable from being stuck. Each retry attempt must
    broadcast a retry_status event (not tool_thinking_chunk — that channel is
    reserved for genuine model reasoning, never retry noise) so the user sees
    progress, not silence."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    broadcaster = EventBroadcaster()
    channel_id = "c1"
    queue = broadcaster.subscribe(channel_id)
    engine = _RaisesThenRecovers(
        fail_count=2,
        exc=RuntimeError("Ollama response contained no text content"),
        final_response={"type": "answer", "thought": "ok", "answer": "42"},
    )
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=engine,
        thread_store=store, orchestrator=None, broadcaster=broadcaster,
        retrieval_client=None)

    await ctrl.handle_message(thread.thread_id, "what is the answer", channel_id=channel_id)

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    retry_events = [e for e in events if e.get("type") == "retry_status"]
    stale_thinking_chunks = [
        e for e in events
        if e.get("type") == "tool_thinking_chunk"
        and "Response failed" in e.get("payload", {}).get("chunk", "")
    ]
    assert len(retry_events) == 2, events  # one per failed attempt before recovery
    assert retry_events[0]["payload"]["reason"] == "malformed_response"
    assert "Ollama response contained no text content" in retry_events[0]["payload"]["message"]
    assert not stale_thinking_chunks, stale_thinking_chunks


class _RaisesMidTurn:
    """Scripted controller engine that raises a plain exception on the Nth step,
    simulating a provider transport failure (not a CancelledError / /stop)."""

    def __init__(self, responses, raise_at, exc):
        self._responses = list(responses)
        self._i = 0
        self._raise_at = raise_at
        self._exc = exc

    async def create_controller_step(self, **kwargs):
        if self._i >= self._raise_at:
            raise self._exc
        resp = self._responses[self._i]
        self._i += 1
        return resp


@pytest.mark.asyncio
async def test_generic_exception_does_not_propagate(tmp_path: Path):
    """handle_message must not raise — unlike /stop's CancelledError, a provider
    failure should be swallowed into a visible turn-ending message."""
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=_RaisesMidTurn(
            [{"type": "tool_call", "thought": "look", "tool": "read_file",
              "args": {"path": "f.py"}}],
            raise_at=1,
            exc=RuntimeError("Ollama response contained no text content"),
        ),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    # No exception propagates out — this is the core regression.
    await ctrl.handle_message(thread.thread_id, "do something", channel_id="c1")

    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    messages = reloaded.messages
    last = messages[-1]
    assert last.role == "agent"
    assert "Ollama response contained no text content" in last.content
    assert "failed" in last.content.lower()


@pytest.mark.asyncio
async def test_generic_exception_persists_partial_history(tmp_path: Path):
    """Same guarantee as the /stop path: whatever the turn accumulated before the
    failure (the read_file it ran) isn't lost — the next turn rehydrates it."""
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=_RaisesMidTurn(
            [{"type": "tool_call", "thought": "look", "tool": "read_file",
              "args": {"path": "f.py"}}],
            raise_at=1,
            exc=RuntimeError("boom"),
        ),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    await ctrl.handle_message(thread.thread_id, "what is x", channel_id="c1")

    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    hist = reloaded.controller_conversation_history
    assert hist, "partial history not persisted on generic exception"
    assert any("read_file" in str(h.get("content", ""))
               or "x = 1" in str(h.get("content", "")) for h in hist), hist
