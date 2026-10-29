"""The Auto judge call (spec §6.3) and its cost guardrails.

A judgment is at most MAX_CHECKS read-only checks plus one verdict, bounded by a
timeout, with transport retries stopped and no correction re-prompts: anything that
goes wrong asks the user. A breaker stops a failing judge from spending on every
command (the compaction-loop class of bug): two consecutive failures in a thread, or a
rate limit, pause it until the user's next message; a rejected model falls back once to
the agent's model, then pauses it process-wide until the provider or model changes; a
per-turn uncached-token budget pauses it for the rest of the turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentd.approvals.judge_checks import run_check
from agentd.approvals.judge_context import JudgeContext
from agentd.approvals.judge_policy import JUDGE_POLICY, JUDGE_SCHEMA, verdict_to_decision
from agentd.approvals.judge_session import JudgeSessions, preferred_judge_model
from agentd.approvals.types import AutoDecision, JudgeRequest, PendingAction
from agentd.providers.context_overflow import ContextWindowExceeded
from agentd.providers.plan_access import ProviderAccessStopped
from agentd.providers.reasoning_effort import EFFORT_OVERRIDE, ReasoningEffort
from agentd.providers.usage import METER, USAGE_OWNER, Usage

logger = logging.getLogger(__name__)

MAX_CHECKS = 3
JUDGE_FAILED_REASON = "Auto couldn't check this"
DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_TURN_UNCACHED_TOKENS = 150_000
_FAILURES_TO_SUSPEND = 2
_RISK = frozenset({"low", "medium", "high", "critical"})
_AUTHORIZATION = frozenset({"high", "medium", "low", "unknown"})


class JudgeRetryAbort(Exception):
    """Raised from on_retry so a transient error ends the judgment instead of resending."""


class _MalformedVerdict(ValueError):
    pass


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def auto_judge_timeout_sec() -> float:
    return _env_number("CRUCIBLE_AUTO_JUDGE_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)


def auto_judge_turn_uncached_tokens() -> int:
    return int(_env_number("CRUCIBLE_AUTO_JUDGE_TURN_UNCACHED_TOKENS",
                           DEFAULT_TURN_UNCACHED_TOKENS))


def _status(exc: BaseException) -> int | None:
    for candidate in (getattr(exc, "status_code", None), getattr(exc, "status", None),
                      getattr(getattr(exc, "response", None), "status_code", None)):
        if isinstance(candidate, int):
            return candidate
    return None


def _is_probative(exc: BaseException) -> bool:
    """Input- or account-determined: retrying the same request would fail the same way."""
    return (isinstance(exc, ProviderAccessStopped | ContextWindowExceeded)
            or _status(exc) == 400)


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:80]


@dataclass
class JudgeBreaker:
    """Per-thread and process-wide suspension of the judge. Single-process asyncio: every
    method is synchronous, so check-and-set never interleaves."""
    turn_budget: int
    _failures: dict[str, int] = field(default_factory=dict)
    _suspended: dict[str, str] = field(default_factory=dict)
    _spent: dict[str, int] = field(default_factory=dict)
    _process_reason: str | None = None
    _process_key: tuple[int, str] | None = None

    def allowed(self, thread_id: str, provider_key: tuple[int, str]) -> str | None:
        if self._process_reason is not None:
            if self._process_key == provider_key:
                return self._process_reason
            self.reset_process()  # the provider or model changed since it was rejected
        if thread_id in self._suspended:
            return self._suspended[thread_id]
        if self._spent.get(thread_id, 0) >= self.turn_budget:
            return "Auto review used its token budget for this turn — asking instead"
        return None

    def record_success(self, thread_id: str) -> None:
        self._failures.pop(thread_id, None)

    def record_failure(self, thread_id: str, exc: BaseException) -> None:
        reason = f"Auto review is unavailable ({_short(exc)}) — asking instead"
        if _status(exc) == 429:
            self._suspended[thread_id] = reason
            return
        count = self._failures.get(thread_id, 0) + 1
        self._failures[thread_id] = count
        if count >= _FAILURES_TO_SUSPEND:
            self._suspended[thread_id] = reason

    def suspend_process(self, exc: BaseException, provider_key: tuple[int, str]) -> None:
        self._process_reason = f"Auto review is unavailable ({_short(exc)}) — asking instead"
        self._process_key = provider_key

    def record_usage(self, thread_id: str, uncached: int) -> None:
        self._spent[thread_id] = self._spent.get(thread_id, 0) + max(0, uncached)

    def reset_thread(self, thread_id: str) -> None:
        """A new user message: the thread's judge gets a fresh start and a fresh budget."""
        self._failures.pop(thread_id, None)
        self._suspended.pop(thread_id, None)
        self._spent.pop(thread_id, None)

    def reset_process(self) -> None:
        self._process_reason = None
        self._process_key = None


