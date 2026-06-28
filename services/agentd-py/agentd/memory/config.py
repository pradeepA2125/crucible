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

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "MemoryConfig":
        return cls(
            enabled=env.get("AI_EDITOR_MEMORY_ENABLED", "").lower() in _TRUTHY,
            db_path=env.get("AI_EDITOR_MEMORY_DB_PATH", ".agentd/memory.sqlite3"),
            trigger_frac=float(env.get("AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC", "0.65")),
            hot_token_frac=float(env.get("AI_EDITOR_MEMORY_HOT_TOKEN_FRAC", "0.4")),
            hot_turns=int(env.get("AI_EDITOR_MEMORY_HOT_TURNS", "10")),
            window_tokens=int(env.get("AI_EDITOR_MEMORY_WINDOW_TOKENS", "128000")),
        )
