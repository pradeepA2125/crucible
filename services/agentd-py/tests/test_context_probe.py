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


@pytest.mark.parametrize("window", [1_024, 4_096, 8_192, 32_768, 128_000])
def test_small_windows_are_filled_as_completely_as_large_ones(window):
    """Headroom is proportional (with a floor), so the fill ratio must not
    collapse arbitrarily at the bottom of the range. A flat reserve left a
    declared 4,096 window only 52% full, and under-filling is the FALSE-PASS
    direction: the probe would confirm a window it never actually tested.

    The lower bound is DERIVED from build_probe's own headroom formula — the max
    of the flat floor, the proportional fraction, AND
    `_PROBE_COMPLETION_TOKENS + _NON_DOCUMENT_SLACK_TOKENS` — not a fixed
    constant. A fixed constant is exactly what let this test keep passing while
    run_context_test asked generate_text for a completion budget that, combined
    with the filled prompt, overran the declared window: first the transport's
    4096-token default (fixed in an earlier round), then later a 16-token budget
    that was too SMALL to let a reasoning model finish thinking and answer,
    which read back as a false "window too large" (see context_probe.py's module
    docstring for both measurements). The second assertion below is the direct
    regression guard for the too-large direction: prompt + the probe's actual
    completion budget must never exceed the declared window, at any window down
    to the route's 1,024 floor.

    The fill ratio is NOT asserted to approach ~0.95 at the smallest windows
    anymore — a 512-token completion budget is a real fixed cost, and at a
    1,024-token window it dominates. This only asserts what the headroom formula
    actually guarantees, not an aspirational ratio.
    """
    headroom = max(
        probe_mod._MIN_HEADROOM_TOKENS,
        int(window * probe_mod._HEADROOM_FRAC),
        probe_mod._PROBE_COMPLETION_TOKENS + probe_mod._NON_DOCUMENT_SLACK_TOKENS,
    )
    expected_floor = (window - headroom) / window

    system, payload = build_probe(window, "a-b-c")
    prompt_tokens = estimated_prompt_tokens(system, payload)
    ratio = prompt_tokens / window

    assert expected_floor <= ratio <= 1.0, f"window {window} filled to {ratio:.3f}"
    assert prompt_tokens + probe_mod._PROBE_COMPLETION_TOKENS <= window


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


def test_probe_completion_budget_is_not_shrunk_below_the_measured_reasoning_floor():
    """A reasoning model (e.g. NVIDIA NIM's nvidia/nemotron-3-ultra-550b-a55b)
    spends its completion budget on hidden thinking BEFORE its visible answer.
    Measured live at a fixed, trivially in-window 5,000-token prompt: a 16-token
    budget was consumed entirely by thinking and returned the truncated
    reasoning fragment 'The user is asking for the passphrase from the' — never
    reaching the answer — for both filler styles tested. A 512-token budget
    produced the correct three-word passphrase for both. That false
    "window too large" result is exactly the failure this module exists to
    avoid, just from the opposite direction of the too-large bug. Do not shrink
    _PROBE_COMPLETION_TOKENS below 512 without a live re-measurement on a
    reasoning model."""
    assert probe_mod._PROBE_COMPLETION_TOKENS >= 512


class _RecallingTransport:
    """Answers with whatever passphrase it was actually shown."""

    supports_token_progress = True

    def __init__(self, *, prompt_tokens: int | None = 31_500) -> None:
        self.prompt_tokens = prompt_tokens
        self.seen_chars = 0
        self.seen_kwargs: dict[str, object] = {}

    async def generate_text(
        self, *, model, system_instructions, user_payload, on_usage=None, **_kw
    ):
        document = str(user_payload["document"])
        self.seen_chars = len(document)
        self.seen_kwargs = _kw
        if on_usage is not None and self.prompt_tokens is not None:
            on_usage(self.prompt_tokens, 8)
        return document.split("\n")[0].removeprefix("PASSPHRASE: ")


class _AmnesiacTransport:
    """The NIM shape: HTTP 200, empty content, no complaint."""

    supports_token_progress = False

    async def generate_text(self, *, model, system_instructions, user_payload, **_kw):
        return ""


class _StrictNoProgressTransport:
    """Declares no supports_token_progress capability AND, unlike the fakes
    above, accepts no catch-all **_kw — so it raises TypeError on ANY kwarg
    the probe is not entitled to send it. Proves max_tokens is gated exactly
    like on_usage, not just that the happy path tolerates it."""

    supports_token_progress = False

    async def generate_text(self, *, model, system_instructions, user_payload):
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
async def test_probe_requests_a_small_completion_budget_not_the_transport_default(
    monkeypatch,
):
    """The probe's visible answer is three words, but the budget must still be big
    enough for a reasoning model to finish thinking before it answers (see
    test_probe_completion_budget_is_not_shrunk_below_the_measured_reasoning_floor).
    It must not ask for the transport's much larger anti-runaway default (4096),
    which is what made prompt + max_tokens overrun a correctly declared window on
    any endpoint that validates the sum."""
    transport = _RecallingTransport()
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: transport
    )
    await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=32_768
    )
    assert "max_tokens" in transport.seen_kwargs
    assert transport.seen_kwargs["max_tokens"] == probe_mod._PROBE_COMPLETION_TOKENS
    assert transport.seen_kwargs["max_tokens"] < 4096


@pytest.mark.asyncio
async def test_transport_without_token_progress_is_not_sent_max_tokens_either(
    monkeypatch,
):
    """Gated exactly like on_usage: a transport that hasn't declared
    supports_token_progress must not receive a max_tokens kwarg it never
    promised to accept. _StrictNoProgressTransport has no **_kw catch-all, so
    this raises TypeError (via run_context_test's own except Exception, which
    would surface it as a failed result, not a clean ok=True) if the gate is
    ever removed."""
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _StrictNoProgressTransport()
    )
    result = await run_context_test(
        backend="gemini", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is True
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
