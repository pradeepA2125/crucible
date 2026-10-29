"""The Auto judge call: verdicts, read-only checks, fail-safe asks and cost guardrails
(spec §6.3, plan Global Constraints → "Judge cost guardrails")."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentd.approvals.judge import JUDGE_FAILED_REASON, AutoJudge
from agentd.approvals.judge_context import JudgeContext
from agentd.approvals.types import JudgeRequest, PendingAction
from agentd.providers.plan_access import PlanUnsupportedCapability
from agentd.providers.usage import METER, USAGE_OWNER

VERDICT_LOW = {"type": "verdict", "check": None, "risk_level": "low",
               "user_authorization": "unknown", "reason": "routine"}


def _verdict(risk: str, auth: str, reason: str) -> dict[str, object]:
    return {"type": "verdict", "check": None, "risk_level": risk,
            "user_authorization": auth, "reason": reason}


class FakeTransport:
    """Scripted generate_json. A BaseException in the script is raised instead."""

    def __init__(self, script: list[object], *, delay: float = 0.0,
                 uncached_per_call: int = 0, retries_before_success: int = 0) -> None:
        self.script = list(script)
        self.delay = delay
        self.uncached_per_call = uncached_per_call
        self.retries_before_success = retries_before_success
        self.calls = 0
        self.attempts = 0
        self.payloads: list[dict[str, object]] = []
        self.models: list[str] = []
        self.owners_seen: list[str | None] = []
        self.active = 0
        self.max_concurrent = 0

    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_retry=None, **_kw):
        self.calls += 1
        self.models.append(model)
        self.payloads.append(user_payload)
        self.owners_seen.append(USAGE_OWNER.get())
        self.active += 1
        self.max_concurrent = max(self.max_concurrent, self.active)
        try:
            for attempt in range(1, self.retries_before_success + 1):
                self.attempts += 1
                if on_retry is not None:
                    on_retry(attempt, 4, "transient", "retrying")  # may raise to stop retries
            self.attempts += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            METER.record(USAGE_OWNER.get(), requests=1, prompt=self.uncached_per_call)
            item = self.script.pop(0) if self.script else VERDICT_LOW
            if isinstance(item, BaseException):
                raise item
            return item
        finally:
            self.active -= 1


def _ctx(_req: JudgeRequest) -> JudgeContext:
    return JudgeContext(authorizations=("push this branch",), agents_md="",
                        action_log=("run_command: pytest",), history_anchor="")


def make_judge(transport: FakeTransport, *, agent_model: str = "agent-m",
               judge_model: str | None = None, timeout_sec: float = 5.0,
               artifacts: Path | None = None, budget: int = 150_000) -> AutoJudge:
    return AutoJudge(
        transport_source=lambda: (transport, agent_model),
        context_source=_ctx,
        artifacts_dir=lambda _req: artifacts,
        timeout_sec=timeout_sec,
        judge_model=(lambda _m: judge_model) if judge_model else None,
        turn_uncached_budget=budget,
    )


REQ = JudgeRequest(thread_id="t1", agent_id="main", workspace=Path("/tmp"))
OTHER = JudgeRequest(thread_id="t2", agent_id="main", workspace=Path("/tmp"))
ACTION = PendingAction("command", "run_command: git push", "")


@pytest.mark.asyncio
async def test_judge_runs_when_user_asked():
    fake = FakeTransport([_verdict("high", "high", "you asked to push")])
    d = await make_judge(fake).judge(REQ, ACTION)
    assert d.kind == "run" and d.note == "Auto: you asked to push"
    payload = fake.payloads[0]
    assert payload["pending_action"]["summary"] == "run_command: git push"
    context = json.loads(payload["conversation_history"][0]["content"])
    assert context["authorizations"] == ["push this branch"]


@pytest.mark.asyncio
async def test_rejected_judge_model_is_remembered():
    fake = FakeTransport([PlanUnsupportedCapability("model"), VERDICT_LOW, VERDICT_LOW])
    j = make_judge(fake, judge_model="codex-auto-review", agent_model="gpt-5.6-luna")
    assert (await j.judge(REQ, ACTION)).kind == "run"
    assert (await j.judge(REQ, ACTION)).kind == "run"
    assert fake.models == ["codex-auto-review", "gpt-5.6-luna", "gpt-5.6-luna"]


@pytest.mark.asyncio
async def test_judge_runs_at_low_effort_when_supported():
    from agentd.providers.reasoning_effort import EFFORT_OVERRIDE, EffortSupport, ReasoningEffort

    seen: list[object] = []

    class EffortFake(FakeTransport):
        async def reasoning_effort_support(self, model):
            return EffortSupport(supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.HIGH}))

        async def generate_json(self, **kw):
            seen.append(EFFORT_OVERRIDE.get())
            return await super().generate_json(**kw)

    await make_judge(EffortFake([VERDICT_LOW])).judge(REQ, ACTION)
    assert seen == [ReasoningEffort.LOW] and EFFORT_OVERRIDE.get() is None


@pytest.mark.asyncio
async def test_usage_goes_to_the_sink_under_the_judge_owner():
    fake = FakeTransport([VERDICT_LOW], uncached_per_call=1200)
    sunk: list[tuple[str, int]] = []
    j = AutoJudge(transport_source=lambda: (fake, "m"), context_source=_ctx,
                  artifacts_dir=lambda _r: None, timeout_sec=5.0,
                  usage_sink=lambda req, u: sunk.append((req.thread_id, u.prompt_tokens)))
    await j.judge(REQ, ACTION)
    assert sunk == [("t1", 1200)]
    assert METER.peek("judge:t1:main").prompt_tokens == 0


@pytest.mark.asyncio
async def test_second_judgment_reuses_the_first_judgments_history():
    fake = FakeTransport([VERDICT_LOW, VERDICT_LOW])
    j = make_judge(fake)
    await j.judge(REQ, ACTION)
    await j.judge(REQ, PendingAction("command", "run_command: ls", ""))
    first, second = fake.payloads
    assert second["conversation_history"][: len(first["conversation_history"])] == \
        first["conversation_history"]


@pytest.mark.asyncio
async def test_judge_asks_with_reason():
    fake = FakeTransport([_verdict("high", "unknown", "you didn't ask for a push")])
    d = await make_judge(fake).judge(REQ, ACTION)
    assert d.kind == "ask" and d.reason == "you didn't ask for a push"


@pytest.mark.asyncio
async def test_judge_check_then_verdict(tmp_path):
    (tmp_path / "build").mkdir()
    req = JudgeRequest(thread_id="t1", agent_id="main", workspace=tmp_path)
    fake = FakeTransport([
        {"type": "check", "check": {"name": "path_info", "path": "build"},
         "risk_level": None, "user_authorization": None, "reason": None},
        _verdict("low", "unknown", "empty folder")])
    assert (await make_judge(fake).judge(req, ACTION)).kind == "run"
    assert "build: folder, empty" in json.dumps(fake.payloads[1])


@pytest.mark.asyncio
async def test_judge_timeout_asks():
    judge = make_judge(FakeTransport([VERDICT_LOW], delay=5.0), timeout_sec=0.05)
    d = await judge.judge(REQ, ACTION)
    assert d.kind == "ask" and d.reason == JUDGE_FAILED_REASON


@pytest.mark.asyncio
async def test_judge_too_many_checks_asks():
    check = {"type": "check", "check": {"name": "git_remotes", "path": None},
             "risk_level": None, "user_authorization": None, "reason": None}
    fake = FakeTransport([check] * 4)
    d = await make_judge(fake).judge(REQ, ACTION)
    assert d.kind == "ask" and fake.calls == 4


@pytest.mark.asyncio
async def test_malformed_output_asks_without_reprompt():
    fake = FakeTransport([{"type": "verdict", "check": None, "risk_level": None,
                           "user_authorization": None, "reason": None}])
    d = await make_judge(fake).judge(REQ, ACTION)
    assert d.kind == "ask" and fake.calls == 1


@pytest.mark.asyncio
async def test_repeated_malformed_output_trips_the_breaker():
    bad = {"type": "verdict", "check": None, "risk_level": None,
           "user_authorization": None, "reason": None}
    fake = FakeTransport([bad, bad, VERDICT_LOW])
    j = make_judge(fake)
    await j.judge(REQ, ACTION)
    await j.judge(REQ, ACTION)
    assert "unavailable" in (await j.judge(REQ, ACTION)).reason and fake.calls == 2


@pytest.mark.asyncio
async def test_transient_error_is_not_retried():
    fake = FakeTransport([VERDICT_LOW], retries_before_success=3)
    d = await make_judge(fake).judge(REQ, ACTION)
    assert d.kind == "ask" and fake.attempts == 1


@pytest.mark.asyncio
async def test_two_failures_suspend_judge_for_thread_until_next_message():
    fake = FakeTransport([RuntimeError("boom"), RuntimeError("boom"), VERDICT_LOW])
    j = make_judge(fake)
    await j.judge(REQ, ACTION)
    await j.judge(REQ, ACTION)
    third = await j.judge(REQ, ACTION)
    assert third.kind == "ask" and "unavailable" in third.reason and fake.calls == 2
    assert (await j.judge(OTHER, ACTION)).kind == "run"  # other threads unaffected
    j.breaker.reset_thread("t1")
    assert (await j.judge(REQ, ACTION)).kind == "run"


@pytest.mark.asyncio
async def test_rate_limit_suspends_immediately():
    class RateLimited(RuntimeError):
        status_code = 429

    fake = FakeTransport([RateLimited("slow down"), VERDICT_LOW])
    j = make_judge(fake)
    await j.judge(REQ, ACTION)
    assert "unavailable" in (await j.judge(REQ, ACTION)).reason and fake.calls == 1


@pytest.mark.asyncio
async def test_rejected_judge_model_falls_back_once_then_suspends_process():
    fake = FakeTransport([PlanUnsupportedCapability("model"), PlanUnsupportedCapability("model")])
    j = make_judge(fake, judge_model="codex-auto-review", agent_model="gpt-5.6-luna")
    assert (await j.judge(REQ, ACTION)).kind == "ask"
    assert fake.models == ["codex-auto-review", "gpt-5.6-luna"]
    assert (await j.judge(OTHER, ACTION)).kind == "ask" and fake.calls == 2


@pytest.mark.asyncio
async def test_process_suspension_lifts_when_model_changes():
    fake = FakeTransport([PlanUnsupportedCapability("model"), VERDICT_LOW])
    model = {"name": "m1"}
    j = AutoJudge(transport_source=lambda: (fake, model["name"]), context_source=_ctx,
                  artifacts_dir=lambda _r: None, timeout_sec=5.0)
    await j.judge(REQ, ACTION)
    assert (await j.judge(OTHER, ACTION)).kind == "ask" and fake.calls == 1
    model["name"] = "m2"
    assert (await j.judge(OTHER, ACTION)).kind == "run"


@pytest.mark.asyncio
async def test_turn_budget_suspends_judge():
    j = make_judge(FakeTransport([VERDICT_LOW] * 5, uncached_per_call=60_000))
    kinds = [(await j.judge(REQ, ACTION)).kind for _ in range(4)]
    assert kinds == ["run", "run", "run", "ask"]  # 3 x 60k crosses the 150k budget


@pytest.mark.asyncio
async def test_judge_usage_owner_is_scoped():
    fake = FakeTransport([VERDICT_LOW])
    token = USAGE_OWNER.set("thread:t1")
    try:
        await make_judge(fake).judge(REQ, ACTION)
        assert USAGE_OWNER.get() == "thread:t1"
    finally:
        USAGE_OWNER.reset(token)
    assert fake.owners_seen == ["judge:t1:main"]


@pytest.mark.asyncio
async def test_judgments_for_one_agent_are_serialized():
    fake = FakeTransport([VERDICT_LOW, VERDICT_LOW], delay=0.05)
    j = make_judge(fake)
    await asyncio.gather(j.judge(REQ, ACTION), j.judge(REQ, ACTION))
    assert fake.max_concurrent == 1


def _wired_controller(tmp_path: Path, fake: FakeTransport):
    from agentd.chat.models import ChatMessage
    from agentd.chat.storage import ChatThreadStore
    from agentd.domain.models import ShellPolicy
    from tests.test_controller_command_gate import _controller

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.append_message(tid, ChatMessage(role="user", content="please push my branch"))
    ctrl = _controller(tmp_path, store, ShellPolicy.AUTO)
    ctrl._attach_judge(lambda: (fake, "agent-m"))
    return ctrl, store, tid


@pytest.mark.asyncio
async def test_controller_judge_sees_the_users_messages(tmp_path):
    fake = FakeTransport([_verdict("high", "high", "you asked to push")])
    ctrl, _store, tid = _wired_controller(tmp_path, fake)
    out = await ctrl._command_approval_cb(tid, f"chat:{tid}", "git", ["push"], "")
    assert out.approved and out.note == "Auto: you asked to push"
    context = json.loads(fake.payloads[0]["conversation_history"][0]["content"])
    assert "please push my branch" in context["authorizations"]


@pytest.mark.asyncio
async def test_controller_asks_with_reason_when_judge_says_ask(tmp_path):
    from tests.gate_helpers import first_gate

    fake = FakeTransport([_verdict("high", "unknown", "you didn't ask to delete it")])
    ctrl, store, tid = _wired_controller(tmp_path, fake)
    task = asyncio.create_task(
        ctrl._command_approval_cb(tid, f"chat:{tid}", "rm", ["-rf", "x"], ""))
    for _ in range(100):
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            break
        await asyncio.sleep(0.01)
    assert gate is not None and gate.payload["auto_reason"] == "you didn't ask to delete it"
    task.cancel()


@pytest.mark.asyncio
async def test_new_user_message_resets_a_suspended_judge(tmp_path):
    fake = FakeTransport([RuntimeError("boom"), RuntimeError("boom"), VERDICT_LOW])
    ctrl, _store, tid = _wired_controller(tmp_path, fake)
    judge = ctrl._auto_gate.judge
    for _ in range(2):
        await judge.judge(JudgeRequest(tid, "main", tmp_path), ACTION)
    assert "unavailable" in (await judge.judge(JudgeRequest(tid, "main", tmp_path), ACTION)).reason
    ctrl._user_spoke(tid, None)
    assert (await judge.judge(JudgeRequest(tid, "main", tmp_path), ACTION)).kind == "run"


@pytest.mark.asyncio
async def test_judgment_artifact_written(tmp_path):
    await make_judge(FakeTransport([VERDICT_LOW]), artifacts=tmp_path).judge(REQ, ACTION)
    files = sorted(tmp_path.glob("auto-judge-*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record["decision"]["kind"] == "run" and record["responses"][0]["type"] == "verdict"
