"""Tool-source seam: a Composite over one-or-many ToolSources.

`BuiltinToolSource` wraps the existing `ToolRegistry` so the controller loop can
drive its tool surface from `definitions()`/`execute()` without caring whether a
tool is built-in or (later) MCP/skill/background-process backed. Adding a source
never touches the loop — only the registry's source list grows.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentd.prompting.tagged import RenderContext
from agentd.tools.arg_aliases import normalize_tool_args
from agentd.tools.registry import ToolDefinition, ToolOutput, ToolRegistry


@runtime_checkable
class ToolSource(Protocol):
    name: str

    def definitions(self) -> list[ToolDefinition]: ...
    def owns(self, tool: str) -> bool: ...
    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput: ...


class BuiltinToolSource:
    """Wraps the existing builtin tools (search_code/read_file/...) behind ToolSource."""

    name = "builtin"

    def __init__(
        self,
        *,
        shadow_root: Path,
        real_workspace_path: Path,
        semantic_index: object | None = None,
        command_approval_callback: object | None = None,
        render_ctx: RenderContext | None = None,
        read_observer: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = ToolRegistry(
            shadow_root,
            real_workspace_path,
            semantic_index=semantic_index,
            command_approval_callback=command_approval_callback,
            render_ctx=render_ctx,
        )
        self._phase = "explore"
        self._read_observer = read_observer

    def use_shadow_for_reads(self) -> None:
        self._inner.use_shadow_for_reads()

    def definitions(self) -> list[ToolDefinition]:
        return self._inner.definitions(self._phase)

    def owns(self, tool: str) -> bool:
        return any(d.name == tool for d in self.definitions())

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        out = await self._inner.execute(tool, args)
        if tool == "read_file" and not out.is_error and self._read_observer is not None:
            # The write guard's read watermark (spec §7.3): only a read that actually
            # returned content counts; an errored read proves nothing was seen.
            raw = normalize_tool_args(tool, args).get("path")
            if isinstance(raw, str):
                self._read_observer(raw)
        return out


class AggregatingToolRegistry:
    """Composite over ToolSources: concat definitions, route execute by ownership.

    Enforces unique tool names across sources at construction (collision = hard
    error) so the model can never address two tools by the same name.
    """

    def __init__(
        self, sources: list[ToolSource], allowed_tools: frozenset[str] | None = None,
    ) -> None:
        seen: set[str] = set()
        for src in sources:
            for d in src.definitions():
                if d.name in seen:
                    raise ValueError(f"Duplicate tool name across sources: {d.name!r}")
                seen.add(d.name)
        self._sources = sources
        # A sub-agent's tool set (spec §5.3): None = every tool the sources offer.
        self._allowed = allowed_tools

    def definitions(self) -> list[ToolDefinition]:
        return [d for s in self._sources for d in s.definitions()
                if self._allowed is None or d.name in self._allowed]

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if self._allowed is None or tool in self._allowed:
            for s in self._sources:
                if s.owns(tool):
                    return await s.execute(tool, args)
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)

    def use_shadow_for_reads(self) -> None:
        for s in self._sources:
            flip = getattr(s, "use_shadow_for_reads", None)
            if callable(flip):
                flip()
