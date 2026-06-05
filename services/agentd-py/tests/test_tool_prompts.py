from agentd.reasoning.tool_prompts import TOOL_LOOP_SYSTEM_PROMPT, AGENT_STEP_RESPONSE_SCHEMA


def test_prompt_lists_replace_range_with_scenario_guide():
    p = TOOL_LOOP_SYSTEM_PROMPT
    assert "replace_range" in p
    assert "best for" in p.lower()


def test_schema_patch_ops_description_includes_replace_range():
    desc = AGENT_STEP_RESPONSE_SCHEMA["properties"]["patch_ops"]["description"]
    assert "replace_range" in desc
