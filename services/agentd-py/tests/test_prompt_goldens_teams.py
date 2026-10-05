"""Team prompt text (spec v2 §7.5): changes to it must be deliberate."""
import json
from pathlib import Path

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.tools import MainTeamOps, MainTeamToolSource

GOLDEN = Path(__file__).parent / "goldens" / "controller_prompt_teams.json"
BRIEF = ("Team 'auth'. Goal: Add login\nRoster:\n- alice (you): general-purpose — edits\n"
         "- bob: explore — reads")


async def _never(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


def _live() -> dict[str, str]:
    main_tools = [d.model_dump() for d in MainTeamToolSource(BUILTIN_AGENTS, MainTeamOps(
        create=_never, resolve=lambda r: r, post=lambda *a: {}, status=lambda t: {},
        disband=_never), first_turn_team_ids=set()).definitions()]
    agent = AgentContext(agent_id="agent-1", name="general-purpose", label="alice", depth=1,
                         parent_agent_id=None, permission="default",
                         allowed_types=("tool_call", "edit", "progress", "report"),
                         persona="", max_iters=10)
    member = RenderContext.for_agent(agent, tools=frozenset({"read_file", "team_post"}),
                                     shell_policy="ask", team_brief=BRIEF)
    return {
        "system/teams_main": format_controller_system_prompt(
            main_tools, task_subsystem_enabled=False, memory_enabled=False),
        "system/teams_member": format_controller_system_prompt(
            [{"name": "team_post"}], task_subsystem_enabled=False, memory_enabled=False,
            render_ctx=member, persona="You edit code."),
    }


def test_teams_text_matches_golden() -> None:
    assert _live() == json.loads(GOLDEN.read_text(encoding="utf-8"))


if __name__ == "__main__":  # python -m tests.test_prompt_goldens_teams  → re-capture
    GOLDEN.write_text(json.dumps(_live(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
