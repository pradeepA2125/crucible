from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field

_TRUTHY = {"1", "true", "yes", "on"}


class MemoryConfig(BaseModel):
    local_models: bool = True
    compact_evidence: bool = False
    recall_v2: bool = False
    curator_v2: bool = False
    curator_model: str = ""
    durable_store_path: str = Field(
        default_factory=lambda: str(Path.home() / ".crucible/memory/memory.sqlite3")
    )
    enabled: bool
    db_path: str
    trigger_frac: float
    reserve_tokens: int
    hot_token_frac: float
    hot_turns: int
    window_tokens: int
    dedup_threshold: float
    recall_token_budget: int
    weights: tuple[float, float, float]
    graph_grounding: bool
    embedding_model: str
    reranker_enabled: bool
    reranker_model: str
    rerank_min_candidates: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> MemoryConfig:
        return cls(
            local_models=env.get("CRUCIBLE_LOCAL_MODELS", "1").lower() in _TRUTHY,
            compact_evidence=env.get("CRUCIBLE_MEMORY_COMPACT_EVIDENCE", "0").lower() in _TRUTHY,
            recall_v2=env.get("CRUCIBLE_MEMORY_RECALL_V2", "0").lower() in _TRUTHY,
            curator_v2=env.get("CRUCIBLE_MEMORY_CURATOR_V2", "0").lower() in _TRUTHY,
            curator_model=env.get("CRUCIBLE_MEMORY_CURATOR_MODEL", "").strip(),
            durable_store_path=str(Path(env.get(
                "CRUCIBLE_MEMORY_STORE_PATH", str(Path.home() / ".crucible/memory/memory.sqlite3")
            )).expanduser()),
            enabled=env.get("CRUCIBLE_MEMORY_ENABLED", "true").lower() in _TRUTHY,
            db_path=env.get("CRUCIBLE_MEMORY_DB_PATH", ".crucible/state/memory.sqlite3"),
            trigger_frac=float(env.get("CRUCIBLE_MEMORY_COMPACT_TRIGGER_FRAC", "0.65")),
            reserve_tokens=int(env.get("CRUCIBLE_MEMORY_COMPACT_RESERVE_TOKENS", "28000")),
            hot_token_frac=float(env.get("CRUCIBLE_MEMORY_HOT_TOKEN_FRAC", "0.4")),
            hot_turns=int(env.get("CRUCIBLE_MEMORY_HOT_TURNS", "10")),
            window_tokens=int(env.get("CRUCIBLE_MEMORY_WINDOW_TOKENS", "128000")),
            dedup_threshold=float(env.get("CRUCIBLE_MEMORY_DEDUP_THRESHOLD", "0.92")),
            recall_token_budget=int(env.get("CRUCIBLE_MEMORY_RECALL_TOKEN_BUDGET", "1500")),
            weights=tuple(  # type: ignore[arg-type]
                float(x) for x in env.get("CRUCIBLE_MEMORY_WEIGHTS", "0.5,0.3,0.2").split(",")
            ),
            graph_grounding=env.get("CRUCIBLE_MEMORY_GRAPH_GROUNDING", "true").lower() in _TRUTHY,
            embedding_model=env.get("CRUCIBLE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            reranker_enabled=env.get(
                "CRUCIBLE_LOCAL_MODELS", env.get("CRUCIBLE_MEMORY_RERANKER", "true")
            ).lower() in _TRUTHY,
            reranker_model=env.get("CRUCIBLE_MEMORY_RERANKER_MODEL", "BAAI/bge-reranker-base"),
            rerank_min_candidates=int(env.get("CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES", "8")),
        )
