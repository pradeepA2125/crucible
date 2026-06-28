from __future__ import annotations

import logging
import math
from datetime import datetime

from agentd.memory.models import Memory

logger = logging.getLogger(__name__)

W_IMP = 0.3
W_REC = 0.2
HALF_LIFE_DAYS = 14.0


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _recency(valid_from: datetime, now: datetime, half_life_days: float) -> float:
    days = max(0.0, (now - valid_from).total_seconds() / 86400.0)
    return math.exp(-days / half_life_days)


def _fuse(
    memories: list[Memory], sem: dict[str, float], lex: dict[str, float],
    struct: dict[str, float], weights: tuple[float, float, float], now: datetime,
) -> list[tuple[Memory, float]]:
    if not memories:
        return []
    w_sem, w_lex, w_struct = weights
    ids = [m.id for m in memories]
    n_sem = dict(zip(ids, _minmax([sem.get(i, 0.0) for i in ids]), strict=True))
    n_lex = dict(zip(ids, _minmax([lex.get(i, 0.0) for i in ids]), strict=True))
    n_str = dict(zip(ids, _minmax([struct.get(i, 0.0) for i in ids]), strict=True))
    n_imp = dict(zip(ids, _minmax([float(m.importance) for m in memories]), strict=True))
    scored: list[tuple[Memory, float]] = []
    for m in memories:
        s = (w_sem * n_sem[m.id] + w_lex * n_lex[m.id] + w_struct * n_str[m.id]
             + W_IMP * n_imp[m.id] + W_REC * _recency(m.valid_from, now, HALF_LIFE_DAYS))
        scored.append((m, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
