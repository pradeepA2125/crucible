from agentd.tools.loop import _anchor_failure_hint


def test_not_found_hint_suggests_replace_range():
    msg = _anchor_failure_hint("Search text not found in file")
    assert "replace_range" in msg


def test_ambiguous_hint_suggests_replace_range():
    msg = _anchor_failure_hint("Search text appears 3 times")
    assert "replace_range" in msg


def test_unknown_error_gives_no_hint():
    assert _anchor_failure_hint("some other error") == ""
