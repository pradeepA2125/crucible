import pytest

from agentd.chat.controller_prompts import format_controller_system_prompt

TOOLS: list[dict[str, object]] = []


def test_no_instructions_no_block() -> None:
    out = format_controller_system_prompt(
        TOOLS, task_subsystem_enabled=False, memory_enabled=False
    )
    assert "PROJECT INSTRUCTIONS" not in out


@pytest.mark.parametrize("batching_enabled", [False, True])
def test_instructions_appended_when_present(monkeypatch, batching_enabled: bool) -> None:
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1" if batching_enabled else "0")
    out = format_controller_system_prompt(
        TOOLS,
        task_subsystem_enabled=False,
        memory_enabled=False,
        project_instructions="Always use tabs, never spaces.",
    )
    assert "PROJECT INSTRUCTIONS" in out
    assert "Always use tabs, never spaces." in out
    # Instructions follow the core prompt, but batching guidance can follow them.
    instructions = out.split("PROJECT INSTRUCTIONS", 1)[1]
    if batching_enabled:
        instructions, guidance = instructions.split("TOOL BATCHES", 1)
        assert "IMPORTANT — BATCH TARGETING AND READING" in guidance
    else:
        assert "TOOL BATCHES" not in out
    assert instructions.rstrip().endswith("Always use tabs, never spaces.")
    assert out.count("Always use tabs, never spaces.") == 1


def test_blank_instructions_no_block() -> None:
    out = format_controller_system_prompt(
        TOOLS,
        task_subsystem_enabled=False,
        memory_enabled=False,
        project_instructions="   \n  ",
    )
    assert "PROJECT INSTRUCTIONS" not in out


def test_instructions_value_with_braces_does_not_crash() -> None:
    # AGENTS.md may contain literal { } — must not be treated as format fields.
    out = format_controller_system_prompt(
        TOOLS,
        task_subsystem_enabled=False,
        memory_enabled=False,
        project_instructions="Use {curly} braces in JSON examples.",
    )
    assert "Use {curly} braces in JSON examples." in out
