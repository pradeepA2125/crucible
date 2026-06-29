from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

_TRUTHY = {"1", "true", "yes", "on"}


class MemoryConfig(BaseModel):
    enabled: bool
    db_path: str
    trigger_frac: float
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
            enabled=env.get("AI_EDITOR_MEMORY_ENABLED", "").lower() in _TRUTHY,
            db_path=env.get("AI_EDITOR_MEMORY_DB_PATH", ".agentd/memory.sqlite3"),
            trigger_frac=float(env.get("AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC", "0.65")),
            hot_token_frac=float(env.get("AI_EDITOR_MEMORY_HOT_TOKEN_FRAC", "0.4")),
            hot_turns=int(env.get("AI_EDITOR_MEMORY_HOT_TURNS", "10")),
            window_tokens=int(env.get("AI_EDITOR_MEMORY_WINDOW_TOKENS", "128000")),
            dedup_threshold=float(env.get("AI_EDITOR_MEMORY_DEDUP_THRESHOLD", "0.92")),
            recall_token_budget=int(env.get("AI_EDITOR_MEMORY_RECALL_TOKEN_BUDGET", "1500")),
            weights=tuple(  # type: ignore[arg-type]
                float(x) for x in env.get("AI_EDITOR_MEMORY_WEIGHTS", "0.5,0.3,0.2").split(",")
            ),
            graph_grounding=env.get("AI_EDITOR_MEMORY_GRAPH_GROUNDING", "true").lower() in _TRUTHY,
            embedding_model=env.get("AI_EDITOR_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            reranker_enabled=env.get("AI_EDITOR_MEMORY_RERANKER", "").lower() in _TRUTHY,
            reranker_model=env.get("AI_EDITOR_MEMORY_RERANKER_MODEL", "BAAI/bge-reranker-base"),
            rerank_min_candidates=int(env.get("AI_EDITOR_MEMORY_RERANK_MIN_CANDIDATES", "8")),
        )
