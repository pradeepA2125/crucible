import re
from pathlib import Path
from typing import List, Tuple, Set, Optional


class RelativeIndenter:
    """Rewrites text files to have relative indentation.
    
    Ported from Aider's RelativeIndenter.
    Handles matching code blocks across different overall indentation levels.
    """

    def __init__(self, texts: List[str]):
        """Choose a unique marker character that isn't in any of the texts."""
        chars = set()
        for text in texts:
            if text:
                chars.update(text)

        ARROW = "←"
        if ARROW not in chars:
            self.marker = ARROW
        else:
            self.marker = self.select_unique_marker(chars)

    def select_unique_marker(self, chars: Set[str]) -> str:
        for codepoint in range(0x10FFFF, 0x10000, -1):
            marker = chr(codepoint)
            if marker not in chars:
                return marker
        raise ValueError("Could not find a unique marker")

    def make_relative(self, text: str) -> str:
        """Transform text to use relative indents."""
        if self.marker in text:
            raise ValueError(f"Text already contains the outdent marker: {self.marker}")

        lines = text.splitlines(keepends=True)
        output = []
        prev_indent = ""
        for line in lines:
            line_without_end = line.rstrip("\n\r")
            len_indent = len(line_without_end) - len(line_without_end.lstrip())
            indent = line[:len_indent]
            change = len_indent - len(prev_indent)
            
            if change > 0:
                cur_indent = indent[-change:]
            elif change < 0:
                cur_indent = self.marker * -change
            else:
                cur_indent = ""

            out_line = cur_indent + "\n" + line[len_indent:]
            output.append(out_line)
            prev_indent = indent

        return "".join(output)

    def make_absolute(self, text: str) -> str:
        """Transform text from relative back to absolute indents."""
        lines = text.splitlines(keepends=True)
        output = []
        prev_indent = ""
        
        # Relative format has 2 lines for each original line: [indent_info, content]
        for i in range(0, len(lines), 2):
            dent = lines[i].rstrip("\r\n")
            non_indent = lines[i + 1]

            if dent.startswith(self.marker):
                len_outdent = len(dent)
                cur_indent = prev_indent[:-len_outdent]
            else:
                cur_indent = prev_indent + dent

            if not non_indent.rstrip("\r\n"):
                out_line = non_indent  # don't indent a blank line
            else:
                out_line = cur_indent + non_indent

            output.append(out_line)
            prev_indent = cur_indent

        res = "".join(output)
        if self.marker in res:
            raise ValueError("Error transforming text back to absolute indents")
        return res


def cleanup_pure_whitespace_lines(lines: List[str]) -> List[str]:
    """Ensure blank lines in hunks don't have accidental trailing spaces."""
    res = [
        line if line.strip() else line[-(len(line) - len(line.rstrip("\r\n"))):] 
        for line in lines
    ]
    return res


def hunk_to_before_after(hunk: List[str], lines: bool = False) -> Tuple[str | List[str], str | List[str]]:
    """Convert a hunk into its 'before' and 'after' text components."""
    before = []
    after = []
    for line in hunk:
        if len(line) < 2:
            op = " "
            content = line
        else:
            op = line[0]
            content = line[1:]

        if op == " ":
            before.append(content)
            after.append(content)
        elif op == "-":
            before.append(content)
        elif op == "+":
            after.append(content)

    if lines:
        return before, after

    return "".join(before), "".join(after)
