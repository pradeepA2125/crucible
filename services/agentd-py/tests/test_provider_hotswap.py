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
    assert result == {"backend": "groq", "model": "m2"}


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
    }


def test_put_route_409_when_no_runtime(tmp_path: Path) -> None:
    client = _client(tmp_path, None)
    assert client.put("/v1/config/provider", json={"backend": "groq"}).status_code == 409
    assert client.get("/v1/config").json()["provider"] is None
