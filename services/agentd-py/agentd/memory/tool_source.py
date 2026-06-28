from __future__ import annotations

from agentd.tools.registry import ToolDefinition, ToolOutput

_KINDS = {"episodic", "semantic", "procedural"}

_REMEMBER_DEF = ToolDefinition(
    name="remember",
    description=(
        "Store a durable memory for future sessions. Use for a fact/decision/how-to worth "
        "recalling later; SKIP for transient detail. kind is one of episodic (something that "
        "happened), semantic (a durable fact), procedural (a reusable how-to)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "kind": {"type": "string", "enum": ["episodic", "semantic", "procedural"]},
            "entities": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "enum": ["workspace", "thread"]},
        },
        "required": ["content", "kind"],
    },
)


class MemoryToolSource:
    """ToolSource exposing the deliberate memory write path (and recall, in Plan 2C)."""

    name = "memory"

    def __init__(self, consolidator: object, scope_kind: str, scope_id: str) -> None:
        self._consolidator = consolidator
        self._scope_kind = scope_kind
        self._scope_id = scope_id

    def definitions(self) -> list[ToolDefinition]:
        return [_REMEMBER_DEF]

    def owns(self, tool: str) -> bool:
        return tool == "remember"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != "remember":
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        content = str(args.get("content", "")).strip()
        kind = str(args.get("kind", ""))
        if not content:
            return ToolOutput(output="remember needs non-empty 'content'.", is_error=True)
        if kind not in _KINDS:
            return ToolOutput(output=f"invalid kind {kind!r}; use {sorted(_KINDS)}.",
                              is_error=True)
        raw_entities = args.get("entities", [])
        entities = [str(e) for e in raw_entities] if isinstance(raw_entities, list) else []
        scope_kind = "thread" if args.get("scope") == "thread" else self._scope_kind
        mid = await self._consolidator.write_explicit(  # type: ignore[attr-defined]
            content, kind, entities, scope_kind, self._scope_id)
        return ToolOutput(output=f"Remembered ({kind}): {content}  [{mid}]")
