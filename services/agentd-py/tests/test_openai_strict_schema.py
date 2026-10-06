"""OpenAI strict-schema codec: our schemas in, OpenAI-strict schemas out, and the
model's reply decoded back into exactly the shape the engine expects."""
from __future__ import annotations

import copy
import json

import pytest

from agentd.providers.openai_strict_schema import (
    StrictSchemaDecodeError,
    encode_strict_schema,
    strict_violations,
)


def _obj(props: dict[str, object], required: list[str]) -> dict[str, object]:
    return {"type": "object", "properties": props, "required": required}


def test_root_union_is_wrapped_in_an_object_and_unwrapped_on_decode() -> None:
    schema = {"anyOf": [
        _obj({"type": {"type": "string", "enum": ["answer"]}, "answer": {"type": "string"}},
             ["type", "answer"]),
        _obj({"type": {"type": "string", "enum": ["clarify"]}, "question": {"type": "string"}},
             ["type", "question"]),
    ]}
    codec = encode_strict_schema(schema)

    assert codec.schema["type"] == "object"
    assert codec.schema["required"] == ["action"]
    assert "anyOf" in codec.schema["properties"]["action"]  # type: ignore[index]
    assert codec.decode({"action": {"type": "answer", "answer": "hi"}}) == {
        "type": "answer", "answer": "hi"}


def test_free_form_object_travels_as_a_json_string() -> None:
    schema = _obj({"tool": {"type": "string"}, "args": {"type": "object"}}, ["tool", "args"])
    codec = encode_strict_schema(schema)

    args = codec.schema["properties"]["args"]  # type: ignore[index]
    assert args["type"] == "string"
    decoded = codec.decode({"tool": "read_file", "args": json.dumps({"path": "a.py"})})
    assert decoded == {"tool": "read_file", "args": {"path": "a.py"}}


def test_free_form_object_that_is_not_json_raises_a_correctable_error() -> None:
    codec = encode_strict_schema(_obj({"args": {"type": "object"}}, ["args"]))
    with pytest.raises(StrictSchemaDecodeError, match="args"):
        codec.decode({"args": "{not json"})
    with pytest.raises(StrictSchemaDecodeError, match="args"):
        codec.decode({"args": "[1, 2]"})
    # A RuntimeError, so every loop's existing malformed-output correction catches it.
    assert issubclass(StrictSchemaDecodeError, RuntimeError)


def test_optional_fields_become_required_nullable_and_null_means_absent() -> None:
    schema = _obj({"a": {"type": "string"}, "b": {"type": "string"},
                   "c": {"type": "string", "enum": ["x", "y"]}}, ["a"])
    codec = encode_strict_schema(schema)

    assert codec.schema["required"] == ["a", "b", "c"]
    props = codec.schema["properties"]  # type: ignore[index]
    assert props["a"]["type"] == "string"
    assert props["b"]["type"] == ["string", "null"]
    assert props["c"]["type"] == ["string", "null"]
    assert None in props["c"]["enum"]
    assert codec.decode({"a": "1", "b": None, "c": "x"}) == {"a": "1", "c": "x"}


def test_optional_ref_or_union_is_wrapped_in_a_null_branch() -> None:
    schema = {
        "type": "object",
        "properties": {"anchor": {"$ref": "#/$defs/Anchor"}},
        "$defs": {"Anchor": _obj({"line": {"type": "integer"}}, ["line"])},
    }
    codec = encode_strict_schema(schema)
    anchor = codec.schema["properties"]["anchor"]  # type: ignore[index]
    assert anchor == {"anyOf": [{"$ref": "#/$defs/Anchor"}, {"type": "null"}]}
    assert codec.decode({"anchor": None}) == {}
    assert codec.decode({"anchor": {"line": 3}}) == {"anchor": {"line": 3}}


def test_every_object_gets_additional_properties_false() -> None:
    schema = _obj({"inner": _obj({"x": {"type": "string"}}, ["x"]),
                   "items": {"type": "array", "items": _obj({"y": {"type": "string"}}, ["y"])}},
                  ["inner", "items"])
    encoded = encode_strict_schema(schema).schema
    assert encoded["additionalProperties"] is False
    assert encoded["properties"]["inner"]["additionalProperties"] is False  # type: ignore[index]
    assert encoded["properties"]["items"]["items"]["additionalProperties"] is False  # type: ignore[index]


def test_one_of_becomes_any_of() -> None:
    schema = _obj({"op": {"oneOf": [
        _obj({"op": {"const": "a"}, "x": {"type": "string"}}, ["op", "x"]),
        _obj({"op": {"const": "b"}, "y": {"type": "string"}}, ["op", "y"]),
    ]}}, ["op"])
    encoded = encode_strict_schema(schema).schema
    op = encoded["properties"]["op"]  # type: ignore[index]
    assert "oneOf" not in op and len(op["anyOf"]) == 2


