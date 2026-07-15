"""Tier 2 — tight (oneOf/anyOf) controller schema, gated by a provider-capability flag.

The flat schema lists every variant's fields as optional siblings, so the grammar
PERMITS cross-variant bleed (e.g. {type:"answer", tool:"x"}). On a provider whose
grammar engine enforces JSON-schema `oneOf` (measured: llama.cpp/TQP does), a
discriminated-union schema makes that bleed STRUCTURALLY impossible.

Provider matrix (live-verified):
  - TurboQuant/llama.cpp: oneOf — strict const-discriminated union, full enforcement
  - watsonx (json_schema mode): anyOf — enforces per-branch required fields at token level
  - OpenAI/Groq/OpenRouter: anyOf — native structured outputs enforce per-branch fields
  - Gemini (response_json_schema): anyOf — enforces required fields within matched branch;
    const discriminator is not strictly enforced (model may emit unknown type values),
    but all required action fields (tool+args, answer, etc.) are present in the output.
  - Anthropic/HuggingFace: prompt-only with narrowing retry fallback

These tests pin: (1) the tight schema shape, (2) the per-provider flag, (3) the
engine wiring that selects flat vs tight off the flag.
"""
from __future__ import annotations

import pytest

from agentd.chat.controller_prompts import controller_response_schema


def _branches_by_type(schema: dict) -> dict[str, dict]:
    """Index a oneOf schema's branches by their const `type` discriminator."""
    out: dict[str, dict] = {}
    for branch in schema["oneOf"]:  # type: ignore[index]
        const = branch["properties"]["type"]["const"]
        out[const] = branch
    return out


# ----------------------------------------------------------------------------
# (1) Tight schema shape
# ----------------------------------------------------------------------------

def test_flat_schema_is_default_and_unchanged() -> None:
    schema = controller_response_schema(phase="DECIDE")
    assert "oneOf" not in schema
    assert schema["properties"]["type"]["enum"] == [  # type: ignore[index]
        "tool_call", "answer", "clarify", "propose_mode"
    ]


def test_tight_decide_is_oneof_of_the_four_phase_variants() -> None:
    schema = controller_response_schema(phase="DECIDE", tight=True)
    assert set(schema.keys()) == {"oneOf"}
    branches = _branches_by_type(schema)
    assert set(branches) == {"tool_call", "answer", "clarify", "propose_mode"}


def test_tight_edit_is_oneof_of_the_edit_phase_variants() -> None:
    schema = controller_response_schema(phase="EDIT", tight=True)
    branches = _branches_by_type(schema)
    assert set(branches) == {"tool_call", "edit", "clarify", "submit_changes"}


def test_tight_branch_forbids_cross_variant_bleed() -> None:
    # The whole point: a propose_mode response can carry ONLY its own fields.
    branches = _branches_by_type(controller_response_schema(phase="DECIDE", tight=True))
    propose = branches["propose_mode"]
    assert propose["additionalProperties"] is False
    props = set(propose["properties"].keys())
    # none of the OTHER variants' discriminating fields may be present
    assert props.isdisjoint({"tool", "args", "answer", "question", "patch_ops", "summary"})


def test_tight_tool_call_requires_tool_and_args() -> None:
    branches = _branches_by_type(controller_response_schema(phase="DECIDE", tight=True))
    tool_call = branches["tool_call"]
    assert set(tool_call["required"]) == {"type", "thought", "tool", "args"}
    assert tool_call["additionalProperties"] is False


def test_tight_answer_requires_a_nonempty_answer_field() -> None:
    branches = _branches_by_type(controller_response_schema(phase="DECIDE", tight=True))
    answer = branches["answer"]
    assert "answer" in answer["required"]
    assert answer["properties"]["answer"]["type"] == "string"


