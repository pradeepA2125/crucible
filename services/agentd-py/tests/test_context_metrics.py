import json

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.context_metrics import (
    logical_context,
    object_components,
    physical_context,
    serialized_size,
)


def test_components_account_for_all_framing_and_nested_values():
    payload = {"unicode": "λ\n", "nested": {"secret": ["private/path"]}}
    assert sum(object_components(payload).values()) == serialized_size(payload)
    parts = {}
    system = format_controller_system_prompt([], component_sizes=parts)
    assert sum(parts.values()) == len(system)
    measured = logical_context(system, payload, {}, parts)
    assert "private/path" not in json.dumps(measured)


def test_physical_accounting_includes_native_schemas_and_accumulated_tails():
    first = {
        "instructions": "rules",
        "input": [{"role": "user", "content": "tail1"}],
        "tools": [{"name": "fn"}],
        "model": "private-model",
        "stream": True,
    }
    second = dict(first, input=first["input"] + [{"role": "user", "content": "tail2"}])
    a, b = physical_context(first), physical_context(second)
    assert b["total_chars"] > a["total_chars"]
    assert sum(a["parts"].values()) == a["total_chars"]
    assert "tools" in a["parts"]
    assert "private-model" not in json.dumps(a)
