import sqlite3

import pytest

from agentd.memory.durable_store import DurableMemoryStore, embedding_hash
from tests.test_memory_durable_store import CTX, OTHER, memory


def publish(store, entry, vector, context=CTX, model="model-a"):
    return store.publish_embedding(
        entry.id, context, vector, model=model, expected_hash=embedding_hash(entry)
    )


def test_knn_filters_scope_and_retirement_before_k(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-a", dimensions=2)
    target = store.put(memory(content="Eligible distant match"), CTX, origin="agent_nomination")
    assert publish(store, target, [3.0, 4.0])
    for i in range(8):
        foreign = store.put(
            memory(
                content=f"Foreign {i}", scope_id=OTHER.repo_id, source_thread_id=OTHER.thread_id
            ),
            OTHER,
            origin="agent_nomination",
        )
        assert publish(store, foreign, [0.0, 0.0], OTHER)
        retired = store.put(memory(content=f"Retired {i}"), CTX, origin="agent_nomination")
        assert publish(store, retired, [0.0, 0.0])
        assert store.forget(retired.id, CTX, origin="user_requested", reason="test retirement")
    found = store.search_semantic([0.0, 0.0], CTX, model="model-a", k=1)
    assert [(entry.id, distance) for entry, distance in found] == [(target.id, 5.0)]
    assert store.search_semantic([0.0, 0.0], CTX, model="wrong-model", k=1) == []
    store.close()


def test_stale_embedding_revision_retirement_and_scope(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-a", dimensions=2)
    old = store.put(memory(), CTX, origin="agent_nomination")
    assert store.pending_embeddings(CTX)[0].id == old.id
    revised = store.revise(
        old.id, {"content": "Corrected reusable claim"}, CTX, origin="curator", reason="evidence"
    )
    assert not publish(store, old, [1.0, 0.0])
    assert not publish(store, revised, [1.0, 0.0], OTHER)
    assert publish(store, revised, [1.0, 0.0])
    assert store.get(old.id, CTX).embedding_state == "done"
    assert store.pending_embeddings(CTX) == []
    detail_only = store.revise(
        old.id, {"detail": "More rationale"}, CTX, origin="curator", reason="details"
    )
    assert detail_only.embedding_state == "done"
    assert store.search_semantic([1.0, 0.0], CTX, model="model-a")[0][0].detail == "More rationale"
    with pytest.raises(ValueError, match="finite"):
        publish(store, detail_only, [float("nan"), 0.0])
    # A failed vector write rolls back its preceding delete.
    assert store.search_semantic([1.0, 0.0], CTX, model="model-a")[0][0].id == old.id
    store.forget(old.id, CTX, origin="user_requested", reason="forget")
    assert not publish(store, revised, [1.0, 0.0])
    store.close()


def test_shared_model_and_dimension_change_reject_old_backend_results(tmp_path):
    path = tmp_path / "shared.db"
    old_backend = DurableMemoryStore(path, embedding_model="model-a", dimensions=2)
    entry = old_backend.put(memory(), CTX, origin="agent_nomination")
    assert publish(old_backend, entry, [1.0, 0.0])
    new_backend = DurableMemoryStore(path, embedding_model="model-b", dimensions=3)
    assert old_backend.search_semantic([1.0, 0.0], CTX, model="model-a") == []
    assert not publish(old_backend, entry, [1.0, 0.0])
    assert new_backend.pending_embeddings(CTX)[0].id == entry.id
    assert new_backend.search_lexical("transactions", CTX)[0][0].id == entry.id
    assert publish(new_backend, entry, [1.0, 0.0, 0.0], model="model-b")
    assert new_backend.search_semantic([1.0, 0.0, 0.0], CTX, model="model-b")[0][0].id == entry.id
    old_backend.close()
    new_backend.close()


def test_extension_failure_and_recovery_keep_keyword_writes(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    original = DurableMemoryStore(path, embedding_model="model-a", dimensions=2)
    old = original.put(memory(), CTX, origin="agent_nomination")
    assert publish(original, old, [1.0, 0.0])
    with monkeypatch.context() as patch:
        patch.setattr(DurableMemoryStore, "_load_vec", lambda self: False)
        degraded = DurableMemoryStore(path)
        assert degraded.search_semantic([1.0, 0.0], CTX, model="model-a") == []
        degraded.forget(old.id, CTX, origin="user_requested", reason="extension unavailable")
        new = degraded.put(memory(content="Pending offline claim"), CTX, origin="agent_nomination")
        assert degraded.search_lexical("offline", CTX)[0][0].id == new.id
        assert not publish(degraded, new, [1.0, 0.0])
        # Another process with the extension loaded must not search stale live metadata.
        assert original.search_semantic([1.0, 0.0], CTX, model="model-a") == []
        assert not publish(original, new, [1.0, 0.0])
        degraded.close()
    original.close()
    recovered = DurableMemoryStore(path)
    assert recovered.search_semantic([1.0, 0.0], CTX, model="model-a") == []
    assert [m.id for m in recovered.pending_embeddings(CTX)] == [new.id]
    assert publish(recovered, new, [1.0, 0.0])
    assert recovered.search_semantic([1.0, 0.0], CTX, model="model-a")[0][0].id == new.id
    recovered.close()


def test_loaded_extension_without_schema_features_keeps_keyword_store(tmp_path, monkeypatch):
    def unsupported(self, dimensions):
        raise sqlite3.OperationalError("unsupported partition metadata")

    monkeypatch.setattr(DurableMemoryStore, "_create_vectors", unsupported)
    store = DurableMemoryStore(tmp_path / "m.db", embedding_model="model-a", dimensions=2)
    entry = store.put(memory(), CTX, origin="agent_nomination")
    assert store.search_lexical("transaction", CTX)[0][0].id == entry.id
    assert not publish(store, entry, [1.0, 0.0])
    assert store.search_semantic([1.0, 0.0], CTX, model="model-a") == []
    store.close()
