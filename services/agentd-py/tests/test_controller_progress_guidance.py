"""The controller asks for progress notes every several steps, written for the user."""
from __future__ import annotations

from agentd.chat.controller_prompts import format_controller_system_prompt
from tests.prompt_goldens import _builtin_defs, _todo_defs


def test_main_prompt_expects_regular_progress_notes() -> None:
    prompt = format_controller_system_prompt(_builtin_defs("explore") + _todo_defs())
    assert "Keep the user oriented" in prompt
    assert "Every several steps" in prompt
    assert "Do not repeat your previous note" in prompt
