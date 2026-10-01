"""Sub-agent identity, config and the child RenderContext (spec §3, §4.7.2, §9.4, §12)."""
import pytest

from agentd.prompting.tagged import RenderContext
from agentd.subagents.config import (
    subagent_max_concurrent,
    subagent_max_depth,
    subagent_max_iters,
)
from agentd.subagents.context import AgentContext, new_agent_id
from agentd.subagents.definitions import BUILTIN_AGENTS


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CRUCIBLE_SUBAGENT_MAX_DEPTH", "CRUCIBLE_SUBAGENT_MAX_CONCURRENT",
                 "CRUCIBLE_SUBAGENT_MAX_ITERS"):
        monkeypatch.delenv(name, raising=False)
    assert (subagent_max_depth(), subagent_max_concurrent(), subagent_max_iters()) == (2, 8, 100)


def test_config_degrades_on_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_DEPTH", "three")
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_CONCURRENT", "0")
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_ITERS", " 40 ")
    assert (subagent_max_depth(), subagent_max_concurrent(), subagent_max_iters()) == (2, 1, 40)


def test_agent_ids_are_unique_and_prefixed() -> None:
    first, second = new_agent_id(), new_agent_id()
    assert first.startswith("agent-") and len(first) == len("agent-") + 12
    assert first != second


def test_builtins() -> None:
    explore = BUILTIN_AGENTS["explore"]
    general = BUILTIN_AGENTS["general-purpose"]
    assert explore.permission == "plan"
    assert explore.tools == frozenset(
        {"search_code", "read_file", "list_directory", "query_graph", "search_semantic"})
    assert general.permission == "default" and general.tools is None
    assert explore.description and general.description and explore.persona


def test_render_context_for_an_agent() -> None:
    agent = AgentContext(
        agent_id="agent-abc", name="explore", label="auth survey", depth=1,
        parent_agent_id=None, permission="plan", allowed_types=("tool_call", "progress", "report"),
        persona="p", max_iters=20)
    ctx = RenderContext.for_agent(agent, tools=frozenset({"read_file"}), shell_policy="ask")
    assert not ctx.is_main
    assert (ctx.permission, ctx.shell_policy, ctx.agent_id, ctx.agent_label) == (
        "plan", "ask", "agent-abc", "auth survey")
    assert ctx.tools == frozenset({"read_file"})
    assert ctx.base_types == frozenset({"tool_call", "progress", "report"})
    hash(ctx)  # stays usable as a render-cache key
