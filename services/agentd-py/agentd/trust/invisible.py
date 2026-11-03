"""Invisible characters that can hide instructions from the user (spec §4.3).

Unicode tag characters (U+E0000–U+E007F) have no legitimate use in tool output and render as
nothing, so they are removed. Zero-width and text-direction controls have real uses (emoji
sequences, right-to-left text), so they are kept and only counted, which the model and the user
are told about.
"""
from __future__ import annotations

from dataclasses import dataclass

_TAG_RANGE = range(0xE0000, 0xE0080)
_FLAGGED = frozenset(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF]
    + list(range(0x202A, 0x202F))
    + list(range(0x2066, 0x206A))
)
_MAX_LINES_LISTED = 10


@dataclass(frozen=True)
class InvisibleScan:
    text: str
    removed: int
    flagged: int
    flagged_lines: tuple[int, ...]


def scan_invisible(text: str) -> InvisibleScan:
    kept: list[str] = []
    removed = 0
    flagged = 0
    lines: list[int] = []
    line = 1
    for index, char in enumerate(text):
        code = ord(char)
        if code in _TAG_RANGE:
            removed += 1
            continue
        kept.append(char)
        if char == "\n":
            line += 1
        # A byte-order mark opening a file is ordinary; anywhere else it hides nothing useful.
        elif code in _FLAGGED and not (code == 0xFEFF and index == 0):
            flagged += 1
            if not lines or lines[-1] != line:
                lines.append(line)
    return InvisibleScan("".join(kept), removed, flagged, tuple(lines))


def invisible_note(scan: InvisibleScan) -> str | None:
    if scan.flagged == 0:
        return None
    listed = ", ".join(str(n) for n in scan.flagged_lines[:_MAX_LINES_LISTED])
    if len(scan.flagged_lines) > _MAX_LINES_LISTED:
        listed += ", …"
    return (f"Note: this output contains {scan.flagged} invisible characters (zero-width or "
            f"text-direction controls) on lines {listed}; they can hide text from the user.")


def render_visible(text: str) -> str:
    """Every invisible character as a visible ⟦U+XXXX⟧ marker, for the trust dialog."""
    return "".join(
        f"⟦U+{ord(c):04X}⟧" if ord(c) in _TAG_RANGE or ord(c) in _FLAGGED else c for c in text
    )
