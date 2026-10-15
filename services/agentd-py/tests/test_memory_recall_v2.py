import json
import threading

import pytest

from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.recall_context import ContextRanking
from agentd.memory.recall_v2 import (
    CandidateSearch,
    RecallCoordinator,
    Signals,
    shown_ids,
    working_items,
)
from agentd.memory.reranker import Reranker
from tests.test_memory_durable_store import CTX, OTHER, memory


class Ranker:
    available = True

    def __init__(self, score):
        self.callback = score
        self.failures = []

    def score_context(self, signals, documents):
        return self.callback(signals, documents)

    def fail(self, exc):
        self.available = False
        self.failures.append(exc)


def seed_ordering_pool(store):
    # Exercise ordering-specific regressions above the small-catalog skip boundary.
    for index in range(8):
        store.put(
            memory(content=f"Memory ordering fixture {index}"), CTX, origin="agent_nomination"
        )


@pytest.mark.asyncio
async def test_existing_bge_formatter_orders_without_rejecting_low_scores(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    seed_ordering_pool(store)
    entry = store.put(
        memory(detail="The entire transaction must commit atomically."),
        CTX,
        origin="agent_nomination",
    )
    seen = []

    def scorer(pairs):
        seen.extend(pairs)
        return [0.00001 for _ in pairs]

    coordinator = RecallCoordinator(CandidateSearch(store), ranker=Reranker(scorer=scorer))
    coordinator.begin("r", "turn", Signals("memory", "prior answer", "recap"))
    message, trace = await coordinator.trigger("r", CTX, [])
    assert entry.id in trace.delivered and trace.verdicts == {}
    assert trace.selection_method == "keyword + BGE ordering"
    assert seen[0][0].startswith("Thread recap:")
    assert "Current request:" in seen[0][0] and any("commit atomically" in body for _, body in seen)
    assert entry.applies_when in message["content"]
    assert trace.rerank_input["max_pair_tokens"] <= 512


@pytest.mark.asyncio
async def test_bge_nonfinite_score_degrades_to_a_catalog(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    seed_ordering_pool(store)
    entry = store.put(memory(), CTX, origin="agent_nomination")
    coordinator = RecallCoordinator(
        CandidateSearch(store), ranker=Reranker(scorer=lambda pairs: [float("nan")] * len(pairs))
    )
    coordinator.begin("r", "turn", Signals("memory"))
    _, trace = await coordinator.trigger("r", CTX, [])
    assert entry.id in trace.delivered and trace.selection_method == "keyword"
    assert trace.scores == {} and "invalid scores" in trace.error


@pytest.mark.asyncio
async def test_candidate_union_scopes_and_keyword_context_signals(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    requested = store.put(memory(content="Transactions need WAL"), CTX, origin="agent_nomination")
    related = store.put(memory(content="Thread recap logging"), CTX, origin="agent_nomination")
    foreign = store.put(
        memory(
            scope_id=OTHER.repo_id,
            source_thread_id=OTHER.thread_id,
            content="Transactions foreign",
        ),
        OTHER,
        origin="agent_nomination",
    )
    search = CandidateSearch(store)
    candidates = await search.search(Signals("transactions", "", "logging"), CTX)
    assert {item.memory.id for item in candidates} == {requested.id, related.id}
    assert foreign.id not in {item.memory.id for item in candidates}
    candidates = await search.search(Signals(""), CTX, requested.entities, {related.id})
    assert [item.memory.id for item in candidates] == [requested.id]
    assert candidates[0].sources == {"entity"}


@pytest.mark.asyncio
async def test_keyword_catalog_cap_disclaimer_and_dedup_without_any_judge(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    for index in range(20):
        store.put(
            memory(content=f"Transaction lesson {index}", detail="FULL PRIVATE DETAIL"),
            CTX,
            origin="agent_nomination",
        )
    coordinator = RecallCoordinator(CandidateSearch(store))
    coordinator.begin("main", "user-message", Signals("transaction"))
    message, trace = await coordinator.trigger("main", CTX, [])
    assert len(trace.delivered) == 15 and trace.judge == "main_model"
    assert trace.selection_method == "keyword" and trace.verdicts == {} and trace.scores == {}
    assert shown_ids([message]) == set(trace.delivered)
    assert message["content"].count("[m:") == 15
    assert "false positives" in message["content"] and "when:" in message["content"]
    assert (
        "read_memory(id)" in message["content"] and "FULL PRIVATE DETAIL" not in message["content"]
    )
    assert message["_evidence"]["catalog_only"] is True
    assert await coordinator.trigger("main", CTX, [message]) == (None, None)
    coordinator.begin("main", "next", Signals("transaction"))
    _, next_trace = await coordinator.trigger("main", CTX, [message])
    assert len(next_trace.delivered) == 5 and not set(next_trace.delivered) & set(trace.delivered)


@pytest.mark.asyncio
async def test_no_candidates_or_disabled_notice_has_no_catalog(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    coordinator = RecallCoordinator(CandidateSearch(store))
    coordinator.begin("r", "first", Signals("no matches"))
    message, trace = await coordinator.trigger("r", CTX, [])
    assert message is None and trace.status == "no_candidates"
    coordinator.begin("r", "notice", Signals("memory"), allow_recall=False)
    assert await coordinator.trigger("r", CTX, []) == (None, None)


@pytest.mark.asyncio
async def test_bge_only_orders_with_full_details_and_applies_when_no_score_gate(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    entries = [
        store.put(
            memory(
                content=f"Transaction rule {i}",
                detail=f"Full constraint {i}",
                applies_when="SQLite transaction work",
            ),
            CTX,
            origin="agent_nomination",
        )
        for i in range(16)
    ]
    values = {entry.content: index / 1000 for index, entry in enumerate(entries)}
    observed = []

    def score(signals, memories):
        observed.append((threading.get_ident(), signals, memories))
        return ContextRanking(
            query="bounded context",
            truncated_signals=[],
            truncated_documents=[],
            max_pair_tokens=512,
            scores=[values[m.splitlines()[0]] for m in memories],
        )

    coordinator = RecallCoordinator(CandidateSearch(store), ranker=Ranker(score))
    coordinator.begin("r", "human", Signals("transaction", "⚠️ Usage limit reached.", "recap"))
    message, trace = await coordinator.trigger("r", CTX, [])
    assert trace.delivered == [entry.id for entry in entries[::-1]][:10]
    assert trace.verdicts == {}  # A catalog offer is not a relevance verdict.
    assert (
        trace.rule == "candidate_catalog_v1" and trace.selection_method == "keyword + BGE ordering"
    )
    assert "BGE ordering" in message["content"] and trace.signals["C"] == ""
    assert observed[0][0] != threading.get_ident()
    assert all("Full constraint" in m for m in observed[0][2])
    assert max(score["combined"] for score in trace.scores.values()) < 0.65


@pytest.mark.asyncio
async def test_bge_failure_degrades_to_catalog_and_keeps_keyword_midturn_refresh(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    seed_ordering_pool(store)
    entry = store.put(memory(), CTX, origin="agent_nomination")

    def fail(signals, memories):
        raise RuntimeError("service failed")

    ranker = Ranker(fail)
    coordinator = RecallCoordinator(CandidateSearch(store), ranker=ranker)
    coordinator.begin("r", "human", Signals("memory"))
    message, trace = await coordinator.trigger("r", CTX, [])
    assert entry.id in trace.delivered and trace.selection_method == "keyword"
    assert "service failed" in trace.error and coordinator.ranker is None
    history = [
        {"role": "tool_result", "content": "observation", "delivered_read": {"path": "src/new.py"}}
    ]
    _, mid = await coordinator.trigger("r", CTX, history, mid_turn=True)
    assert mid is not None and "BGE" not in mid.selection_method
    assert coordinator.ranker is None


@pytest.mark.asyncio
async def test_compaction_reappends_only_live_current_catalog_once(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    entries = [
        store.put(memory(content=f"Memory rule {i}"), CTX, origin="agent_nomination")
        for i in range(12)
    ]
    coordinator = RecallCoordinator(CandidateSearch(store))
    coordinator.begin("r", "human", Signals("memory"))
    message, trace = await coordinator.trigger("r", CTX, [])
    assert len(trace.delivered) == 12
    assert coordinator.after_compaction("r", CTX, [message]) is None
    store.forget(entries[0].id, CTX, origin="user_requested", reason="user forgot")
    again = coordinator.after_compaction("r", CTX, [])
    assert len(shown_ids([again])) == 11 and entries[0].id not in shown_ids([again])
    assert "false positives" in again["content"]
    assert coordinator.after_compaction("r", CTX, []) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["retire", "revise"])
async def test_retirement_and_revision_during_ordering_are_not_offered(tmp_path, change):
    import asyncio

    store = DurableMemoryStore(tmp_path / "m.db")
    seed_ordering_pool(store)
    entry = store.put(memory(), CTX, origin="agent_nomination")
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def score(signals, memories):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(2)
        return ContextRanking(
            scores=[0.9] * len(memories),
            query="bounded context",
            truncated_signals=[],
            truncated_documents=[],
            max_pair_tokens=512,
        )

    coordinator = RecallCoordinator(CandidateSearch(store), ranker=Ranker(score))
    coordinator.begin("r", "human", Signals("memory"))
    task = asyncio.create_task(coordinator.trigger("r", CTX, []))
    await asyncio.wait_for(started.wait(), timeout=1)
    try:
        if change == "retire":
            store.forget(entry.id, CTX, origin="user_requested", reason="concurrent retirement")
        else:
            store.revise(
                entry.id,
                {"detail": "Corrected evidence"},
                CTX,
                origin="user_requested",
                reason="concurrent correction",
            )
    finally:
        release.set()
    message, trace = await task
    assert message is not None and entry.id not in trace.delivered
    assert len(trace.delivered) == 8
    assert trace.invalidated_ids == [entry.id]


def test_working_set_paths_symbols_and_error_signatures():
    history = [
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "type": "tool_calls",
                    "calls": [
                        {"tool": "read_file", "args": {"path": "/repo/src/foo.py"}},
                    ],
                }
            ),
        },
        {
            "role": "tool_result",
            "tool": "run_command",
            "status": "failed",
            "is_error": True,
            "content": "ImportError: missing module\nsrc/foo.py:Symbol",
        },
    ]
    assert working_items(history, "/repo") == {
        "src/foo.py",
        "src/foo.py:Symbol",
        "error:ImportError: missing module",
    }


@pytest.mark.asyncio
async def test_keyword_midturn_trigger_cap_and_no_repeated_working_set(tmp_path):
    store = DurableMemoryStore(tmp_path / "m.db")
    store.put(memory(), CTX, origin="agent_nomination")
    coordinator = RecallCoordinator(CandidateSearch(store))
    coordinator.begin("r", "human", Signals("memory"))
    await coordinator.trigger("r", CTX, [])
    history = []
    for index in range(7):
        history.append(
            {
                "role": "tool_result",
                "content": "new observation",
                "delivered_read": {"path": f"src/path{index}.py"},
            }
        )
        _, trace = await coordinator.trigger("r", CTX, history, mid_turn=True)
        assert (trace is not None) == (index < 6)
        assert await coordinator.trigger("r", CTX, history, mid_turn=True) == (None, None)
    assert coordinator.turns["r"].mid_triggers == 6


@pytest.mark.asyncio
async def test_bge_deadline_still_delivers_retrieval_catalog(tmp_path, monkeypatch):
    import asyncio
    import time

    import agentd.memory.recall_v2 as module

    original = asyncio.wait_for

    async def short_deadline(awaitable, timeout):
        return await original(awaitable, timeout=0.01)

    monkeypatch.setattr(module.asyncio, "wait_for", short_deadline)
    store = DurableMemoryStore(tmp_path / "m.db")
    seed_ordering_pool(store)
    entry = store.put(memory(), CTX, origin="agent_nomination")

    def slow(signals, memories):
        time.sleep(0.03)
        return ContextRanking(
            query="bounded context",
            truncated_signals=[],
            truncated_documents=[],
            max_pair_tokens=512,
            scores=[0.9],
        )

    coordinator = RecallCoordinator(CandidateSearch(store), ranker=Ranker(slow))
    coordinator.begin("r", "human", Signals("memory"))
    _, trace = await coordinator.trigger("r", CTX, [])
    assert entry.id in trace.delivered and "TimeoutError" in trace.error
    assert trace.selection_method == "keyword" and trace.verdicts == {}
