"""C1b: the merged entry-hint signal. active_entry (todo-vs-direct-edit guidance)
must persist across iterations exactly like the old edit_entry did — no iteration
clause — to preserve the empty-edit-fumble recovery this codebase already fixed
once. skill_check_due (the skill-triage half) is strictly one-shot, matching the
old decide_entry."""
from agentd.chat.controller_prompts import build_controller_step_payload


def _payload(plan_context, phase="ACTIVE", history=None):
    return build_controller_step_payload(
        plan_context, history or [], [], phase=phase, skills_available=False)


def test_active_entry_preserves_empty_edit_recovery_after_first_action():
    # Simulates iteration 1 (history non-empty) with nothing started yet — the
    # fumble-recovery case: an empty edit failed at iteration 0, nothing landed.
    ctx = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": False, "iteration": 1,
    }
    payload = _payload(ctx, history=[{"role": "assistant", "content": "{}"},
                                      {"role": "tool_result", "tool": "", "content": "x"}])
    instruction = payload["instruction"]
    assert "FIRST action" not in instruction
    assert "nothing is started" not in instruction
    assert "No edit or todo list has started yet" in instruction
    assert "START A TODO LIST FIRST" in instruction
    assert "empty patch_ops" in instruction
    assert "reflect on your last edit" not in instruction


def test_first_action_uses_turn_iteration_with_prior_conversation():
    payload = _payload(
        {"active_entry": True, "iteration": 0},
        history=[{"role": "user", "content": "previous question"},
                 {"role": "assistant", "content": "previous answer"}])
    assert "FIRST action" in payload["instruction"]


def test_successful_read_gets_evidence_reuse_hint_before_any_edit():
    history = [
        {"role": "assistant", "content": "{}"},
        {"role": "tool_result", "tool": "read_file", "status": "success",
         "is_error": False, "content": "def hello(): return 'hello'"},
    ]
    payload = _payload({"active_entry": True, "iteration": 1}, history=history)
    instruction = payload["instruction"]
    assert payload["conversation_history"] == history
    assert "FIRST action" not in instruction
    assert "Reuse successful tool results" in instruction
    assert "read only missing evidence" in instruction
    assert "emit type='answer' now" in instruction


def test_empty_history_does_not_reset_explicit_turn_iteration():
    payload = _payload({"active_entry": True, "iteration": 2}, history=[])
    assert "FIRST action" not in payload["instruction"]
    assert "Continue from completed actions" in payload["instruction"]


def test_skill_check_due_only_fires_once():
    ctx_iter0 = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": True,
    }
    payload0 = build_controller_step_payload(
        ctx_iter0, [], [], phase="ACTIVE", skills_available=True)
    assert "SKILL CHECK" in payload0["instruction"]

    ctx_iter1 = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": False,
    }
    payload1 = build_controller_step_payload(
        ctx_iter1, [{"role": "assistant", "content": "{}"},
                    {"role": "tool_result", "tool": "", "content": "x"}],
        [], phase="ACTIVE", skills_available=True)
    assert "SKILL CHECK" not in payload1["instruction"]


def test_active_entry_clears_once_edit_applied_or_ledger_started():
    # Once active_entry is False (an edit landed or a list started), the mid-turn
    # reconcile hint shows instead of the entry hint.
    ctx = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": False, "skill_check_due": False, "todo_status": "",
    }
    payload = _payload(ctx, history=[{"role": "assistant", "content": "{}"},
                                      {"role": "tool_result", "tool": "", "content": "x"}])
    instruction = payload["instruction"]
    assert "RECONCILE" in instruction or "reflect on your last edit" in instruction
