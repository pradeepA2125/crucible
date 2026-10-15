import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.models import DurableMemory
from agentd.memory.scope import ScopeContext

CTX = ScopeContext("git:host/repo", "thread")
OTHER = ScopeContext("git:host/other", "other")


def memory(**changes):
    return DurableMemory.model_validate(
        {
            "scope_kind": "repo",
            "scope_id": CTX.repo_id,
            "kind": "procedural",
            "content": "Use a scoped transaction for memory changes",
            "topic": "memory writes",
            "applies_when": "when updating durable memory",
            "provenance": "tool_verified",
            "source_thread_id": CTX.thread_id,
            "source_message_id": 1,
            "entities": ["agentd/memory/store.py"],
            **changes,
        }
    )


def test_fresh_reopen_scope_and_indexes(tmp_path):
    path = tmp_path / "new.sqlite3"
    store = DurableMemoryStore(path)
    entry = store.put(memory(), CTX, origin="agent_nomination")
    assert store.get(entry.id, OTHER) is None
    assert store.get("absent", OTHER) is None
    assert store.list_memories(OTHER) == []
    assert store.search_lexical("transaction OR --memory", OTHER) == []
    assert store.search_entities(entry.entities, OTHER) == []
    assert store.audit(entry.id, OTHER) == []
    assert store.replacement_chain(entry.id, OTHER) == []
    assert store.search_lexical("transactions", CTX)[0][0].id == entry.id  # porter stemming
    assert store.search_lexical("memory writes", CTX)[0][0].id == entry.id  # topic/applicability
    assert store.search_entities(entry.entities, CTX)[0].id == entry.id
    store.close()
    reopened = DurableMemoryStore(path)
    assert reopened.get(entry.id, CTX) == entry
    assert reopened._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    reopened.close()


def test_no_legacy_import_or_mutation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE memories(id TEXT PRIMARY KEY,content TEXT)")
        conn.execute("INSERT INTO memories VALUES('legacy','old status')")
    with pytest.raises(ValueError, match="not migrated"):
        DurableMemoryStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM memories").fetchall() == [("legacy", "old status")]
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    fresh = DurableMemoryStore(tmp_path / "fresh.sqlite3")
    assert fresh.list_memories(CTX) == []
    fresh.close()


def test_claim_bounds_and_source_rules(tmp_path):
    with pytest.raises(ValidationError):
        memory(content=" " * 10)
    with pytest.raises(ValidationError):
        memory(content="x" * 141)
    with pytest.raises(ValidationError):
        memory(detail="x" * 2001)
    store = DurableMemoryStore(tmp_path / "m.db")
    with pytest.raises(ValidationError):
        store.put(memory().model_copy(update={"content": "x" * 141}), CTX,
                  origin="agent_nomination")
    with pytest.raises(ValueError, match="scope"):
        store.put(memory(scope_id=OTHER.repo_id), CTX, origin="agent_nomination")
    with pytest.raises(ValueError, match="source"):
        store.put(memory(source_thread_id="foreign"), CTX, origin="curator")
    with pytest.raises(ValueError, match="User-scope"):
        store.put(memory(scope_kind="user", scope_id="user"), CTX, origin="agent_nomination")
    with pytest.raises(ValueError, match="episodic"):
        store.put(memory(kind="episodic"), CTX, origin="curator")
    user = store.put(
        memory(scope_kind="user", scope_id="user", provenance="user_said"),
        CTX,
        origin="user_requested",
    )
    assert store.get(user.id, OTHER) is not None
    with pytest.raises(ValueError, match="user-said"):
        store.revise(user.id, {"detail": "rewrite"}, CTX, origin="curator", reason="automatic")
    with pytest.raises(ValueError, match="user request"):
        store.forget(user.id, CTX, origin="curator", reason="automatic")
    store.close()


def test_dedup_is_per_live_scope_and_concurrent(tmp_path):
    path = tmp_path / "shared.db"
    store = DurableMemoryStore(path)
    first = store.put(memory(), CTX, origin="agent_nomination")
    duplicate = store.put(
        memory(content="  Use a scoped transaction for memory changes  "),
        CTX,
        origin="agent_nomination",
    )
    assert duplicate.id == first.id
    foreign = store.put(
        memory(scope_id=OTHER.repo_id, source_thread_id=OTHER.thread_id),
        OTHER,
        origin="agent_nomination",
    )
    assert foreign.id != first.id
    store.close()

    def write(_):
        connection = DurableMemoryStore(path)
        try:
            return connection.put(
                memory(content="Concurrent duplicate"), CTX, origin="agent_nomination"
            ).id
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(write, range(2)))) == 1


