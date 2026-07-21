"""Fix: embedder and reranker warmup must not run concurrently.

Root cause (found live 2026-07-17 via two independent `sample` snapshots of a
wedged backend, both showing separate OS threads stuck in the identical
PyTorch MPS call chain — MetalShaderLibrary's internal shader hash table):
`build_memory_harness` spawned TWO separate daemon threads back to back
(reranker.warmup, then embedder.warmup) with no synchronization between them.
Both raced to initialize PyTorch's MPS backend for the first time
concurrently. PyTorch's MPS backend is not thread-safe for concurrent lazy
first-init — the race deadlocked the whole process (not just one turn),
confirmed reproducible 3 times in one session on real hardware.

Fix: run both warmups sequentially in ONE background thread instead of two
racing ones.
"""
from __future__ import annotations

import threading
import time

from agentd.memory.config import MemoryConfig
from agentd.memory.embedder import Embedder
from agentd.memory.harness import build_memory_harness
from agentd.memory.reranker import Reranker


class _FakeTransport:
    async def generate_text(self, *, model, system_instructions, user_payload, on_thinking=None):
        return "<summary>SUMMARY</summary>"


def test_embedder_and_reranker_warmup_do_not_race(monkeypatch, tmp_path):
    events: list[tuple[str, str]] = []
    lock = threading.Lock()

    def fake_embedder_warmup(self) -> None:
        with lock:
            events.append(("embedder", "start"))
        time.sleep(0.05)
        with lock:
            events.append(("embedder", "end"))

    def fake_reranker_warmup(self) -> None:
        with lock:
            events.append(("reranker", "start"))
        time.sleep(0.05)
        with lock:
            events.append(("reranker", "end"))

    monkeypatch.setattr(Embedder, "warmup", fake_embedder_warmup)
    monkeypatch.setattr(Reranker, "warmup", fake_reranker_warmup)

    cfg = MemoryConfig.from_env({
        "CRUCIBLE_MEMORY_ENABLED": "1",
        "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "m.sqlite3"),
        "CRUCIBLE_MEMORY_RERANKER": "1",
    })
    build_memory_harness(cfg, _FakeTransport(), "m1", workspace_path=str(tmp_path))

    deadline = time.time() + 2
    while len(events) < 4 and time.time() < deadline:
        time.sleep(0.01)

    assert events in (
        [("embedder", "start"), ("embedder", "end"),
         ("reranker", "start"), ("reranker", "end")],
        [("reranker", "start"), ("reranker", "end"),
         ("embedder", "start"), ("embedder", "end")],
    ), f"warmups must run sequentially, never overlap: {events}"
