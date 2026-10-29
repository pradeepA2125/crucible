"""Auto review usage is persisted per thread and shown as its own line (plan Task 12)."""

from __future__ import annotations

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ShellPolicy
from agentd.providers.usage import Usage
from tests.test_controller_command_gate import _controller


def test_judge_usage_accumulates_per_thread(tmp_path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.add_judge_usage(tid, Usage(requests=1, prompt_tokens=5000, completion_tokens=80,
                                     cached_tokens=4000))
    store.add_judge_usage(tid, Usage(requests=1, prompt_tokens=5200, completion_tokens=60,
                                     cached_tokens=5000))
    used = store.judge_usage(tid)
    assert (used.requests, used.prompt_tokens, used.completion_tokens, used.cached_tokens) == \
        (2, 10200, 140, 9000)
    assert store.thread_usage(tid).requests == 0  # kept apart from the main agent's


def test_usage_breakdown_has_auto_review(tmp_path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, ShellPolicy.AUTO)
    assert ctrl.usage_breakdown(tid)["auto_review"] is None
    store.add_judge_usage(tid, Usage(requests=2, prompt_tokens=900, completion_tokens=40,
                                     cached_tokens=600))
    review = ctrl.usage_breakdown(tid)["auto_review"]
    assert review == {"requests": 2, "input": 900, "output": 40, "cached": 600}


def test_rewind_forgets_judge_sessions(tmp_path):
    from agentd.approvals.judge_context import JudgeContext
    from agentd.approvals.types import PendingAction

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, ShellPolicy.AUTO)
    ctrl._attach_judge(lambda: (object(), "m"))
    sessions = ctrl._auto_judge.sessions
    ctx = JudgeContext(("push it",), "", (), "0", authorization_total=1)
    sessions.get(tid, "main", "0").payload(ctx, PendingAction("command", "x", ""), [])
    ctrl._forget_judge_history(tid)
    fresh = sessions.get(tid, "main", ctrl._judge_anchor(tid))
    assert fresh.history == []
