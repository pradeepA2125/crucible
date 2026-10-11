"""A blocked duplicate call says how to get what the model was after.

Live (2026-10-11 profile): two blocked searches cost 12.5 s of model rounds, and the bare
"reuse visible evidence" refusal gave no next step. One repeat differed only in
context_lines (deliberately not part of a search's identity), i.e. the model wanted more
lines around a match — which read_file gives."""
from agentd.reasoning.react_common import duplicate_refusal


def test_a_repeated_search_points_at_its_result_and_at_read_file() -> None:
    text = duplicate_refusal("search_code", 4)
    assert text.startswith("DUPLICATE BLOCKED (iter 4)")
    assert "result is above" in text
    assert "context_lines" in text and "read_file" in text and "specific start_line" in text
    assert "earlier reads do not already cover" in text


def test_a_repeated_read_asks_for_a_different_range() -> None:
    text = duplicate_refusal("read_file", 2)
    assert text.startswith("DUPLICATE BLOCKED (iter 2)")
    assert "unchanged" in text and "specific start_line/end_line range" in text
    assert "earlier reads do not already cover" in text


def test_other_tools_keep_the_generic_refusal() -> None:
    assert duplicate_refusal("list_directory", 1) == (
        "DUPLICATE BLOCKED (iter 1); reuse visible evidence or request the missing portion.")
