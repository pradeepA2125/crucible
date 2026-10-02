"""CRUCIBLE_SUBAGENTS_ENABLED (spec §12): default ON since Phase 5; a kill-switch."""
import pytest

from agentd.chat.controller_factory import is_subagents_enabled


def test_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED", raising=False)
    assert is_subagents_enabled() is True


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), (" YES ", True), ("on", True),
    ("0", False), ("false", False), ("off", False), ("", False), ("maybe", False),
])
def test_parsing(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", raw)
    assert is_subagents_enabled() is expected
