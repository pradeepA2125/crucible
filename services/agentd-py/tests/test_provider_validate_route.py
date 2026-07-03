from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.validate as validate_mod
from agentd.api.routes import build_router
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _OkTransport:
    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


class _BoomTransport:
    async def generate_text(self, *, model, system_instructions, user_payload):
        raise RuntimeError("401 invalid api key")


class _RecordingFactory:
    def __init__(self, transport) -> None:
        self._transport = transport
        self.last_credentials: dict[str, str] | None = None

    def __call__(self, backend, credentials=None):
        self.last_credentials = credentials
        return self._transport


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(),
            object(),
            ShadowWorkspaceManager(tmp_path / "shadows"),
            None,
            None,
        )
    )
    return app


def _client(
    tmp_path: Path, transport, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, _RecordingFactory]:
    factory = _RecordingFactory(transport)
    monkeypatch.setattr(validate_mod, "build_transport", factory)
    return TestClient(_app(tmp_path)), factory


def test_validate_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory = _client(tmp_path, _OkTransport(), monkeypatch)
    resp = client.post(
        "/v1/providers/validate",
        json={"backend": "groq", "credentials": {"GROQ_API_KEY": "sk-x"}},
    )
    body = resp.json()
    assert resp.status_code == 200 and body["ok"] is True
    assert body["model"]  # resolved default
    assert factory.last_credentials == {"GROQ_API_KEY": "sk-x"}


def test_validate_provider_error_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, _BoomTransport(), monkeypatch)
    body = client.post("/v1/providers/validate", json={"backend": "groq"}).json()
    assert body["ok"] is False and "invalid api key" in body["error"]


def test_validate_unknown_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, _OkTransport(), monkeypatch)
    body = client.post("/v1/providers/validate", json={"backend": "nope"}).json()
    assert body["ok"] is False and "Unsupported backend" in body["error"]


def test_validate_missing_key_is_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No factory stubbing here — the REAL factory + a deleted env key must come
    # back as ok:false with the transport's actionable message, not a 500.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    body = TestClient(_app(tmp_path)).post(
        "/v1/providers/validate", json={"backend": "openai"}
    ).json()
    assert body["ok"] is False and "OPENAI_API_KEY" in body["error"]
