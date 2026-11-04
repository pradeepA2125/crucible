"""Tool output as the model sees it (prompt-injection Phase 1, spec §4.2–§4.3).

Output from files, commands, MCP servers and the web is framed as data the system labels, with
invisible tag characters removed and zero-width or text-direction characters counted in a note.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentd.subagents.framing import MENTION_KIND, TOOL_KIND, frame
from agentd.trust.config import is_injection_defense_enabled
from agentd.trust.invisible import invisible_note, scan_invisible

_SUMMARY_MAX = 80


@dataclass(frozen=True)
class FramedOutput:
    content: str
    hidden_chars: bool


def _summary(args: dict[str, object]) -> str:
    for value in args.values():
        text = " ".join(str(value).split())
        return text[:_SUMMARY_MAX]
    return ""


def frame_tool_output(tool: str, args: dict[str, object], output: str) -> FramedOutput:
    """The history copy of an untrusted tool's output. The caller checks the flag."""
    scan = scan_invisible(output)
    body = scan.text
    note = invisible_note(scan)
    if note is not None:
        body = f"{body}\n{note}" if body else note
    summary = _summary(args)
    source = f"{tool}: {summary}" if summary else tool
    return FramedOutput(frame("tool", TOOL_KIND, body, source=source),
                        hidden_chars=bool(scan.removed or scan.flagged))


def retrieval_refresh_content(delta: str) -> str:
    """Index updates name files and symbols from the workspace, so they are data too."""
    if not is_injection_defense_enabled():
        return delta
    return frame("tool", TOOL_KIND, scan_invisible(delta).text, source="index update")


def mention_block(path: str, content: str) -> str:
    """One @-mentioned file in the turn's goal: the user chose the file, not its contents."""
    if not is_injection_defense_enabled():
        return f"### {path}\n```\n{content}\n```"
    return frame(f"@{path}", MENTION_KIND, scan_invisible(content).text, source=f"@{path}")
