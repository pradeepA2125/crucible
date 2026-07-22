from agentd.chat.controller_loop import _propose_mode_correction

_IMPLEMENT_ONLY = frozenset({"implement"})
_FULL = frozenset({"implement", "create_task", "resume"})


def _resp(modes):
    return {
        "type": "propose_mode",
        "options": [{"mode": m, "label": m, "description": m} for m in modes],
    }


def test_create_task_rejected_when_disabled():
    assert _propose_mode_correction(_resp(["implement", "create_task"]), _IMPLEMENT_ONLY) is not None


def test_implement_allowed_when_disabled():
    assert _propose_mode_correction(_resp(["implement"]), _IMPLEMENT_ONLY) is None


def test_create_task_allowed_when_enabled():
    assert _propose_mode_correction(_resp(["implement", "create_task"]), _FULL) is None
