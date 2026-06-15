import json

from agentd.reasoning.react_common import assistant_turn, dedup_key


def test_assistant_turn_strips_thought():
    entry = assistant_turn(
        {"type": "tool_call", "thought": "secret", "tool": "read_file", "args": {}})
    assert entry["role"] == "assistant"
    body = json.loads(entry["content"])
    assert "thought" not in body and body["type"] == "tool_call"


def test_dedup_key_normalizes_search_context_lines():
    k1 = dedup_key("search_code", {"pattern": "x", "context_lines": 3})
    k2 = dedup_key("search_code", {"pattern": "x", "context_lines": 9})
    assert k1 == k2
    assert dedup_key("read_file", {"path": "a"}) != dedup_key("read_file", {"path": "b"})
