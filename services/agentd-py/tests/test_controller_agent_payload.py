import pytest

from agentd.chat.controller_prompts import build_controller_step_payload

_TOOLS = [{"name": "search_code"}, {"name": "query_graph"}]
_BANNED = ("submit_changes", "propose_mode", "Plan Mode", "clarify", "SKILL CHECK")


def _instruction(**ctx) -> str:
    base = {"workspace_path": "/w", "goal": "g", "max_iters": 10}
    return str(build_controller_step_payload({**base, **ctx}, [{"role": "user", "content": "x"}],
                                             _TOOLS, phase="AGENT")["instruction"])


def test_entry_hint_grounds_first_and_names_report() -> None:
    text = _instruction(iteration=0)
    assert text.startswith("Phase=AGENT. This is your FIRST action.")
    assert "search_code/query_graph" in text and "type='report'" in text


def test_penultimate_and_final_hints_line_up_with_the_budget() -> None:
    assert "One step left" in _instruction(iteration=9)
    final = _instruction(iteration=10)
    assert "BUDGET REACHED" in final and "Unfinished" in final


def test_mid_hint_for_editing_and_readonly_children() -> None:
    assert "PATCH FAILED" in _instruction(iteration=3)
    readonly = _instruction(iteration=3, agent_readonly=True)
    assert "edit" not in readonly.replace("Phase=AGENT.", "") and "report" in readonly


def test_agent_reconcile_checkpoint_uses_report_wording() -> None:
    text = _instruction(iteration=3, todo_status="[~] a", pending_reconcile_files=["a.py"],
                        reconcile_item={"title": "a", "status": "in_progress"})
    assert text.startswith("Phase=AGENT. CHECKPOINT — you just edited a.py.")
    assert "then continue or report" in text


@pytest.mark.parametrize("iteration", [0, 3, 9, 10])
def test_agent_instructions_never_name_parent_actions(iteration: int) -> None:
    text = _instruction(iteration=iteration, todo_status="[~] a", pending_reconcile_files=["a.py"],
                        reconcile_item={"title": "a", "status": "in_progress"})
    assert not any(b in text for b in _BANNED), text
