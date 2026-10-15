from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from agentd.local_models import LocalModels
from agentd.model_loading import MODEL_LOCK, shared_sentence_transformer

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_DIM = 384


def _unit(v: list[float]) -> list[float]:
    if not v or not all(math.isfinite(value) for value in v):
        raise ValueError("Embedder returned an empty or non-finite vector")
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
        local_models: LocalModels | None = None,
    ) -> None:
        self._model_name = model_name
        self._encoder = encoder
        self._available = True
        self._model: Any = None  # lazy SentenceTransformer
        self._capability = local_models

    @property
    def dim(self) -> int:
        return _DIM

    @property
    def available(self) -> bool:
        if self._capability is not None:
            return self._capability.ready("embedder") and self._available
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
        with MODEL_LOCK:  # every model call too — see model_loading
            if self._capability is not None and self._capability.state("embedder").state in {
                "disabled", "unavailable",
            }:
                raise RuntimeError("Local embedder is unavailable")
            if self._model is None:
                self._model = shared_sentence_transformer(self._model_name)
            rows = self._model.encode(texts)
        return [list(map(float, row)) for row in rows]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self._available or (
            self._capability is not None and not self._capability.ready("embedder")
        ):
            return []
        try:
            return [_unit(v) for v in self._encode(texts)]
        except Exception as exc:  # degrade: recall/consolidation handle empty embeddings
            logger.warning("[memory] embedder unavailable for model=%s", self._model_name)
            self._available = False
            if self._capability is not None:
                self._capability.fail("embedder", exc)
            return []

    def warmup(self) -> None:
        """Explicit, testable entry point to trigger the lazy model load off the hot path."""
        if self._capability is None:
            self.embed(["warmup"])
            return
        if self._capability.state("embedder").state != "loading":
            return
        try:
            rows = self._encode(["warmup"])
            if len(rows) != 1:
                raise ValueError("Embedder warmup returned no vector")
            _unit(rows[0])
            self._capability.mark_ready("embedder")
        except Exception as exc:
            self._available = False
            self._capability.fail("embedder", exc)
