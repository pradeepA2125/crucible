"""Auto judge policy, verdict mapping and read-only checks (spec §6.3)."""

from __future__ import annotations

import pytest

from agentd.approvals.judge_checks import run_check
from agentd.approvals.judge_policy import JUDGE_POLICY, verdict_to_decision


@pytest.mark.parametrize(
    "risk,auth,kind",
    [
        ("low", "unknown", "run"),
        ("high", "high", "run"),
        ("high", "medium", "run"),
        ("high", "low", "ask"),
        ("medium", "unknown", "ask"),
        ("critical", "unknown", "ask"),
        ("critical", "high", "ask"),
    ],
)
def test_verdict_mapping(risk, auth, kind):
    assert verdict_to_decision(risk, auth, "r").kind == kind


def test_run_verdict_has_pill_note():
    assert (
        verdict_to_decision("high", "high", "you asked to push").note == "Auto: you asked to push"
    )


def test_judge_never_denies():
    kinds = {
        verdict_to_decision(r, a, "x").kind
        for r in ("low", "medium", "high", "critical")
        for a in ("high", "medium", "low", "unknown")
    }
    assert "deny" not in kinds


def test_path_info_never_returns_contents(tmp_path):
    (tmp_path / "secret.txt").write_text("TOKEN=abc")
    out = run_check("path_info", "secret.txt", tmp_path)
    assert "abc" not in out and "file" in out


def test_path_info_reports_empty_dir_and_missing(tmp_path):
    (tmp_path / "build").mkdir()
    assert "empty" in run_check("path_info", "build", tmp_path)
    assert "does not exist" in run_check("path_info", "nope", tmp_path)


def test_inside_workspace(tmp_path):
    assert run_check("inside_workspace", "a/b.py", tmp_path) == "inside the workspace"
    assert run_check("inside_workspace", "/etc/passwd", tmp_path) == "outside the workspace"


def test_unknown_check_is_refused(tmp_path):
    assert "unknown check" in run_check("cat", "x", tmp_path)


def test_policy_names_trusted_sources():
    for phrase in ("user's own messages", "AGENTS.md", "untrusted"):
        assert phrase in JUDGE_POLICY