TransportSource = Callable[[], tuple[Any, str]]


class AutoJudge:
    def __init__(self, *, transport_source: TransportSource,
                 context_source: Callable[[JudgeRequest], JudgeContext],
                 artifacts_dir: Callable[[JudgeRequest], Path | None],
                 timeout_sec: float,
                 judge_model: Callable[[str], str] | None = None,
                 turn_uncached_budget: int = DEFAULT_TURN_UNCACHED_TOKENS,
                 usage_sink: Callable[[JudgeRequest, Usage], None] | None = None) -> None:
        self._transport_source = transport_source
        self._context_source = context_source
        self._artifacts_dir = artifacts_dir
        self._timeout_sec = timeout_sec
        self._judge_model = judge_model
        self._usage_sink = usage_sink
        self.breaker = JudgeBreaker(turn_budget=turn_uncached_budget)
        self.sessions = JudgeSessions()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # (transport id, model) pairs the provider refused as a judge model: use the
        # agent's model for them instead of paying for a failing call every judgment.
        self._rejected_models: set[tuple[int, str]] = set()

    def forget_thread(self, thread_id: str) -> None:
        """A rewind: drop the thread's judge conversations so rewound messages stop
        authorizing anything."""
        self.sessions.forget_thread(thread_id)

    def _model_for(self, transport: Any, agent_model: str) -> str:
        preferred = (self._judge_model(agent_model) if self._judge_model
                     else preferred_judge_model(transport, agent_model))
        if (id(transport), preferred) in self._rejected_models:
            return agent_model
        return preferred

    async def judge(self, request: JudgeRequest, action: PendingAction) -> AutoDecision:
        key = (request.thread_id, request.agent_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._judge_locked(request, action)

    async def _judge_locked(self, request: JudgeRequest, action: PendingAction) -> AutoDecision:
        transport, agent_model = self._transport_source()
        provider_key = (id(transport), agent_model)
        suspended = self.breaker.allowed(request.thread_id, provider_key)
        if suspended is not None:
            return AutoDecision("ask", suspended, None)
        model = self._model_for(transport, agent_model)
        record: dict[str, object] = {"model": model, "action": asdict(action), "responses": []}
        owner = f"judge:{request.thread_id}:{request.agent_id}"
        try:
            decision = await self._attempt(transport, model, request, action, owner, record)
        except Exception as exc:  # noqa: BLE001 — every judge failure asks the user
            decision = await self._after_failure(exc, transport, agent_model, model, request,
                                                 action, owner, record, provider_key)
        else:
            self.breaker.record_success(request.thread_id)
        used = METER.take(owner)
        self.breaker.record_usage(request.thread_id, used.prompt_tokens - used.cached_tokens)
        record["usage"] = asdict(used)
        if self._usage_sink is not None:
            try:
                self._usage_sink(request, used)
            except Exception:  # noqa: BLE001 — usage accounting must never fail a judgment
                logger.warning("[auto-judge] usage sink failed", exc_info=True)
        record["decision"] = asdict(decision)
        self._write_artifact(request, record)
        return decision

    async def _after_failure(self, exc: Exception, transport: Any, agent_model: str,
                             model: str, request: JudgeRequest, action: PendingAction,
                             owner: str, record: dict[str, object],
                             provider_key: tuple[int, str]) -> AutoDecision:
        logger.warning("[auto-judge] judgment failed thread=%s agent=%s model=%s: %s",
                       request.thread_id, request.agent_id, model, exc, exc_info=True)
        record["error"] = repr(exc)
        if not _is_probative(exc):
            self.breaker.record_failure(request.thread_id, exc)
            return AutoDecision("ask", JUDGE_FAILED_REASON, None)
        if model != agent_model:
            # The judge model was rejected: try the agent's own model once.
            record["fallback_model"] = agent_model
            try:
                decision = await self._attempt(transport, agent_model, request, action,
                                               owner, record)
            except Exception as retry_exc:  # noqa: BLE001
                logger.warning("[auto-judge] fallback model failed too: %s", retry_exc)
                exc = retry_exc
            else:
                self._rejected_models.add((id(transport), model))
                self.breaker.record_success(request.thread_id)
                return decision
        self.breaker.suspend_process(exc, provider_key)
        return AutoDecision("ask", JUDGE_FAILED_REASON, None)

    async def _attempt(self, transport: Any, model: str, request: JudgeRequest,
                       action: PendingAction, owner: str,
                       record: dict[str, object]) -> AutoDecision:
        # Malformed output propagates like any other failure, so a model that keeps
        # answering badly trips the breaker instead of costing a call per command.
        token = USAGE_OWNER.set(owner)
        effort_token = EFFORT_OVERRIDE.set(await _low_effort(transport, model))
        try:
            return await asyncio.wait_for(
                self._run(transport, model, request, action, record), self._timeout_sec)
        finally:
            EFFORT_OVERRIDE.reset(effort_token)
            USAGE_OWNER.reset(token)

    async def _run(self, transport: Any, model: str, request: JudgeRequest,
                   action: PendingAction, record: dict[str, object]) -> AutoDecision:
        context = self._context_source(request)
        session = self.sessions.get(request.thread_id, request.agent_id, context.history_anchor)
        checks: list[dict[str, str]] = []
        responses = record["responses"]
        assert isinstance(responses, list)
        for _ in range(MAX_CHECKS + 1):
            payload = session.payload(context, action, checks, _instruction(len(checks)))
            response = await transport.generate_json(
                model=model, schema_name="auto_judge", schema=JUDGE_SCHEMA,
                system_instructions=JUDGE_POLICY, user_payload=payload, on_retry=_abort_retry)
            responses.append(response)
            if not isinstance(response, dict):
                raise _MalformedVerdict("judge returned no object")
            if response.get("type") == "check" and len(checks) < MAX_CHECKS:
                check = response.get("check")
                if not isinstance(check, dict):
                    raise _MalformedVerdict("check without a check")
                name = str(check.get("name", ""))
                path = check.get("path") if isinstance(check.get("path"), str) else None
                result = await asyncio.to_thread(run_check, name, path, request.workspace)
                checks.append({"check": name, "path": path or "", "result": result})
                continue
            return _decision(response)
        raise _MalformedVerdict("too many checks")

    def _write_artifact(self, request: JudgeRequest, record: dict[str, object]) -> None:
        try:
            directory = self._artifacts_dir(request)
            if directory is None:
                return
            directory.mkdir(parents=True, exist_ok=True)
            index = len(list(directory.glob("auto-judge-*.json"))) + 1
            name = f"auto-judge-{request.agent_id}-{index:02d}.json"
            (directory / name).write_text(json.dumps(record, indent=2, default=str))
        except OSError:
            logger.debug("[auto-judge] artifact write failed", exc_info=True)


def _abort_retry(*_args: object) -> None:
    raise JudgeRetryAbort("transient provider error during an Auto judgment")


def _instruction(checks_used: int) -> str:
    remaining = MAX_CHECKS - checks_used
    return "Judge the pending action. " + (
        f"You may request up to {remaining} more check(s)." if remaining
        else "No checks left: return a verdict.")


async def _low_effort(transport: Any, model: str) -> ReasoningEffort | None:
    """LOW when the model lists it as supported; otherwise leave the transport's own."""
    support = getattr(transport, "reasoning_effort_support", None)
    if not callable(support):
        return None
    try:
        result = await support(model)
    except Exception:  # noqa: BLE001 — capability lookup is best-effort
        return None
    supported: frozenset[ReasoningEffort] = getattr(result, "supported", frozenset())
    return ReasoningEffort.LOW if ReasoningEffort.LOW in supported else None


def _decision(response: dict[str, object]) -> AutoDecision:
    risk = response.get("risk_level")
    authorization = response.get("user_authorization")
    reason = response.get("reason")
    if (response.get("type") != "verdict" or risk not in _RISK
            or authorization not in _AUTHORIZATION
            or not isinstance(reason, str) or not reason.strip()):
        raise _MalformedVerdict("incomplete verdict")
    return verdict_to_decision(str(risk), str(authorization), reason.strip())