def test_tight_propose_mode_requires_all_its_fields() -> None:
    branches = _branches_by_type(controller_response_schema(phase="DECIDE", tight=True))
    propose = branches["propose_mode"]
    assert set(propose["required"]) == {
        "type", "thought", "plan_sketch", "recommended", "reason", "options"
    }


def test_tight_edit_patch_ops_items_require_op_file_reason() -> None:
    branches = _branches_by_type(controller_response_schema(phase="EDIT", tight=True))
    edit = branches["edit"]
    assert "patch_ops" in edit["required"]
    item = edit["properties"]["patch_ops"]["items"]
    # The item is a oneOf over per-op branches; EVERY branch still requires the
    # op-agnostic fields (op/file/reason) on top of its op-specific requireds.
    for branch in item["oneOf"]:
        assert {"op", "file", "reason"} <= set(branch["required"])


def test_tight_submit_changes_requires_summary() -> None:
    branches = _branches_by_type(controller_response_schema(phase="EDIT", tight=True))
    assert "summary" in branches["submit_changes"]["required"]


# ----------------------------------------------------------------------------
# (2) Provider-capability flag
# ----------------------------------------------------------------------------

def test_turboquant_supports_oneof_when_strict_and_thinking_off() -> None:
    from agentd.providers.turboquant_transport import TurboQuantTransport

    t = TurboQuantTransport.for_model("devstral", strict_json=True)
    # devstral has thinking_budget == 0
    assert t.supports_oneof_grammar is True


def test_turboquant_no_oneof_when_strict_json_disabled() -> None:
    from agentd.providers.turboquant_transport import TurboQuantTransport

    t = TurboQuantTransport.for_model("devstral", strict_json=False)
    assert t.supports_oneof_grammar is False


def test_turboquant_no_oneof_when_thinking_on() -> None:
    # Thinking on => llama.cpp silently drops grammar enforcement => oneOf NOT enforced.
    import dataclasses

    from agentd.providers.turboquant_transport import PROFILES, TurboQuantTransport

    thinking_profile = dataclasses.replace(PROFILES["qwen3"], thinking_budget=2048)
    t = TurboQuantTransport(profile=thinking_profile, strict_json=True)
    assert t.supports_oneof_grammar is False


# ----------------------------------------------------------------------------
# (3) Engine wiring — flat vs tight selected off the flag
# ----------------------------------------------------------------------------

class _RecordingTransport:
    """Captures the schema passed to generate_json; returns a valid answer."""

    def __init__(self, *, supports_oneof_grammar: bool) -> None:
        self.supports_oneof_grammar = supports_oneof_grammar
        self.captured_schema: dict | None = None

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload, on_thinking=None, on_retry=None):
        self.captured_schema = schema
        return {"type": "answer", "thought": "t", "answer": "a"}

    async def generate_text(self, *, model, system_instructions,
                            user_payload, on_thinking=None):
        return ""


@pytest.mark.asyncio
async def test_engine_uses_tight_schema_when_provider_supports_oneof() -> None:
    from agentd.reasoning.engine import DefaultReasoningEngine

    transport = _RecordingTransport(supports_oneof_grammar=True)
    engine = DefaultReasoningEngine(model="m", transport=transport)  # type: ignore[arg-type]
    await engine.create_controller_step(
        {"goal": "g", "workspace_path": "/w"}, [], [], phase="DECIDE")
    assert transport.captured_schema is not None
    assert "oneOf" in transport.captured_schema


@pytest.mark.asyncio
async def test_engine_uses_flat_schema_when_provider_lacks_oneof() -> None:
    from agentd.reasoning.engine import DefaultReasoningEngine

    transport = _RecordingTransport(supports_oneof_grammar=False)
    engine = DefaultReasoningEngine(model="m", transport=transport)  # type: ignore[arg-type]
    await engine.create_controller_step(
        {"goal": "g", "workspace_path": "/w"}, [], [], phase="DECIDE")
    assert transport.captured_schema is not None
    assert "oneOf" not in transport.captured_schema
