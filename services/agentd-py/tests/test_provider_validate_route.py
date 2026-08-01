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


# ---------------------------------------------------------------------------
# JSON-mode capability probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_reports_strict_support_for_openai_compatible(monkeypatch) -> None:
    """The probe turns 'will this endpoint work with Crucible?' into a visible fact."""
    from agentd.providers import validate as validate_mod

    class _FakeTransport:
        supports_oneof_grammar = True
        json_mode = "strict"

        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            return {"ok": True}

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _FakeTransport())
    result = await validate_mod.ping_provider(
        "openai_compatible", "some/model", {"CRUCIBLE_OPENAI_COMPAT_BASE_URL": "http://x/v1"}
    )
    assert result.model == "some/model"
    assert result.json_mode == "strict"
    assert result.warning is None


@pytest.mark.asyncio
async def test_validate_warns_when_strict_schema_unsupported(monkeypatch) -> None:
    from agentd.providers import validate as validate_mod

    class _NoSchemaTransport:
        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            raise RuntimeError("response_format not supported")

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _NoSchemaTransport())
    result = await validate_mod.ping_provider(
        "openai_compatible", "some/model", {"CRUCIBLE_OPENAI_COMPAT_BASE_URL": "http://x/v1"}
    )
    assert result.json_mode == "json_object"
    assert result.warning is not None
    assert "lower reliability" in result.warning


@pytest.mark.asyncio
async def test_validate_skips_probe_for_known_providers(monkeypatch) -> None:
    """Known providers must not pay an extra request to re-confirm what we know."""
    from agentd.providers import validate as validate_mod

    calls = {"json": 0}

    class _T:
        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            calls["json"] += 1
            return {}

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _T())
    result = await validate_mod.ping_provider("openai", "gpt-5", None)
    assert calls["json"] == 0
    assert result.json_mode is None
    assert result.warning is None


@pytest.mark.asyncio
async def test_probe_reads_the_transports_own_verdict_not_just_the_exception(
    monkeypatch,
) -> None:
    """The real transport does NOT raise when strict is rejected — it falls back to
    json_object and returns a perfectly good dict. A probe that only watches for an
    exception would report "strict" for exactly the endpoint the feature exists for.
    The sticky `json_mode` the transport records is the authoritative answer.
    """
    from agentd.providers import validate as validate_mod

    class _FallsBackSilently:
        def __init__(self) -> None:
            self.json_mode = "strict"

        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            self.json_mode = "json_object"  # what _downgrade_json_mode does
            return {"ok": True}  # the fallback succeeded — no exception reaches us

    monkeypatch.setattr(
        validate_mod, "build_transport", lambda *a, **k: _FallsBackSilently()
    )
    result = await validate_mod.ping_provider("openai_compatible", "m", None)
    assert result.json_mode == "json_object"
    assert result.warning is not None and "lower reliability" in result.warning


@pytest.mark.asyncio
async def test_transient_probe_failure_is_reported_as_unknown_not_degraded(
    monkeypatch,
) -> None:
    """A rate limit or a broken stream says nothing about response_format support.
    Reporting "json_object" for it would push the user toward a worse config on the
    strength of one blip — the same asymmetry the sticky downgrade guards against.
    """
    from agentd.providers import validate as validate_mod
    from agentd.providers.openai_compatible_transport import TransientTransportError

    class _Transient:
        json_mode = "strict"  # the transport judged the failure non-probative

        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            raise TransientTransportError("stream failed mid-response")

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _Transient())
    result = await validate_mod.ping_provider("openai_compatible", "m", None)
    assert result.json_mode is None
    assert result.warning is not None and "Could not" in result.warning


@pytest.mark.asyncio
async def test_rate_limited_probe_on_a_stateless_transport_is_not_degraded(
    monkeypatch,
) -> None:
    """Same asymmetry for a transport that exposes no mode of its own — the probe
    reuses the transport module's probative/non-probative predicate rather than
    treating every exception as proof."""
    from agentd.providers import validate as validate_mod

    class _RateLimited(Exception):
        status_code = 429

    class _T:
        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            raise _RateLimited("429 too many requests")

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _T())
    result = await validate_mod.ping_provider("openai_compatible", "m", None)
    assert result.json_mode is None


@pytest.mark.asyncio
async def test_probe_is_bounded_by_its_own_timeout(monkeypatch) -> None:
    """A hung probe must not hold the validate request open forever."""
    import asyncio

    from agentd.providers import validate as validate_mod

    class _Hangs:
        json_mode = "strict"

        async def generate_json(self, **kwargs):
            await asyncio.sleep(30)
            return {}

    assert await validate_mod.probe_json_mode(_Hangs(), "m", timeout_sec=0.01) is None


def test_route_surfaces_json_mode_and_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoSchema:
        async def generate_text(self, *, model, system_instructions, user_payload):
            return "OK"

        async def generate_json(self, **kwargs):
            raise RuntimeError("response_format not supported")

    client, _ = _client(tmp_path, _NoSchema(), monkeypatch)
    body = client.post(
        "/v1/providers/validate", json={"backend": "openai_compatible", "model": "m"}
    ).json()
    assert body["ok"] is True and body["model"] == "m"
    assert body["json_mode"] == "json_object"
    assert "lower reliability" in body["warning"]


def test_route_omits_json_mode_for_known_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, _OkTransport(), monkeypatch)
    body = client.post("/v1/providers/validate", json={"backend": "groq"}).json()
    assert body["ok"] is True
    assert "json_mode" not in body and "warning" not in body
