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

_RECALL_DEF = ToolDefinition(
    name="recall",
    description=(
        "Look up relevant memories from earlier sessions on this project (facts, decisions, "
        "how-tos). Query with symbols/paths/topics. Pass verbatim=true to also see the original "
        "source text of the top hit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "verbatim": {"type": "boolean"},
        },
        "required": ["query"],
    },
)


class MemoryToolSource:
    """ToolSource exposing the deliberate memory write path + on-demand recall."""

    name = "memory"

    def __init__(
        self, consolidator: object, scope_kind: str, scope_id: str,
        *, recall_engine: object | None = None, store: object | None = None,
    ) -> None:
        self._consolidator = consolidator
        self._scope_kind = scope_kind
        self._scope_id = scope_id
        self._recall_engine = recall_engine
        self._store = store

    def definitions(self) -> list[ToolDefinition]:
        defs = [_REMEMBER_DEF]
        if self._recall_engine is not None:
            defs.append(_RECALL_DEF)
        return defs

    def owns(self, tool: str) -> bool:
        return tool in {"remember", "recall"}

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool == "recall":
            return await self._recall(args)
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

    async def _recall(self, args: dict[str, object]) -> ToolOutput:
        if self._recall_engine is None:
            return ToolOutput(output="recall unavailable", is_error=True)
        query = str(args.get("query", "")).strip()
        mems = await self._recall_engine.recall(  # type: ignore[attr-defined]
            query, self._scope_kind, self._scope_id, k=8)
        if not mems:
            return ToolOutput(output="(no relevant memories)")
        lines = [f"- ({m.kind}) {m.content}" for m in mems]
        top = mems[0]
        if args.get("verbatim") and self._store is not None and top.source_seq_lo is not None:
            segs = [s for s in self._store.get_segments(top.source_ref)  # type: ignore[attr-defined]
                    if top.source_seq_lo <= s.seq <= (top.source_seq_hi or s.seq)]
            if segs:
                lines.append("\nVerbatim source of top hit:\n"
                             + "\n".join(s.content for s in segs))
        return ToolOutput(output="\n".join(lines))
