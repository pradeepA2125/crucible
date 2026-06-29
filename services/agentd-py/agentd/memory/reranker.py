from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from agentd.memory.models import Memory

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-reranker-base"


class Reranker:
    """Local cross-encoder reranker. Lazy load; degrade-not-raise (model absent → input order).
    `scorer` is injectable for tests (bypasses the model)."""

    def __init__(
        self, model_name: str = _DEFAULT_MODEL, *,
        scorer: Callable[[list[tuple[str, str]]], list[float]] | None = None,
    ) -> None:
        self._model_name = model_name
        self._scorer = scorer
        self._available = True
        self._model: Any = None

    @property
    def available(self) -> bool:
        return self._available

    def _score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._scorer is not None:
            return self._scorer(pairs)
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self._model_name)
        return [float(s) for s in self._model.predict(pairs)]

    def rerank(self, query: str, candidates: list[Memory]) -> list[tuple[Memory, float]]:
        if not candidates:
            return []
        try:
            scores = self._score([(query, c.content) for c in candidates])
            scored = list(zip(candidates, scores, strict=True))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored
        except Exception:  # noqa: BLE001 — degrade: recall falls back to fused order
            logger.warning("[memory] reranker unavailable for model=%s", self._model_name)
            self._available = False
            return [(c, 0.0) for c in candidates]

    def warmup(self) -> None:
        self._score([("warmup", "warmup")])
