from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

from agentd.history_metadata import message_sequence
from agentd.image_context import history_visual_tokens, image_tombstone
from agentd.memory.evidence import evidence_turns, render_evidence
from agentd.memory.folding import (
    HARD_INPUT_TOKENS,
    TARGET_INPUT_TOKENS,
    EvidenceBudgetExceeded,
    InputTokens,
    next_fold,
)
from agentd.memory.history import EvidenceHistory
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
    return (sum(estimate_tokens(str(m.get("content", ""))) for m in history)
            + history_visual_tokens(history))


def _is_usable(history: History, observed: ObservedPrompt | None) -> bool:
    # A count longer than the history it claims to describe cannot be trusted —
    # that is exactly the shape a compaction leaves behind after rewriting it.
    return observed is not None and observed.message_count <= len(history)


def input_tokens(
    history: History, observed: ObservedPrompt | None, unmeasured_overhead: int = 0,
) -> int:
    """Total input tokens the next call will send: system prompt, schemas and
    history together.

    The window holds all of it, so all of it counts against the trigger. The
    character estimate could only ever see message contents, missing the system
    prompt entirely — about 14.5k tokens on a real controller turn. Without a
    measured count (the first call, or a provider that reports no usage) the
    caller's `unmeasured_overhead` estimate of those parts stands in for it.
    """
    if not _is_usable(history, observed):
        return _history_tokens(history) + max(0, unmeasured_overhead)
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
    measured = observed.history_tokens
    if measured is None:
        measured = _history_tokens(history[:observed.message_count])
    return max(0, observed.tokens - measured)


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
        t = _history_tokens([m, *hot]) - _history_tokens(hot)
        if hot and (used + t > hot_budget_tokens or len(hot) >= hot_turns_cap):
            break
        hot.insert(0, m)
        used += t
    while len(hot) > 1 and not _is_turn_start(hot[0]):
        hot.pop(0)
    used = _history_tokens(hot)  # recompute after trim
    evicted = history[: len(history) - len(hot)]
    return evicted, hot, used


def _make_segment(
    run_id: str, seq: int, content: str, now: datetime, ms: int,
    message: dict[str, object] | None = None,
) -> CompactionSegment:
    source = message or {}
    role, tool = source.get("role"), source.get("tool")
    return CompactionSegment(
        id=f"{run_id}-{seq}-{ms}", run_id=run_id, seq=seq, content=content, created_at=now,
        mseq=message_sequence(source),
        role=role if isinstance(role, str) else None,
        tool=tool if isinstance(tool, str) else None,
        metadata={key: source[key] for key in (
            "_evidence", "is_error", "status", "delivered_read", "batch_id",
            "id", "call_id", "tool_call_id", "provider_call_id", "image_refs", "exit_code",
        ) if key in source},
    )


