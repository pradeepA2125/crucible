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


@dataclass
class TurnPreparation:
    history: History
    recalled_memories: History = field(default_factory=list)
    compacted: bool = False
