from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MemoryKind(str, Enum):
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


class CompactionResult(BaseModel):
    compacted: bool
    history: list[dict[str, object]]
    anchor: str | None = None
    degraded: bool = False


class TurnPreparation(BaseModel):
    history: list[dict[str, object]]
    recalled_memories: list[dict[str, object]] = Field(default_factory=list)
    compacted: bool = False
