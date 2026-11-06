"""One trajectory case through the real chat controller, then checked and judged
(observability spec §6.3). Runs in the injection harness's trial world (`evals.common`): a
temp copy of the fixture, a temp HOME, only provider settings carried over."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ShellPolicy
from agentd.eval.trajectory.episode import build_episode
from agentd.eval.trajectory.judge import JudgeResult, judge_episode
from agentd.telemetry.context import EVAL_TAGS
from agentd.tools.shell import _build_shell_command_line, _split_command
from evals.common.cards import CardAnswerer, GateRecord
from evals.common.commands import CommandRecorder, command_may_run
from evals.common.environment import TRACING_ENV, applied_environment, isolated_environment
from evals.common.world import (
    MAX_CONTINUATIONS,
    build_controller,
    start_controller,
    stop_controller,
)
from evals.injection.scoring import DiffRecord
from evals.trajectory.cases import Case
from evals.trajectory.checks import CheckResult, run_checks

# A careful user restates the request rather than picking one of the agent's options.
CLARIFY_ANSWER = "Just do what I originally asked."
_SETTLE_POLL_SEC = 0.2
# Defaults for every case; a case's own flags win.
_CASE_FLAGS: dict[str, str] = {
    "CRUCIBLE_CHAT_CONTROLLER": "1",
    "CRUCIBLE_WEB_SEARCH_ENABLED": "0",
    "CRUCIBLE_WEB_FETCH_ENABLED": "0",
    "CRUCIBLE_MCP_ENABLED": "0",
    "CRUCIBLE_SKILLS_ENABLED": "0",
    "CRUCIBLE_SUBAGENTS_ENABLED": "0",
    "CRUCIBLE_TEAMS_ENABLED": "0",
    "CRUCIBLE_EXEC_SESSIONS_ENABLED": "0",
    "CRUCIBLE_MEMORY_ENABLED": "1",
    "CRUCIBLE_CONTROLLER_MAX_ITERS": "80",
}


@dataclass
class CaseResult:
    case_id: str
    repeat: int
    checks: list[CheckResult]
    judge: JudgeResult | None
    trace_id: str | None
    span_id: str | None
    usage: dict[str, object]
    wall_seconds: float
    error: str | None
    episode_dir: Path | None
    answer: str = ""
    gates: list[GateRecord] = field(default_factory=list)
    annotations_posted: int = 0


class CasePolicy:
    """The careful user for a case: approves the commands the case allows and every edit
    (the `edits_only_in` check is what catches a stray one); never an MCP call."""

    def __init__(self, case: Case, workspace: Path) -> None:
        self._case = case
        self._workspace = workspace

    def command_allowed(self, payload: dict[str, object]) -> bool:
        if payload.get("escalation"):
            return False
        args = payload.get("args")
        line = _build_shell_command_line(*_split_command(
            str(payload.get("command", "")),
            [str(a) for a in args] if isinstance(args, list) else [], self._workspace))
        return command_may_run(line, self._case.allow_commands, "")

    def mcp_allowed(self, tool: str) -> bool:
        return False

    def edit_allowed(self, payload: dict[str, object]) -> bool:
        return True


def _set_approval(ctrl: ChatController, case: Case) -> None:
    full = case.approval == "full"
    state = ctrl.approval_state()
    ctrl.set_approval_mode(
        auto_accept=full, shell_policy=ShellPolicy.ALLOW_ALL if full else ShellPolicy.ASK,
        resolve_waiting=False, expected_process_id=state.process_id,
        expected_version=state.version)


def _record_events(ctrl: ChatController, events: list[dict[str, object]]) -> None:
    broadcaster: Any = ctrl._broadcaster  # noqa: SLF001
    original = broadcaster.broadcast

    def recording(channel_id: str, event: dict[str, object]) -> Any:
        events.append(event)
        return original(channel_id, event)

    broadcaster.broadcast = recording


async def _converse(ctrl: ChatController, store: ChatThreadStore, thread_id: str, case: Case,
                    gates: list[GateRecord]) -> str | None:
    """`evals.common.world.converse`, but every turn starts the way production starts it
    (launch_turn): the thread counts as active, a sub-agent's report reaches the turn's
    inbox, and turn-end bookkeeping (notice re-arming, queued messages) runs."""
    channel = f"chat:{thread_id}"
    await ctrl.launch_turn(thread_id, ctrl.handle_message(thread_id, case.prompt,
                                                          channel_id=channel),
                           channel_id=channel)
    for attempt in range(MAX_CONTINUATIONS + 1):
        thread = store.get_thread(thread_id)
        pending = {g.kind: g for g in (thread.pending_controller_gates if thread else [])}
        if "mode" not in pending and "clarify" not in pending:
            return None
        if attempt == MAX_CONTINUATIONS:
            break
        if "mode" in pending:
            gates.append(GateRecord("mode", dict(pending["mode"].payload), "implement"))
            turn = ctrl.resolve_mode(thread_id, "implement", channel_id=channel,
                                     goal=case.prompt)
        else:
            gates.append(GateRecord("clarify", dict(pending["clarify"].payload),
                                    CLARIFY_ANSWER))
            turn = ctrl.resolve_clarify(thread_id, CLARIFY_ANSWER, channel_id=channel,
                                        goal=case.prompt)
        await ctrl.launch_turn(thread_id, turn, channel_id=channel)
    return f"turn still waiting on a card after {MAX_CONTINUATIONS} answers"


async def _until_settled(ctrl: ChatController, thread_id: str) -> None:
    while not ctrl.episode_settled(thread_id):
        await asyncio.sleep(_SETTLE_POLL_SEC)


def _answer_and_diffs(store: ChatThreadStore, thread_id: str) -> tuple[str, list[DiffRecord]]:
    thread = store.get_thread(thread_id)
    if thread is None:
        return "", []
    diffs = [DiffRecord(str(e.get("path", "")), str(e.get("unified_diff", "")),
                        (m.metadata or {}).get("resolved") == "applied")
             for m in thread.messages if m.type == "diff_card"
             for e in (m.metadata or {}).get("diff_entries") or [] if isinstance(e, dict)]
    answers = [m.content for m in thread.messages
               if m.role == "agent" and m.type == "text" and m.content.strip()
               and not (m.metadata or {}).get("breadcrumb")]
    return (answers[-1] if answers else ""), diffs


async def run_case(
    case: Case, root: Path, *, repeat: int, run_id: str, engine: Any, backend: str,
    model: str, judge_transport: Any, judge_model: str,
    env_overrides: Mapping[str, str] | None = None, keep: bool = False,
) -> CaseResult:
    """Never raises: every failure, the harness's own included, is the result's `error`."""
    started = time.monotonic()
    result = CaseResult(case_id=case.id, repeat=repeat, checks=[], judge=None, trace_id=None,
                        span_id=None, usage={}, wall_seconds=0.0, error=None,
                        episode_dir=root if keep else None)
    ws, home, state = root / "ws", root / "home", root / "state"
    events: list[dict[str, object]] = []
    store: ChatThreadStore | None = None
    thread_id = ""
    try:
        shutil.copytree(case.workspace, ws)
        home.mkdir(parents=True)
        state.mkdir(parents=True)
        env = isolated_environment(os.environ, home, state,
                                   flags={**_CASE_FLAGS, **case.flags, **(env_overrides or {})},
                                   keep_extra=TRACING_ENV)
        # The case's own fixture: commands it allows really run, edits included (the
        # agent's work on the fixture is what is graded).
        recorder = CommandRecorder(case.allow_commands)
        with applied_environment(env), recorder.install():
            store = ChatThreadStore(state / "chat.sqlite3")
            ctrl, _actions = build_controller(ws, state, store, engine=engine, backend=backend,
                                              model=model)
            try:
                await start_controller(ctrl, live=engine is None, backend=backend, model=model)
                _set_approval(ctrl, case)
                _record_events(ctrl, events)
                thread_id = store.create_thread(str(ws), title=case.id).thread_id
                answerer = CardAnswerer(ctrl, store, thread_id, CasePolicy(case, ws))
                stop = asyncio.Event()
                answering = asyncio.create_task(answerer.run(stop))
                tags = EVAL_TAGS.set({"crucible.eval_run_id": run_id,
                                      "crucible.eval_case_id": case.id})
                try:
                    async def episode() -> None:
                        error = await _converse(ctrl, store, thread_id, case, result.gates)
                        if error is not None:
                            result.error = error
                        await _until_settled(ctrl, thread_id)

                    await asyncio.wait_for(episode(), timeout=case.max_seconds)
                except TimeoutError:
                    result.error = f"time cap of {case.max_seconds:g}s reached"
                    await ctrl.stop_all_agents(thread_id)
                    await ctrl.stop_turn(thread_id)
                finally:
                    EVAL_TAGS.reset(tags)
                    stop.set()
                    await answering
                    result.gates.extend(r for r in answerer.records if r not in result.gates)
                result.usage = ctrl.usage_breakdown(thread_id)
            finally:
                await stop_controller(ctrl)
        result.answer, diffs = _answer_and_diffs(store, thread_id)
        result.checks = run_checks(case, ws, events, result.usage, result.answer, diffs)
        episode_record = build_episode(store, ws, thread_id)
        if episode_record.turns:
            result.trace_id = episode_record.turns[0].trace_id
            result.span_id = episode_record.turns[0].span_id
        if result.error is None and judge_transport is not None:
            result.judge = await judge_episode(judge_transport, judge_model, episode_record,
                                               run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — a harness failure is a recorded result
        result.error = result.error or f"harness: {type(exc).__name__}: {exc}"
    result.wall_seconds = time.monotonic() - started
    return result
