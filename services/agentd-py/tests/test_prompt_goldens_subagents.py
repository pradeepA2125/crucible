"""Main-agent text with sub-agents on (Phase 2): changes to it must be deliberate."""
import json
from pathlib import Path

import pytest

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.tool_source import SubAgentOps, SubAgentToolSource
from tests.prompt_goldens import default_capture


@pytest.fixture(autouse=True)
def _injection_defense_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # The goldens pin the shipped prompt, and the prompt-injection defense ships on.
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")

GOLDEN = Path(__file__).parent / "goldens" / "controller_prompt_subagents.json"


async def _never(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


@default_capture
def _live() -> dict[str, str]:
    tools = [d.model_dump() for d in SubAgentToolSource(BUILTIN_AGENTS, SubAgentOps(
        dispatch=_never, wait=_never, message=_never, stop=_never)).definitions()]
    return {"system/subagents_on": format_controller_system_prompt(
        tools, task_subsystem_enabled=False, memory_enabled=False)}


def test_subagents_main_text_matches_golden() -> None:
    assert _live() == json.loads(GOLDEN.read_text(encoding="utf-8"))


if __name__ == "__main__":  # python -m tests.test_prompt_goldens_subagents  → re-capture
    GOLDEN.write_text(json.dumps(_live(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
