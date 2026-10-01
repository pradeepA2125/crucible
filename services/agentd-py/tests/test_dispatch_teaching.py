"""Parent teaching for dispatch_agents (spec §6.6)."""
from agentd.chat.controller_prompts import (
    build_controller_step_payload,
    format_controller_system_prompt,
)
from agentd.prompting.tagged import RenderContext

DISPATCH = {"name": "dispatch_agents", "description": "d",
            "parameters": {"type": "object"}}
CHILD = RenderContext(audience="child", permission="default",
                      tools=frozenset({"dispatch_agents", "read_file"}),
                      base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                      agent_id="agent-1", agent_label="lead")


def _prompt(tools: list[dict[str, object]], ctx: RenderContext | None = None) -> str:
    return format_controller_system_prompt(
        tools, task_subsystem_enabled=False, memory_enabled=False, render_ctx=ctx)


def test_the_block_appears_only_with_the_tool() -> None:
    assert "SUB-AGENTS (dispatch_agents)" not in _prompt([])
    main = _prompt([DISPATCH])
    assert "SUB-AGENTS (dispatch_agents)" in main
    assert "Commit after the batch yourself" in main


def test_a_child_that_may_dispatch_gets_the_rules_without_main_only_lines() -> None:
    child = _prompt([DISPATCH], CHILD)
    assert "SUB-AGENTS (dispatch_agents)" in child
    assert "Commit after the batch yourself" not in child
    assert "first action instead of write_todos" not in child


def _entry_hint(dispatch_available: bool) -> str:
    context: dict[str, object] = {"goal": "g", "workspace_path": "/w", "active_entry": True,
                                  "iteration": 0, "max_iters": 10}
    if dispatch_available:
        context["dispatch_available"] = True
    return str(build_controller_step_payload(context, [], [], phase="ACTIVE")["instruction"])


def test_the_entry_hint_clause_is_conditional() -> None:
    clause = ("Or, for independent parts touching disjoint files, dispatch them in parallel "
              "with dispatch_agents (a tool_call — see SUB-AGENTS).")
    assert clause in _entry_hint(True)
    assert clause not in _entry_hint(False)