def _anchor_message(text: str, *, evidence: bool = False) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "user",
        "content": f"[MEMORY] Summary of earlier conversation that was compacted:\n{text}",
    }
    if evidence:
        message["_evidence"] = {"kind": "compaction_summary"}
    return message


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
        reserve_tokens: int = 28_000,
        compact_evidence: bool = False,
        summary_input_tokens: InputTokens | None = None,
    ) -> None:
        self._store = store
        self._summarize = summarize
        self._window_tokens = window_tokens
        self._trigger_frac = trigger_frac
        self._reserve_tokens = reserve_tokens
        self._hot_token_frac = hot_token_frac
        self._hot_turns = hot_turns
        self._compact_evidence = compact_evidence
        self._summary_input_tokens = summary_input_tokens or (
            lambda old, text: estimate_tokens(old + text) + 4096
        )

    async def _fold_evidence(
        self, run_id: str, old_text: str, evicted: History, notices: list[str],
    ) -> tuple[str, int, bool, History, Exception | None]:
        rows = render_evidence(evicted, include_thoughts=True)
        turns = evidence_turns(rows)
        # Source offsets, rather than mseq, also work for retained legacy data.
        starts = [row.source_index for i, row in enumerate(rows) if row.starts_turn or i == 0]
        completed = 0
        current = self._store.get_anchor(run_id)
        version = current.version if current else 0
        # The response reserve also covers provider reasoning/output. Small windows use
        # the existing fractional floor; never permit a call larger than the window.
        anchor = old_text
        try:
            while turns:
                # A settings change or overflow-reported limit can arrive while a fold awaits.
                capacity = min(HARD_INPUT_TOKENS, self.trigger_tokens(), self._window_tokens)
                target = min(TARGET_INPUT_TOKENS, capacity)
                text, consumed = next_fold(
                    turns, anchor, self._summary_input_tokens, target=target, hard=capacity,
                )
                candidate = await self._summarize(anchor, text)
                if not candidate.strip():
                    raise ValueError("Summarizer returned an empty anchor")
                if notices:
                    missing = [
                        notice for notice in notices if notice not in candidate.splitlines()
                    ]
                    if missing:
                        candidate += "\n" + "\n".join(missing)
                # Do not accept an anchor that would prevent even an empty next input.
                if self._summary_input_tokens(candidate, "") > capacity:
                    raise EvidenceBudgetExceeded(
                        "Summary output exceeds the next call's input budget",
                    )
                committed = self._store.upsert_anchor(run_id, candidate)
                anchor, version = committed.summary_md, committed.version
                completed += consumed
                turns = turns[consumed:]
            if not version and not anchor:
                return "", 0, False, [], None
            # A round containing only excluded bodies can still add image tombstones.
            if current is not None and version == current.version and anchor != current.summary_md:
                committed = self._store.upsert_anchor(run_id, anchor)
                version = committed.version
            return anchor, version, False, [], None
        except Exception as exc:
            logger.warning(
                "[memory] evidence fold failed for run=%s at anchor=v%d; raw segments retained",
                run_id, version, exc_info=True,
            )
            # Persisting raw evidence is insufficient if no tool can restore it
            # into the next model call. Retain the unsummarized suffix so a retry
            # starts after the last committed fold, without losing or double-folding it.
            pending = evicted[starts[completed]:] if completed < len(starts) else []
            return anchor, version, True, pending, exc

    def set_window_tokens(self, window_tokens: int) -> None:
        """Re-point the trigger at a new context window mid-process.

        Trigger, eviction floor and fold budgets read it per call, so there is no
        derived state to invalidate and
        the change lands on the next loop iteration. This exists so the settings
        panel's context-window field can hot-apply the same way the provider
        hot-swap does, instead of requiring a backend restart.
        """
        self._window_tokens = window_tokens

    def cap_window_tokens(self, limit: int) -> None:
        """Lower the window to one the provider stated in an overflow error. Only ever
        lower: the declared window may be the smaller, right one (an error naming the
        model's maximum says nothing about what this deployment allows)."""
        if limit > 0:
            self._window_tokens = min(self._window_tokens, limit)

    def trigger_tokens(self) -> int:
        """Input size at which the next request is compacted first.

        The count compared against it already holds everything that request sends (the
        last call's measured input, that call's output and the new tool results), so the
        only thing that can still land on top is the NEXT response: one reserve's worth,
        measured at most ~1.5k tokens on 388 calls and more for a large single-file edit.
        Small windows keep the trigger_frac floor, where window − reserve would compact
        almost at once.
        """
        return max(int(self._window_tokens * self._trigger_frac),
                   self._window_tokens - self._reserve_tokens)

    async def maybe_compact(
        self, history: History, run_id: str, observed: ObservedPrompt | None = None,
        *, force: bool = False, projected_history: History | None = None,
        unmeasured_overhead: int = 0,
    ) -> CompactionResult:
        accounting = projected_history if projected_history is not None else history
        # Pure token-trigger check (no count short-circuit: a short history of oversized
        # turns can be over budget and must still compact). Below threshold is the common
        # per-iteration case — return without touching the store (no hot-path DB read).
        #
        # `observed` is the provider's exact count for the last call when there is
        # one; input_tokens degrades to the old estimate when there is not, so the
        # eight transports that report no usage are unaffected.
        # force: the provider refused the last request as over its window, so the input
        # must shrink whatever the count says.
        if not force and (input_tokens(accounting, observed, unmeasured_overhead)
                          < self.trigger_tokens()):
            return CompactionResult(compacted=False, history=history)
        now = datetime.now(UTC)
        ms = int(now.timestamp() * 1000)
        # The floor budgets retained HISTORY, but the trigger measures total input, so
        # in principle the system prompt's share has to come off the top — otherwise
        # compaction could target a number the input can never reach. In practice this
        # only ever SUBTRACTS something when the char-estimate under-counts the
        # measured history slice (fixed_overhead = max(0, observed.tokens -
        # estimate(history[:message_count]))). The char/3 ratio over-counts by
        # design (see _CHARS_PER_TOKEN above), and that over-count grows with history
        # size while the system prompt it would need to outrun is fixed (~14.5k
        # tokens) — so at the history sizes large enough to trip the trigger, the
        # estimate for the history slice ALONE routinely already exceeds the
        # measured TOTAL (history + system + schemas), the max(0, ...) clamp fires,
        # and this subtraction is inert. It only engages for a history whose real
        # tokens-per-char run under 3 (denser text than the estimate assumes).
        # Clamped to a quarter of the nominal floor, not to 1: at 1, _select_hot
        # keeps a single message and the backstop below truncates it to
        # _truncate_to_tokens's own 8-char floor, destroying the user's newest
        # message instead of merely being conservative about how much history
        # survives (final whole-branch review, finding 3).
        nominal_hot_budget = int(self._window_tokens * self._hot_token_frac)
        hot_budget = max(nominal_hot_budget // 4,
                          nominal_hot_budget - fixed_overhead(accounting, observed))
        evicted_view, hot_view, hot_used = _select_hot(accounting, hot_budget, self._hot_turns)
        boundary = len(evicted_view)
        evicted, hot = history[:boundary], history[boundary:]
        retained_ids = {r.get("asset_id") for m in hot
                        for r in cast(list[dict[str, object]], m.get("image_refs", []))}
        lost = {r.get("asset_id"): r for m in evicted
                for r in cast(list[dict[str, object]], m.get("image_refs", []))
                if r.get("asset_id") not in retained_ids}
        tombstones = "\n".join(image_tombstone(r, "history compaction") for r in lost.values())
        base = self._store.next_seq(run_id)  # run-monotonic seq across compaction rounds
        degraded = False
        extra: list[CompactionSegment] = []
        # Backstop: a single newest turn that alone busts the hot budget must be truncated
        # in-window, else compaction cannot get us back under the window.
        if hot_used > hot_budget and len(hot) == 1:
            full = str(hot[0].get("content", ""))
            extra.append(_make_segment(run_id, base + len(evicted), full, now, ms, hot[0]))
            hot = [{**hot[0], "content": _truncate_to_tokens(full, hot_budget)}]
            degraded = True
        segments = [
            _make_segment(run_id, base + i, str(m.get("content", "")), now, ms, m)
            for i, m in enumerate(evicted)
        ]
        if segments or extra:
            self._store.add_segments(segments + extra)  # persist BEFORE summarize -> lossless
        old = self._store.get_anchor(run_id)
        old_text = old.summary_md if old else ""
        if tombstones:
            old_text += "\n" + tombstones
        evicted_count = len(evicted) + len(extra)
        old_version = old.version if old else 0
        # Segment seq span just written, so the harness can consolidate exactly this slice.
        seq_lo = base if evicted_count else None
        seq_hi = base + evicted_count - 1 if evicted_count else None
        if not evicted:
            keep = [_anchor_message(old_text, evidence=isinstance(history, EvidenceHistory))
                    ] if old_text else []
            return CompactionResult(
                compacted=True, history=[*keep, *hot], anchor=old_text or None, degraded=degraded,
                evicted_count=evicted_count, anchor_version=old_version,
                evicted_seq_lo=seq_lo, evicted_seq_hi=seq_hi,
            )
        if self._compact_evidence:
            notices = list(dict.fromkeys(
                line for line in (old_text + "\n" + _render(evicted)).splitlines()
                if line.startswith("[Image ") and "unavailable:" in line
            ))
            anchor, version, failed, pending, error = await self._fold_evidence(
                run_id, old_text, evicted, notices,
            )
            keep = [_anchor_message(anchor, evidence=isinstance(history, EvidenceHistory))
                    ] if anchor else []
            return CompactionResult(
                compacted=True, history=[*keep, *pending, *hot], anchor=anchor or None,
                degraded=degraded or failed, evicted_count=evicted_count, anchor_version=version,
                failed=failed, error=error,
                evicted_seq_lo=seq_lo, evicted_seq_hi=seq_hi,
            )
        try:
            new_anchor = await self._summarize(old_text, _render(evicted))
        except Exception as exc:
            logger.warning(
                "[memory] anchor summarize failed for run=%s; degrading", run_id, exc_info=True
            )
            return CompactionResult(
                compacted=False, history=history, anchor=old_text or None, degraded=True,
                failed=True, error=exc,
                evicted_count=evicted_count, anchor_version=old_version,
                evicted_seq_lo=seq_lo, evicted_seq_hi=seq_hi,
            )
        notices = list(dict.fromkeys(
            line for line in (old_text + "\n" + _render(evicted)).splitlines()
            if line.startswith("[Image ") and "unavailable:" in line))
        if notices:
            new_anchor += "\n" + "\n".join(notices)
        summary = self._store.upsert_anchor(run_id, new_anchor)
        logger.info(
            "[memory] compacted run=%s anchor=v%d evicted=%d anchor_chars=%d%s",
            run_id, summary.version, evicted_count, len(new_anchor),
            " (degraded)" if degraded else "",
        )
        return CompactionResult(
            compacted=True,
            history=[_anchor_message(new_anchor, evidence=isinstance(history, EvidenceHistory)),
                     *hot],
            anchor=new_anchor,
            evicted_count=evicted_count,
            anchor_version=summary.version,
            degraded=degraded,
            evicted_seq_lo=seq_lo,
            evicted_seq_hi=seq_hi,
        )
