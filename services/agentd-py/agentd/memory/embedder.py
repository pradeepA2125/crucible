from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_DIM = 384


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return v if n == 0 else [x / n for x in v]


class Embedder:
    """Shared bge-small wrapper. Returns unit-normalized vectors so the vec0 L2 store ranks
    identically to cosine. The model loads lazily (never on construction or an `available`
    check); a missing model degrades to empty embeddings rather than raising. `encoder` is
    injectable for tests."""

    def __init__(
        self, model_name: str = _DEFAULT_MODEL, *,
        encoder: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self._model_name = model_name
        self._encoder = encoder
        self._available = True
        self._model: Any = None  # lazy SentenceTransformer
        # Guards lazy construction: the background warmup thread (harness.py) and a real
        # turn's demand-triggered embed() can both race to construct the model the first
        # time (found live — this was still a crash even after serializing the embedder/
        # reranker warmup threads against each other, because warmup-vs-real-call was a
        # SEPARATE unlocked race on the same `if self._model is None` check).
        self._load_lock = threading.Lock()

    @property
    def dim(self) -> int:
        return _DIM

    @property
    def available(self) -> bool:
        # Probe an injected encoder (cheap); the real model is assumed available until a real
        # embed proves otherwise — never load the model just to answer this.
        if self._encoder is not None:
            try:
                self._encoder(["probe"])
            except Exception:  # noqa: BLE001
                self._available = False
            return self._available
        return self._available

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if self._encoder is not None:
            return self._encoder(texts)
        if self._model is None:
            with self._load_lock:
                if self._model is None:  # re-check: another thread may have won the race
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self._model_name)
        return [list(map(float, row)) for row in self._model.encode(texts)]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return [_unit(v) for v in self._encode(texts)]
        except Exception:  # noqa: BLE001 — degrade: recall/consolidation handle empty embeddings
            logger.warning("[memory] embedder unavailable for model=%s", self._model_name)
            self._available = False
            return []

    def warmup(self) -> None:
        """Explicit, testable entry point to trigger the lazy model load off the hot path."""
        self.embed(["warmup"])
