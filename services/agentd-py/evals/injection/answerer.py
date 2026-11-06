"""Answers a trial's approval cards like a careful user (spec §5).

It approves exactly what the user's real request needs (the scenario's `allow`) and rejects
everything else, recording every card either way (`evals.common.cards`)."""
from __future__ import annotations

from pathlib import Path

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.tools.shell import _build_shell_command_line, _split_command
from evals.common.cards import CardAnswerer
from evals.injection.isolation import command_may_run
from evals.injection.models import Scenario


class ScenarioPolicy:
    def __init__(self, scenario: Scenario, workspace: Path, marker: str) -> None:
        self._scenario = scenario
        self._workspace = workspace
        self._marker = marker

    def mcp_allowed(self, tool: str) -> bool:
        return tool in self._scenario.allow.mcp_tools

    def command_allowed(self, payload: dict[str, object]) -> bool:
        # Leaving the sandbox or opening hosts is never part of a scenario's real request.
        if payload.get("escalation"):
            return False
        args = payload.get("args")
        line = _build_shell_command_line(*_split_command(
            str(payload.get("command", "")),
            [str(a) for a in args] if isinstance(args, list) else [], self._workspace))
        return command_may_run(line, self._scenario.allow, self._marker)

    def edit_allowed(self, payload: dict[str, object]) -> bool:
        allowed = set(self._scenario.allow.edit_paths)
        check = self._scenario.benign_check
        if check.kind == "file_matches":
            allowed.add(check.path)
        entries = payload.get("diff_entries")
        paths = [str(e.get("path", "")) for e in entries or [] if isinstance(e, dict)] \
            if isinstance(entries, list) else []
        return bool(paths) and all(p in allowed for p in paths)


class GateAnswerer(CardAnswerer):
    def __init__(self, ctrl: ChatController, store: ChatThreadStore, thread_id: str,
                 scenario: Scenario, workspace: Path, marker: str) -> None:
        super().__init__(ctrl, store, thread_id, ScenarioPolicy(scenario, workspace, marker))
