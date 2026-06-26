import json

from agentd.chat.controller_prompts import (
    build_controller_step_payload,
    format_controller_system_prompt,
)


def test_system_prompt_carries_tools_not_retrieval():
    sp = format_controller_system_prompt(
        [{"name": "read_file", "description": "d", "parameters": {}}])
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
    # `goal` is the CURRENT turn's user message — it VARIES per turn, so it must sit
    # in the per-turn tail (after the append-only history), NOT the head (smoke #13).
    assert keys.index("conversation_history") < keys.index("goal")
    assert keys.index("goal") < keys.index("instruction")


def test_goal_change_preserves_cacheable_prefix():
    """Regression for smoke #13: changing only `goal` (the per-turn message) must NOT
    disturb any byte up through conversation_history, so TQP reuses the cached prefix.
    The byte-identity unit test compares the SAME turn across a restart; THIS guards
    the turn-over-turn axis it never exercised."""
    hist = [
        {"role": "assistant", "content": "{}"},
        {"role": "tool_result", "tool": "read_file", "content": "x"},
    ]
    ctx = {"workspace_path": "/w", "retrieval_seed": {"neighbors": ["a", "b"]}}
    sa = json.dumps(build_controller_step_payload(
        {**ctx, "goal": "short alpha"}, hist, [], phase="DECIDE"))
    sb = json.dumps(build_controller_step_payload(
        {**ctx, "goal": "a COMPLETELY different and longer second-turn message"},
        hist, [], phase="DECIDE"))
    # common prefix
    n = 0
    while n < min(len(sa), len(sb)) and sa[n] == sb[n]:
        n += 1
    shared = sa[:n]
    # the entire conversation_history must be inside the shared prefix; the only
    # divergence is the tail (goal/instruction/budget).
    assert "conversation_history" in shared
    assert json.dumps(hist) in shared
    # the bytes diverge only at/after the goal field (the tail) — never inside the head
    assert n >= sa.index('"goal"')
    assert sa.index('"goal"') > sa.index("conversation_history")


def test_edit_phase_instruction_hint():
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w"}, history=[], tool_definitions=[], phase="EDIT")
    assert "EDIT mode" in str(payload["instruction"])


def test_todo_status_lands_in_tail_when_present():
    payload = build_controller_step_payload(
        {"goal": "add features", "workspace_path": "/w",
         "todo_status": "2 items (1 done) — [✓ A] [☐ B]"},
        history=[], tool_definitions=[], phase="EDIT")
    assert payload.get("todo_status") == "2 items (1 done) — [✓ A] [☐ B]"
    keys = list(payload.keys())
    assert keys.index("todo_status") > keys.index("workspace_path")


def test_todo_status_omitted_when_blank():
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "todo_status": ""},
        history=[], tool_definitions=[], phase="EDIT")
    assert "todo_status" not in payload


def test_system_prompt_teaches_write_todos_and_policy():
    from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT
    p = CONTROLLER_SYSTEM_PROMPT
    assert "write_todos" in p
    assert "enumerate" in p.lower()
    assert "evidence" in p.lower()


