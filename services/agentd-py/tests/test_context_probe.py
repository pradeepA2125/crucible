import asyncio
import json
import random
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.context_probe as probe_mod
from agentd.api.routes import build_router
from agentd.providers.context_probe import (
    PROBE_CHARS_PER_TOKEN,
    build_probe,
    estimated_prompt_tokens,
    new_passphrase,
    recalled,
    run_context_test,
)
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def test_passphrase_is_three_reproducible_words():
    phrase = new_passphrase(random.Random(1))
    assert phrase.count("-") == 2
    assert phrase.replace("-", "").isalpha()
    assert new_passphrase(random.Random(1)) == phrase  # seeded => deterministic


def test_passphrases_differ_across_runs():
    """A fixed passphrase would let a cached or echoed response fake a pass."""
    seen = {new_passphrase() for _ in range(50)}
    assert len(seen) > 40


def test_probe_puts_the_passphrase_at_the_very_front_of_the_filler():
    """The FRONT of the prompt is what falls off when the window is overrun, so
    that is the only position that proves anything."""
    _system, payload = build_probe(4096, "velvet-harbor-quasar")
    document = str(payload["document"])
    assert document.index("velvet-harbor-quasar") < 200
    assert "velvet-harbor-quasar" not in document[len(document) // 2:]


def test_probe_is_sized_to_the_declared_window():
    _system, payload = build_probe(50_000, "a-b-c")
    estimated = estimated_prompt_tokens(_system, payload)
    assert 0.85 * 50_000 <= estimated <= 50_000


@pytest.mark.parametrize("window", [4_096, 8_192, 32_768, 128_000])
def test_small_windows_are_filled_as_completely_as_large_ones(window):
    """Headroom is proportional, so the fill ratio must not collapse at the bottom
    of the range. A flat reserve left a declared 4,096 window only 52% full, and
    under-filling is the FALSE-PASS direction: the probe would confirm a window it
    never actually tested."""
    system, payload = build_probe(window, "a-b-c")
    ratio = estimated_prompt_tokens(system, payload) / window
    assert 0.85 <= ratio <= 1.0, f"window {window} filled to {ratio:.3f}"


def test_probe_json_serializes():
    """The payload is sent as JSON by every transport; a non-serializable value
    would fail at the boundary, far from here."""
    _system, payload = build_probe(4096, "a-b-c")
    assert json.loads(json.dumps(payload))["document"]


def test_recall_is_case_and_whitespace_insensitive():
    assert recalled("The passphrase is  Velvet-Harbor-Quasar.", "velvet-harbor-quasar")
    assert recalled("velvet harbor quasar", "velvet-harbor-quasar")


def test_no_recall_when_the_answer_is_empty_or_wrong():
    """NVIDIA NIM answered an over-long prompt with completion_tokens:1 and empty
    content, HTTP 200 — an empty answer is a FAIL, not an inconclusive result."""
    assert not recalled("", "velvet-harbor-quasar")
    assert not recalled("   ", "velvet-harbor-quasar")
    assert not recalled("I don't see a passphrase.", "velvet-harbor-quasar")


def test_partial_recall_is_not_recall():
    assert not recalled("velvet harbor", "velvet-harbor-quasar")


def test_chars_per_token_sits_inside_the_measured_range():
    assert 3.71 <= PROBE_CHARS_PER_TOKEN <= 4.40


class _RecallingTransport:
    """Answers with whatever passphrase it was actually shown."""

    supports_token_progress = True

    def __init__(self, *, prompt_tokens: int | None = 31_500) -> None:
        self.prompt_tokens = prompt_tokens
        self.seen_chars = 0

    async def generate_text(
        self, *, model, system_instructions, user_payload, on_usage=None, **_kw
    ):
        document = str(user_payload["document"])
        self.seen_chars = len(document)
        if on_usage is not None and self.prompt_tokens is not None:
            on_usage(self.prompt_tokens, 8)
        return document.split("\n")[0].removeprefix("PASSPHRASE: ")


class _AmnesiacTransport:
    """The NIM shape: HTTP 200, empty content, no complaint."""

    supports_token_progress = False

    async def generate_text(self, *, model, system_instructions, user_payload, **_kw):
        return ""


class _FailingTransport:
    async def generate_text(self, *, model, system_instructions, user_payload, **_kw):
        raise RuntimeError("NIM API error: 400 context length exceeded")


@pytest.mark.asyncio
async def test_recall_passes_and_reports_the_providers_own_count(monkeypatch):
    transport = _RecallingTransport()
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: transport
    )
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is True and result.recalled is True
    assert result.prompt_tokens == 31_500 and result.exact is True
    assert result.error is None


@pytest.mark.asyncio
async def test_empty_answer_is_a_failed_window_not_an_error(monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _AmnesiacTransport()
    )
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=600_000
    )
    assert result.ok is True  # the CALL succeeded
    assert result.recalled is False  # the WINDOW did not
    assert result.error is None
    assert result.exact is False and result.prompt_tokens is not None  # our estimate


@pytest.mark.asyncio
async def test_provider_error_is_surfaced_verbatim(monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _FailingTransport()
    )
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is False and result.recalled is False
    assert "context length exceeded" in (result.error or "")


@pytest.mark.asyncio
async def test_timeout_is_reported_as_a_timeout(monkeypatch):
    class _Slow:
        async def generate_text(self, **_kw):
            await asyncio.sleep(5)
            return "never"

    monkeypatch.setattr(probe_mod, "build_transport", lambda b, credentials=None: _Slow())
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None,
        window_tokens=32_768, timeout_sec=0.05,
    )
    assert result.ok is False and "did not respond" in (result.error or "")


@pytest.mark.asyncio
async def test_transport_without_token_progress_never_sees_on_usage(monkeypatch):
    """Gated exactly like reasoning/engine.py: the other eight transports must not
    receive a kwarg they do not declare."""
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _AmnesiacTransport()
    )
    result = await run_context_test(
        backend="gemini", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is True  # no TypeError from an unexpected kwarg


def _route_client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(), object(),
            ShadowWorkspaceManager(tmp_path / "shadows"), None, None,
        )
    )
    return TestClient(app)


def test_context_test_route_is_always_200(tmp_path, monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _FailingTransport()
    )
    response = _route_client(tmp_path).post(
        "/v1/providers/context-test",
        json={"backend": "openai_compatible", "model": "m", "context_window": 32768},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and "context length exceeded" in body["error"]


def test_context_test_route_returns_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _RecallingTransport()
    )
    body = _route_client(tmp_path).post(
        "/v1/providers/context-test",
        json={"backend": "openai_compatible", "model": "m", "context_window": 32768},
    ).json()
    assert body == {
        "ok": True, "recalled": True, "prompt_tokens": 31_500, "exact": True,
    }
