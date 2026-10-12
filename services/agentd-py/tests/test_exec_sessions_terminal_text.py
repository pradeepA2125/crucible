"""Session output reaches the model and the Sessions card as plain text.

A session runs in a real PTY (TERM=xterm-256color), so colored tools emit ANSI escapes:
pytest wraps every passing-test dot in ESC[32m … ESC[0m. Decoded as-is, the card showed
`[32m.[0m` per test (ESC is invisible) and the model paid ~10x the tokens for progress
output. Escapes are stripped and PTY line endings normalized where the buffer is read."""
from __future__ import annotations

import sys

import pytest

from agentd.exec_sessions.manager import RingBuffer, SessionManager
from agentd.exec_sessions.terminal_text import plain_terminal_text

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")


def test_color_codes_are_stripped_from_pytest_dots():
    raw = "\x1b[32m.\x1b[0m\x1b[32m.\x1b[0m\x1b[31mF\x1b[0m [  5%]\r\n"
    assert plain_terminal_text(raw) == "..F [  5%]\n"


def test_cursor_and_title_sequences_are_stripped():
    raw = "\x1b]0;my title\x07hello\x1b[2K\x1b[1G world\x1b(B\x1b[?25l"
    assert plain_terminal_text(raw) == "hello world"


def test_bare_carriage_returns_become_line_breaks():
    assert plain_terminal_text("a\r\nb\rc") == "a\nb\nc"


def test_an_escape_split_across_reads_is_not_shown_as_text():
    buf = RingBuffer(cap=1024)
    buf.append(b"\x1b[32m.\x1b[3")
    first, cursor = buf.read_from(0)
    assert first == "."
    buf.append(b"2m.\x1b[0m")
    second, cursor = buf.read_from(cursor)
    assert second == "."
    assert cursor == buf.end


def test_a_line_ending_split_across_reads_is_one_newline():
    buf = RingBuffer(cap=1024)
    buf.append(b"line\r")
    first, cursor = buf.read_from(0)
    buf.append(b"\nnext")
    second, cursor = buf.read_from(cursor)
    assert first + second == "line\nnext"


def test_the_final_read_flushes_an_incomplete_tail():
    # A session drops once its output is fully read; a held-back tail must not keep
    # an exited session alive forever.
    buf = RingBuffer(cap=1024)
    buf.append(b"done\x1b[3")
    text, cursor = buf.read_from(0, final=True)
    assert text == "done"
    assert cursor == buf.end


def test_the_transcript_tail_is_plain_text():
    buf = RingBuffer(cap=1024)
    buf.append(b"\x1b[32mPASSED\x1b[0m\r\n")
    assert buf.tail(100) == "PASSED\n"


@pytest.mark.asyncio
async def test_a_colored_command_reads_back_clean(tmp_path):
    m = SessionManager(tmp_path)
    script = "print('\\x1b[32mok\\x1b[0m', flush=True)"
    r = await m.start("t1", sys.executable, ["-c", script], None, 5000)
    assert r.new_output.strip() == "ok"
    assert "\x1b" not in r.new_output and "\r" not in r.new_output
    await m.shutdown()
