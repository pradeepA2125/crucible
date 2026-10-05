"""Team limits (spec v2 §3.11, §11.1). Read per call, so a test's monkeypatch applies."""
from __future__ import annotations

from agentd.subagents.config import _int_env


def team_max_members() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_MEMBERS", 6, 2)


def team_max_live_per_thread() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_LIVE_PER_THREAD", 2, 1)


def team_budget_per_member() -> int:
    return _int_env("CRUCIBLE_TEAM_REQUEST_BUDGET_PER_MEMBER", 80, 1)


def team_max_budget() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_BUDGET", 1000, 1)


def team_max_wakes() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_WAKES", 15, 1)
