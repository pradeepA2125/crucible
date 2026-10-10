"""Every controller audience receives research policy for its actual capabilities."""

import pytest

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext


def prompt(audience, tools, *, permission="default", context_tools=None):
    ctx = RenderContext(
        audience="main" if audience == "main" else "child",
        permission=permission,
        tools=frozenset(tools if context_tools is None else context_tools),
        base_types=frozenset({"tool_call", "progress", "report"}),
        agent_id="researcher" if audience != "main" else "",
        team_brief="Goal: investigate dependency support" if audience == "team" else "",
    )
    rendered = format_controller_system_prompt(
        [{"name": name} for name in tools],
        render_ctx=ctx,
        task_subsystem_enabled=False,
        memory_enabled=False,
    )
    return rendered.split("WEB RESEARCH AND FACTUAL CONFIDENCE", 1)[1]


@pytest.mark.parametrize("audience", ["main", "child", "team"])
@pytest.mark.parametrize("tools", [[], ["web_search"], ["web_fetch"], ["web_search", "web_fetch"]])
def test_native_web_guidance_matches_effective_capabilities(audience, tools):
    text = prompt(audience, tools)
    assert ('"tool":"web_search"' in text) == ("web_search" in tools)
    assert ('"tool":"web_fetch"' in text) == ("web_fetch" in tools)
    if not tools:
        assert "Native web tools are unavailable" in text
    elif tools == ["web_search"]:
        assert "Page fetching is unavailable" in text
    elif tools == ["web_fetch"]:
        assert "Source discovery is unavailable" in text
    else:
        assert "fetch the relevant result pages" in text
    assert "Research, search, look up" in text
    assert "changed since your training" in text
    assert "search the repository" in text
    assert "source URLs" in text and "untrusted" in text
    assert "Respect approval denials and disabled tools" in text
    assert "shared board" in text if audience == "team" else "shared board" not in text
    if audience == "child":
        assert "report to the parent" in text
    if audience == "main":
        assert "report to the parent" not in text


@pytest.mark.parametrize("audience", ["child", "team"])
def test_child_policy_intersects_definitions_with_capability_context(audience):
    text = prompt(audience, ["web_search", "web_fetch"], context_tools=["web_search"])
    assert '"tool":"web_search"' in text
    assert '"tool":"web_fetch"' not in text
    assert "Page fetching is unavailable" in text


@pytest.mark.parametrize("audience", ["child", "team"])
def test_read_only_child_never_gets_network_call_instructions(audience):
    text = prompt(audience, ["web_search", "web_fetch"], permission="plan")
    assert '"tool":"web_search"' not in text and '"tool":"web_fetch"' not in text
    assert "Native web tools are unavailable" in text
    assert "bypass restrictions" in text and "state what you cannot verify" in text


@pytest.mark.parametrize("permission", ["default", "acceptEdits", "dontAsk"])
def test_non_read_only_children_keep_web_policy_without_approval_bypass(permission):
    text = prompt("child", ["web_search", "web_fetch"], permission=permission)
    assert '"tool":"web_search"' in text and '"tool":"web_fetch"' in text
    assert "do not retry rejected calls" in text
