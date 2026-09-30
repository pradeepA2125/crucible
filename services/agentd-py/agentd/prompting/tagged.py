"""Tagged prompt templates: one source text, audience-tagged regions, one pure resolver.

Spec: docs/superpowers/specs/2026-09-29-subagents-design.md §4.7.

Syntax: ``<<tag>>`` opens a region, ``<</tag>>`` closes it; regions nest. A region renders
iff its own tag holds for the RenderContext AND every enclosing region renders. Untagged
text renders for everyone.

Whitespace (the only rules):
  * a marker alone on its line (spaces/tabs allowed around it) is removed together with
    that line's newline;
  * a marker-only LAST line with no newline of its own is removed together with the newline
    immediately before it in the output;
  * an inline marker is removed with no other change; dropped content is removed verbatim.
No trimming or normalization anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

Audience = Literal["main", "child"]
Permission = Literal["default", "acceptEdits", "dontAsk", "plan"]
ShellPolicy = Literal["ask", "allow_all"]

_PERMISSIONS = frozenset({"default", "acceptEdits", "dontAsk", "plan"})
_SHELL_POLICIES = frozenset({"ask", "allow_all"})
_VARIANTS = frozenset({
    "tool_call", "answer", "clarify", "propose_mode", "edit", "submit_changes", "progress",
    "report",
})
_CHILD_SUBTAGS = frozenset({"edit", "readonly"})

_MARKER_RE = re.compile(r"<<(/?)([a-z_]+)(?::([A-Za-z0-9_]+))?>>")


class PromptTemplateError(ValueError):
    """A tagged template is malformed. Raised at import time via ``tagged()``."""


@dataclass(frozen=True)
class RenderContext:
    """Everything a tag can test. Hashable so renders are cacheable.

    ``agent_id``/``agent_label`` identify a sub-agent (render cache + per-agent scripts);
    both are empty for the main agent.
    """

    audience: Audience = "main"
    permission: Permission = "default"
    shell_policy: ShellPolicy = "ask"
    tools: frozenset[str] = field(default_factory=frozenset)
    base_types: frozenset[str] = field(default_factory=frozenset)
    agent_id: str = ""
    agent_label: str = ""

    @classmethod
    def main(cls) -> RenderContext:
        return _MAIN

    @property
    def is_main(self) -> bool:
        return self.audience == "main"


_MAIN = RenderContext()


def _holds(kind: str, arg: str | None, ctx: RenderContext) -> bool:
    if kind == "main":
        return ctx.is_main
    if kind == "child":
        if arg is None:
            return not ctx.is_main
        if arg == "edit":
            return not ctx.is_main and ctx.permission != "plan"
        return not ctx.is_main and ctx.permission == "plan"  # "readonly"
    if kind == "perm":
        return not ctx.is_main and ctx.permission == arg
    if kind == "shell":
        return not ctx.is_main and ctx.shell_policy == arg
    if kind == "tool":
        # Main is never gated by capability tags (§4.7.1): its prompt today is
        # unconditional in every tagged region.
        return ctx.is_main or arg in ctx.tools
    if kind == "type":
        if ctx.is_main:
            return arg != "report"
        return arg in ctx.base_types
    raise AssertionError(f"unvalidated tag {kind!r}")  # pragma: no cover


def _check_tag(kind: str, arg: str | None, where: str) -> None:
    ok = (
        (kind == "main" and arg is None)
        or (kind == "child" and (arg is None or arg in _CHILD_SUBTAGS))
        or (kind == "perm" and arg in _PERMISSIONS)
        or (kind == "shell" and arg in _SHELL_POLICIES)
        or (kind == "tool" and arg is not None)
        or (kind == "type" and arg in _VARIANTS)
    )
    if not ok:
        tag = kind if arg is None else f"{kind}:{arg}"
        raise PromptTemplateError(f"{where}: unknown tag <<{tag}>>")


def _tag_key(kind: str, arg: str | None) -> str:
    return kind if arg is None else f"{kind}:{arg}"


def validate_template(text: str, name: str = "<template>") -> None:
    """Raise PromptTemplateError for any structural problem (§4.7.3)."""
    stack: list[tuple[str, bool, int]] = []  # (tag, is_block, line_no)
    lines = text.split("\n")
    for line_no, line in enumerate(lines, start=1):
        where = f"{name}:{line_no}"
        markers = list(_MARKER_RE.finditer(line))
        # every "<<" must start a well-formed marker
        starts = {m.start() for m in markers}
        for idx in (i for i in range(len(line)) if line.startswith("<<", i)):
            if idx not in starts and not any(m.start() < idx < m.end() for m in markers):
                raise PromptTemplateError(f"{where}: stray '<<' that is not a valid tag")
        if not markers:
            continue
        residue = _MARKER_RE.sub("", line).strip(" \t")
        is_block_line = residue == ""
        if is_block_line and len(markers) > 1:
            raise PromptTemplateError(f"{where}: more than one marker on a marker-only line")
        for m in markers:
            closing, kind, arg = m.group(1) == "/", m.group(2), m.group(3)
            _check_tag(kind, arg, where)
            key = _tag_key(kind, arg)
            if not closing:
                stack.append((key, is_block_line, line_no))
                continue
            if not stack or stack[-1][0] != key:
                raise PromptTemplateError(f"{where}: <</{key}>> does not close the open region")
            open_key, open_block, open_line = stack.pop()
            if open_block != is_block_line:
                raise PromptTemplateError(
                    f"{where}: region <<{open_key}>> opened on line {open_line} mixes block and "
                    "inline markers")
            if not is_block_line and open_line != line_no:
                raise PromptTemplateError(
                    f"{where}: inline region <<{open_key}>> spans a newline")
    if stack:
        key, _, line_no = stack[-1]
        raise PromptTemplateError(f"{name}:{line_no}: <<{key}>> is never closed")


def tagged(name: str, text: str) -> str:
    """Validate a template at import time and return it unchanged."""
    validate_template(text, name)
    return text


@lru_cache(maxsize=512)
def render_prompt(template: str, ctx: RenderContext) -> str:
    """Render ``template`` for ``ctx``. Pure; cached per (template, ctx)."""
    out: list[str] = []
    active_stack: list[bool] = []

    def active() -> bool:
        return all(active_stack)

    lines = template.split("\n")
    last = len(lines) - 1
    for i, line in enumerate(lines):
        has_newline = i < last
        markers = list(_MARKER_RE.finditer(line))
        residue = _MARKER_RE.sub("", line).strip(" \t") if markers else None
        if markers and residue == "":
            m = markers[0]
            if m.group(1) == "/":
                active_stack.pop()
            else:
                active_stack.append(_holds(m.group(2), m.group(3), ctx))
            if not has_newline and out and out[-1].endswith("\n"):
                out[-1] = out[-1][:-1]
            continue
        if not active():
            continue
        if markers:
            pieces: list[str] = []
            inline_stack: list[bool] = []
            pos = 0
            for m in markers:
                if all(inline_stack):
                    pieces.append(line[pos:m.start()])
                if m.group(1) == "/":
                    inline_stack.pop()
                else:
                    inline_stack.append(_holds(m.group(2), m.group(3), ctx))
                pos = m.end()
            pieces.append(line[pos:])
            line = "".join(pieces)
        out.append(line + ("\n" if has_newline else ""))
    return "".join(out)
