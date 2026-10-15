from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentd.memory.scope import ScopeKind

if TYPE_CHECKING:
    from agentd.memory.recall_v2 import RecallTraceV2

# A conversation history / message list as the loops pass it around.
History = list[dict[str, object]]


class MemoryKind(StrEnum):
    """Content-type taxonomy. Defined now for Phase-2 forward-compat; unused in Phase 1."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class CompactionSegment(BaseModel):
    """One evicted message, persisted raw (lossless on disk) for later recall."""

    id: str
    run_id: str
    seq: int
    content: str
    created_at: datetime
    mseq: int | None = None
    role: str | None = None
    tool: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class AnchoredSummary(BaseModel):
    """The running, merged summary of everything evicted from a run so far."""

    run_id: str
    summary_md: str
    version: int
    updated_at: datetime


# CompactionResult / TurnPreparation are internal transfer objects, not persisted or
# validated — dataclasses (not pydantic) so they hold the exact list object passed in
# (pydantic v2 would copy it, breaking the disabled-harness byte-identical no-op).
@dataclass
class CompactionResult:
    compacted: bool
    history: History
    anchor: str | None = None
    degraded: bool = False
    failed: bool = False
    evicted_count: int = 0  # messages evicted this round (for the observability event)
    anchor_version: int = 0  # anchored-summary version after this round
    evicted_seq_lo: int | None = None  # segment seq span evicted this round (consolidation input)
    evicted_seq_hi: int | None = None
    error: Exception | None = None  # retained so access refusals can reach the caller


@dataclass
class TurnPreparation:
    history: History
    recalled_memories: list[str] = field(default_factory=list)  # rendered memory lines (tail)
    compacted: bool = False
    compaction_failed: bool = False
    evicted_count: int = 0  # surfaced from CompactionResult so the loops can broadcast it
    anchor_version: int = 0
    evicted_seq_lo: int | None = None  # surfaced so the harness can consolidate the evicted slice
    evicted_seq_hi: int | None = None
    recall_trace: RecallTrace | None = None  # Phase 3 — inspector trace (forward-ref)
    recall_traces: list[RecallTraceV2] = field(default_factory=list)


@dataclass(frozen=True)
class ObservedPrompt:
    """The provider's exact prompt_tokens for one call, pinned to the slice of
    history it measured.

    `prompt_tokens` describes the call already made, while compaction decides
    what to send next — so the count alone is not enough. `message_count` records
    how long `history` was when that call was built, which is what lets the
    compactor estimate only the messages appended since and take the rest exact.
    It is also how a stale observation is detected: after compaction rewrites
    history, the pinned count no longer refers to anything.
    """

    tokens: int
    message_count: int
    projection_revision: str = ""
    history_tokens: int | None = None


class Memory(BaseModel):
    """A distilled, retrievable long-term memory (L3 / durable L2)."""

    id: str
    scope_kind: str            # 'workspace' | 'thread' | 'global' (global unwritten in P2)
    scope_id: str
    kind: str                  # 'episodic' | 'semantic' | 'procedural'
    content: str
    entities: list[str]
    importance: int            # LLM-rated salience 1-10
    valid_from: datetime       # event time
    valid_to: datetime | None  # None = currently true
    superseded_by: str | None
    source_kind: str           # 'consolidation' | 'agent_tool'
    source_ref: str
    source_seq_lo: int | None  # A+link span into compaction_segments
    source_seq_hi: int | None
    created_at: datetime       # ingestion time


class DurableMemory(BaseModel):
    """V2 durable claim, separate from legacy entries during rollout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    scope_kind: ScopeKind
    scope_id: str = Field(min_length=1)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=140)
    topic: str = Field(min_length=1, max_length=60)
    applies_when: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=2000)
    entities: list[str] = Field(default_factory=list)
    provenance: Literal["user_said", "tool_verified", "agent_inferred"]
    source_thread_id: str = Field(min_length=1)
    source_message_id: int = Field(ge=1, strict=True)
    revision: int = Field(default=1, ge=1)
    valid_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_to: datetime | None = None
    superseded_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    embedding_state: Literal["pending", "done"] = "pending"
    embedding_model: str | None = None
    embedded_hash: str | None = None

    @field_validator("content", "topic", "applies_when", "detail", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Memory text must be a string")
        return value.strip()

    @property
    def scope_key(self) -> str:
        return f"{self.scope_kind}:{self.scope_id}"

    @property
    def embedding_text(self) -> str:
        return f"{self.topic}\n{self.applies_when}\n{self.content}"


class CandidateMemory(BaseModel):
    """What the consolidator LLM proposes — content-level fields only; Python assigns the rest."""

    kind: str
    content: str
    entities: list[str]
    importance: int
    contradicts: str | None = None


class RecallTraceEntry(BaseModel):
    """One candidate's scoring breakdown for the inspector (incl. below-floor candidates)."""

    memory_id: str
    kind: str
    content: str
    importance: int
    signals: dict[str, float]  # normalized semantic/lexical/structural/importance/recency
    fused_score: float
    rerank_score: float | None
    final_rank: int
    injected: bool


class RecallTrace(BaseModel):
    """What recall did this turn — turn-level summary + all scored candidates."""

    query: str
    scope_kind: str
    scope_id: str
    k: int
    floor: float
    reranked: bool
    entries: list[RecallTraceEntry]