def test_exact_dedup_preserves_identifiers_and_user_confirmation(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    inferred = store.put(
        memory(content="Use Foo", provenance="agent_inferred"), CTX, origin="agent_nomination"
    )
    distinct = store.put(memory(content="Use foo"), CTX, origin="agent_nomination")
    assert distinct.id != inferred.id
    confirmed = store.put(
        memory(content="Use Foo", provenance="user_said", source_message_id=3),
        CTX,
        origin="user_requested",
    )
    assert confirmed.id == inferred.id
    assert confirmed.revision == 2
    assert confirmed.source_message_id == 3
    assert confirmed.provenance == "user_said"
    assert store.audit(confirmed.id, CTX)[-1].action == "confirm"
    with pytest.raises(ValueError, match="user-said"):
        store.revise(
            confirmed.id, {"content": "Use Baz"}, CTX, origin="curator", reason="inferred"
        )
    store.close()


def test_correction_forget_and_audit_are_atomic(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    entry = store.put(memory(), CTX, origin="agent_nomination")
    rewritten = store.revise(
        entry.id,
        {"content": "Use WAL for concurrent writers"},
        CTX,
        origin="curator",
        reason="new evidence",
    )
    assert rewritten.revision == 2
    with pytest.raises(ValueError, match="audit reason"):
        store.forget(entry.id, CTX, origin="user_requested", reason="")
    assert store.get(entry.id, CTX).valid_to is None
    assert store.search_lexical("transaction", CTX) == []
    assert store.search_lexical("WAL", CTX)[0][0].id == entry.id
    assert store.forget(entry.id, CTX, origin="user_requested", reason="user correction")
    assert store.get(entry.id, CTX).valid_to is not None
    assert store.list_memories(CTX) == []
    assert store.search_lexical("WAL", CTX) == []
    assert store.search_entities(entry.entities, CTX) == []
    audit = store.audit(entry.id, CTX)
    assert [a.action for a in audit] == ["save", "rewrite", "forget"]
    assert audit[1].old.content == entry.content
    assert audit[1].new.content == rewritten.content
    assert audit[1].reason == "new evidence"
    store.close()


def test_supersede_rollback_scope_protection_and_real_replacement(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    old = store.put(memory(content="Old claim"), CTX, origin="agent_nomination")
    with pytest.raises(ValueError, match="same scope"):
        store.supersede(
            old.id,
            memory(scope_kind="thread", scope_id=CTX.thread_id),
            CTX,
            origin="curator",
            reason="bad",
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.supersede(old.id, memory(id=old.id), CTX, origin="curator", reason="bad id")
    assert store.get(old.id, CTX).valid_to is None
    assert len(store.audit(old.id, CTX)) == 1
    existing = store.put(memory(content="Existing claim"), CTX, origin="agent_nomination")
    actual = store.supersede(
        old.id, memory(content="Existing claim"), CTX, origin="curator", reason="merged"
    )
    assert actual.id == existing.id
    assert store.get(old.id, CTX).superseded_by == existing.id
    assert store.audit(old.id, CTX)[-1].new.superseded_by == existing.id
    assert [m.id for m in store.replacement_chain(existing.id, CTX)] == [old.id, existing.id]
    assert store.supersede(old.id, memory(), CTX, origin="curator", reason="retired") is None
    store.close()


def test_rewind_only_retires_scoped_source_span(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    keep = store.put(memory(content="Earlier", source_message_id=1), CTX, origin="agent_nomination")
    drop = store.put(memory(content="Later", source_message_id=5), CTX, origin="agent_nomination")
    other = store.put(
        memory(content="Other thread", source_thread_id=OTHER.thread_id, scope_id=OTHER.repo_id),
        OTHER,
        origin="agent_nomination",
    )
    assert store.retire_since(OTHER.thread_id, 1, CTX, reason="rewind") == 0
    assert store.retire_since(CTX.thread_id, 5, CTX, reason="rewind") == 1
    assert store.get(keep.id, CTX).valid_to is None
    assert store.get(drop.id, CTX).valid_to is not None
    assert store.get(other.id, OTHER).valid_to is None
    assert store.audit(drop.id, CTX)[-1].action == "rewind"
    store.close()


def test_thread_scope_and_corrupt_replacement_cycle_cannot_escape(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    sibling = ScopeContext(CTX.repo_id, "sibling")
    private = store.put(
        memory(scope_kind="thread", scope_id=CTX.thread_id), CTX, origin="agent_nomination"
    )
    assert store.get(private.id, sibling) is None
    assert store.search_lexical("memory", sibling) == []
    assert store.search_entities(private.entities, sibling) == []
    assert store.replacement_chain(private.id, sibling) == []
    first = store.put(memory(content="First claim"), CTX, origin="agent_nomination")
    second = store.supersede(
        first.id, memory(content="Second claim"), CTX, origin="curator", reason="corrected"
    )
    # Simulate corrupt persisted metadata; readers must terminate without revealing a
    # foreign replacement or following a cyclic chain forever.
    with store._conn:
        store._conn.execute(
            "UPDATE memories SET superseded_by=? WHERE id=?", (first.id, second.id)
        )
    assert {entry.id for entry in store.replacement_chain(first.id, CTX)} == {first.id, second.id}
    with store._conn:
        store._conn.execute(
            "UPDATE memories SET superseded_by=? WHERE id=?", (private.id, first.id)
        )
    assert store.get(first.id, sibling).superseded_by is None
    chain_ids = {entry.id for entry in store.replacement_chain(first.id, sibling)}
    assert chain_ids <= {first.id, second.id}
    store.close()
