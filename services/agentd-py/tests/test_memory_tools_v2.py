import json

import pytest

from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.recall_v2 import CandidateSearch, RecallCoordinator, Signals, catalog_notice
from agentd.memory.reranker import Reranker
from agentd.memory.scope import ScopeContext
from agentd.memory.tool_source_v2 import DurableMemoryToolSource
from tests.test_memory_durable_store import CTX, OTHER, memory


def payload(output):
    return json.loads(output.output)


@pytest.mark.asyncio
async def test_immediate_save_read_forget_and_foreign_statuses(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    tools = DurableMemoryToolSource(store, CTX, source_mseq=lambda: 3)
    args = {
        "content": "Keep memory writes atomic",
        "topic": "memory",
        "applies_when": "when writing",
        "kind": "procedural",
        "origin": "user_requested",
        "scope": "thread",
        "detail": "Evidence",
    }
    saved = payload(await tools.execute("remember", args))
    entry = payload(await tools.execute("read_memory", {"id": saved["id"]}))
    assert entry["status"] == "ok" and entry["entry"]["detail"] == "Evidence"
    assert entry["entry"]["scope_id"] == CTX.thread_id
    assert entry["entry"]["source_message_id"] == 3 and entry["entry"]["provenance"] == "user_said"
    sibling = DurableMemoryToolSource(store, ScopeContext(CTX.repo_id, "sibling"))
    assert payload(await sibling.execute("read_memory", {"id": saved["id"]})) == {
        "status": "not_found"
    }
    assert (
        payload(await tools.execute("forget", {"id": saved["id"], "reason": "user asks"}))["status"]
        == "ok"
    )
    assert payload(await tools.execute("read_memory", {"id": saved["id"]}))["status"] == "retired"


@pytest.mark.asyncio
async def test_children_have_only_read_search_and_source_failures_are_honest(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    child = DurableMemoryToolSource(store, CTX, allow_writes=False)
    assert {d.name for d in child.definitions()} == {"read_memory", "search_memory"}
    assert (await child.execute("remember", {})).is_error
    tools = DurableMemoryToolSource(store, CTX)
    assert (
        await tools.execute(
            "remember",
            {
                "origin": "agent_nomination",
                "content": "Claim",
            },
        )
    ).is_error
    assert (
        payload(await DurableMemoryToolSource(None, CTX).execute("search_memory", {}))["status"]
        == "unavailable"
    )


@pytest.mark.asyncio
async def test_scoped_exact_filters_and_pagination_cursor(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    for i in range(18):
        store.put(
            memory(content=f"Transaction {i}", topic="writes"), CTX, origin="agent_nomination"
        )
    store.put(
        memory(
            content="Foreign",
            scope_id=OTHER.repo_id,
            source_thread_id=OTHER.thread_id,
            topic="writes",
        ),
        OTHER,
        origin="agent_nomination",
    )
    tools = DurableMemoryToolSource(store, CTX)
    first = payload(
        await tools.execute(
            "search_memory",
            {
                "topic": "writes",
                "file": "agentd/memory/store.py",
            },
        )
    )
    second = payload(
        await tools.execute(
            "search_memory",
            {
                "topic": "writes",
                "file": "agentd/memory/store.py",
                "cursor": first["cursor"],
            },
        )
    )
    assert len(first["entries"]) == 15 and len(second["entries"]) == 3
    assert "false positives" in first["disclaimer"]
    assert first["selection_method"] == "scope listing"
    assert not set(entry["id"] for entry in first["entries"]) & set(
        entry["id"] for entry in second["entries"]
    )
    assert all("Foreign" not in entry["catalog"] for entry in first["entries"] + second["entries"])
    assert (
        payload(
            await tools.execute(
                "search_memory",
                {
                    "topic": "other",
                    "cursor": first["cursor"],
                },
            )
        )["status"]
        == "unavailable"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 8, 9])
async def test_small_catalogs_skip_bge_for_injection_and_explicit_search(tmp_path, count):
    store = DurableMemoryStore(tmp_path / "m.db")
    entries = [
        store.put(memory(content=f"Transaction rule {i}"), CTX, origin="agent_nomination")
        for i in range(count)
    ]
    calls = []

    def score(pairs):
        calls.append(pairs)
        return [0.001] * len(pairs)

    search = CandidateSearch(store)
    catalogs = RecallCoordinator(search, ranker=Reranker(scorer=score))
    catalogs.begin("r", "t", Signals("transaction"))
    message, trace = await catalogs.trigger("r", CTX, [])
    tools = DurableMemoryToolSource(store, CTX, search=search, catalogs=catalogs)
    found = payload(await tools.execute("search_memory", {"query": "transaction"}))
    assert set(trace.delivered) == {entry.id for entry in entries}
    assert {entry["id"] for entry in found["entries"]} == set(trace.delivered)
    assert len(calls) == (2 if count > 8 else 0)
    assert ("BGE ordering" in found["selection_method"]) == (count > 8)
    assert bool(trace.scores) == (count > 8)
    if count:
        assert message["content"].startswith(found["disclaimer"])


@pytest.mark.asyncio
async def test_explicit_search_shares_bge_catalog_and_disclaimer_with_injection(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    entries = [
        store.put(
            memory(content=f"Transaction rule {i}", detail=f"Detailed evidence {i}"),
            CTX,
            origin="agent_nomination",
        )
        for i in range(18)
    ]
    scores = {entry.content: index / 1000 for index, entry in enumerate(entries)}
    ranker = Reranker(scorer=lambda pairs: [scores[body.splitlines()[0]] for _, body in pairs])
    search = CandidateSearch(store)
    catalogs = RecallCoordinator(search, ranker=ranker)
    tools = DurableMemoryToolSource(store, CTX, search=search, catalogs=catalogs)
    first = payload(await tools.execute("search_memory", {"query": "transaction"}))
    assert catalogs.turns == {}  # An explicit search does not inject or spend a turn trigger.
    assert len(first["entries"]) == 10
    assert first["selection_method"] == "keyword + BGE ordering"
    assert first["disclaimer"] == catalog_notice(first["selection_method"])
    assert "applies-when" in first["disclaimer"] and "read_memory(id)" in first["disclaimer"]
    assert "Detailed evidence" not in json.dumps(first)
    catalogs.begin("r", "t", Signals("transaction"))
    message, trace = await catalogs.trigger("r", CTX, [])
    assert [entry["id"] for entry in first["entries"]] == trace.delivered
    assert message["content"].startswith(first["disclaimer"])
    second = payload(
        await tools.execute(
            "search_memory",
            {
                "query": "transaction",
                "cursor": first["cursor"],
            },
        )
    )
    assert len(second["entries"]) == 8 and second["cursor"] is None
    assert not {entry["id"] for entry in first["entries"]} & {
        entry["id"] for entry in second["entries"]
    }


@pytest.mark.asyncio
async def test_search_cursor_rejects_changed_revisions_and_empty_search_has_disclaimer(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    tools = DurableMemoryToolSource(store, CTX)
    empty = payload(await tools.execute("search_memory", {"query": "unrelated"}))
    assert empty["status"] == "empty" and "false positives" in empty["disclaimer"]
    entries = [
        store.put(memory(content=f"Transaction rule {i}"), CTX, origin="agent_nomination")
        for i in range(18)
    ]
    first = payload(await tools.execute("search_memory", {"query": "transaction"}))
    assert len(first["entries"]) == 15 and first["selection_method"] == "keyword"
    store.revise(
        entries[0].id,
        {"detail": "Corrected evidence"},
        CTX,
        origin="user_requested",
        reason="correction",
    )
    changed = payload(
        await tools.execute(
            "search_memory",
            {
                "query": "transaction",
                "cursor": first["cursor"],
            },
        )
    )
    assert changed["status"] == "unavailable" and "changed" in changed["error"]


@pytest.mark.asyncio
async def test_user_correction_supersedes_same_scope_and_protects_origin(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    old = store.put(memory(), CTX, origin="agent_nomination")
    tools = DurableMemoryToolSource(store, CTX, source_mseq=lambda: 3)
    args = {
        "content": "Use the corrected procedure",
        "topic": "writes",
        "applies_when": "when writing",
        "kind": "procedural",
        "origin": "agent_nomination",
        "supersedes": old.id,
    }
    assert (await tools.execute("remember", args)).is_error
    corrected = payload(await tools.execute("remember", {**args, "origin": "user_requested"}))
    retired = payload(await tools.execute("read_memory", {"id": old.id}))
    assert retired["status"] == "retired" and retired["replacement_id"] == corrected["id"]
