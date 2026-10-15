import asyncio
import threading

import pytest

from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.embedding import EmbeddingWriter
from tests.test_memory_durable_store import CTX, memory


@pytest.mark.asyncio
async def test_waits_for_readiness_then_drains_and_learns_dimensions(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-a")
    entry = store.put(memory(), CTX, origin="agent_nomination")
    ready = False
    calls = []

    def encode(texts):
        calls.extend(texts)
        return [[1.0, 0.0] for text in texts]

    writer = EmbeddingWriter(store, "model-a", encode=encode, ready=lambda: ready)
    assert await writer.drain(CTX) == 0 and calls == []
    ready = True
    assert await writer.drain(CTX) == 1
    assert calls == [entry.embedding_text]
    assert store.embedding_configuration() == ("model-a", 2)
    assert store.search_semantic([1.0, 0.0], CTX, model="model-a")[0][0].id == entry.id
    reopened = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-a")
    assert reopened.embedding_configuration() == ("model-a", 2)
    assert reopened.search_semantic([1.0, 0.0], CTX, model="model-a")[0][0].id == entry.id
    reopened.close()
    assert await writer.drain(CTX) == 0
    store.close()


@pytest.mark.asyncio
async def test_revision_during_encoding_cannot_publish_stale_text(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-a", dimensions=2)
    entry = store.put(memory(), CTX, origin="agent_nomination")
    started, release = threading.Event(), threading.Event()

    def encode(texts):
        started.set()
        assert release.wait(2)
        return [[1.0, 0.0]]

    writer = EmbeddingWriter(store, "model-a", encode=encode, ready=lambda: True)
    task = asyncio.create_task(writer.drain(CTX))
    assert await asyncio.to_thread(started.wait, 2)
    store.revise(
        entry.id,
        {"content": "Changed while the model was running"},
        CTX,
        origin="curator",
        reason="new evidence",
    )
    release.set()
    assert await task == 0
    assert store.pending_embeddings(CTX)[0].revision == 2
    assert store.search_semantic([1.0, 0.0], CTX, model="model-a") == []
    store.close()


@pytest.mark.asyncio
async def test_old_model_and_failed_encoder_do_not_repeatedly_consume_cpu(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-b", dimensions=2)
    store.put(memory(), CTX, origin="agent_nomination")
    calls = []

    def broken(texts):
        calls.append(texts)
        raise RuntimeError("model unavailable")

    stale = EmbeddingWriter(store, "model-a", encode=broken, ready=lambda: True)
    assert await stale.drain(CTX) == 0 and calls == []
    assert not store.configure_embeddings("model-a", 3, expected_model="model-a")
    assert store.embedding_configuration() == ("model-b", 2)
    writer = EmbeddingWriter(store, "model-b", encode=broken, ready=lambda: True)
    with pytest.raises(RuntimeError, match="model unavailable"):
        await writer.drain(CTX)
    assert await writer.drain(CTX) == 0 and len(calls) == 1
    store.close()
