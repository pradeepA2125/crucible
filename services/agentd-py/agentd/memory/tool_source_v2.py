"""Scoped, immediate memory tools; children receive only read/search access."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable

from agentd.memory.durable_store import DurableMemoryStore, WriteOrigin
from agentd.memory.models import DurableMemory
from agentd.memory.recall_v2 import (
    Candidate,
    CandidateSearch,
    RecallCoordinator,
    RecallTraceV2,
    Signals,
    catalog,
    catalog_notice,
)
from agentd.memory.scope import ScopeContext
from agentd.tools.registry import ToolDefinition, ToolOutput

_DEFINITIONS = [
    ToolDefinition(
        name="read_memory",
        description="Read a scoped memory's complete entry and status before relying on it.",
        parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    ),
    ToolDefinition(
        name="search_memory",
        description=(
            "Find a scoped memory candidate catalog, possibly containing false positives. "
            "Check applies_when, ignore unrelated entries and read_memory before relying on one."
        ),
        parameters={
            "type": "object",
            "properties": {key: {"type": "string"} for key in ("query", "topic", "file", "cursor")},
        },
    ),
    ToolDefinition(
        name="remember",
        description=(
            "Publish a durable lesson with its condition and evidence, or a user-requested memory. "
            "Skip task status. User scope requires origin=user_requested. Main agent only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 140},
                "topic": {"type": "string", "maxLength": 60},
                "applies_when": {"type": "string", "maxLength": 160},
                "kind": {"type": "string", "enum": ["episodic", "semantic", "procedural"]},
                "scope": {"type": "string", "enum": ["repo", "thread", "user"]},
                "origin": {"type": "string", "enum": ["user_requested", "agent_nomination"]},
                "entities": {"type": "array", "items": {"type": "string"}},
                "detail": {"type": "string", "maxLength": 2000},
                "supersedes": {"type": "string"},
            },
            "required": ["content", "topic", "applies_when", "kind", "origin"],
        },
    ),
    ToolDefinition(
        name="forget",
        description="Retire a memory immediately, only when the user asks.",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["id", "reason"],
        },
    ),
]


class DurableMemoryToolSource:
    name = "memory"

    def __init__(
        self,
        store: DurableMemoryStore | None,
        context: ScopeContext,
        *,
        search: CandidateSearch | None = None,
        catalogs: RecallCoordinator | None = None,
        allow_writes: bool = True,
        source_mseq: Callable[[], int | None] = lambda: None,
    ) -> None:
        self.store, self.context, self.search = store, context, search
        self.catalogs = catalogs or (
            RecallCoordinator(search or CandidateSearch(store)) if store is not None else None
        )
        self.allow_writes, self.source_mseq = allow_writes, source_mseq

    def definitions(self) -> list[ToolDefinition]:
        return _DEFINITIONS[:] if self.allow_writes else _DEFINITIONS[:2]

    def owns(self, tool: str) -> bool:
        return any(definition.name == tool for definition in self.definitions())

    @staticmethod
    def _result(status: str, **fields: object) -> ToolOutput:
        return ToolOutput(
            output=json.dumps({"status": status, **fields}, ensure_ascii=False, default=str),
            is_error=status == "unavailable",
        )

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if not self.owns(tool):
            return self._result("unavailable", error="Memory tool is not allowed")
        if self.store is None:
            return self._result("unavailable", error="Durable memory store is unavailable")
        try:
            if tool == "read_memory":
                memory = self.store.get(str(args.get("id", "")), self.context)
                if memory is None:
                    return self._result("not_found")
                return self._result(
                    "retired" if memory.valid_to is not None else "ok",
                    entry=memory.model_dump(mode="json"),
                    replacement_id=memory.superseded_by,
                )
            if tool == "search_memory":
                return await self._search(args)
            if tool == "remember":
                requested_origin = args.get("origin")
                if requested_origin not in {"user_requested", "agent_nomination"}:
                    raise ValueError("remember requires an explicit origin")
                origin: WriteOrigin = (
                    "user_requested" if requested_origin == "user_requested" else "agent_nomination"
                )
                source = self.source_mseq()
                if source is None:
                    raise ValueError("The evidence message has no persistent source ID")
                scope = str(args.get("scope") if args.get("scope") is not None else "repo")
                scope_id = {
                    "repo": self.context.repo_id,
                    "thread": self.context.thread_id,
                    "user": "user",
                }.get(scope)
                memory = DurableMemory.model_validate(
                    {
                        "scope_kind": scope,
                        "scope_id": scope_id,
                        "content": args.get("content"),
                        "topic": args.get("topic"),
                        "applies_when": args.get("applies_when"),
                        "kind": args.get("kind"),
                        "entities": args.get("entities")
                        if args.get("entities") is not None
                        else [],
                        "detail": args.get("detail") if args.get("detail") is not None else "",
                        "provenance": "user_said"
                        if origin == "user_requested"
                        else "agent_inferred",
                        "source_thread_id": self.context.thread_id,
                        "source_message_id": source,
                    }
                )
                if args.get("supersedes"):
                    if origin != "user_requested":
                        raise ValueError("Explicit corrections require a user request")
                    saved = self.store.supersede(
                        str(args["supersedes"]),
                        memory,
                        self.context,
                        origin=origin,
                        reason="user-requested correction",
                    )
                    if saved is None:
                        return self._result("not_found")
                else:
                    saved = self.store.put(memory, self.context, origin=origin)
                return self._result(
                    "ok", id=saved.id, revision=saved.revision, catalog=catalog(saved)
                )
            retired = self.store.forget(
                str(args.get("id", "")),
                self.context,
                origin="user_requested",
                reason=str(args.get("reason", "")),
            )
            return self._result("ok" if retired else "not_found")
        except Exception as exc:
            return self._result("unavailable", error=str(exc)[:500])

    async def _search(self, args: dict[str, object]) -> ToolOutput:
        assert self.store is not None
        query, topic, path = (str(args.get(key) or "") for key in ("query", "topic", "file"))
        fingerprint = hashlib.sha256(
            json.dumps(
                [self.context.keys, query, topic, path],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        offset = 0
        if args.get("cursor"):
            cursor = json.loads(base64.urlsafe_b64decode(str(args["cursor"])))
            if cursor.get("fingerprint") != fingerprint or type(cursor.get("offset")) is not int:
                raise ValueError("Cursor does not belong to this search")
            offset = cursor["offset"]
            if not 0 <= offset <= 10_000_000:
                raise ValueError("Invalid cursor offset")
        assert self.catalogs is not None
        signals = Signals(query)
        if query:
            candidates = await self.catalogs.search.search(
                signals, self.context, [path] if path else []
            )
        else:
            candidates = [
                Candidate(memory, sources={"scope_listing"})
                for memory in self.store.list_memories(self.context)
            ]
        # Apply explicit filters before BGE's bounded pool so unrelated topics
        # cannot consume the slots requested for a specific topic/file.
        candidates = [
            item
            for item in candidates
            if (not topic or item.memory.topic == topic)
            and (not path or path in item.memory.entities)
        ]
        trace = RecallTraceV2(
            trigger="turn_start", signals=signals.payload(), status="no_candidates"
        )
        ordered, limit = await self.catalogs.order_candidates(
            candidates,
            signals,
            self.context,
            trace,
        )
        selection = hashlib.sha256(
            json.dumps(
                [
                    trace.selection_method,
                    [(item.memory.id, item.memory.revision) for item in ordered],
                ]
            ).encode()
        ).hexdigest()
        if args.get("cursor") and cursor.get("selection") != selection:
            raise ValueError("Memory search results changed; restart without the cursor")
        page = ordered[offset : offset + limit]
        cursor_value = None
        if offset + limit < len(ordered):
            cursor_value = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "selection": selection,
                        "offset": offset + limit,
                    }
                ).encode()
            ).decode()
        return self._result(
            "ok" if page else "empty",
            label="candidates — read before relying on one",
            selection_method=trace.selection_method,
            disclaimer=catalog_notice(trace.selection_method),
            entries=[{"id": item.memory.id, "catalog": catalog(item.memory)} for item in page],
            cursor=cursor_value,
            ordering_error=trace.error,
        )
