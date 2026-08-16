"""Ollama live token counting.

Only openai_compatible (and openrouter by inheritance) set supports_token_progress,
so the WorkBar's token counter was blank on every other provider — observed live on
Ollama-hosted nemotron-3-ultra, where the thinking label moved but no numbers ever
appeared. Ollama already streams NDJSON with content and thinking separated per
delta and reports exact counts on the terminal chunk, so it can report progress with
the same fidelity as openai_compatible.
"""
import json

import pytest

from agentd.providers.ollama_transport import OllamaJsonTransport


class _FakeLines:
    def __init__(self, lines): self._lines = lines
    async def aiter_lines(self):
        for ln in self._lines:
            yield ln
    @property
    def status_code(self): return 200
    async def aread(self): return b""


def _chunk(content="", thinking="", done=False, **extra):
    msg = {}
    if content: msg["content"] = content
    if thinking: msg["thinking"] = thinking
    d = {"message": msg}
    if done:
        d["done"] = True
        d.update(extra)
    return json.dumps(d)


def test_transport_advertises_token_progress() -> None:
    assert OllamaJsonTransport.supports_token_progress is True


@pytest.mark.asyncio
async def test_stream_reports_running_and_then_exact_counts(monkeypatch) -> None:
    t = OllamaJsonTransport(host="http://x")
    seen: list[tuple[int, int, int | None, bool]] = []

    def on_progress(r, c, *, input_n=None, exact=False):
        seen.append((r, c, input_n, exact))

    lines = [
        _chunk(thinking="thinking hard about this "),
        _chunk(content='{"type":'),
        _chunk(content=' "answer"}'),
        _chunk(done=True, prompt_eval_count=1234, eval_count=88),
    ]
    # Bypass HTTP: exercise the real line-parsing/merge loop directly.
    merged = await t._stream_chat_lines(_FakeLines(lines), on_chunk=None,
                                        on_progress=on_progress)

    assert merged["message"]["content"] == '{"type": "answer"}'
    assert seen, "no progress reported"
    # Closing tick carries the provider's own numbers, flagged exact.
    r, c, input_n, exact = seen[-1]
    assert exact is True
    assert input_n == 1234
    assert c == 88
