"""C3: the static system prompt must not describe the old gated model (edit only
"after the user picked edit", propose_mode as if every change routes through it,
edit|create_task|explain as the mode vocabulary)."""
from agentd.chat.controller_prompts import format_controller_system_prompt


def test_edit_variant_has_no_stale_gate_qualifier():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=False)
    assert 'after the user picked "edit"' not in prompt
    assert "EDIT mode only" not in prompt


def test_submit_changes_variant_has_no_stale_edit_mode_qualifier():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=False)
    assert "EDIT mode, when all edits are done" not in prompt


def test_propose_mode_modes_disabled_uses_implement_not_edit():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=False)
    assert '"implement"' in prompt
    assert "explain" not in prompt
    # "edit" the ACTION TYPE still legitimately appears elsewhere in the prompt (the
    # edit variant itself) — this test only asserts the propose_mode mode-vocabulary
    # no longer offers a mode literally named "edit".
    assert '<edit|explain>' not in prompt
    assert "mode\": <edit" not in prompt


def test_propose_mode_modes_enabled_uses_implement_create_task_resume():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=True)
    assert "implement | create_task | resume" in prompt
    assert "explain" not in prompt
