import pytest

from agentd.chat.controller_phase import ControllerPhaseSM


def test_default_start_is_active():
    sm = ControllerPhaseSM()
    assert sm.phase == "ACTIVE"
    assert "edit" in sm.allowed_types()
    assert "run_command" not in sm.allowed_types()  # run_command rides tool_call, not its own type
    assert "propose_mode" not in sm.allowed_types()  # only via the Task 6 task-subsystem carve-out


def test_explicit_active_start():
    sm = ControllerPhaseSM(start="ACTIVE")
    assert sm.phase == "ACTIVE"
    assert "submit_changes" in sm.allowed_types()
    assert "answer" in sm.allowed_types()
    assert "clarify" in sm.allowed_types()
    assert "tool_call" in sm.allowed_types()


def test_explicit_plan_start():
    sm = ControllerPhaseSM(start="PLAN")
    assert sm.phase == "PLAN"
    assert "propose_mode" in sm.allowed_types()
    assert "answer" in sm.allowed_types()
    assert "clarify" in sm.allowed_types()
    assert "tool_call" in sm.allowed_types()
    assert "edit" not in sm.allowed_types()
    assert "submit_changes" not in sm.allowed_types()


def test_invalid_start_raises():
    with pytest.raises(ValueError):
        ControllerPhaseSM(start="EDIT")  # the old phase name is no longer valid
    with pytest.raises(ValueError):
        ControllerPhaseSM(start="bogus")


def test_no_post_construction_transition_methods():
    # enter_edit_mode/enter_explain_mode are removed — every phase entry is a fresh
    # instance constructed with start=, never a mutation.
    sm = ControllerPhaseSM()
    assert not hasattr(sm, "enter_edit_mode")
    assert not hasattr(sm, "enter_explain_mode")


def test_default_pin_regression():
    # Explicit pin, independent of the 43-site audit above: catches an accidental
    # future flip back to a restricted default.
    sm = ControllerPhaseSM()
    assert sm.phase == "ACTIVE"
    assert "edit" in sm.allowed_types()
