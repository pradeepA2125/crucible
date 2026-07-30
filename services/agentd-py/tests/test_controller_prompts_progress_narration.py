"""Finding 5 (final whole-branch review, 2026-07-25 controller-progress-action spec):
the per-iteration payload tail is the highest-recency text the model reads every
iteration, and pre-fix it presented CLOSED enumerations that never mentioned
`progress` as an option — exactly where the production narration-pressure failure
(TurboQuant qwen3.6:35b-a3b-q4_K_M narrating mid-execution via a terminal `answer`)
actually occurs.

Per-site decision (surgical, not blanket — see CLAUDE.md's no-superiority framing too):
  - ACTIVE entry hint            -> progress ADDED (a genuine continuation point: the
                                     model may explore read-only for several iterations
                                     before its first edit/write_todos, and narration
                                     pressure can build there same as mid-turn).
  - ACTIVE mid-turn hint         -> progress ADDED as option (C), alongside (A)/(B).
  - ACTIVE final_call hint       -> UNCHANGED. This is the one true budget-exhaustion
                                     "land it now" point; allowing progress here invites
                                     an infinite narration loop right where the model
                                     MUST commit (confirmed by tracing _iterate: once
                                     final_call is True it stays True for every
                                     remaining iteration, and if the model never lands a
                                     terminal action the loop's fallback is a bare
                                     "(loop ended)" — silently worse than forcing a
                                     commit).
  - PLAN mid-turn hint           -> progress ADDED as option (C).
  - PLAN final_call hint         -> UNCHANGED, same reasoning as ACTIVE's.
"""
from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT, build_controller_step_payload


def _payload(plan_context, phase, history):
    return build_controller_step_payload(
        plan_context, history, [], phase=phase, skills_available=False)


def _history(n_pairs: int) -> list[dict[str, object]]:
    """`iteration = len(history) // 2` (controller_prompts.py) — build n_pairs
    assistant/tool_result pairs to land on a specific iteration count."""
    hist: list[dict[str, object]] = []
    for _ in range(n_pairs):
        hist.append({"role": "assistant", "content": "{}"})
        hist.append({"role": "tool_result", "tool": "", "content": "x"})
    return hist


def test_active_entry_hint_mentions_progress():
    ctx = {
        "workspace_path": "/tmp", "goal": "g", "max_iters": 32,
        "active_entry": True, "skill_check_due": False,
    }
    payload = _payload(ctx, "ACTIVE", [])
    assert "type='progress'" in payload["instruction"]


def test_active_mid_turn_hint_offers_progress_as_a_third_option():
    ctx = {
        "workspace_path": "/tmp", "goal": "g", "max_iters": 32,
        "active_entry": False, "skill_check_due": False, "todo_status": "",
    }
    payload = _payload(ctx, "ACTIVE", _history(1))
    assert "(C)" in payload["instruction"]
    assert "type='progress'" in payload["instruction"]


def test_active_final_call_hint_does_not_mention_progress():
    ctx = {
        "workspace_path": "/tmp", "goal": "g", "max_iters": 4,
        "active_entry": False, "skill_check_due": False, "todo_status": "",
    }
    payload = _payload(ctx, "ACTIVE", _history(3))  # iteration=3 >= max_iters-1=3
    assert "FINAL STEP" in payload["instruction"]
    assert "progress" not in payload["instruction"]


def test_plan_mid_turn_hint_offers_progress_as_a_third_option():
    ctx = {
        "workspace_path": "/tmp", "goal": "g", "max_iters": 32,
        "plan_entry": False,
    }
    payload = _payload(ctx, "PLAN", _history(1))
    assert "(C)" in payload["instruction"]
    assert "type='progress'" in payload["instruction"]


def test_plan_final_call_hint_does_not_mention_progress():
    ctx = {
        "workspace_path": "/tmp", "goal": "g", "max_iters": 4,
        "plan_entry": False,
    }
    payload = _payload(ctx, "PLAN", _history(3))  # iteration=3 >= max_iters-1=3
    assert "FINAL STEP" in payload["instruction"]
    assert "progress" not in payload["instruction"]


def test_system_prompt_wrong_tool_example_names_progress_too():
    # Finding 3's prompt twin: the WRONG example enumerating this schema's own
    # top-level response types (never callable as a tool_call 'tool') must list
    # every one of them, including 'progress'.
    assert "answer/clarify/propose_mode/edit/submit_changes/progress" in CONTROLLER_SYSTEM_PROMPT
