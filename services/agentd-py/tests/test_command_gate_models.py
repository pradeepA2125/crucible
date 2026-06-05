"""Domain-model assertions for the shell-command approval gate (T1)."""
from agentd.domain.models import (
    CommandApprovalRequest,
    CommandDecision,
    CommandRule,
    ShellPolicy,
    TaskCreateRequest,
    TaskExecutionState,
    TaskStatus,
)


def test_command_gate_models_exist() -> None:
    assert TaskStatus.AWAITING_COMMAND_DECISION == "AWAITING_COMMAND_DECISION"
    assert ShellPolicy.ASK == "ask"
    assert ShellPolicy.ALLOW_ALL == "allow_all"

    req = CommandApprovalRequest(
        decision_id="d1",
        command="python",
        args=["-c", "print(1)"],
        cwd="services/agentd-py",
        step_id="s1",
    )
    assert req.command == "python"
    assert req.args == ["-c", "print(1)"]

    dec = CommandDecision(approve=True, remember=True, scope="prefix")
    assert dec.scope == "prefix"
    assert dec.rule_value is None

    rule = CommandRule(type="prefix", value="python -c", added_at="2026-05-28T00:00:00Z")
    assert rule.type == "prefix"

    state = TaskExecutionState()
    assert state.pending_command_request is None
    assert state.approved_commands == []

    assert TaskCreateRequest(goal="g", workspace_path=".").shell_policy is None
