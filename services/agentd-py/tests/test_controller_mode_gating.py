from agentd.chat.controller_loop import _propose_mode_correction

_EDIT_ONLY = frozenset({"edit", "explain"})
_FULL = frozenset({"edit", "create_task", "resume", "explain"})


def _resp(modes):
    return {
        "type": "propose_mode",
        "options": [{"mode": m, "label": m, "description": m} for m in modes],
    }


def test_create_task_rejected_when_disabled():
    assert _propose_mode_correction(_resp(["edit", "create_task"]), _EDIT_ONLY) is not None


def test_edit_explain_allowed_when_disabled():
    assert _propose_mode_correction(_resp(["edit", "explain"]), _EDIT_ONLY) is None


def test_create_task_allowed_when_enabled():
    assert _propose_mode_correction(_resp(["edit", "create_task"]), _FULL) is None
