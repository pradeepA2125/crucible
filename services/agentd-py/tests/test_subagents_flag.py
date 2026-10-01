"""CRUCIBLE_SUBAGENTS_ENABLED (spec §12): default OFF until Phase 5 flips it."""
import pytest

from agentd.chat.controller_factory import is_subagents_enabled


def test_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED", raising=False)
    assert is_subagents_enabled() is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), (" YES ", True), ("on", True),
    ("0", False), ("false", False), ("off", False), ("", False), ("maybe", False),
])
def test_parsing(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", raw)
    assert is_subagents_enabled() is expected
