from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from agentd.memory.models import CompactionResult, CompactionSegment
from agentd.memory.store import MemoryStore

logger = logging.getLogger(__name__)

AnchorSummarizer = Callable[[str, str], Awaitable[str]]

_CONTINUATION_ROLES = {"tool_result", "tool"}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _history_tokens(history: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) for m in history)


def _render(messages: list[dict]) -> str:
    return "\n".join(f"{m.get('role', '')}: {m.get('content', '')}" for m in messages)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max(8, max_tokens * 4)
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n…[truncated]…\n" + text[-tail:]


def _is_turn_start(m: dict) -> bool:
    # A turn starts at a user/assistant (or any non-continuation) message; tool results
    # attach backward to the action that produced them. Unknown roles default to turn-start.
    return str(m.get("role", "")) not in _CONTINUATION_ROLES


def _select_hot(
    history: list[dict], hot_budget_tokens: int, hot_turns_cap: int
) -> tuple[list[dict], list[dict], int]:
    """Newest *whole logical turns* that fit the token budget and the count cap.

    Lossless at turn boundaries: never keeps a partial turn. If the budget boundary falls
    inside a turn, the partial remainder is pushed to eviction (it survives via the anchored
    summary) by trimming leading continuation messages so hot begins at a turn start. Always
    keeps >=1 message even if the whole hot set is continuations (degenerate).
    """
    hot: list[dict] = []
    used = 0
    for m in reversed(history):
        t = estimate_tokens(str(m.get("content", "")))
        if hot and (used + t > hot_budget_tokens or len(hot) >= hot_turns_cap):
            break
        hot.insert(0, m)
        used += t
    while len(hot) > 1 and not _is_turn_start(hot[0]):
        hot.pop(0)
    used = sum(estimate_tokens(str(m.get("content", ""))) for m in hot)  # recompute after trim
    evicted = history[: len(history) - len(hot)]
    return evicted, hot, used


def _anchor_message(text: str) -> dict:
    return {
        "role": "user",
        "content": f"[MEMORY] Summary of earlier conversation that was compacted:\n{text}",
    }


class Compactor:
    def __init__(
        self,
        store: MemoryStore,
        summarize: AnchorSummarizer,
        *,
        window_tokens: int,
        trigger_frac: float = 0.65,
        hot_token_frac: float = 0.4,
        hot_turns: int = 10,
    ) -> None:
        self._store = store
        self._summarize = summarize
        self._window_tokens = window_tokens
        self._trigger_frac = trigger_frac
        self._hot_token_frac = hot_token_frac
        self._hot_turns = hot_turns

    async def maybe_compact(self, history: list[dict], run_id: str) -> CompactionResult:
        # Pure token-trigger check (no count short-circuit: a short history of oversized
        # turns can be over budget and must still compact).
        if _history_tokens(history) < self._window_tokens * self._trigger_frac:
            anchor = self._store.get_anchor(run_id)
            return CompactionResult(
                compacted=False,
                history=history,
                anchor=anchor.summary_md if anchor else None,
            )
        # Compaction logic added in Task 4.
        return CompactionResult(compacted=False, history=history)
