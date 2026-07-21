"""Fix: Embedder/Reranker's lazy model construction must be thread-safe.

Root cause (found live 2026-07-17, second occurrence after the warmup-race
fix): the background warmup thread (`harness.py`'s `_warmup_all`) and a real
turn's demand-triggered `embed()`/`rerank()` call both check `if self._model
is None` with no synchronization — a classic unlocked double-checked-locking
race. If the warmup thread hasn't finished constructing the model by the time
a real turn needs it, BOTH code paths construct a SECOND `SentenceTransformer`/
`CrossEncoder` concurrently, re-triggering the same PyTorch MPS first-init
race the warmup-serialization fix was meant to close. Confirmed live: backend
crashed again right as the first real chat turn arrived, even though the
warmup itself completed cleanly and sequentially.

Fix: a lock around the lazy-construct-and-use path in both classes.
"""
from __future__ import annotations

import sys
import threading
import time
import types

from agentd.memory.embedder import Embedder
from agentd.memory.reranker import Reranker


def test_concurrent_embed_constructs_model_only_once(monkeypatch):
    construct_count = {"n": 0}
    counting_lock = threading.Lock()

    class FakeModel:
        def __init__(self, name):
            with counting_lock:
                construct_count["n"] += 1
            time.sleep(0.05)  # widen the race window

        def encode(self, texts):
            return [[1.0, 0.0] for _ in texts]

    fake_module = types.SimpleNamespace(SentenceTransformer=FakeModel)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    emb = Embedder("fake-model")
    results: list[list[list[float]]] = []
    results_lock = threading.Lock()

    def worker() -> None:
        r = emb.embed(["x"])
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert construct_count["n"] == 1, (
        f"model constructed {construct_count['n']} times concurrently — "
        "a race, not a lazy singleton")
    assert len(results) == 4


def test_concurrent_rerank_constructs_model_only_once(monkeypatch):
    construct_count = {"n": 0}
    counting_lock = threading.Lock()

    class FakeCrossEncoder:
        def __init__(self, name):
            with counting_lock:
                construct_count["n"] += 1
            time.sleep(0.05)

        def predict(self, pairs):
            return [0.5 for _ in pairs]

    fake_module = types.SimpleNamespace(CrossEncoder=FakeCrossEncoder)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    rr = Reranker("fake-model")

    def worker() -> None:
        rr._score([("q", "d")])  # noqa: SLF001 — exercising the lazy-load path directly

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert construct_count["n"] == 1, (
        f"model constructed {construct_count['n']} times concurrently — "
        "a race, not a lazy singleton")
