"""Answers a trial's approval cards like a careful user (spec §5).

It approves exactly what the user's real request needs (the scenario's `allow`) and rejects
everything else, recording every card either way. Mode and clarify cards are left alone: they
end the turn, and `run_trial` continues it."""
from __future__ import annotations

import asyncio
from pathlib import Path

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, McpToolDecision
from agentd.tools.shell import _build_shell_command_line, _split_command
from evals.injection.isolation import command_may_run
from evals.injection.models import Scenario
from evals.injection.scoring import GateRecord

_POLL_SEC = 0.1


class GateAnswerer:
    def __init__(self, ctrl: ChatController, store: ChatThreadStore, thread_id: str,
                 scenario: Scenario, workspace: Path, marker: str) -> None:
        self._ctrl = ctrl
        self._store = store
        self._thread_id = thread_id
        self._scenario = scenario
        self._workspace = workspace
        self._marker = marker
        self._seen: set[str] = set()
        self.records: list[GateRecord] = []

    async def run(self, stop: asyncio.Event) -> None:
        while True:
            await self.answer_pending()
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=_POLL_SEC)
            except TimeoutError:
                pass

    async def answer_pending(self) -> None:
        thread = self._store.get_thread(self._thread_id)
        if thread is None:
            return
        for gate in thread.pending_controller_gates:
            if gate.gate_id in self._seen or gate.kind not in ("command", "mcp_tool", "edit"):
                continue
            self._seen.add(gate.gate_id)
            payload = dict(gate.payload)
            if gate.kind == "command":
                approve = self._command_allowed(payload)
                await self._ctrl.resolve_command(
                    self._thread_id, CommandDecision(approve=approve), gate.gate_id)
            elif gate.kind == "mcp_tool":
                approve = str(payload.get("tool", "")) in self._scenario.allow.mcp_tools
                await self._ctrl.resolve_mcp(
                    self._thread_id, McpToolDecision(approve=approve), gate.gate_id)
            else:
                approve = self._edit_allowed(payload)
                await self._ctrl.resolve_edit(
                    self._thread_id,
                    {"decision": "accept" if approve else "reject", "reason": "eval"},
                    gate.gate_id)
            self.records.append(GateRecord(gate.kind, payload, "approve" if approve else "reject"))

    def _command_allowed(self, payload: dict[str, object]) -> bool:
        # Leaving the sandbox or opening hosts is never part of a scenario's real request.
        if payload.get("escalation"):
            return False
        args = payload.get("args")
        line = _build_shell_command_line(*_split_command(
            str(payload.get("command", "")),
            [str(a) for a in args] if isinstance(args, list) else [], self._workspace))
        return command_may_run(line, self._scenario.allow, self._marker)

    def _edit_allowed(self, payload: dict[str, object]) -> bool:
        allowed = set(self._scenario.allow.edit_paths)
        check = self._scenario.benign_check
        if check.kind == "file_matches":
            allowed.add(check.path)
        entries = payload.get("diff_entries")
        paths = [str(e.get("path", "")) for e in entries or [] if isinstance(e, dict)] \
            if isinstance(entries, list) else []
        return bool(paths) and all(p in allowed for p in paths)
