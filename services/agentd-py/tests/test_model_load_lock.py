"""All local models load under ONE process-wide lock.

The embedder, reranker and semantic index each move a model onto MPS at construction.
torch's MPS kernel cache is not thread-safe, so concurrent loads crashed the backend
(SIGSEGV) or corrupted the cache into a 300% CPU spin that wedged turns.

Counts only this test's own model name: a real-model warmup daemon thread left behind by an
earlier test can still be queued on the process-wide MODEL_LOAD_LOCK and, once it gets it,
import this test's fake module.
"""
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from agentd.memory.embedder import Embedder
from agentd.memory.reranker import Reranker
from agentd.retrieval.semantic_index import SemanticIndex


class _OverlapProbe:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.loads = 0

    def construct(self, name: str) -> None:
        if not name.startswith("fake-"):
            return
        with self._guard:
            self.active += 1
            self.loads += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)  # widen the window a racing load would hit
        with self._guard:
            self.active -= 1


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _OverlapProbe:
    probe = _OverlapProbe()

    class _FakeModel:
        def __init__(self, name: str) -> None:
            probe.construct(name)

        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] + [0.0] * 383 for _ in texts]

        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            return [0.0 for _ in pairs]

        def get_embedding_dimension(self) -> int:
            return 384

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _FakeModel  # type: ignore[attr-defined]
    fake.CrossEncoder = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return probe


def test_loads_across_all_three_models_never_overlap(probe: _OverlapProbe, tmp_path: Path) -> None:
    embedder, reranker = Embedder("fake-embedder"), Reranker("fake-reranker")
    index = SemanticIndex(tmp_path / "idx", model_name="fake-index")
    loads = [
        lambda: embedder.embed(["x"]),
        lambda: reranker._score([("q", "c")]),
        lambda: index._get_model(),
    ] * 2  # each model twice: also covers the same-instance warmup-vs-call race
    threads = [threading.Thread(target=fn) for fn in loads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert probe.max_active == 1
    assert probe.loads == 3  # double-checked: one construction per instance
