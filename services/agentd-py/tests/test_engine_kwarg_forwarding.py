"""The reasoning engine must only pass optional callbacks a transport declares.

Live-found bug: `supports_token_progress` gated FOUR different kwargs
(on_progress / on_usage / on_salvage / unconstrained), so TurboQuant — which sets
the flag and accepts on_progress but not the rest — got an unexpected keyword and
every controller turn died after the retry budget. Scripted test engines take
**kwargs, so nothing caught it until a real turn ran.
"""
import pytest

from agentd.reasoning.engine import DefaultReasoningEngine


class _PartialTransport:
    """Streams progress, reports no usage — exactly TurboQuant's shape."""
    supports_token_progress = True

    def __init__(self):
        self.seen: dict[str, object] = {}

    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_thinking=None, on_retry=None,
                            on_progress=None):
        self.seen = {"on_progress": on_progress is not None}
        return {"type": "answer", "thought": "t", "answer": "ok"}


class _FullTransport:
    supports_token_progress = True

    def __init__(self):
        self.seen: dict[str, object] = {}

    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_thinking=None, on_retry=None,
                            on_progress=None, on_usage=None, on_salvage=None,
                            unconstrained=False):
        self.seen = {"on_usage": on_usage is not None, "on_salvage": on_salvage is not None}
        return {"type": "answer", "thought": "t", "answer": "ok"}


@pytest.mark.asyncio
async def test_transport_missing_on_usage_is_not_handed_one():
    transport = _PartialTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    out = await engine.create_controller_step(
        plan_context={"goal": "g"}, history=[], tool_definitions=[], phase="ACTIVE",
        on_progress=lambda *a, **k: None, on_usage=lambda *a: None,
        on_salvage=lambda *a: None,
    )
    assert out["type"] == "answer"
    assert transport.seen == {"on_progress": True}


@pytest.mark.asyncio
async def test_transport_declaring_the_callbacks_still_receives_them():
    transport = _FullTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    await engine.create_controller_step(
        plan_context={"goal": "g"}, history=[], tool_definitions=[], phase="ACTIVE",
        on_progress=lambda *a, **k: None, on_usage=lambda *a: None,
        on_salvage=lambda *a: None,
    )
    assert transport.seen == {"on_usage": True, "on_salvage": True}


@pytest.mark.asyncio
async def test_the_rate_limit_wrapper_does_not_hide_what_the_transport_declares():
    # Live-found (ChatGPT plan smoke, 2026-10-07): every production transport is
    # wrapped in RateLimitedTransport, whose generate_json takes **kwargs, so the
    # engine read "accepts anything" and every turn on a transport without
    # on_salvage died with "unexpected keyword argument 'on_salvage'".
    from agentd.providers.rate_limit import RateLimitedTransport

    class _Limiter:
        async def acquire(self, priority: str) -> float:
            return 0.0

    inner = _PartialTransport()
    engine = DefaultReasoningEngine(model="m", transport=RateLimitedTransport(inner, _Limiter()))
    out = await engine.create_controller_step(
        plan_context={"goal": "g"}, history=[], tool_definitions=[], phase="ACTIVE",
        on_progress=lambda *a, **k: None, on_usage=lambda *a: None,
        on_salvage=lambda *a: None,
    )
    assert out["type"] == "answer"
    assert inner.seen == {"on_progress": True}
