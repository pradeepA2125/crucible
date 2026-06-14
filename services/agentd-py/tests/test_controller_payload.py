from agentd.chat.controller_prompts import (
    build_controller_step_payload,
    format_controller_system_prompt,
)


def test_system_prompt_carries_tools_not_retrieval():
    sp = format_controller_system_prompt([{"name": "read_file", "description": "d", "parameters": {}}])
    assert "read_file" in sp
    assert "retrieval_seed" not in sp  # retrieval never in the system string


def test_payload_key_order_is_cache_stable():
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "retrieval_seed": {"neighbors": []}},
        history=[{"role": "assistant", "content": "{}"}],
        tool_definitions=[],
        phase="DECIDE",
    )
    keys = list(payload.keys())
    assert keys.index("retrieval_seed") < keys.index("conversation_history")
    assert keys[-1] == "budget_status"
    assert keys.index("instruction") < keys.index("budget_status")
    assert keys.index("conversation_history") < keys.index("instruction")


def test_edit_phase_instruction_hint():
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w"}, history=[], tool_definitions=[], phase="EDIT")
    assert "EDIT mode" in str(payload["instruction"])
