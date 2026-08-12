import pytest

from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
from agentd.providers.runtime import ProviderRuntime


class _Transport:
    def __init__(self, support: EffortSupport | None = None) -> None:
        self.effort: ReasoningEffort | None = None
        self._support = support if support is not None else EffortSupport(
            supported=frozenset(ReasoningEffort)
        )

    def set_reasoning_effort(self, level):
        self.effort = level

    async def reasoning_effort_support(self, model):
        return self._support

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


class _PlainTransport:
    """A transport that never grew the optional members — must never be asked."""

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


@pytest.mark.asyncio
async def test_support_degrades_to_all_unknown_for_a_transport_without_the_members():
    rt = ProviderRuntime(
        backend="scripted", model="m", engines=[], transport=_PlainTransport()
    )
    support = await rt.effort_support()
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_support_degrades_to_all_unknown_when_the_transport_raises():
    class _Boom(_Transport):
        async def reasoning_effort_support(self, model):
            raise RuntimeError("registry down")

    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=_Boom())
    support = await rt.effort_support()
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_apply_effort_clamps_and_records_the_note():
    transport = _Transport(
        EffortSupport(
            supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.HIGH}),
            unsupported={ReasoningEffort.MAX: "tops out at high"},
        )
    )
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    await rt.apply_reasoning_effort(ReasoningEffort.MAX)
    assert transport.effort == ReasoningEffort.HIGH
    assert rt.reasoning_effort == ReasoningEffort.HIGH
    assert rt.reasoning_effort_note is not None


@pytest.mark.asyncio
async def test_apply_none_clears_the_transport_and_the_note():
    transport = _Transport()
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    await rt.apply_reasoning_effort(ReasoningEffort.HIGH)
    await rt.apply_reasoning_effort(None)
    assert transport.effort is None
    assert rt.reasoning_effort is None
    assert rt.reasoning_effort_note is None


@pytest.mark.asyncio
async def test_swap_carries_the_effort_onto_the_new_transport(monkeypatch):
    import agentd.providers.runtime as runtime_mod

    built = _Transport()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda backend, credentials=None: built)
    monkeypatch.setattr(runtime_mod, "resolve_model", lambda backend: "new-model")

    async def _ok(transport, model):
        return None

    monkeypatch.setattr(runtime_mod, "ping_transport", _ok)

    class _Engine:
        def set_provider(self, *, model, transport):
            self.model = model

    rt = ProviderRuntime(
        backend="b",
        model="m",
        engines=[_Engine()],
        transport=_Transport(),
        reasoning_effort=ReasoningEffort.LOW,
    )
    result = await rt.swap(backend="b2")
    # A model-only swap must carry the existing level onto the freshly built
    # transport — absent means "unchanged", exactly like context_window.
    assert built.effort == ReasoningEffort.LOW
    assert result["reasoning_effort"] == "low"
    assert "supported" in result["reasoning_effort_support"]
