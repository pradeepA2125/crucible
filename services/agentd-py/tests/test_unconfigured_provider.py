"""A provider that cannot construct must not kill the backend at import.

Found live: a managed install whose stored provider was `openai_compatible`
without CRUCIBLE_OPENAI_COMPAT_BASE_URL. `build_transport` raised at module
import, uvicorn died with a Python traceback, the extension's health poll timed
out after 60s, and the user was left with no way back — the Settings panel is
the in-product fix for a wrong provider, and it needs a LIVE backend to validate
against. A config mistake became an unrecoverable install.

So construction failure has to degrade, not abort: the app boots, every route
still answers, and the failure is reported as data the UI can act on. The
existing hot-swap (PUT /v1/config/provider builds a NEW transport) is then the
recovery path it was always meant to be.
"""
from __future__ import annotations

import pytest

from agentd.providers.unconfigured import (
    UnconfiguredProviderTransport,
    build_transport_or_placeholder,
)


def test_returns_the_real_transport_when_construction_succeeds():
    sentinel = object()
    transport, error = build_transport_or_placeholder(
        "gemini", build=lambda _b: sentinel
    )
    assert transport is sentinel
    assert error is None


def test_degrades_to_a_placeholder_when_construction_raises():
    def _boom(_backend: str) -> object:
        raise RuntimeError("CRUCIBLE_OPENAI_COMPAT_BASE_URL is required")

    transport, error = build_transport_or_placeholder("openai_compatible", build=_boom)
    assert isinstance(transport, UnconfiguredProviderTransport)
    # The provider's own message is what names the fix — keep it verbatim.
    assert "CRUCIBLE_OPENAI_COMPAT_BASE_URL is required" in (error or "")


@pytest.mark.asyncio
async def test_the_placeholder_reports_the_original_reason_when_used():
    placeholder = UnconfiguredProviderTransport("openai_compatible", "base url missing")
    with pytest.raises(RuntimeError) as excinfo:
        await placeholder.generate_text(
            model="m", system_instructions="s", user_payload={}
        )
    message = str(excinfo.value)
    assert "openai_compatible" in message
    assert "base url missing" in message
    # It must point at the way out, or the user is stuck with the same dead end.
    assert "Settings" in message


@pytest.mark.asyncio
async def test_the_placeholder_fails_json_calls_the_same_way():
    placeholder = UnconfiguredProviderTransport("gemini", "GEMINI_API_KEY is required")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required"):
        await placeholder.generate_json(
            model="m", schema_name="S", schema={}, system_instructions="s",
            user_payload={},
        )


def test_the_placeholder_satisfies_the_transport_shape_the_engine_reads():
    """DefaultReasoningEngine reads these off the transport at construction; a
    placeholder missing them would swap one import-time crash for another."""
    placeholder = UnconfiguredProviderTransport("openai", "no key")
    assert placeholder.supports_oneof_grammar is False
    assert placeholder.supports_anyof_grammar is False
