"""C1b: the merged entry-hint signal. active_entry (todo-vs-direct-edit guidance)
must persist across iterations exactly like the old edit_entry did — no iteration
clause — to preserve the empty-edit-fumble recovery this codebase already fixed
once. skill_check_due (the skill-triage half) is strictly one-shot, matching the
old decide_entry."""
from agentd.chat.controller_prompts import build_controller_step_payload


def _payload(plan_context, phase="ACTIVE", history=None):
    return build_controller_step_payload(
        plan_context, history or [], [], phase=phase, skills_available=False)


def test_active_entry_has_no_iteration_gating():
    # Simulates iteration 1 (history non-empty) with nothing started yet — the
    # fumble-recovery case: an empty edit failed at iteration 0, nothing landed.
    ctx = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": False,
    }
    payload = _payload(ctx, history=[{"role": "assistant", "content": "{}"},
                                      {"role": "tool_result", "tool": "", "content": "x"}])
    assert "FIRST action" in payload["instruction"] or "nothing is started" in payload["instruction"]


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
    assert "RECONCILE" in payload["instruction"] or "reflect on your last edit" in payload["instruction"]
