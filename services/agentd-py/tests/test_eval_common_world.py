"""The trial world both eval harnesses run in (observability spec §6.3)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from evals.common.cards import CardAnswerer
from evals.common.commands import CommandRecorder, command_may_run
from evals.common.environment import TRACING_ENV, isolated_environment


def test_isolated_environment_keeps_only_provider_settings_and_applies_flags(
    tmp_path: Path,
) -> None:
    real = {"HOME": "/Users/me", "PATH": "/bin", "CRUCIBLE_DB_PATH": "/real/agentd.sqlite3",
            "CRUCIBLE_OPENAI_MODEL": "gpt-5.6-luna", "CRUCIBLE_TRACING_ENABLED": "1",
            "CRUCIBLE_SUBAGENTS_ENABLED": "0"}
    env = isolated_environment(real, tmp_path / "home", tmp_path / "state",
                               flags={"CRUCIBLE_SUBAGENTS_ENABLED": "1"})
    assert env["CRUCIBLE_DB_PATH"] == str(tmp_path / "state" / "agentd.sqlite3")
    assert env["CRUCIBLE_OPENAI_MODEL"] == "gpt-5.6-luna"
    assert env["CRUCIBLE_SUBAGENTS_ENABLED"] == "1"
    assert env["HOME"] == str(tmp_path / "home") and env["PATH"] == "/bin"
    # Tracing settings are dropped unless the caller keeps them.
    assert "CRUCIBLE_TRACING_ENABLED" not in env
    kept = isolated_environment(real, tmp_path / "home", tmp_path / "state", flags={},
                                keep_extra=TRACING_ENV)
    assert kept["CRUCIBLE_TRACING_ENABLED"] == "1"


def test_command_rules_take_plain_globs(tmp_path: Path) -> None:
    assert command_may_run("python -m pytest -q", ["*pytest*"], "")
    assert not command_may_run("pytest; rm -rf /", ["*pytest*"], "")
    assert not command_may_run("ls", ["*pytest*"], "")
    assert CommandRecorder(["*pytest*"]).records == []


class _EditsOnly:
    def command_allowed(self, payload: dict[str, object]) -> bool:
        return False

    def mcp_allowed(self, tool: str) -> bool:
        return False

    def edit_allowed(self, payload: dict[str, object]) -> bool:
        return True


class _Ctrl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def resolve_command(self, thread_id: str, decision: Any, gate_id: str) -> None:
        self.calls.append(("command", decision.approve))

    async def resolve_mcp(self, thread_id: str, decision: Any, gate_id: str) -> None:
        self.calls.append(("mcp", decision.approve))

    async def resolve_edit(self, thread_id: str, decision: dict[str, str], gate_id: str) -> None:
        self.calls.append(("edit", decision["decision"]))


@pytest.mark.asyncio
async def test_card_answerer_uses_the_policy(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    tid = store.create_thread(str(tmp_path), "t").thread_id
    store.add_controller_gate(tid, PendingGate(gate_id="g1", kind="edit", payload={}))
    store.add_controller_gate(tid, PendingGate(gate_id="g2", kind="command",
                                               payload={"command": "ls"}))
    ctrl = _Ctrl()
    answerer = CardAnswerer(ctrl, store, tid, _EditsOnly())  # type: ignore[arg-type]
    await answerer.answer_pending()
    await answerer.answer_pending()  # a card is answered once
    assert ctrl.calls == [("edit", "accept"), ("command", False)]
    assert [(r.kind, r.decision) for r in answerer.records] == [
        ("edit", "approve"), ("command", "reject")]
