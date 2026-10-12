"""Turn raw PTY output into plain text for the model and the Sessions card.

A session runs in a real terminal (TERM=xterm-256color, kept so interactive programs
work), so colored tools emit ANSI escapes and every line ends in CR LF. Decoded as-is,
pytest's progress line reads `[32m.[0m` per test once the invisible ESC is dropped,
and the model pays for every byte of it."""
from __future__ import annotations

import re

_ESCAPE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"               # CSI: colors, cursor moves, erase
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"    # OSC: window title, hyperlinks
    r"|\x1b[()*+][0-9A-Za-z]"                # character-set designation
    r"|\x1b[@-Z\\-_]"                        # remaining two-byte escapes
)
# An escape the stream ended in the middle of; it continues in the next read.
_UNFINISHED = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*|[()*+])?\Z")
_UNFINISHED_BYTES = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*|[()*+])?")
# Controls a terminal acts on rather than prints (bell, backspace, a stray ESC);
# newline and tab are kept.
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Longest unfinished tail worth holding back; anything longer is not an escape that
# will complete, and holding it would stall the reader.
_MAX_HELD = 64


def plain_terminal_text(text: str) -> str:
    text = _UNFINISHED.sub("", _ESCAPE.sub("", text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROLS.sub("", text)


def unfinished_tail_length(chunk: bytes) -> int:
    """Bytes at the end of `chunk` that the next read may complete: an escape sequence
    cut off mid-way, or a CR whose LF has not arrived (CR LF must stay one line break)."""
    escape = chunk.rfind(b"\x1b")
    tail = len(chunk) - escape
    if escape != -1 and tail <= _MAX_HELD and _UNFINISHED_BYTES.fullmatch(chunk[escape:]):
        return tail
    return 1 if chunk.endswith(b"\r") else 0
