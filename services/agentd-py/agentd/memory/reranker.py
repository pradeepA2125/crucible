from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from typing import Any

from agentd.local_models import LocalModels
from agentd.memory.models import Memory
from agentd.memory.recall_context import CharacterCodec, ContextRanking, context_pairs
from agentd.model_loading import MODEL_LOCK, shared_cross_encoder

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-reranker-base"


class Reranker:
    """Local cross-encoder reranker. Lazy load; degrade-not-raise (model absent → input order).
    `scorer` is injectable for tests (bypasses the model)."""

    def __init__(
        self, model_name: str = _DEFAULT_MODEL, *,
        scorer: Callable[[list[tuple[str, str]]], list[float]] | None = None,
        local_models: LocalModels | None = None,
        bounded: bool = False,
    ) -> None:
        self._model_name = model_name
        self._scorer = scorer
        self._available = True
        self._model: Any = None
        self._capability = local_models
        self._bounded = bounded

    @property
    def available(self) -> bool:
        return self._available and (
            self._capability is None or self._capability.ready("reranker")
        )

    def _score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._scorer is not None:
            return self._scorer(pairs)
        with MODEL_LOCK:  # every model call too — see model_loading
            if self._capability is not None and self._capability.state("reranker").state in {
                "disabled", "unavailable",
            }:
                raise RuntimeError("Local reranker is unavailable")
            if self._model is None:
                self._model = shared_cross_encoder(self._model_name, bounded=self._bounded)
            scores = self._model.predict(pairs)
        return [float(s) for s in scores]

    def rerank(self, query: str, candidates: list[Memory]) -> list[tuple[Memory, float]]:
        if not candidates:
            return []
        if not self.available:
            return [(c, 0.0) for c in candidates]
        try:
            scores = self.score_pairs([(query, c.content) for c in candidates])
            scored = list(zip(candidates, scores, strict=True))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored
        except Exception as exc:  # degrade: recall falls back to fused order
            logger.warning("[memory] reranker unavailable for model=%s", self._model_name)
            self._available = False
            if self._capability is not None:
                self._capability.fail("reranker", exc)
            return [(c, 0.0) for c in candidates]

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """V2 callers need a real failure signal, never invented relevance scores."""
        if not self.available:
            raise RuntimeError("Local reranker is not ready")
        try:
            scores = self._score(pairs)
            if len(scores) != len(pairs) or not all(math.isfinite(score) for score in scores):
                raise ValueError("Reranker returned invalid scores")
            return scores
        except Exception as exc:
            self._available = False
            if self._capability is not None:
                self._capability.fail("reranker", exc)
            raise

    def score_context(self, signals: Mapping[str, str], documents: list[str]) -> ContextRanking:
        """One combined-context/full-body pair per memory, within the real token budget."""
        if not self.available:
            raise RuntimeError("Local reranker is not ready")
        try:
            with MODEL_LOCK:
                if self._scorer is not None:
                    codec: Any = CharacterCodec()
                else:
                    if self._model is None:
                        self._model = shared_cross_encoder(self._model_name, bounded=self._bounded)
                    codec = self._model.tokenizer
                pairs, result = context_pairs(signals, documents, codec)
                result.scores = self.score_pairs(pairs)
                return result
        except Exception as exc:
            self._available = False
            if self._capability is not None:
                self._capability.fail("reranker", exc)
            raise

    def warmup(self) -> None:
        if self._capability is not None and self._capability.state("reranker").state != "loading":
            return
        try:
            scores = self._score([("warmup", "warmup")])
            if len(scores) != 1 or not math.isfinite(scores[0]):
                raise ValueError("Reranker warmup returned invalid scores")
            if self._capability is not None:
                self._capability.mark_ready("reranker")
        except Exception as exc:
            self._available = False
            if self._capability is not None:
                self._capability.fail("reranker", exc)
