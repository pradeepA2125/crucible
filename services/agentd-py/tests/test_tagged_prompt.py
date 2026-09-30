import pytest

from agentd.prompting.tagged import (
    PromptTemplateError,
    RenderContext,
    render_prompt,
    tagged,
    validate_template,
)

MAIN = RenderContext.main()
CHILD = RenderContext(
    audience="child", permission="default", tools=frozenset({"write_todos"}),
    base_types=frozenset({"tool_call", "edit", "progress", "report"}),
    agent_id="a1", agent_label="impl")
READONLY = RenderContext(
    audience="child", permission="plan",
    base_types=frozenset({"tool_call", "progress", "report"}),
    agent_id="a2", agent_label="survey")


def test_block_regions_are_removed_with_their_lines() -> None:
    t = "a\n<<main>>\nm\n<</main>>\n<<child>>\nc\n<</child>>\nz\n"
    assert render_prompt(t, MAIN) == "a\nm\nz\n"
    assert render_prompt(t, CHILD) == "a\nc\nz\n"


def test_inline_regions_leave_surrounding_text_untouched() -> None:
    t = "You are <<main>>main<</main>><<child>>child<</child>>.\n"
    assert render_prompt(t, MAIN) == "You are main.\n"
    assert render_prompt(t, CHILD) == "You are child.\n"


def test_marker_on_last_line_without_newline_eats_the_preceding_newline() -> None:
    # _MEMORY_BLOCK / _INSTRUCTIONS_BLOCK_TEMPLATE end without a trailing newline (spec §4.7.2).
    t = "head\n<<tool:remember>>\n- remember it.\n<</tool:remember>>"
    assert render_prompt(t, MAIN) == "head\n- remember it."
    assert render_prompt(t, CHILD) == "head"


def test_a_region_renders_only_if_every_enclosing_region_does() -> None:
    t = ("<<perm:dontAsk>>\n<<shell:ask>>\nask\n<</shell:ask>>\n"
         "<<shell:allow_all>>\nall\n<</shell:allow_all>>\n<</perm:dontAsk>>\n")
    dont_ask = RenderContext(audience="child", permission="dontAsk", shell_policy="allow_all")
    assert render_prompt(t, dont_ask) == "all\n"
    assert render_prompt(t, MAIN) == ""
    assert render_prompt(t, CHILD) == ""


def test_main_is_never_gated_by_capability_tags() -> None:
    t = ("<<tool:nope>>\nt\n<</tool:nope>>\n<<type:clarify>>\nc\n<</type:clarify>>\n"
         "<<type:report>>\nr\n<</type:report>>\n")
    assert render_prompt(t, MAIN) == "t\nc\n"
    assert render_prompt(t, CHILD) == "r\n"


def test_child_edit_and_child_readonly_follow_effective_permission() -> None:
    t = "<<child:edit>>\ne\n<</child:edit>>\n<<child:readonly>>\nro\n<</child:readonly>>\n"
    assert render_prompt(t, CHILD) == "e\n"
    assert render_prompt(t, READONLY) == "ro\n"
    assert render_prompt(t, MAIN) == ""


def test_perm_tags_never_render_for_main() -> None:
    t = "<<perm:default>>x<</perm:default>>y"
    assert render_prompt(t, MAIN) == "y"
    assert render_prompt(t, CHILD) == "xy"


def test_render_is_cached_per_template_and_context() -> None:
    t = "<<perm:default>>x<</perm:default>>y"
    assert render_prompt(t, CHILD) is render_prompt(t, CHILD)


def test_tagged_returns_the_text_unchanged_when_valid() -> None:
    text = "a <<main>>b<</main>> c\n"
    assert tagged("ok", text) is text


@pytest.mark.parametrize(("text", "message"), [
    ("<<main>>\nx\n", "never closed"),
    ("<</main>>\n", "does not close"),
    ("<<main>>\n<<child>>\n<</main>>\n<</child>>\n", "does not close"),
    ("<<bogus>>\nx\n<</bogus>>\n", "unknown tag"),
    ("<<perm:root>>\nx\n<</perm:root>>\n", "unknown tag"),
    ("<<type:banana>>\n<</type:banana>>\n", "unknown tag"),
    ("a <<main>>b\nc<</main>> d\n", "spans a newline"),
    ("<<main>>\nx <</main>> y\n", "mixes block and inline"),
    ("<<main>>\nx\n<</main>><<child>>\ny\n<</child>>\n", "more than one marker"),
    ("a << b\n", "stray"),
    ("cat <<EOF\n", "stray"),
])
def test_validator_rejects_malformed_templates(text: str, message: str) -> None:
    with pytest.raises(PromptTemplateError, match=message):
        validate_template(text, "t")
    with pytest.raises(PromptTemplateError, match=message):
        tagged("t", text)
