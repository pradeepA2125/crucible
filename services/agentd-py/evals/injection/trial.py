"""Running one trial through the real chat controller (spec §3)."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agentd.chat.controller import ChatController
from agentd.chat.controller_factory import select_chat_handler
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.trust.project_trust import kind_for_path, project_trust_for
from evals.injection.answerer import GateAnswerer
from evals.injection.isolation import CommandRecorder, applied_environment, eval_environment
from evals.injection.models import Scenario, Trial
from evals.injection.scenarios import materialize, read_sink
from evals.injection.scoring import DiffRecord, TrialRecord

# A scripted engine for the harness's own tests; None means the live provider from the env.
EngineFactory = Callable[[Trial, Path], Any]

_MAX_CONTINUATIONS = 3
# Text the controller writes when a turn dies, rather than an answer from the agent.
_FAILED_TURN_PREFIX = "⚠️ The turn failed"
_CLARIFY_FALLBACK = "Go ahead with whatever you think is best."


def trial_id(trial: Trial) -> str:
    defense = "on" if trial.arm.defense else "off"
    return f"{trial.scenario.id}:{defense}:{trial.arm.approval}:{trial.index}"


async def run_trial(
    trial: Trial, root: Path, engine_factory: EngineFactory | None = None,
    max_seconds: float = 300.0, *, backend: str = "", model: str = "",
    env_overrides: Mapping[str, str] | None = None,
) -> TrialRecord:
    """Run one trial in a fresh world under `root` and return everything it left behind.

    Every failure is recorded as `error` (and so excluded from rates), never as a clean
    result."""
    scenario = trial.scenario
    ws, home, state = root / "ws", root / "home", root / "state"
    home.mkdir(parents=True)
    state.mkdir(parents=True)
    materialize(scenario, trial.marker, ws)
    env = eval_environment(os.environ, home, trial, state)
    env.update(env_overrides or {})
    recorder = CommandRecorder(scenario.allow)
    record = TrialRecord(
        trial_id=trial_id(trial), scenario_id=scenario.id, arm=trial.arm, marker=trial.marker,
        gates=[], commands=recorder.records, tool_events=[], diffs=[], answer="", sink=[],
        workspace=ws, error=None, usage={},
    )
    with applied_environment(env), recorder.install():
        ctrl: ChatController | None = None
        try:
            _trust_user_files(ws, scenario.user_trusted)
            store = ChatThreadStore(state / "chat.sqlite3")
            ctrl = _build_controller(trial, ws, store, engine_factory, backend, model)
            await _start(ctrl)
            approval = ctrl.approval_state()
            ctrl.set_approval_mode(
                # Auto applies edits on its own, as the composer's Auto mode does; protected
                # files still raise a card.
                auto_accept=trial.arm.approval == "auto",
                shell_policy=ShellPolicy.AUTO if trial.arm.approval == "auto" else ShellPolicy.ASK,
                resolve_waiting=False, expected_process_id=approval.process_id,
                expected_version=approval.version)
            thread = store.create_thread(str(ws), title=scenario.id)
            answerer = GateAnswerer(ctrl, store, thread.thread_id, scenario, ws)
            stop = asyncio.Event()
            answering = asyncio.create_task(answerer.run(stop))
            try:
                await asyncio.wait_for(
                    _converse(ctrl, store, thread.thread_id, scenario), timeout=max_seconds)
            except TimeoutError:
                record.error = f"time cap of {max_seconds:g}s reached"
            finally:
                stop.set()
                await answering
                record.gates = answerer.records
            _collect(record, store, thread.thread_id)
            record.usage = ctrl.usage_breakdown(thread.thread_id)
        except Exception as exc:  # noqa: BLE001 — a harness failure is a recorded result
            record.error = record.error or f"harness: {type(exc).__name__}: {exc}"
        finally:
            if ctrl is not None:
                await _stop(ctrl)
        record.sink = read_sink(ws)
    return record


def _trust_user_files(ws: Path, paths: tuple[str, ...]) -> None:
    """Files the user set up themselves are trusted, as they would have done in the app."""
    trust = project_trust_for(ws)
    for rel in paths:
        kind = kind_for_path(ws, ws / rel)
        if kind is None:
            raise ValueError(f"user_trusted path {rel!r} is not a file that takes trust")
        sha = trust.fingerprint(kind)
        if sha is None:
            raise ValueError(f"user_trusted file {rel!r} is missing from the workspace")
        trust.trust(kind, sha)


def _build_controller(
    trial: Trial, ws: Path, store: ChatThreadStore, engine_factory: EngineFactory | None,
    backend: str, model: str,
) -> ChatController:
    # Built inside the trial's environment: flags and Path.home() are read at construction.
    scripted = engine_factory(trial, ws) if engine_factory is not None else None
    if scripted is None:
        from agentd.providers.factory import build_transport, resolve_model
        transport = build_transport(backend)
        model = model or resolve_model(backend)
    else:
        transport = None
    ctrl = select_chat_handler(
        workspace_path=str(ws), transport=transport, model=model, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster(), shell_policy=ShellPolicy.ASK)
    if not isinstance(ctrl, ChatController):
        raise RuntimeError("the eval needs the chat controller (CRUCIBLE_CHAT_CONTROLLER)")
    if scripted is not None:
        # Every collaborator stays the factory's; only the model is the script.
        ctrl._reasoning = scripted  # noqa: SLF001
    return ctrl


async def _start(ctrl: ChatController) -> None:
    """What main.py does at startup: connect MCP servers and start the sandbox."""
    manager = getattr(ctrl, "_mcp_manager", None)
    if manager is not None:
        await manager.start()
    sandbox = ctrl.sandbox_runtime
    if sandbox is not None:
        try:
            await sandbox.start()
        except Exception:  # noqa: BLE001 — as in main.py: commands then run unsandboxed
            pass


async def _stop(ctrl: ChatController) -> None:
    manager = getattr(ctrl, "_mcp_manager", None)
    if manager is not None:
        await manager.shutdown()
    if ctrl.sandbox_runtime is not None:
        await ctrl.sandbox_runtime.close()


async def _converse(
    ctrl: ChatController, store: ChatThreadStore, thread_id: str, scenario: Scenario,
) -> None:
    channel = f"chat:{thread_id}"
    await ctrl.handle_message(thread_id, scenario.user_message, channel_id=channel)
    # A turn that stops on a mode or clarify card isn't finished: a careful user answers it.
    for _ in range(_MAX_CONTINUATIONS):
        thread = store.get_thread(thread_id)
        gates = {g.kind: g for g in (thread.pending_controller_gates if thread else [])}
        if "mode" in gates:
            await ctrl.resolve_mode(thread_id, "implement", channel_id=channel,
                                    goal=scenario.user_message)
        elif "clarify" in gates:
            options = gates["clarify"].payload.get("options") or []
            answer = str(options[0]) if isinstance(options, list) and options else _CLARIFY_FALLBACK
            await ctrl.resolve_clarify(thread_id, answer, channel_id=channel,
                                       goal=scenario.user_message)
        else:
            return


def _collect(record: TrialRecord, store: ChatThreadStore, thread_id: str) -> None:
    thread = store.get_thread(thread_id)
    if thread is None:
        raise RuntimeError(f"thread {thread_id} vanished")
    for message in thread.messages:
        meta = message.metadata or {}
        record.tool_events.extend(meta.get("tool_events") or [])
        if message.type == "diff_card":
            applied = meta.get("resolved") == "applied"
            for entry in meta.get("diff_entries") or []:
                record.diffs.append(DiffRecord(
                    str(entry.get("path", "")), str(entry.get("unified_diff", "")), applied))
    answers = [m.content for m in thread.messages
               if m.role == "agent" and m.type != "diff_card" and m.content.strip()
               and not (m.metadata or {}).get("breadcrumb")]
    final = answers[-1] if answers else ""
    if final.startswith(_FAILED_TURN_PREFIX) or (
            thread.messages and (thread.messages[-1].metadata or {}).get("system_notice")):
        record.error = record.error or f"turn failed: {final[:300]}"
    record.answer = final
