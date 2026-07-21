"""Fix: submit_changes must not rely on a stale lint/test 'done' claim from earlier in
the todo list — the model must be told to re-verify immediately before submitting.

Root cause (found live 2026-07-17): the 'Run lint check and verify' todo item was marked
done early in the session (Task 1), but by session end — after Tasks 3/4/5 added many new
files — `ruff check core/ tests/` showed 18 fresh errors that were never re-caught, because
nothing re-triggers verification once an item is already 'done'.
"""
from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT


def test_submit_changes_teaching_requires_fresh_verification():
    idx = CONTROLLER_SYSTEM_PROMPT.find("Variant — submit_changes")
    assert idx != -1, "submit_changes variant block not found"
    # Slice just that variant's teaching (up to the next "Variant —" or section break).
    next_variant = CONTROLLER_SYSTEM_PROMPT.find("Variant —", idx + 1)
    block = CONTROLLER_SYSTEM_PROMPT[idx:next_variant if next_variant != -1 else idx + 2000]
    assert "re-run" in block.lower() or "one more time" in block.lower(), (
        "submit_changes teaching must require a fresh verification run, not rely on an "
        "earlier 'done' claim that may now be stale"
    )
