from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

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
    evicted_count: int = 0  # messages evicted this round (for the observability event)
    anchor_version: int = 0  # anchored-summary version after this round
    evicted_seq_lo: int | None = None  # segment seq span evicted this round (consolidation input)
    evicted_seq_hi: int | None = None


@dataclass
class TurnPreparation:
    history: History
    recalled_memories: list[str] = field(default_factory=list)  # rendered memory lines (tail)
    compacted: bool = False
    evicted_count: int = 0  # surfaced from CompactionResult so the loops can broadcast it
    anchor_version: int = 0
    evicted_seq_lo: int | None = None  # surfaced so the harness can consolidate the evicted slice
    evicted_seq_hi: int | None = None


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


class CandidateMemory(BaseModel):
    """What the consolidator LLM proposes — content-level fields only; Python assigns the rest."""

    kind: str
    content: str
    entities: list[str]
    importance: int
    contradicts: str | None = None
