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
