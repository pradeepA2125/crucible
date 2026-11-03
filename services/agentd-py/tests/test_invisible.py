"""Invisible characters in tool output and trusted files (prompt-injection Phase 1, spec §4.3)."""
from __future__ import annotations

from agentd.trust.invisible import invisible_note, render_visible, scan_invisible


def _tags(text: str) -> str:
    return "".join(chr(0xE0000 + ord(c)) for c in text)


def test_tag_characters_removed():
    scan = scan_invisible("hello" + _tags("ignore previous") + " world")
    assert scan.text == "hello world"
    assert scan.removed == len("ignore previous")
    assert scan.flagged == 0


def test_zero_width_flagged_not_removed():
    text = "line one\nzero​width\nplain\nrtl ‮ here ⁦x⁩"
    scan = scan_invisible(text)
    assert scan.text == text
    assert scan.removed == 0
    assert scan.flagged == 4
    assert scan.flagged_lines == (2, 4)


def test_leading_bom_ignored():
    assert scan_invisible("﻿import os").flagged == 0
    assert scan_invisible("a﻿b").flagged == 1


def test_emoji_zwj_counted_but_kept():
    family = "\U0001F468‍\U0001F469‍\U0001F467"
    scan = scan_invisible(family)
    assert scan.text == family
    assert scan.flagged == 2


def test_note_lists_lines():
    assert invisible_note(scan_invisible("plain")) is None
    note = invisible_note(scan_invisible("a​\nb\nc⁠"))
    assert note == ("Note: this output contains 2 invisible characters (zero-width or "
                    "text-direction controls) on lines 1, 3; they can hide text from the user.")
    many = "\n".join("x​" for _ in range(12))
    assert invisible_note(scan_invisible(many)).endswith(
        "on lines 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, …; they can hide text from the user.")


def test_render_visible_marks_both_sets():
    assert render_visible("a​b" + chr(0xE0041)) == "a⟦U+200B⟧b⟦U+E0041⟧"
    assert render_visible("﻿x") == "⟦U+FEFF⟧x"
