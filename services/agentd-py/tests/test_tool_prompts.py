from agentd.reasoning.tool_prompts import TOOL_LOOP_SYSTEM_PROMPT, AGENT_STEP_RESPONSE_SCHEMA


def test_prompt_lists_replace_range_with_scenario_guide():
    p = TOOL_LOOP_SYSTEM_PROMPT
    assert "replace_range" in p
    assert "best for" in p.lower()


def test_schema_patch_ops_description_includes_replace_range():
    desc = AGENT_STEP_RESPONSE_SCHEMA["properties"]["patch_ops"]["description"]
    assert "replace_range" in desc


def test_patch_ops_items_require_op_file_reason():
    """Every patch op must carry op/file/reason (shared by all PatchDocumentV2 op types)
    so a strict json_schema grammar enforces them — otherwise the model can omit `reason`
    and the op fails only at PatchDocumentV2 validation ("reason Field required")."""
    items = AGENT_STEP_RESPONSE_SCHEMA["properties"]["patch_ops"]["items"]
    assert set(items["required"]) == {"op", "file", "reason"}
    # Flat (no oneOf — Gemini deadlocks on those) with op-specific fields still allowed.
    assert items["additionalProperties"] is True