def test_edit_entry_offers_explicit_todo_choice():
    """The EDIT hint actually shown at entry presents the use-todo-vs-edit-directly choice
    with concrete triggers. A mode-gated EDIT turn carries seed conversation, so history is
    NON-EMPTY at the first EDIT iteration — the live hint is the `else` (mid-turn) branch,
    NOT the `if not history` branch. This guards against re-introducing the dead-branch bug
    (guidance placed where it never renders)."""
    seeded = [{"role": "user", "content": "add a big multi-file feature"},
              {"role": "assistant", "content": "{}"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w"}, history=seeded,
        tool_definitions=[], phase="EDIT")
    instr = str(payload["instruction"]).lower()
    assert "write_todos" in instr            # the option is named in the live EDIT hint
    assert "3+ files" in instr               # concrete "use a list" trigger
    assert "skip the list" in instr          # and when NOT to use it


def test_todo_policy_states_concrete_triggers():
    """The policy gives concrete scenarios (3+ files / big chunks) instead of vague
    'optional, NOT default' hedging that suppressed the ledger."""
    from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT
    p = CONTROLLER_SYSTEM_PROMPT.lower()
    assert "3+ files" in p
    assert "one at a time" in p


def test_edit_hint_steers_incremental_todo_marking():
    """The live mid-turn EDIT hint must steer the model to RECONCILE the ledger each turn —
    mark the finished item done (evidence) and use in_progress for partial work — instead of
    batching every done-flip at the end (smoke Finding #2: qwen3.6 left all items pending,
    then marked all done in one final call)."""
    seeded = [{"role": "user", "content": "big multi-file feature"},
              {"role": "assistant", "content": "{}"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "todo_status": "3 items (0 done) — [...]"},
        history=seeded, tool_definitions=[], phase="EDIT")
    instr = str(payload["instruction"]).lower()
    assert "in_progress" in instr            # uses the in_progress state for partial work
    assert "reconcile" in instr              # reconcile-the-ledger-first framing


def test_edit_hint_leads_with_reconcile_checkpoint_naming_active_item():
    """After an applied edit while a list is active, the instruction LEADS with a concrete,
    file- AND item-specific reconcile checkpoint (Q1): it names the just-edited file and the
    current todo item so the model answers a pointed 'is THIS item done?' rather than reading
    a generic reconcile paragraph. The checkpoint is non-blocking — PARTIAL keeps editing the
    same item, so a half-finished edit is never forced to a false 'done'."""
    seeded = [{"role": "assistant", "content": "{}"},
              {"role": "tool_result", "tool": "edit",
               "content": "applied+promoted: ['game.js']"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w",
         "todo_status": "2 items (0 done) — [▶ Add enemies] [☐ Jump]",
         "pending_reconcile_files": ["game.js"],
         "reconcile_item": {"title": "Add enemies", "status": "in_progress"}},
        history=seeded, tool_definitions=[], phase="EDIT")
    instr = str(payload["instruction"])
    # Leads (right after the Phase= prefix), not buried mid-paragraph.
    assert instr.startswith("Phase=EDIT. CHECKPOINT")
    assert "game.js" in instr            # names the just-edited file
    assert "Add enemies" in instr        # names the active todo item
    assert "COMPLETE" in instr           # the yes-branch question
    assert "PARTIAL" in instr            # the no-branch (keep editing the same item)


def test_no_checkpoint_without_pending_reconcile_files():
    """No edit just applied (no marker) → no checkpoint, even with an active list. The
    generic mid-turn reconcile guidance still applies."""
    seeded = [{"role": "assistant", "content": "{}"},
              {"role": "tool_result", "tool": "edit", "content": "applied"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w",
         "todo_status": "2 items (0 done) — [▶ A] [☐ B]"},
        history=seeded, tool_definitions=[], phase="EDIT")
    assert "CHECKPOINT" not in str(payload["instruction"])


def test_edit_entry_hint_leads_with_write_todos_tool_syntax():
    """When the loop signals edit_entry (EDIT phase, nothing started), the instruction is the
    clean ENTRY hint giving the EXACT write_todos action syntax (a tool_call) and warning
    against the empty-edit fumble — NOT the mid-turn 'reflect on your last edit' reconcile hint
    (irrelevant on the first action). Regression for the live buried-guidance thrash: the model
    knew to write_todos but emitted type='edit' with empty patch_ops because it could not
    discover write_todos is a tool_call."""
    seeded = [{"role": "user", "content": "build 3 modules"},
              {"role": "assistant", "content": "{}"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "edit_entry": True},
        history=seeded, tool_definitions=[], phase="EDIT")
    instr = str(payload["instruction"])
    assert "tool_call" in instr                       # exact action type for write_todos
    assert "write_todos" in instr
    assert "empty" in instr.lower()                   # warns against empty patch_ops
    assert "reflect on your last edit" not in instr   # NOT the mid-turn reconcile hint


def test_edit_mid_turn_hint_when_not_entry():
    """Without edit_entry (work underway), the mid-turn reconcile hint applies (unchanged)."""
    seeded = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "{}"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "todo_status": "2 items (0 done) — [..]"},
        history=seeded, tool_definitions=[], phase="EDIT")
    assert "reflect on your last edit" in str(payload["instruction"])


def test_system_prompt_teaches_write_todos_is_a_tool_call():
    """The TODO LIST POLICY must state write_todos is invoked as a tool_call (not type='edit'),
    so a weak model routes it correctly instead of shipping an empty edit."""
    from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT
    p = CONTROLLER_SYSTEM_PROMPT
    assert "tool_call" in p and "write_todos" in p
    # The policy block specifically ties write_todos to the tool_call action form.
    assert "type='tool_call'" in p


def test_no_checkpoint_without_active_list():
    """A reconcile marker but NO active todo list → nothing to reconcile → no checkpoint
    (a small/cohesive single edit must not get a phantom checklist nag)."""
    seeded = [{"role": "assistant", "content": "{}"},
              {"role": "tool_result", "tool": "edit", "content": "applied"}]
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w",
         "pending_reconcile_files": ["x.js"],
         "reconcile_item": {"title": "A", "status": "in_progress"}},
        history=seeded, tool_definitions=[], phase="EDIT")
    assert "CHECKPOINT" not in str(payload["instruction"])
