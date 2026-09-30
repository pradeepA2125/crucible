"""Tagged controller prompt constants (spec §4.7.4 property test + §4.7.2 injection rule)."""
import re

import pytest

from agentd.chat import controller_prompts as cp
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext, render_prompt

TAGGED_CONSTANTS = [
    "CONTROLLER_SYSTEM_PROMPT", "_PROPOSE_MODE_MODES_ENABLED", "_PROPOSE_MODE_MODES_DISABLED",
    "_MEMORY_BLOCK", "_INSTRUCTIONS_BLOCK_TEMPLATE", "_MCP_BLOCK", "_SESSIONS_BLOCK",
    "_SKILLS_BLOCK_HEADER",
]
_NON_MAIN = r"child(?::\w+)?|perm:\w+|shell:\w+|type:report"
_MARK = r"<</?[a-z_]+(?::\w+)?>>"


def _expected_main(template: str) -> str:
    """Independent of the resolver: delete regions that never render for main, then strip
    the remaining markers by the §4.7.2 whitespace rules."""
    t = re.sub(rf"^[ \t]*<<({_NON_MAIN})>>[ \t]*\n.*?^[ \t]*<</\1>>[ \t]*(?:\n|\Z)", "", template,
               flags=re.M | re.S)
    t = re.sub(rf"<<({_NON_MAIN})>>.*?<</\1>>", "", t)
    t = re.sub(rf"^[ \t]*{_MARK}[ \t]*\n", "", t, flags=re.M)
    t = re.sub(rf"\n[ \t]*{_MARK}[ \t]*\Z", "", t)
    return re.sub(_MARK, "", t)


@pytest.mark.parametrize("name", TAGGED_CONSTANTS)
def test_constant_is_tagged(name: str) -> None:
    assert "<<" in getattr(cp, name) or name == "_SESSIONS_BLOCK"


@pytest.mark.parametrize("name", TAGGED_CONSTANTS)
def test_main_render_equals_template_minus_non_main_regions(name: str) -> None:
    template = getattr(cp, name)
    assert render_prompt(template, RenderContext.main()) == _expected_main(template)


def test_injected_instructions_are_never_parsed_for_tags() -> None:
    out = format_controller_system_prompt(
        [], task_subsystem_enabled=False, memory_enabled=False,
        project_instructions="Keep <<main>>this<</main>> literal {x}")
    assert "Keep <<main>>this<</main>> literal {x}" in out
    assert "<<child>>" not in out and "<</main>>\n" not in out.split("Keep")[0]
