import pytest

from agentd.chat.controller_phase import ControllerPhaseSM


def test_decide_forbids_edit_until_mode_chosen():
    sm = ControllerPhaseSM()
    assert sm.phase == "DECIDE"
    assert "edit" not in sm.allowed_types()
    assert "propose_mode" in sm.allowed_types()
    sm.enter_edit_mode()
    assert sm.phase == "EDIT"
    assert "edit" in sm.allowed_types()
    assert "propose_mode" not in sm.allowed_types()


def test_enter_edit_only_from_decide():
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    with pytest.raises(ValueError):
        sm.enter_edit_mode()


def test_edit_phase_allows_clarify_but_not_propose_mode():
    # The agent must be able to ask a clarifying question if it gets blocked
    # mid-edit (reading the workspace can't resolve the ambiguity), but it must
    # NOT re-open mode selection — it already committed to editing.
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    assert "clarify" in sm.allowed_types()
    assert "propose_mode" not in sm.allowed_types()
    assert "edit" in sm.allowed_types()


def test_explain_phase_forbids_propose_mode_and_edit():
    # After the user picks "explain", the re-entered turn must DESCRIBE the approach,
    # not re-open the mode gate (finding 4). EXPLAIN allows exploring + answer/clarify
    # but forbids propose_mode (the re-propose loop) and edit.
    sm = ControllerPhaseSM()
    sm.enter_explain_mode()
    assert sm.phase == "EXPLAIN"
    assert "answer" in sm.allowed_types()
    assert "tool_call" in sm.allowed_types()
    assert "clarify" in sm.allowed_types()
    assert "propose_mode" not in sm.allowed_types()
    assert "edit" not in sm.allowed_types()


def test_enter_explain_only_from_decide():
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    with pytest.raises(ValueError):
        sm.enter_explain_mode()