def test_unsupported_keywords_are_dropped_but_properties_with_those_names_survive() -> None:
    schema = {
        "type": "object", "title": "Doc", "description": "keep me",
        "properties": {
            "title": {"type": "string", "default": "x", "minLength": 1, "title": "Title"},
            "ops": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        },
        "required": ["title", "ops"],
        "discriminator": {"propertyName": "op"},
    }
    encoded = encode_strict_schema(schema).schema
    assert "title" not in encoded and "discriminator" not in encoded
    assert encoded["description"] == "keep me"
    assert encoded["properties"]["title"] == {"type": "string"}  # type: ignore[index]
    assert encoded["properties"]["ops"]["minItems"] == 1  # type: ignore[index]


def test_decode_picks_the_matching_union_branch() -> None:
    # args is free-form only in the tool_call branch; answer text that LOOKS like JSON
    # in the other branch must not be parsed.
    schema = {"anyOf": [
        _obj({"type": {"type": "string", "enum": ["tool_call"]}, "args": {"type": "object"}},
             ["type", "args"]),
        _obj({"type": {"type": "string", "enum": ["answer"]}, "answer": {"type": "string"}},
             ["type", "answer"]),
    ]}
    codec = encode_strict_schema(schema)
    assert codec.decode({"action": {"type": "tool_call", "args": '{"p": 1}'}}) == {
        "type": "tool_call", "args": {"p": 1}}
    assert codec.decode({"action": {"type": "answer", "answer": '{"p": 1}'}}) == {
        "type": "answer", "answer": '{"p": 1}'}


def test_refs_are_followed_when_decoding() -> None:
    schema = {
        "type": "object",
        "properties": {"steps": {"type": "array", "items": {"$ref": "#/$defs/Step"}}},
        "required": ["steps"],
        "$defs": {"Step": _obj({"id": {"type": "string"}, "meta": {"type": "object"},
                                "note": {"type": "string"}}, ["id", "meta"])},
    }
    codec = encode_strict_schema(schema)
    decoded = codec.decode({"steps": [{"id": "s1", "meta": '{"k": 2}', "note": None}]})
    assert decoded == {"steps": [{"id": "s1", "meta": {"k": 2}}]}


def test_input_schema_is_not_mutated() -> None:
    schema = {"anyOf": [_obj({"args": {"type": "object"}, "n": {"type": "string"}}, ["args"])]}
    before = copy.deepcopy(schema)
    encode_strict_schema(schema)
    assert schema == before


def test_strict_violations_reports_what_openai_would_reject() -> None:
    assert strict_violations({"anyOf": [{"type": "string"}]})
    assert strict_violations({"type": "object", "properties": {}, "required": []})
    assert strict_violations(_obj({"a": {"type": "string"}}, []))
    assert strict_violations(_obj({"a": {"type": "object"}}, ["a"]))
    assert not strict_violations(encode_strict_schema(_obj({"a": {"type": "object"}}, [])).schema)


def _every_schema_we_send() -> list[tuple[str, dict[str, object]]]:
    """Every schema the codebase hands to generate_json. A new one must be added here."""
    from agentd.chat.agent import _EXPLORE_SCHEMA
    from agentd.chat.classifier import _CLASSIFY_SCHEMA
    from agentd.chat.controller_prompts import controller_response_schema
    from agentd.domain.models import PatchDocumentV2, PlanDocument
    from agentd.memory.consolidator import CANDIDATE_MEMORY_SCHEMA
    from agentd.planning.prompts import planning_response_schema
    from agentd.providers.validate import _PROBE_SCHEMA
    from agentd.reasoning.env_prompts import DRAFT_CONVENTIONS_RESPONSE_SCHEMA
    from agentd.reasoning.narrative_prompts import TASK_NARRATIVE_RESPONSE_SCHEMA
    from agentd.reasoning.tool_prompts import AGENT_STEP_RESPONSE_SCHEMA

    out: list[tuple[str, dict[str, object]]] = [
        ("explore", _EXPLORE_SCHEMA),
        ("classify", _CLASSIFY_SCHEMA),
        ("plan_document", PlanDocument.model_json_schema()),
        ("patch_document_v2", PatchDocumentV2.model_json_schema()),
        ("consolidator", CANDIDATE_MEMORY_SCHEMA),
        ("probe", _PROBE_SCHEMA),
        ("conventions", DRAFT_CONVENTIONS_RESPONSE_SCHEMA),
        ("narrative", TASK_NARRATIVE_RESPONSE_SCHEMA),
        ("agent_step", AGENT_STEP_RESPONSE_SCHEMA),
        ("planning", planning_response_schema(allow_plan_patch=True)),
        ("planning_no_patch", planning_response_schema(allow_plan_patch=False)),
    ]
    for phase in ("ACTIVE", "PLAN", "AGENT"):
        for team in (False, True):
            out.append((f"controller_{phase}_anyof_team{team}",
                        controller_response_schema(phase=phase, anyof=True, team_member=team)))
            out.append((f"controller_{phase}_flat_team{team}",
                        controller_response_schema(phase=phase, team_member=team)))
    return out


@pytest.mark.parametrize(("name", "schema"), _every_schema_we_send())
def test_every_schema_we_send_encodes_to_openai_strict(
    name: str, schema: dict[str, object]
) -> None:
    encoded = encode_strict_schema(schema).schema
    assert strict_violations(encoded) == [], name
