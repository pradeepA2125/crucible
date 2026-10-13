"""Provider-native catalog controls and real request mappings, without account credentials."""

from __future__ import annotations

import pytest

from agentd.chatgpt_auth.service import CatalogModel
from agentd.providers.claude_subscription import ClaudeSubscriptionTransport
from agentd.providers.openai_transport import OpenAIJsonTransport
from agentd.providers.reasoning_effort import (
    EffortControlBuilder,
    EffortSupport,
    catalog_effort_support,
    control_for_transport,
)
from agentd.providers.reasoning_effort import (
    ReasoningEffort as E,
)
from agentd.providers.runtime import ProviderRuntime
from agentd.providers.validate import ProviderValidationError


def values(support):
    return [row.value.value for row in support.control.options]


def test_catalog_preserves_native_values_descriptions_and_default():
    support = catalog_effort_support(
        {
            "supported_reasoning_levels": [
                {"effort": "none", "description": "No reasoning"},
                {"effort": "minimal"},
                {"effort": "high"},
                {"effort": "xhigh", "description": "More depth"},
                {"effort": "ultra"},
            ],
            "default_reasoning_level": "high",
        }
    )
    assert values(support) == ["none", "minimal", "high", "xhigh", "ultra"]
    payload = support.payload()["control"]
    assert payload["source"] == "catalog" and payload["default"] == "high"
    assert payload["options"][3]["label"] == "Extra high"
    assert payload["options"][3]["description"] == "More depth"
    assert support.resolve(E.MAX)[0] == E.XHIGH  # no generic high/max substitution


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"supported_reasoning_levels": None},
        {"supported_reasoning_levels": [42]},
        {"supported_reasoning_levels": [{"effort": "future-value"}]},
    ],
)
def test_absent_or_unusable_metadata_is_unknown(metadata):
    assert catalog_effort_support(metadata) is None
    fallback = control_for_transport(EffortSupport(), writable=True)
    assert values(fallback) == ["off", "low", "medium", "high", "max"]
    assert all(row.state == "unknown" for row in fallback.control.options)


def test_explicit_no_effort_is_not_a_fallback():
    support = catalog_effort_support({"supported_reasoning_levels": []})
    assert values(support) == []
    assert support.resolve(E.HIGH)[0] is None
    assert values(control_for_transport(EffortSupport(), writable=False)) == []


@pytest.mark.asyncio
async def test_chatgpt_transport_uses_account_catalog_and_native_wire_values():
    support = catalog_effort_support(
        {
            "supported_reasoning_levels": [
                {"effort": "none"},
                {"effort": "minimal"},
                {"effort": "xhigh"},
            ]
        }
    )

    async def lookup(model):
        return CatalogModel(slug=model, display_name=model, reasoning_effort_support=support)

    transport = OpenAIJsonTransport(
        plan_route=True, responses_client=object(), plan_model_lookup=lookup
    )
    assert values(await transport.reasoning_effort_support("catalog-model")) == [
        "none",
        "minimal",
        "xhigh",
    ]
    for value in (E.NONE, E.MINIMAL, E.XHIGH):
        transport.set_reasoning_effort(value)
        assert (
            transport._body("catalog-model", "app prompt", {})["reasoning"]["effort"] == value.value
        )
    transport.set_reasoning_effort(None)
    assert "effort" not in transport._body("catalog-model", "app prompt", {})["reasoning"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata,expected",
    [
        (
            {
                "supported": True,
                "low": True,
                "medium": True,
                "high": True,
                "xhigh": True,
                "max": True,
            },
            ["low", "medium", "high", "xhigh", "max"],
        ),
        (
            {
                "supported": True,
                "low": True,
                "medium": True,
                "high": True,
                "xhigh": False,
                "max": False,
            },
            ["low", "medium", "high"],
        ),
        ({"supported": False}, []),
    ],
)
async def test_claude_model_capabilities_and_request_mapping(metadata, expected):
    async def lookup(model):
        return {"reasoning_effort": metadata}

    async def bearer():
        return "fixture-only"

    transport = ClaudeSubscriptionTransport(bearer, registration_id="fixture", model_lookup=lookup)
    support = await transport.reasoning_effort_support("catalog-model")
    assert values(support) == expected
    assert "off" not in values(support)
    runtime = ProviderRuntime(
        backend="claude_subscription", model="catalog-model", engines=[], transport=transport
    )
    await runtime.apply_reasoning_effort(E.XHIGH)
    body = transport._body(
        "catalog-model",
        "own agent prompt",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    )
    if expected:
        assert body["output_config"]["effort"] == ("xhigh" if "xhigh" in expected else "high")
    else:
        assert "output_config" not in body
    await runtime.apply_reasoning_effort(None)
    assert "output_config" not in transport._body(
        "catalog-model",
        "own prompt",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    )


@pytest.mark.asyncio
async def test_swap_validates_selected_effort_and_failed_swap_leaves_live_state(monkeypatch):
    import agentd.providers.runtime as module

    class Transport:
        effort = None

        def set_reasoning_effort(self, level):
            self.effort = level

        async def reasoning_effort_support(self, model):
            return EffortControlBuilder("catalog").option(E.LOW).option(E.XHIGH).build()

    old, candidate = Transport(), Transport()
    runtime = ProviderRuntime(
        backend="old", model="old-model", engines=[], transport=old, reasoning_effort=E.LOW
    )
    monkeypatch.setattr(module, "build_transport", lambda *args, **kwargs: candidate)

    async def fail(transport, model):
        assert transport.effort == E.XHIGH
        raise ProviderValidationError("fixture rejection")

    monkeypatch.setattr(module, "ping_transport", fail)
    with pytest.raises(ProviderValidationError):
        await runtime.swap(backend="new", model="new-model", reasoning_effort=E.XHIGH)
    assert runtime._transport is old and runtime.reasoning_effort == E.LOW
    assert runtime.backend == "old" and runtime.model == "old-model"

    async def reset_ping(transport, model):
        assert transport.effort is None

    monkeypatch.setattr(module, "ping_transport", reset_ping)
    result = await runtime.swap(backend="new", model="new-model", reset_reasoning_effort=True)
    assert result["reasoning_effort"] is None
