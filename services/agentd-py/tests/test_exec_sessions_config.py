from agentd.exec_sessions.config import (
    clamp_yield_ms,
    is_exec_sessions_enabled,
    max_session_count,
)


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", raising=False)
    assert is_exec_sessions_enabled() is False


def test_flag_truthy(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", "1")
    assert is_exec_sessions_enabled() is True


def test_clamp_yield_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_EXEC_SESSION_DEFAULT_YIELD_MS", raising=False)
    assert clamp_yield_ms(None) == 2000
    assert clamp_yield_ms(50) == 250
    assert clamp_yield_ms(99_999) == 30_000
    assert clamp_yield_ms("not a number") == 2000
    assert clamp_yield_ms(5000) == 5000


def test_max_count_env(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSION_MAX_COUNT", "3")
    assert max_session_count() == 3
