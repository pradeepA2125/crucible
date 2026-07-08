from pathlib import Path

from agentd.chat.agent import ChatAgent
from agentd.chat.controller import ChatController
from agentd.chat.controller_factory import select_chat_handler
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster


class _DummyTransport:
    """Construction-only stub — neither handler calls the transport in __init__."""


def _deps(tmp_path: Path) -> dict:
    return dict(
        workspace_path=str(tmp_path),
        transport=_DummyTransport(),
        model="m",
        thread_store=ChatThreadStore(tmp_path / "c.sqlite3"),
        orchestrator=None,
        broadcaster=EventBroadcaster(),
        retrieval_client=None,
    )


def test_flag_on_selects_controller(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "1")
    assert isinstance(select_chat_handler(**_deps(tmp_path)), ChatController)


def test_flag_off_selects_legacy_agent(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "0")
    assert isinstance(select_chat_handler(**_deps(tmp_path)), ChatAgent)


def test_default_is_legacy_until_smoke_verified(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("CRUCIBLE_CHAT_CONTROLLER", raising=False)
    assert isinstance(select_chat_handler(**_deps(tmp_path)), ChatAgent)
