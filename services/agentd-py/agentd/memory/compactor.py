from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from agentd.memory.models import CompactionResult, CompactionSegment, History, ObservedPrompt
from agentd.memory.store import MemoryStore

logger = logging.getLogger(__name__)

AnchorSummarizer = Callable[[str, str], Awaitable[str]]

_CONTINUATION_ROLES = {"tool_result", "tool"}

# len//3, not len//4: a 2026-07-17 live-smoke session measured a real TurboQuant
# KV-cache token count (cache_n=98244) against the len//4 estimate for equivalent
# conversation_history content (~59117) — a 1.66x undercount that let compaction's
# trigger fire too late to prevent a hard context-size overflow. len//3 halves the
# undercount without adding a real-tokenizer dependency; if this proves insufficient
# in practice, the next escalation is a real BPE-based estimate (e.g. tiktoken as an
# approximation even for non-OpenAI models), not a further ratio tweak.
# Single source of truth for BOTH directions of the chars<->tokens conversion
# (estimate_tokens and _truncate_to_tokens) so they can't drift out of agreement.
_CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _history_tokens(history: History) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) for m in history)


def _is_usable(history: History, observed: ObservedPrompt | None) -> bool:
    # A count longer than the history it claims to describe cannot be trusted —
    # that is exactly the shape a compaction leaves behind after rewriting it.
    return observed is not None and observed.message_count <= len(history)


def input_tokens(history: History, observed: ObservedPrompt | None) -> int:
    """Total input tokens the next call will send: system prompt, schemas and
    history together.

    The window holds all of it, so all of it counts against the trigger. The
    character estimate could only ever see message contents, missing the system
    prompt entirely — about 14.5k tokens on a real controller turn.
    """
    if not _is_usable(history, observed):
        return _history_tokens(history)
    assert observed is not None  # narrowed by _is_usable
    return observed.tokens + _history_tokens(history[observed.message_count:])


def fixed_overhead(history: History, observed: ObservedPrompt | None) -> int:
    """Tokens the input carries that are NOT history — system prompt and schemas.

    Needed because the eviction floor budgets retained HISTORY, while the trigger
    now measures total input. Derived by subtraction, so it inherits the
    chars-per-token error on the measured slice: the trigger is exact, this is
    not. Clamped at zero because the estimate can exceed the real count.
    """
    if not _is_usable(history, observed):
        return 0
    assert observed is not None  # narrowed by _is_usable
    return max(0, observed.tokens - _history_tokens(history[:observed.message_count]))


