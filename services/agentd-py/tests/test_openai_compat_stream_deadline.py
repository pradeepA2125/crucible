"""A stream that never finishes is cut off at a total deadline (found live on NIM).

The per-read HTTP timeout never fires while an endpoint keeps trickling chunks, so
before this a single call could hang a whole chat turn for 10+ minutes.
"""
import asyncio
import json

import pytest

from agentd.providers.openai_compatible_transport import (
    OpenAICompatibleTransport,
    TransientTransportError,
)
from tests.test_openai_compatible_transport import _FakeCompletions, _GoodStream, _StreamChunk


class _TricklingStream:
    """Sends an empty chunk every 10 ms forever: never idle, never done."""

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        while True:
            await asyncio.sleep(0.01)
            yield _StreamChunk(None)

    async def close(self) -> None:
        self.closed = True


def _transport(streams: list[object], deadline: float) -> OpenAICompatibleTransport:
    return OpenAICompatibleTransport(
        base_url="https://example.test/v1", completions_client=_FakeCompletions(streams),
        json_mode="none", stream_timeout_sec=deadline)


@pytest.mark.asyncio
async def test_a_never_ending_stream_times_out_and_is_closed() -> None:
    stalled = _TricklingStream()
    transport = _transport([stalled], deadline=0.2)
    with pytest.raises(TransientTransportError, match="timed out"):
        await asyncio.wait_for(transport._stream_with_finish_reason(
            {"model": "m", "messages": []}, on_thinking=lambda _c: None), timeout=5)
    assert stalled.closed, "the abandoned HTTP stream must be closed, not leaked"


@pytest.mark.asyncio
async def test_a_stalled_call_is_retried_and_recovers() -> None:
    transport = _transport([_TricklingStream(), _GoodStream(json.dumps({"ok": True}))],
                           deadline=0.2)
    result = await asyncio.wait_for(transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
        on_thinking=lambda _c: None), timeout=10)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_a_normal_stream_is_unaffected() -> None:
    transport = _transport([_GoodStream(json.dumps({"ok": 1}))], deadline=5)
    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={}, on_thinking=lambda _c: None)
    assert result == {"ok": 1}
