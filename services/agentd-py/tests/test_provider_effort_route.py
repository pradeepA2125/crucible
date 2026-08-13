# services/agentd-py/tests/test_provider_effort_route.py
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.runtime as runtime_mod
from agentd.api.routes import build_router
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
from agentd.providers.runtime import ProviderRuntime
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _Transport:
    def __init__(self) -> None:
        self.effort = None

    def set_reasoning_effort(self, level):
        self.effort = level

    async def reasoning_effort_support(self, model):
        return EffortSupport(
            supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.HIGH}),
            unsupported={ReasoningEffort.MAX: "tops out at high"},
        )

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


def _client(tmp_path: Path, rt: ProviderRuntime) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(),
            object(),
            ShadowWorkspaceManager(tmp_path / "shadows"),
            None,
            None,
            provider_runtime=rt,
        )
    )
    return TestClient(app)


def test_get_config_reports_the_level_and_the_support_map(tmp_path: Path) -> None:
    rt = ProviderRuntime(
        backend="openai_compatible",
        model="m",
        engines=[],
        transport=_Transport(),
        reasoning_effort=ReasoningEffort.HIGH,
    )
    body = _client(tmp_path, rt).get("/v1/config").json()
    provider = body["provider"]
    assert provider["reasoning_effort"] == "high"
    assert provider["reasoning_effort_support"]["supported"] == ["high", "low"]
    assert provider["reasoning_effort_support"]["unsupported"]["max"] == "tops out at high"


@pytest.mark.asyncio
async def test_get_config_reports_the_clamp_note_so_it_survives_a_reload(tmp_path: Path) -> None:
    """The note is what makes a silent rung substitution visible after a model
    swap or a plain page reload (neither replays the PUT response that first
    carried it) — GET /v1/config must be sourced from the SAME durable place
    (ProviderRuntime.reasoning_effort_note) rather than only the swap response."""
    transport = _Transport()
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    await rt.apply_reasoning_effort(ReasoningEffort.MAX)  # unsupported -> clamps to high

    body = _client(tmp_path, rt).get("/v1/config").json()
    provider = body["provider"]
    assert provider["reasoning_effort"] == "high"
    assert provider["reasoning_effort_note"] is not None
    assert "tops out at high" in provider["reasoning_effort_note"]


@pytest.mark.asyncio
async def test_get_config_reports_no_note_when_nothing_clamped(tmp_path: Path) -> None:
    transport = _Transport()
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    await rt.apply_reasoning_effort(ReasoningEffort.HIGH)  # supported -> no clamp

    body = _client(tmp_path, rt).get("/v1/config").json()
    assert body["provider"]["reasoning_effort_note"] is None


def test_put_clamps_and_returns_the_effective_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _Transport()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda backend, credentials=None: transport)
    monkeypatch.setattr(runtime_mod, "resolve_model", lambda backend: "m")

    async def _ok(t, model):
        return None

    monkeypatch.setattr(runtime_mod, "ping_transport", _ok)

    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    res = _client(tmp_path, rt).put(
        "/v1/config/provider", json={"backend": "b", "reasoning_effort": "max"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reasoning_effort"] == "high"
    assert "tops out at high" in body["reasoning_effort_note"]


def test_put_without_the_field_leaves_the_level_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _Transport()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda backend, credentials=None: transport)
    monkeypatch.setattr(runtime_mod, "resolve_model", lambda backend: "m")

    async def _ok(t, model):
        return None

    monkeypatch.setattr(runtime_mod, "ping_transport", _ok)

    rt = ProviderRuntime(
        backend="b",
        model="m",
        engines=[],
        transport=transport,
        reasoning_effort=ReasoningEffort.LOW,
    )
    body = _client(tmp_path, rt).put("/v1/config/provider", json={"backend": "b"}).json()
    assert body["reasoning_effort"] == "low"


def test_an_unrecognized_level_is_rejected(tmp_path: Path) -> None:
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=_Transport())
    res = _client(tmp_path, rt).put(
        "/v1/config/provider", json={"backend": "b", "reasoning_effort": "banana"}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_a_seeded_rung_reaches_the_transport_only_after_it_is_applied() -> None:
    """ProviderRuntime.__init__ is sync and cannot await, so a rung passed in at
    construction (e.g. the CRUCIBLE_REASONING_EFFORT env seed in main.py) is only
    a raw attribute assignment until something calls apply_reasoning_effort. Before
    main.py's startup hook was added, nothing ever did — GET /v1/config reported
    the seeded level while the transport never received it."""
    transport = _Transport()
    rt = ProviderRuntime(
        backend="b",
        model="m",
        engines=[],
        transport=transport,
        reasoning_effort=ReasoningEffort.HIGH,
    )
    assert rt.reasoning_effort == ReasoningEffort.HIGH
    assert transport.effort is None

    await rt.apply_reasoning_effort(rt.reasoning_effort)

    assert transport.effort == ReasoningEffort.HIGH
