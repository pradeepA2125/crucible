"""Suite-wide fixtures.

CRUCIBLE_SUBAGENTS_ENABLED defaults ON (spec §12, Phase 5). With it on, every controller
built in a test would offer dispatch_agents, append the SUB-AGENTS teaching block and track
reads in a write log — changing prompts, tool lists and goldens that have nothing to do
with sub-agents. So the suite runs with it OFF; sub-agent tests opt in with
monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1") (spec §14).
CRUCIBLE_TEAMS_ENABLED (default ON since Phase 6) is forced off for the same reason; team tests
opt in with "1". To test "off", set "0" explicitly: delenv now means the default, which is ON.
"""
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _subagents_off_unless_a_test_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "0")


@pytest.fixture(autouse=True)
def _teams_off_unless_a_test_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "0")


@pytest.fixture(autouse=True)
def _fresh_shared_models() -> Iterator[None]:
    # Tests swap in fake `sentence_transformers` modules; a model cached by one test must
    # not be served to the next.
    from agentd.model_loading import clear_shared_models
    clear_shared_models()
    yield
    clear_shared_models()
