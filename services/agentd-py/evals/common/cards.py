"""Answers a trial's approval cards like a careful user (injection spec §5).

A policy says what the user's real request needs; everything else is rejected, and every
card is recorded either way. Mode and clarify cards are left alone: they end the turn, and the
harness continues it."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, McpToolDecision

_POLL_SEC = 0.1


@dataclass(frozen=True)
class GateRecord:
    kind: str
    payload: dict[str, Any]
    decision: str


class CardPolicy(Protocol):
    def command_allowed(self, payload: dict[str, object]) -> bool: ...
    def mcp_allowed(self, tool: str) -> bool: ...
    def edit_allowed(self, payload: dict[str, object]) -> bool: ...


class CardAnswerer:
    def __init__(self, ctrl: ChatController, store: ChatThreadStore, thread_id: str,
                 policy: CardPolicy) -> None:
        self._ctrl = ctrl
        self._store = store
        self._thread_id = thread_id
        self._policy = policy
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
                approve = self._policy.command_allowed(payload)
                await self._ctrl.resolve_command(
                    self._thread_id, CommandDecision(approve=approve), gate.gate_id)
            elif gate.kind == "mcp_tool":
                approve = self._policy.mcp_allowed(str(payload.get("tool", "")))
                await self._ctrl.resolve_mcp(
                    self._thread_id, McpToolDecision(approve=approve), gate.gate_id)
            else:
                approve = self._policy.edit_allowed(payload)
                await self._ctrl.resolve_edit(
                    self._thread_id,
                    {"decision": "accept" if approve else "reject", "reason": "eval"},
                    gate.gate_id)
            self.records.append(GateRecord(gate.kind, payload, "approve" if approve else "reject"))
