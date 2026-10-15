"""Publish pending scoped embeddings once a local model has completed warmup."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agentd.memory.durable_store import DurableMemoryStore, embedding_hash
from agentd.memory.scope import ScopeContext


class EmbeddingWriter:
    def __init__(
        self,
        store: DurableMemoryStore,
        model: str,
        *,
        encode: Callable[[list[str]], list[list[float]]],
        ready: Callable[[], bool],
    ) -> None:
        self._store = store
        self._model = model
        self._encode = encode
        self._ready = ready
        self._lock = asyncio.Lock()
        self._failed = False

    async def drain(self, context: ScopeContext, *, limit: int = 100) -> int:
        if self._lock.locked() or self._failed or not self._ready():
            return 0
        async with self._lock:
            if (
                not self._store.vectors_available
                or self._store.embedding_configuration()[0] != self._model
            ):
                return 0
            entries = self._store.pending_embeddings(context, limit=limit)
            if not entries:
                return 0
            try:
                # Only model execution runs on a worker. SQLite remains on its owner thread.
                vectors = await asyncio.to_thread(
                    self._encode,
                    [entry.embedding_text for entry in entries],
                )
                if not vectors:
                    self._failed = True
                    return 0
                if len(vectors) != len(entries):
                    raise ValueError("Embedder returned the wrong number of vectors")
                dimensions = len(vectors[0])
                if not dimensions or any(len(vector) != dimensions for vector in vectors):
                    raise ValueError("Embedder returned inconsistent vector dimensions")
                if not self._ready():
                    return 0
                model, configured_dimensions = self._store.embedding_configuration()
                if model != self._model:
                    return 0
                if configured_dimensions != dimensions and not self._store.configure_embeddings(
                    self._model,
                    dimensions,
                    expected_model=self._model,
                ):
                    return 0
                return sum(
                    self._store.publish_embedding(
                        entry.id,
                        context,
                        vector,
                        model=self._model,
                        expected_hash=embedding_hash(entry),
                    )
                    for entry, vector in zip(entries, vectors, strict=True)
                )
            except Exception:
                # Readiness will become the shared local-model capability in phase 3.
                # Meanwhile a broken encoder must not burn CPU on every chat iteration.
                self._failed = True
                raise
