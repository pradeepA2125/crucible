from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.runtime as runtime_mod
from agentd.api.routes import build_router
from agentd.providers.runtime import ProviderRuntime
from agentd.reasoning.engine import DefaultReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _Transport:
    def __init__(self, name: str = "t") -> None:
        self.name = name

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


def _runtime() -> tuple[ProviderRuntime, DefaultReasoningEngine]:
    engine = DefaultReasoningEngine(model="old-model", transport=_Transport("old"))
    return ProviderRuntime(backend="openai", model="old-model", engines=[engine]), engine


def _client(tmp_path: Path, rt: ProviderRuntime | None) -> TestClient:
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


@pytest.mark.asyncio
async def test_swap_mutates_every_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    rt, engine = _runtime()
    new_transport = _Transport("new")
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: new_transport
    )
    result = await rt.swap(backend="groq", model="m2")
    assert engine._model == "m2" and engine._transport is new_transport
    assert (rt.backend, rt.model) == ("groq", "m2")
    assert result == {
        "backend": "groq",
        "model": "m2",
        "reasoning_effort": None,
        "reasoning_effort_note": None,
        "reasoning_effort_support": {"supported": [], "unsupported": {}},
    }


@pytest.mark.asyncio
async def test_swap_failure_leaves_engine_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentd.providers.validate import ProviderValidationError

    rt, engine = _runtime()

    async def _boom(transport, model, timeout_sec=30.0):
        raise ProviderValidationError("bad key")

    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport()
    )
    monkeypatch.setattr(runtime_mod, "ping_transport", _boom)
    with pytest.raises(ProviderValidationError):
        await rt.swap(backend="groq")
    assert engine._model == "old-model" and rt.backend == "openai"


def test_put_route_and_config_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt, _ = _runtime()
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport()
    )
    client = _client(tmp_path, rt)
    resp = client.put("/v1/config/provider", json={"backend": "groq", "model": "m2"})
    assert resp.status_code == 200 and resp.json()["model"] == "m2"
    assert client.get("/v1/config").json()["provider"] == {
        "backend": "groq",
        "model": "m2",
        "context_window": None,
    }


def test_put_route_400_when_transport_cannot_be_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport that refuses to construct is a user configuration mistake with
    its own actionable message ("set CRUCIBLE_OPENAI_COMPAT_BASE_URL"). It raises
    RuntimeError, which the route does not catch — so it used to escape as an
    unactionable 500. The openai_compatible backend makes a missing base URL a
    routine mistake rather than an exotic one."""
    rt, engine = _runtime()

    def _boom(backend, credentials=None):
        raise RuntimeError("CRUCIBLE_OPENAI_COMPAT_BASE_URL is required")

    monkeypatch.setattr(runtime_mod, "build_transport", _boom)
    resp = _client(tmp_path, rt).put(
        "/v1/config/provider", json={"backend": "openai_compatible"}
    )
    assert resp.status_code == 400
    assert "CRUCIBLE_OPENAI_COMPAT_BASE_URL" in resp.json()["detail"]
    # A failed swap leaves the live engines untouched, same as a failed ping.
    assert engine._model == "old-model" and rt.backend == "openai"


def test_put_route_409_when_no_runtime(tmp_path: Path) -> None:
    client = _client(tmp_path, None)
    assert client.put("/v1/config/provider", json={"backend": "groq"}).status_code == 409
    assert client.get("/v1/config").json()["provider"] is None


class _WindowSink:
    def __init__(self) -> None:
        self.window: int | None = None

    def set_window_tokens(self, window_tokens: int) -> None:
        self.window = window_tokens


@pytest.mark.asyncio
async def test_swap_applies_the_context_window_to_every_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = DefaultReasoningEngine(model="old-model", transport=_Transport("old"))
    sink_a, sink_b = _WindowSink(), _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model", engines=[engine],
        window_sinks=[sink_a, sink_b], context_window=128_000,
    )
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    result = await rt.swap(backend="groq", model="m2", context_window=32_768)
    assert sink_a.window == 32_768 and sink_b.window == 32_768
    assert rt.context_window == 32_768
    assert result["context_window"] == 32_768


@pytest.mark.asyncio
async def test_swap_without_a_window_leaves_the_existing_one_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-only hot-swap (the composer's model menu) must not reset the window
    the user declared in Settings."""
    sink = _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
        window_sinks=[sink], context_window=200_000,
    )
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    result = await rt.swap(backend="groq", model="m2")
    assert sink.window is None  # never touched
    assert rt.context_window == 200_000
    assert result["context_window"] == 200_000


@pytest.mark.asyncio
async def test_failed_swap_does_not_apply_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate-then-mutate: a bad key must not silently change compaction."""
    from agentd.providers.validate import ProviderValidationError

    sink = _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
        window_sinks=[sink], context_window=128_000,
    )

    async def _boom(transport, model, timeout_sec=30.0):
        raise ProviderValidationError("bad key")

    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport()
    )
    monkeypatch.setattr(runtime_mod, "ping_transport", _boom)
    with pytest.raises(ProviderValidationError):
        await rt.swap(backend="groq", model="m2", context_window=8192)
    assert sink.window is None and rt.context_window == 128_000


@pytest.mark.asyncio
async def test_put_provider_applies_the_context_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
        window_sinks=[sink], context_window=128_000,
    )
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    client = _client(tmp_path, rt)
    response = client.put(
        "/v1/config/provider",
        json={"backend": "groq", "model": "m2", "context_window": 32768},
    )
    assert response.status_code == 200
    assert response.json()["context_window"] == 32768
    assert sink.window == 32768


def test_put_provider_rejects_a_nonsense_window(tmp_path: Path) -> None:
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
    )
    client = _client(tmp_path, rt)
    assert client.put(
        "/v1/config/provider", json={"backend": "groq", "context_window": 0}
    ).status_code == 422


def test_config_reports_the_effective_context_window(tmp_path: Path) -> None:
    rt = ProviderRuntime(
        backend="openai", model="gpt-5",
        engines=[DefaultReasoningEngine(model="gpt-5", transport=_Transport())],
        context_window=200_000,
    )
    payload = _client(tmp_path, rt).get("/v1/config").json()
    assert payload["provider"] == {
        "backend": "openai", "model": "gpt-5", "context_window": 200_000
    }


@pytest.mark.asyncio
async def test_a_successful_swap_clears_the_startup_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of degrading instead of aborting: the backend stays up so
    the user can swap to a working provider, and that swap must stop reporting a
    failure that no longer describes the process."""
    rt = ProviderRuntime(
        backend="openai_compatible", model="m",
        engines=[DefaultReasoningEngine(model="m", transport=_Transport("old"))],
        config_error="CRUCIBLE_OPENAI_COMPAT_BASE_URL is required",
    )
    assert rt.config_error is not None
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    await rt.swap(backend="gemini", model="gemini-flash-latest")
    assert rt.config_error is None


@pytest.mark.asyncio
async def test_a_failed_swap_leaves_the_startup_error_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentd.providers.validate import ProviderValidationError

    rt = ProviderRuntime(
        backend="openai_compatible", model="m",
        engines=[DefaultReasoningEngine(model="m", transport=_Transport("old"))],
        config_error="base url missing",
    )

    async def _boom(transport, model, timeout_sec=30.0):
        raise ProviderValidationError("still broken")

    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport()
    )
    monkeypatch.setattr(runtime_mod, "ping_transport", _boom)
    with pytest.raises(ProviderValidationError):
        await rt.swap(backend="gemini", model="m2")
    assert rt.config_error == "base url missing"
