"""Add-on-only judge conversations (plan Task 12): each judgment extends the previous
input, so the provider's prompt cache keeps the prefix."""

from __future__ import annotations

import json

from agentd.approvals.judge_context import JudgeContext
from agentd.approvals.judge_session import JudgeSessions, preferred_judge_model
from agentd.approvals.types import PendingAction


def ctx(auths: list[str], actions: list[str], anchor: str = "0") -> JudgeContext:
    return JudgeContext(authorizations=tuple(auths), agents_md="rules", action_log=tuple(actions),
                        history_anchor=anchor, authorization_total=len(auths),
                        action_total=len(actions))


def action(summary: str) -> PendingAction:
    return PendingAction("command", summary, "")


def test_second_judgment_extends_the_first():
    s = JudgeSessions().get("t", "main", "0")
    p1 = s.payload(ctx(["fix bug"], ["read_file(a)"]), action("git status"), [])
    p2 = s.payload(ctx(["fix bug", "push it"], ["read_file(a)", "run_command: pytest"]),
                   action("git push"), [])
    h1, h2 = p1["conversation_history"], p2["conversation_history"]
    assert h2[: len(h1)] == h1
    added = json.dumps(h2[len(h1):])
    assert "push it" in added and "run_command: pytest" in added and "fix bug" not in added


def test_nothing_new_adds_no_history():
    s = JudgeSessions().get("t", "main", "0")
    p1 = s.payload(ctx(["a"], ["x"]), action("ls"), [])
    p2 = s.payload(ctx(["a"], ["x"]), action("pwd"), [])
    assert p2["conversation_history"] == p1["conversation_history"]
    assert p2["pending_action"]["summary"] == "pwd"


def test_first_item_carries_full_context_and_history_leads_the_payload():
    p = JudgeSessions().get("t", "main", "0").payload(ctx(["a"], ["x"]), action("ls"), [])
    assert list(p)[0] == "conversation_history"
    first = json.loads(p["conversation_history"][0]["content"])
    assert first == {"agents_md": "rules", "authorizations": ["a"], "action_log": ["x"]}


def test_anchor_change_starts_fresh():
    sessions = JudgeSessions()
    sessions.get("t", "main", "0").payload(ctx(["a"], ["x"]), action("ls"), [])
    p = sessions.get("t", "main", "1").payload(ctx(["a", "b"], ["x"]), action("ls"), [])
    assert len(p["conversation_history"]) == 1
    assert json.loads(p["conversation_history"][0]["content"])["authorizations"] == ["a", "b"]


def test_shrinking_counts_start_fresh():
    s = JudgeSessions().get("t", "main", "0")
    s.payload(ctx(["a"], ["x", "y", "z"]), action("ls"), [])
    p = s.payload(ctx(["a"], ["z"]), action("ls"), [])  # compaction evicted history
    assert len(p["conversation_history"]) == 1


def test_forget_thread_drops_its_sessions():
    sessions = JudgeSessions()
    sessions.get("t", "main", "0").payload(ctx(["a"], []), action("ls"), [])
    sessions.forget_thread("t")
    p = sessions.get("t", "main", "0").payload(ctx(["a"], []), action("ls"), [])
    assert len(p["conversation_history"]) == 1


def test_checks_ride_the_tail_not_the_history():
    s = JudgeSessions().get("t", "main", "0")
    p = s.payload(ctx(["a"], []), action("rm -rf build"),
                  [{"check": "path_info", "path": "build", "result": "build: folder, empty"}])
    assert p["checks"][0]["result"] == "build: folder, empty"
    assert "empty" not in json.dumps(p["conversation_history"])


class _Inner:
    pass


def test_preferred_judge_model(monkeypatch):
    from agentd.providers.claude_subscription import ClaudeSubscriptionTransport
    from agentd.providers.openai_transport import OpenAIJsonTransport

    plan = OpenAIJsonTransport(responses_client=object(), plan_route=True)
    api = OpenAIJsonTransport(responses_client=object())
    claude = ClaudeSubscriptionTransport(object(), registration_id="r", client=object())  # type: ignore[arg-type]
    assert preferred_judge_model(plan, "gpt-5.6-luna") == "codex-auto-review"
    assert preferred_judge_model(api, "gpt-5.6-luna") == "gpt-5.6-luna"
    assert preferred_judge_model(claude, "claude-sonnet-5-5") == "claude-haiku-5-5"
    assert preferred_judge_model(_Inner(), "nemotron") == "nemotron"
    wrapped = type("W", (), {"wrapped": plan})()
    assert preferred_judge_model(wrapped, "gpt-5.6-luna") == "codex-auto-review"
    monkeypatch.setenv("CRUCIBLE_AUTO_JUDGE_MODEL", "my-judge")
    assert preferred_judge_model(api, "gpt-5.6-luna") == "my-judge"
