from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime

from agentd.memory.embedder import Embedder
from agentd.memory.models import Memory, RecallTrace, RecallTraceEntry
from agentd.memory.store import MemoryStore

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


def _score_candidates(
    memories: list[Memory], sem: dict[str, float], lex: dict[str, float],
    struct: dict[str, float], weights: tuple[float, float, float], now: datetime,
) -> list[tuple[Memory, float, dict[str, float]]]:
    """Signal-capturing core: per memory → (memory, fused_score, normalized signals)."""
    if not memories:
        return []
    w_sem, w_lex, w_struct = weights
    ids = [m.id for m in memories]
    n_sem = dict(zip(ids, _minmax([sem.get(i, 0.0) for i in ids]), strict=True))
    n_lex = dict(zip(ids, _minmax([lex.get(i, 0.0) for i in ids]), strict=True))
    n_str = dict(zip(ids, _minmax([struct.get(i, 0.0) for i in ids]), strict=True))
    n_imp = dict(zip(ids, _minmax([float(m.importance) for m in memories]), strict=True))
    out: list[tuple[Memory, float, dict[str, float]]] = []
    for m in memories:
        rec = _recency(m.valid_from, now, HALF_LIFE_DAYS)
        signals = {"semantic": n_sem[m.id], "lexical": n_lex[m.id], "structural": n_str[m.id],
                   "importance": n_imp[m.id], "recency": rec}
        fused = (w_sem * n_sem[m.id] + w_lex * n_lex[m.id] + w_struct * n_str[m.id]
                 + W_IMP * n_imp[m.id] + W_REC * rec)
        out.append((m, fused, signals))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _fuse(
    memories: list[Memory], sem: dict[str, float], lex: dict[str, float],
    struct: dict[str, float], weights: tuple[float, float, float], now: datetime,
) -> list[tuple[Memory, float]]:
    return [(m, s) for m, s, _ in _score_candidates(memories, sem, lex, struct, weights, now)]


_ENTITY_RE = re.compile(r"[\w./:]+")


def _query_entities(query: str) -> set[str]:
    # path-ish tokens: contain a / . or : (e.g. src/tax.py, foo.py:Bar)
    return {t for t in _ENTITY_RE.findall(query) if any(c in t for c in "/.:")}


class RecallEngine:
    def __init__(
        self, store: MemoryStore, embedder: Embedder, *,
        weights: tuple[float, float, float], candidate_k: int = 30, min_score: float = 0.15,
        reranker: object | None = None, rerank_min_candidates: int = 8,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._weights = weights
        self._cand_k = candidate_k
        self._min_score = min_score  # FIX #7: relevance floor
        self._reranker = reranker  # Phase 3.1 — count-gated cross-encoder (None = off)
        self._rerank_min = rerank_min_candidates

    async def recall(self, query: str, scope_kind: str, scope_id: str, k: int) -> list[Memory]:
        # UNCHANGED signature — the recall() tool + recall_grounded keep calling this.
        memories, _trace = await self.recall_with_trace(query, scope_kind, scope_id, k)
        return memories

    async def recall_with_trace(
        self, query: str, scope_kind: str, scope_id: str, k: int,
    ) -> tuple[list[Memory], RecallTrace]:
        try:
            return await self._recall_with_trace(query, scope_kind, scope_id, k)
        except Exception:  # noqa: BLE001 — best-effort: never break the turn
            logger.warning("[memory] recall failed for scope=%s", scope_id, exc_info=True)
            empty = RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id, k=k,
                                floor=self._min_score, reranked=False, entries=[])
            return [], empty

    async def _recall_with_trace(
        self, query: str, scope_kind: str, scope_id: str, k: int,
    ) -> tuple[list[Memory], RecallTrace]:
        sem: dict[str, float] = {}
        if self._embedder.available:
            # FIX #3: embed off the event loop (sync CPU + first-call model load).
            vecs = await asyncio.to_thread(self._embedder.embed, [query])
            if vecs:
                for mid, dist in self._store.search_semantic(vecs[0], self._cand_k,
                                                             scope_kind, scope_id):
                    sem[mid] = 1.0 - (dist * dist) / 2.0  # cosine from L2 (unit vectors)
        lex = {mid: -rank for mid, rank in
               self._store.search_lexical(query, self._cand_k, scope_kind, scope_id)}
        qents = _query_entities(query)
        ids = set(sem) | set(lex)
        mems = [m for m in (self._store.get_memory(i) for i in ids) if m is not None]
        struct = {m.id: float(len(qents & set(m.entities))) for m in mems}
        scored = _score_candidates(mems, sem, lex, struct, self._weights, datetime.now(UTC))
        passing = [t for t in scored if t[1] >= self._min_score]
        below = [t for t in scored if t[1] < self._min_score]

        reranked = False
        rr_scores: dict[str, float] = {}
        ordered = passing
        if (self._reranker is not None and getattr(self._reranker, "available", False)
                and len(passing) > self._rerank_min):
            reranked = True
            rr = self._reranker.rerank(query, [m for m, _, _ in passing])  # type: ignore[attr-defined]
            rr_scores = {m.id: sc for m, sc in rr}
            order = {m.id: i for i, (m, _) in enumerate(rr)}
            ordered = sorted(passing, key=lambda t: order[t[0].id])

        injected = [m for m, _, _ in ordered[:k]]
        injected_ids = {m.id for m in injected}
        entries = [
            RecallTraceEntry(
                memory_id=m.id, kind=m.kind, content=m.content[:160], importance=m.importance,
                signals={kk: round(vv, 4) for kk, vv in sig.items()},
                fused_score=round(fused, 4),
                rerank_score=round(rr_scores[m.id], 4) if m.id in rr_scores else None,
                final_rank=rank, injected=m.id in injected_ids)
            for rank, (m, fused, sig) in enumerate([*ordered, *below])
        ]
        trace = RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id, k=k,
                            floor=self._min_score, reranked=reranked, entries=entries)
        return injected, trace

    async def recall_grounded(
        self, query: str, scope_kind: str, scope_id: str, k: int,
        ground: Callable[[str], str] | None = None,
    ) -> list[str]:
        """Recall + render to lines; optionally ground the top 1-2 in the code graph."""
        mems = await self.recall(query, scope_kind, scope_id, k)
        lines = [f"- ({m.kind}) {m.content}" for m in mems]
        if ground is not None:
            for i, m in enumerate(mems[:2]):  # top 1-2 only (cost-bounded)
                if not m.entities:
                    continue
                try:
                    g = ground(m.entities[0])
                    if g:
                        lines[i] += f"  (grounding: {g[:120]})"
                except Exception:  # noqa: BLE001 — best-effort: grounding never breaks recall
                    logger.warning("[memory] grounding failed for entity=%s", m.entities[0])
        return lines
