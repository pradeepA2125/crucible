"""StaleWriteError (spec §7.4): the typed refusal for editing a file another agent
changed after this agent's last read."""
from agentd.chat.controller_loop import _edit_failure_guidance
from agentd.chat.edit_session import StaleWriteError
from agentd.domain.models import PatchFailureCode
from agentd.patch.engine import PatchPreflightFailed


def test_stale_write_error_carries_a_stale_read_issue() -> None:
    exc = StaleWriteError("src/a.py", "`src/a.py` was modified by agent `impl` (general) "
                                      "after your last read — read it again before editing.")
    assert isinstance(exc, PatchPreflightFailed) and isinstance(exc, RuntimeError)
    assert exc.path == "src/a.py"
    assert [(i.code, i.file) for i in exc.issues] == [(PatchFailureCode.STALE_READ, "src/a.py")]
    assert str(exc).startswith("`src/a.py` was modified by agent `impl`")


def test_stale_read_has_its_own_guidance() -> None:
    guidance = _edit_failure_guidance(StaleWriteError("a.py", "stale"))
    assert guidance == ("Another agent changed this file after your last read. read_file it "
                        "again, then re-emit your edit against its current content.")