def _render(messages: History) -> str:
    return "\n".join(f"{m.get('role', '')}: {m.get('content', '')}" for m in messages)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max(8, max_tokens * _CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n…[truncated]…\n" + text[-tail:]


def _is_turn_start(m: dict[str, object]) -> bool:
    # A turn starts at a user/assistant (or any non-continuation) message; tool results
    # attach backward to the action that produced them. Unknown roles default to turn-start.
    return str(m.get("role", "")) not in _CONTINUATION_ROLES


def _select_hot(
    history: History, hot_budget_tokens: int, hot_turns_cap: int
) -> tuple[History, History, int]:
    """Newest *whole logical turns* that fit the token budget and the count cap.

    Lossless at turn boundaries: never keeps a partial turn. If the budget boundary falls
    inside a turn, the partial remainder is pushed to eviction (it survives via the anchored
    summary) by trimming leading continuation messages so hot begins at a turn start. Always
    keeps >=1 message even if the whole hot set is continuations (degenerate).
    """
    hot: History = []
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


def _make_segment(run_id: str, seq: int, content: str, now: datetime, ms: int) -> CompactionSegment:
    return CompactionSegment(
        id=f"{run_id}-{seq}-{ms}", run_id=run_id, seq=seq, content=content, created_at=now
    )


def _anchor_message(text: str) -> dict[str, object]:
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

    async def maybe_compact(
        self, history: History, run_id: str, observed: ObservedPrompt | None = None
    ) -> CompactionResult:
        # Pure token-trigger check (no count short-circuit: a short history of oversized
        # turns can be over budget and must still compact). Below threshold is the common
        # per-iteration case — return without touching the store (no hot-path DB read).
        #
        # `observed` is the provider's exact count for the last call when there is
        # one; input_tokens degrades to the old estimate when there is not, so the
        # eight transports that report no usage are unaffected.
        if input_tokens(history, observed) < self._window_tokens * self._trigger_frac:
            return CompactionResult(compacted=False, history=history)
        now = datetime.now(UTC)
        ms = int(now.timestamp() * 1000)
        # The floor budgets retained HISTORY, but the trigger measures total input,
        # so the system prompt's share has to come off the top — otherwise
        # compaction targets a number the input can never reach. Floored at 1 so a
        # pathological overhead cannot ask for a negative budget.
        hot_budget = max(1, int(self._window_tokens * self._hot_token_frac)
                          - fixed_overhead(history, observed))
        evicted, hot, hot_used = _select_hot(history, hot_budget, self._hot_turns)
        base = self._store.next_seq(run_id)  # run-monotonic seq across compaction rounds
        degraded = False
        extra: list[CompactionSegment] = []
        # Backstop: a single newest turn that alone busts the hot budget must be truncated
        # in-window, else compaction cannot get us back under the window.
        if hot_used > hot_budget and len(hot) == 1:
            full = str(hot[0].get("content", ""))
            extra.append(_make_segment(run_id, base + len(evicted), full, now, ms))
            hot = [{**hot[0], "content": _truncate_to_tokens(full, hot_budget)}]
            degraded = True
        segments = [
            _make_segment(run_id, base + i, str(m.get("content", "")), now, ms)
            for i, m in enumerate(evicted)
        ]
        if segments or extra:
            self._store.add_segments(segments + extra)  # persist BEFORE summarize -> lossless
        old = self._store.get_anchor(run_id)
        old_text = old.summary_md if old else ""
        evicted_count = len(evicted) + len(extra)
        old_version = old.version if old else 0
        # Segment seq span just written, so the harness can consolidate exactly this slice.
        seq_lo = base if evicted_count else None
        seq_hi = base + evicted_count - 1 if evicted_count else None
        if not evicted:
            keep = [_anchor_message(old_text)] if old_text else []
            return CompactionResult(
                compacted=True, history=[*keep, *hot], anchor=old_text or None, degraded=degraded,
                evicted_count=evicted_count, anchor_version=old_version,
                evicted_seq_lo=seq_lo, evicted_seq_hi=seq_hi,
            )
        try:
            new_anchor = await self._summarize(old_text, _render(evicted))
        except Exception:  # best-effort: never fail a loop iteration
            logger.warning(
                "[memory] anchor summarize failed for run=%s; degrading", run_id, exc_info=True
            )
            keep = (
                [_anchor_message(f"(earlier context summary unavailable)\n{old_text}")]
                if old_text
                else []
            )
            return CompactionResult(
                compacted=True, history=[*keep, *hot], anchor=old_text or None, degraded=True,
                evicted_count=evicted_count, anchor_version=old_version,
                evicted_seq_lo=seq_lo, evicted_seq_hi=seq_hi,
            )
        summary = self._store.upsert_anchor(run_id, new_anchor)
        logger.info(
            "[memory] compacted run=%s anchor=v%d evicted=%d anchor_chars=%d%s",
            run_id, summary.version, evicted_count, len(new_anchor),
            " (degraded)" if degraded else "",
        )
        return CompactionResult(
            compacted=True,
            history=[_anchor_message(new_anchor), *hot],
            anchor=new_anchor,
            evicted_count=evicted_count,
            anchor_version=summary.version,
            degraded=degraded,
            evicted_seq_lo=seq_lo,
            evicted_seq_hi=seq_hi,
        )
