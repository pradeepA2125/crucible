"""Select complete evidence turns under an anchor-aware model input budget."""

from __future__ import annotations

from collections.abc import Callable, Sequence

InputTokens = Callable[[str, str], int]
TARGET_INPUT_TOKENS = 200_000
HARD_INPUT_TOKENS = 500_000


class EvidenceBudgetExceeded(ValueError):
    """An anchor or indivisible turn cannot fit; its raw evidence remains on disk."""


def next_fold(
    turns: Sequence[str],
    anchor: str,
    input_tokens: InputTokens,
    *,
    target: int,
    hard: int,
) -> tuple[str, int]:
    """Prefer the target; one large complete turn may use the remaining hard budget."""
    if not turns:
        return "", 0
    hard = min(hard, HARD_INPUT_TOKENS)
    target = min(target, hard, TARGET_INPUT_TOKENS)
    if input_tokens(anchor, turns[0]) > hard:
        raise EvidenceBudgetExceeded("The prior anchor plus one evidence turn exceeds input budget")
    if input_tokens(anchor, turns[0]) > target:
        return turns[0], 1
    # Token estimates of the serialized text are monotonic. Binary search avoids rendering
    # every growing prefix of a large history at quadratic cost.
    lo, hi = 1, len(turns)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if input_tokens(anchor, "\n\n".join(turns[:mid])) <= target:
            lo = mid
        else:
            hi = mid - 1
    text = "\n\n".join(turns[:lo])
    if input_tokens(anchor, text) > min(target, hard):
        raise EvidenceBudgetExceeded("Evidence chunk exceeds input budget")
    return text, lo
