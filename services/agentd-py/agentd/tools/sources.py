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
        command_guard: Callable[[str], str | None] | None = None,
        retrieval_client: object | None = None,
        sandbox_runtime: object | None = None,
        escalation_approval_callback: object | None = None,
    ) -> None:
        self._inner = ToolRegistry(
            shadow_root,
            real_workspace_path,
            semantic_index=semantic_index,
            command_approval_callback=command_approval_callback,
            render_ctx=render_ctx,
            command_guard=command_guard,
            retrieval_client=retrieval_client,
            sandbox_runtime=sandbox_runtime,  # type: ignore[arg-type]
            escalation_approval_callback=escalation_approval_callback,  # type: ignore[arg-type]
        )
        self._phase = "explore"
        self._read_observer = read_observer

    def use_shadow_for_reads(self) -> None:
        self._inner.use_shadow_for_reads()

    def definitions(self) -> list[ToolDefinition]:
        return self._inner.definitions(self._phase)

    def owns(self, tool: str) -> bool:
        return any(d.name == tool for d in self.definitions())

    async def prepare(self, tool: str, args: dict[str, object]):
        if tool == "run_command":
            return await self._inner.prepare_command(args)
        async def run():
            return await self.execute(tool, args)
        return run

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        out = await self._inner.execute(tool, args)
        if tool == "read_file" and not out.is_error and self._read_observer is not None:
            metadata = out.delivered_read
            if metadata is not None and metadata.has_content:
                delivered = getattr(self._read_observer, "delivered", None)
                if callable(delivered):
                    delivered(metadata)
                else:
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
        execution_guard: Callable[[str], str | None] | None = None,
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
        self._execution_guard = execution_guard
        builtin = next((s for s in sources if isinstance(s, BuiltinToolSource)), None)
        self.shadow_root = builtin._inner._shadow_root if builtin else Path.cwd()
        self.real_root = builtin._inner._real_workspace_path if builtin else Path.cwd()

    def definitions(self) -> list[ToolDefinition]:
        return [d for s in self._sources for d in s.definitions()
                if self._allowed is None or d.name in self._allowed]

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        from agentd.tools.batching import Call, batching_enabled, resources_for, workspace_pool
        prepared = await self.prepare(tool, args)
        if isinstance(prepared, ToolOutput):
            return prepared
        if not batching_enabled() or tool in {"dispatch_agents", "wait_agents"}:
            return await prepared()
        async with workspace_pool(self.real_root).lease(resources_for(
                Call("single", tool, args), self.shadow_root, self.real_root)):
            return await prepared()

    def validate_call(self, tool: str, args: dict[str, object]) -> None:
        # Only local filesystem tools use these keys as workspace paths.
        if tool in {"read_file", "list_directory", "run_command", "setup_env", "init_workspace"}:
            key = "path" if tool in {"read_file", "list_directory"} else "cwd"
            root = self.real_root if key == "path" else self.shadow_root
            path = (root / str(args.get(key) or ".")).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError(f"{tool}: {key} is outside the workspace")

    def read_root_for(self, path: str) -> Path:
        builtin = next((s for s in self._sources if isinstance(s, BuiltinToolSource)), None)
        return builtin._inner._read_root_for(path) if builtin else self.real_root

    async def prepare(self, tool: str, args: dict[str, object]):
        if self._execution_guard is not None:
            refusal = self._execution_guard(tool)
            if refusal:
                return ToolOutput(output=refusal, is_error=True)
        if self._allowed is None or tool in self._allowed:
            for s in self._sources:
                if s.owns(tool):
                    prepare = getattr(s, "prepare", None)
                    if callable(prepare):
                        prepared = await prepare(tool, args)
                    else:
                        async def run(source=s):
                            return await source.execute(tool, args)
                        prepared = run
                    if isinstance(prepared, ToolOutput):
                        return prepared
                    async def guarded(run=prepared):
                        if self._execution_guard is not None:
                            refusal = self._execution_guard(tool)
                            if refusal:
                                return ToolOutput(output=refusal, is_error=True)
                        return await run()
                    return guarded
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)

    def use_shadow_for_reads(self) -> None:
        for s in self._sources:
            flip = getattr(s, "use_shadow_for_reads", None)
            if callable(flip):
                flip()
