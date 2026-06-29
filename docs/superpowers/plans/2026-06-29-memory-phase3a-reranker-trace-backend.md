# Memory Phase 3-A — Reranker + Trace + Inspect APIs (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the cross-encoder reranker, the per-turn recall trace (with persistence), the store's read-only browse helpers, and the three read-only inspect/browse/chain routes — the backend the inspector panel (Plan 3-B) consumes.

**Architecture:** A `Reranker` (local CrossEncoder, degrade-not-raise) slots into `RecallEngine` at the post-floor seam, count-gated. `recall()` keeps its signature; a new `recall_with_trace` returns `(memories, RecallTrace)` capturing per-signal scores; the controller loop persists the trace as a per-turn artifact. Three read-only GET routes serve the trace + a memory browser.

**Tech Stack:** Python 3.13, `sentence-transformers` CrossEncoder, SQLite/sqlite-vec/FTS5 (Plan 2A store), FastAPI, pytest-asyncio.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-29-memory-phase3-reranker-inspector-design.md`.
- **`recall()` signature is UNCHANGED** (`-> list[Memory]`) — the `recall()` tool and `recall_grounded` keep calling it. Only the harness `_fill_recall` switches to `recall_with_trace`. Every `_SpyRecall` test fake gains a `recall_with_trace` (update in lockstep — same breakage class as Phase-2's `prepare_turn(query=…)`).
- Reranker is **independent of `MEMORY_ENABLED`** (own flag `AI_EDITOR_MEMORY_RERANKER`, default off) and **degrades to fused order** if the model/lib is missing — recall never depends on it.
- The trace `entries` cover **all** scored candidates (incl. below-floor, `injected=false`), not just the returned `k`.
- All three routes are **read-only GETs**, gated by `is_memory_enabled()`; they self-resolve `MemoryStore(MemoryConfig.from_env(os.environ).db_path)` + workspace from `AI_EDITOR_WORKSPACE_PATH`.
- Lints clean (`ruff`, line 100 — DON'T pipe ruff through `tail`, check the exit code); `mypy agentd/memory` clean. Tests use real `tmp_path` SQLite + injected fakes (no real model in unit tests; one `@pytest.mark.slow` smoke for the real CrossEncoder if desired).
- Run from `services/agentd-py` with `source .venv/bin/activate`.

---

### Task 1: `Reranker` unit + config flags

**Files:**
- Create: `services/agentd-py/agentd/memory/reranker.py`
- Modify: `services/agentd-py/agentd/memory/config.py`
- Test: `services/agentd-py/tests/test_memory_reranker.py` (create)

**Interfaces:**
- Produces: `Reranker(model_name="BAAI/bge-reranker-base", *, scorer=None)`. `rerank(query: str, candidates: list[Memory]) -> list[tuple[Memory, float]]` (desc by cross-encoder score; degrade → input order paired with 0.0, `available=False`). `available: bool`, `warmup()`. `scorer` is an injectable `Callable[[list[tuple[str,str]]], list[float]]`. `MemoryConfig` gains `reranker_enabled: bool`, `reranker_model: str`, `rerank_min_candidates: int`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_memory_reranker.py
from datetime import UTC, datetime

from agentd.memory.config import MemoryConfig
from agentd.memory.models import Memory
from agentd.memory.reranker import Reranker


def _m(mid, content):
    now = datetime(2026, 6, 29, tzinfo=UTC)
    return Memory(id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic",
                  content=content, entities=[], importance=5, valid_from=now, valid_to=None,
                  superseded_by=None, source_kind="consolidation", source_ref="r",
                  source_seq_lo=None, source_seq_hi=None, created_at=now)


def test_rerank_reorders_by_scorer():
    cands = [_m("a", "auth flow"), _m("b", "tax compute")]
    # scorer ranks the 2nd pair higher → b should come first
    rr = Reranker(scorer=lambda pairs: [0.1, 0.9])
    out = rr.rerank("anything", cands)
    assert [m.id for m, _ in out] == ["b", "a"] and out[0][1] == 0.9


def test_rerank_degrades_to_input_order():
    def boom(pairs):
        raise RuntimeError("no model")
    rr = Reranker(scorer=boom)
    cands = [_m("a", "x"), _m("b", "y")]
    out = rr.rerank("q", cands)
    assert [m.id for m, _ in out] == ["a", "b"]  # input order preserved
    assert rr.available is False


def test_config_reranker_defaults():
    c = MemoryConfig.from_env({})
    assert c.reranker_enabled is False
    assert c.reranker_model == "BAAI/bge-reranker-base"
    assert c.rerank_min_candidates == 8
```

- [ ] **Step 2: Run → FAIL**

Run: `python -m pytest tests/test_memory_reranker.py -v`
Expected: FAIL — module `agentd.memory.reranker` missing.

- [ ] **Step 3: Implement the reranker + config**

```python
# services/agentd-py/agentd/memory/reranker.py
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from agentd.memory.models import Memory

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-reranker-base"


class Reranker:
    """Local cross-encoder reranker. Lazy load; degrade-not-raise (model absent → input
    order). `scorer` is injectable for tests (bypasses the model)."""

    def __init__(
        self, model_name: str = _DEFAULT_MODEL, *,
        scorer: Callable[[list[tuple[str, str]]], list[float]] | None = None,
    ) -> None:
        self._model_name = model_name
        self._scorer = scorer
        self._available = True
        self._model: Any = None

    @property
    def available(self) -> bool:
        return self._available

    def _score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._scorer is not None:
            return self._scorer(pairs)
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self._model_name)
        return [float(s) for s in self._model.predict(pairs)]

    def rerank(self, query: str, candidates: list[Memory]) -> list[tuple[Memory, float]]:
        if not candidates:
            return []
        try:
            scores = self._score([(query, c.content) for c in candidates])
            scored = list(zip(candidates, scores, strict=True))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored
        except Exception:  # noqa: BLE001 — degrade: recall falls back to fused order
            logger.warning("[memory] reranker unavailable for model=%s", self._model_name)
            self._available = False
            return [(c, 0.0) for c in candidates]

    def warmup(self) -> None:
        self._score([("warmup", "warmup")])
```

In `config.py`, add to `MemoryConfig` body: `reranker_enabled: bool`, `reranker_model: str`, `rerank_min_candidates: int`; and to `from_env`'s `cls(...)`:
```python
            reranker_enabled=env.get("AI_EDITOR_MEMORY_RERANKER", "").lower() in _TRUTHY,
            reranker_model=env.get("AI_EDITOR_MEMORY_RERANKER_MODEL", "BAAI/bge-reranker-base"),
            rerank_min_candidates=int(env.get("AI_EDITOR_MEMORY_RERANK_MIN_CANDIDATES", "8")),
```

- [ ] **Step 4: Run → PASS**

Run: `python -m pytest tests/test_memory_reranker.py -v` · Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/reranker.py services/agentd-py/agentd/memory/config.py services/agentd-py/tests/test_memory_reranker.py
git commit -m "feat(memory): cross-encoder Reranker + config flags"
```

---

### Task 2: `RecallTrace` models + `recall_with_trace` + reranker integration

**Files:**
- Modify: `services/agentd-py/agentd/memory/models.py`
- Modify: `services/agentd-py/agentd/memory/recall.py`
- Test: `services/agentd-py/tests/test_recall_trace.py` (create)

**Interfaces:**
- Consumes: `Reranker` (Task 1), `_minmax`/`_recency` (Plan 2C).
- Produces: `RecallTraceEntry` + `RecallTrace` (pydantic). `RecallEngine.__init__` gains `reranker: object | None = None, rerank_min_candidates: int = 8`. `RecallEngine.recall_with_trace(query, scope_kind, scope_id, k) -> tuple[list[Memory], RecallTrace]`. `recall(...)` = `(await self.recall_with_trace(...))[0]` — UNCHANGED externally. New internal `_score_candidates(memories, sem, lex, struct, now) -> list[tuple[Memory, float, dict[str,float]]]` (the per-signal breakdown); `_fuse` is refactored to derive from it so its existing tests stay green.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_recall_trace.py
import pytest

from agentd.memory.embedder import Embedder
from agentd.memory.models import RecallTrace
from agentd.memory.recall import RecallEngine
from agentd.memory.reranker import Reranker
from agentd.memory.store import MemoryStore
from tests.test_memory_store_phase2 import _mem


def _emb():
    table: dict[str, list[float]] = {}
    def enc(texts):
        out = []
        for t in texts:
            if t not in table:
                v = [0.0] * 384; v[len(table) % 384] = 1.0; table[t] = v
            out.append(table[t])
        return out
    return Embedder(encoder=enc)


@pytest.mark.asyncio
async def test_recall_with_trace_captures_signals(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    store.insert_memory(_mem("a", content="auth flow", entities=("src/auth.py",)),
                        emb.embed(["auth flow"])[0])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    mems, trace = await eng.recall_with_trace("auth", "workspace", "/ws", k=5)
    assert isinstance(trace, RecallTrace)
    assert trace.entries and set(trace.entries[0].signals) == {
        "semantic", "lexical", "structural", "importance", "recency"}
    assert trace.entries[0].injected is True and trace.reranked is False
    assert [m.id for m in mems] == [trace.entries[0].memory_id]


@pytest.mark.asyncio
async def test_recall_unchanged_returns_memories(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    store.insert_memory(_mem("a", content="auth flow", entities=()), emb.embed(["auth flow"])[0])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    out = await eng.recall("auth", "workspace", "/ws", k=5)  # same signature as before
    assert [m.id for m in out] == ["a"]


@pytest.mark.asyncio
async def test_reranker_gated_and_reorders(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    for i in range(10):
        store.insert_memory(_mem(f"m{i}", content=f"fact {i}", entities=()),
                            emb.embed([f"fact {i}"])[0])
    # scorer ranks last candidate highest → it should lead after rerank
    rr = Reranker(scorer=lambda pairs: list(range(len(pairs))))
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0,
                       reranker=rr, rerank_min_candidates=8)
    mems, trace = await eng.recall_with_trace("fact", "workspace", "/ws", k=10)
    assert trace.reranked is True
    assert trace.entries[0].rerank_score is not None


@pytest.mark.asyncio
async def test_reranker_skipped_below_gate(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    store.insert_memory(_mem("a", content="x", entities=()), emb.embed(["x"])[0])
    called = []
    rr = Reranker(scorer=lambda pairs: called.append(1) or [0.0] * len(pairs))
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0,
                       reranker=rr, rerank_min_candidates=8)
    _, trace = await eng.recall_with_trace("x", "workspace", "/ws", k=5)
    assert trace.reranked is False and called == []  # 1 candidate ≤ gate → no model call
```

- [ ] **Step 2: Run → FAIL**

Run: `python -m pytest tests/test_recall_trace.py -v` · Expected: FAIL — `RecallTrace`/`recall_with_trace` missing.

- [ ] **Step 3: Add the models**

Append to `models.py`:
```python
class RecallTraceEntry(BaseModel):
    memory_id: str
    kind: str
    content: str
    importance: int
    signals: dict[str, float]  # normalized semantic/lexical/structural/importance/recency
    fused_score: float
    rerank_score: float | None
    final_rank: int
    injected: bool


class RecallTrace(BaseModel):
    query: str
    scope_kind: str
    scope_id: str
    k: int
    floor: float
    reranked: bool
    entries: list[RecallTraceEntry]
```

- [ ] **Step 4: Refactor scoring + add `recall_with_trace`**

In `recall.py`, replace `_fuse`'s body so it derives from a signal-capturing core, and rewrite the engine to produce the trace. Add `from agentd.memory.models import Memory, RecallTrace, RecallTraceEntry`.

```python
def _score_candidates(
    memories: list[Memory], sem: dict[str, float], lex: dict[str, float],
    struct: dict[str, float], weights: tuple[float, float, float], now: datetime,
) -> list[tuple[Memory, float, dict[str, float]]]:
    if not memories:
        return []
    w_sem, w_lex, w_struct = weights
    ids = [m.id for m in memories]
    n_sem = dict(zip(ids, _minmax([sem.get(i, 0.0) for i in ids]), strict=True))
    n_lex = dict(zip(ids, _minmax([lex.get(i, 0.0) for i in ids]), strict=True))
    n_str = dict(zip(ids, _minmax([struct.get(i, 0.0) for i in ids]), strict=True))
    n_imp = dict(zip(ids, _minmax([float(m.importance) for m in memories]), strict=True))
    out: list[tuple[Memory, float, dict[str, float]]] = []
    for m in memories:
        rec = _recency(m.valid_from, now, HALF_LIFE_DAYS)
        signals = {"semantic": n_sem[m.id], "lexical": n_lex[m.id], "structural": n_str[m.id],
                   "importance": n_imp[m.id], "recency": rec}
        fused = (w_sem * n_sem[m.id] + w_lex * n_lex[m.id] + w_struct * n_str[m.id]
                 + W_IMP * n_imp[m.id] + W_REC * rec)
        out.append((m, fused, signals))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _fuse(memories, sem, lex, struct, weights, now):  # kept for existing scoring tests
    return [(m, s) for m, s, _ in _score_candidates(memories, sem, lex, struct, weights, now)]
```

Update `RecallEngine.__init__` to accept `reranker=None, rerank_min_candidates=8` (store them). Replace `recall`/`_recall` so `recall` delegates:

```python
    async def recall(self, query, scope_kind, scope_id, k) -> list[Memory]:
        memories, _ = await self.recall_with_trace(query, scope_kind, scope_id, k)
        return memories

    async def recall_with_trace(self, query, scope_kind, scope_id, k):
        try:
            return await self._recall_with_trace(query, scope_kind, scope_id, k)
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("[memory] recall failed for scope=%s", scope_id, exc_info=True)
            empty = RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id, k=k,
                                floor=self._min_score, reranked=False, entries=[])
            return [], empty

    async def _recall_with_trace(self, query, scope_kind, scope_id, k):
        sem: dict[str, float] = {}
        if self._embedder.available:
            vecs = await asyncio.to_thread(self._embedder.embed, [query])
            if vecs:
                for mid, dist in self._store.search_semantic(vecs[0], self._cand_k,
                                                             scope_kind, scope_id):
                    sem[mid] = 1.0 - (dist * dist) / 2.0
        lex = {mid: -rank for mid, rank in
               self._store.search_lexical(query, self._cand_k, scope_kind, scope_id)}
        qents = _query_entities(query)
        ids = set(sem) | set(lex)
        mems = [m for m in (self._store.get_memory(i) for i in ids) if m is not None]
        struct = {m.id: float(len(qents & set(m.entities))) for m in mems}
        scored = _score_candidates(mems, sem, lex, struct, self._weights, datetime.now(UTC))
        passing = [(m, f, sig) for m, f, sig in scored if f >= self._min_score]
        below = [(m, f, sig) for m, f, sig in scored if f < self._min_score]

        reranked = False
        rr_scores: dict[str, float] = {}
        ordered = passing
        if (self._reranker is not None and getattr(self._reranker, "available", False)
                and len(passing) > self._rerank_min):
            reranked = True
            rr = self._reranker.rerank(query, [m for m, _, _ in passing])
            rr_scores = {m.id: sc for m, sc in rr}
            order = {m.id: i for i, (m, _) in enumerate(rr)}
            ordered = sorted(passing, key=lambda t: order[t[0].id])

        injected = [m for m, _, _ in ordered[:k]]
        injected_ids = {m.id for m in injected}
        entries: list[RecallTraceEntry] = []
        for rank, (m, fused, sig) in enumerate(ordered + below):
            entries.append(RecallTraceEntry(
                memory_id=m.id, kind=m.kind, content=m.content[:160], importance=m.importance,
                signals={kk: round(vv, 4) for kk, vv in sig.items()},
                fused_score=round(fused, 4),
                rerank_score=round(rr_scores[m.id], 4) if m.id in rr_scores else None,
                final_rank=rank, injected=m.id in injected_ids))
        trace = RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id, k=k,
                            floor=self._min_score, reranked=reranked, entries=entries)
        return injected, trace
```
Keep the existing `recall_grounded` (it calls `self.recall`, still valid).

- [ ] **Step 5: Run → PASS**

Run: `python -m pytest tests/test_recall_trace.py tests/test_recall_engine.py tests/test_recall_scoring.py -v`
Expected: PASS (new + existing recall tests — `_fuse` refactor keeps them green).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/memory/models.py services/agentd-py/agentd/memory/recall.py services/agentd-py/tests/test_recall_trace.py
git commit -m "feat(memory): recall_with_trace + reranker integration (recall() unchanged)"
```

---

### Task 3: `MemoryStore` browse helpers

**Files:**
- Modify: `services/agentd-py/agentd/memory/store.py`
- Test: `services/agentd-py/tests/test_memory_store_phase2.py` (extend)

**Interfaces:**
- Produces: `list_memories(scope_kind, scope_id, kind=None, include_retired=False) -> list[Memory]`; `get_supersede_chain(memory_id) -> list[Memory]` (oldest→newest via `superseded_by`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_memory_store_phase2.py
def test_list_memories_filters(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("live"), [0.1] * 384)
    retired = _mem("dead").model_copy(update={"valid_to": _mem("dead").valid_from})
    store.insert_memory(retired, [0.1] * 384)
    epi = _mem("epi").model_copy(update={"kind": "episodic"})
    store.insert_memory(epi, [0.1] * 384)
    assert {m.id for m in store.list_memories("workspace", "/ws")} == {"live", "epi"}
    assert {m.id for m in store.list_memories("workspace", "/ws", include_retired=True)} == {
        "live", "epi", "dead"}
    assert {m.id for m in store.list_memories("workspace", "/ws", kind="episodic")} == {"epi"}


def test_supersede_chain(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("old", content="v1"), [0.1] * 384)
    store.supersede("old", _mem("new", content="v2"), [0.2] * 384)
    chain = store.get_supersede_chain("new")
    assert [m.id for m in chain] == ["old", "new"]  # oldest → newest
```

- [ ] **Step 2: Run → FAIL** · `python -m pytest tests/test_memory_store_phase2.py -k "list_memories or supersede_chain" -v`

- [ ] **Step 3: Implement**

Add to `MemoryStore`:
```python
    def list_memories(
        self, scope_kind: str, scope_id: str, kind: str | None = None,
        include_retired: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE scope_kind=? AND scope_id=?"
        args: list[object] = [scope_kind, scope_id]
        if not include_retired:
            sql += " AND valid_to IS NULL"
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY importance DESC, valid_from DESC"
        return [self._row_to_memory(r) for r in self._conn.execute(sql, args).fetchall()]

    def get_supersede_chain(self, memory_id: str) -> list[Memory]:
        # walk backward via the row that points here, then forward via superseded_by
        seen: dict[str, Memory] = {}
        cur = self.get_memory(memory_id)
        while cur is not None:  # walk back to the oldest
            prev = self._conn.execute(
                "SELECT * FROM memories WHERE superseded_by=?", (cur.id,)).fetchone()
            if prev is None:
                break
            cur = self._row_to_memory(prev)
        chain: list[Memory] = []
        while cur is not None and cur.id not in seen:
            seen[cur.id] = cur
            chain.append(cur)
            cur = self.get_memory(cur.superseded_by) if cur.superseded_by else None
        return chain
```

- [ ] **Step 4: Run → PASS** · `python -m pytest tests/test_memory_store_phase2.py -v`
- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/store.py services/agentd-py/tests/test_memory_store_phase2.py
git commit -m "feat(memory): read-only list_memories + get_supersede_chain"
```

---

### Task 4: Harness wires trace + reranker; loop persists the trace artifact

**Files:**
- Modify: `services/agentd-py/agentd/memory/models.py` (`TurnPreparation.recall_trace`)
- Modify: `services/agentd-py/agentd/memory/harness.py` (`_fill_recall` captures trace; `build_memory_harness` constructs `Reranker` when flagged)
- Modify: `services/agentd-py/agentd/chat/controller_loop.py` (persist the trace artifact)
- Test: `services/agentd-py/tests/test_memory_recall_wiring.py` (extend — fakes gain `recall_with_trace`)

**Interfaces:**
- Consumes: `recall_with_trace` (Task 2), `Reranker` (Task 1), `chat_turn_artifacts_root` (`agentd.runtime.artifacts`).
- Produces: `TurnPreparation.recall_trace: RecallTrace | None`. `_fill_recall` calls `recall_with_trace` and stores the trace on the prep. `build_memory_harness` builds a `Reranker` when `config.reranker_enabled` and passes it to `RecallEngine`. The controller loop writes `memory-recall-NN.json` to the turn artifact dir.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_memory_recall_wiring.py — and UPDATE _SpyRecall:
#   give it BOTH recall(...) and recall_with_trace(...):
#     async def recall_with_trace(self, query, scope_kind, scope_id, k):
#         self.calls += 1; self.last_query = query
#         from agentd.memory.models import RecallTrace
#         return self._mems, RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id,
#                                        k=k, floor=0.0, reranked=False, entries=[])
@pytest.mark.asyncio
async def test_prepare_turn_exposes_recall_trace(tmp_path):
    spy = _SpyRecall(mems=[])
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=spy,
                            scope_kind="workspace", scope_id="/ws")
    prep = await harness.prepare_turn([], "thread-z", query="what does X do")
    assert prep.recall_trace is not None and prep.recall_trace.query == "what does X do"
```

- [ ] **Step 2: Run → FAIL** (`_SpyRecall` lacks `recall_with_trace` / `recall_trace` missing on prep).

- [ ] **Step 3: Implement**

In `models.py` add to `TurnPreparation`: `recall_trace: "RecallTrace | None" = None` (and `from __future__ import annotations` is already present; reference by string or import `RecallTrace`).

In `harness.py` `_fill_recall`, switch the call and capture the trace onto the harness for the prep to read. Simplest: have `_fill_recall` return `(lines, trace)` and `prepare_turn` set `prep.recall_trace`:
```python
        if self._recall_engine is not None:
            lines, trace = await self._fill_recall(history, run_id, query)
            prep.recalled_memories = lines
            prep.recall_trace = trace
```
and in `_fill_recall` replace `mems = await self._recall_engine.recall(...)` with
`mems, trace = await self._recall_engine.recall_with_trace(query, self._scope_kind, self._scope_id, k=8)`,
cache `lines` as before, and `return self._recall_cache.get(run_id, []), trace` (return `None` trace when the query is empty/cached-without-recall).

In `build_memory_harness`, when `config.reranker_enabled`, build `Reranker(config.reranker_model)`, warm it in the daemon thread alongside the embedder, and pass `reranker=reranker, rerank_min_candidates=config.rerank_min_candidates` into `RecallEngine(...)`.

In `controller_loop.py`, right after `plan_context["recalled_memories"] = _prep.recalled_memories`, persist the trace:
```python
            if _prep.recall_trace is not None:
                try:
                    from agentd.runtime.artifacts import chat_turn_artifacts_root
                    tid = str(plan_context.get("artifact_thread_id") or "")
                    turn = str(plan_context.get("artifact_turn_id") or "")
                    ws = str(plan_context.get("workspace_path") or "")
                    if tid and turn:
                        out = chat_turn_artifacts_root(tid, turn, ws)
                        out.mkdir(parents=True, exist_ok=True)
                        (out / f"memory-recall-{iteration:02d}.json").write_text(
                            _prep.recall_trace.model_dump_json(indent=2), encoding="utf-8")
                except Exception:  # noqa: BLE001 — best-effort
                    logger.warning("[memory] recall-trace dump failed")
```

- [ ] **Step 4: Run → PASS**

Run: `python -m pytest tests/test_memory_recall_wiring.py tests/test_memory_harness.py -v`
Expected: PASS (updated fakes + new trace exposure).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/models.py services/agentd-py/agentd/memory/harness.py services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/tests/test_memory_recall_wiring.py
git commit -m "feat(memory): expose recall trace on prep + persist per-turn trace artifact"
```

---

### Task 5: Inspect / browse / chain routes

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_memory_routes.py` (create)

**Interfaces:**
- Consumes: `MemoryStore.list_memories`/`get_supersede_chain` (Task 3), `chat_turn_artifacts_root`, `is_memory_enabled` (`controller_factory`).
- Produces (read-only GETs, registered in `build_router`): `GET /v1/memory/inspect?thread_id=` → latest `RecallTrace` JSON or `{}`; `GET /v1/memory?scope_kind=&scope_id=&kind=&include_retired=` → `list[Memory]` JSON; `GET /v1/memory/{memory_id}/chain` → `list[Memory]`. `/v1/config` gains `memory_enabled`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_memory_routes.py
import os

from fastapi.testclient import TestClient

from agentd.chat.app_factory import build_app  # test-only app builder


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_EDITOR_MEMORY_ENABLED", "1")
    monkeypatch.setenv("AI_EDITOR_MEMORY_DB_PATH", str(tmp_path / "m.sqlite3"))
    monkeypatch.setenv("AI_EDITOR_WORKSPACE_PATH", str(tmp_path))
    return TestClient(build_app())


def test_config_reports_memory_enabled(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/v1/config").json()["memory_enabled"] is True


def test_browse_returns_memories(tmp_path, monkeypatch):
    from agentd.memory.store import MemoryStore
    from tests.test_memory_store_phase2 import _mem
    MemoryStore(tmp_path / "m.sqlite3").insert_memory(
        _mem("a").model_copy(update={"scope_id": str(tmp_path)}), [0.1] * 384)
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/memory", params={"scope_kind": "workspace", "scope_id": str(tmp_path)})
    assert r.status_code == 200 and any(m["id"] == "a" for m in r.json())


def test_inspect_soft_empty_without_trace(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/memory/inspect", params={"thread_id": "chat-none"})
    assert r.status_code == 200 and r.json().get("entries", []) == []
```

- [ ] **Step 2: Run → FAIL** (routes not registered; `memory_enabled` absent).

- [ ] **Step 3: Implement**

In `routes.py` `get_config`, add `memory_enabled`:
```python
        from agentd.chat.controller_factory import is_memory_enabled
        ...
        "memory_enabled": is_memory_enabled(),
```
Register the three routes inside `build_router` (read-only; self-resolve store + workspace from env):
```python
    @router.get("/memory")
    async def browse_memories(scope_kind: str, scope_id: str, kind: str | None = None,
                              include_retired: bool = False) -> list[dict]:
        from agentd.chat.controller_factory import is_memory_enabled
        from agentd.memory.config import MemoryConfig
        from agentd.memory.store import MemoryStore
        if not is_memory_enabled():
            return []
        st = MemoryStore(MemoryConfig.from_env(os.environ).db_path)
        return [m.model_dump(mode="json")
                for m in st.list_memories(scope_kind, scope_id, kind, include_retired)]

    @router.get("/memory/{memory_id}/chain")
    async def memory_chain(memory_id: str) -> list[dict]:
        from agentd.chat.controller_factory import is_memory_enabled
        from agentd.memory.config import MemoryConfig
        from agentd.memory.store import MemoryStore
        if not is_memory_enabled():
            return []
        st = MemoryStore(MemoryConfig.from_env(os.environ).db_path)
        return [m.model_dump(mode="json") for m in st.get_supersede_chain(memory_id)]

    @router.get("/memory/inspect")
    async def inspect_recall(thread_id: str) -> dict:
        import glob
        import json as _json
        from agentd.chat.controller_factory import is_memory_enabled
        from agentd.runtime.artifacts import chat_turn_artifacts_root
        if not is_memory_enabled():
            return {"entries": []}
        ws = os.getenv("AI_EDITOR_WORKSPACE_PATH", "")
        base = chat_turn_artifacts_root(thread_id, "", ws).parent  # …/chat/<thread>/
        files = sorted(glob.glob(str(base / "*" / "memory-recall-*.json")),
                       key=os.path.getmtime)
        if not files:
            return {"entries": []}
        return _json.loads(open(files[-1]).read())
```
Add `import os` at the top of `routes.py` if absent.

- [ ] **Step 4: Run → PASS** · `python -m pytest tests/test_memory_routes.py -v`
- [ ] **Step 5: Full backend memory suite + lint + types**

```bash
python -m pytest tests/ -k "memory or recall or consolidator or reranker" -q
ruff check agentd/memory/ ; echo "ruff exit=$?"
mypy agentd/memory
```
Expected: all green; ruff exit 0; mypy clean for `agentd/memory`.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/tests/test_memory_routes.py
git commit -m "feat(memory): read-only inspect/browse/chain routes + memory_enabled in /config"
```

---

## Self-Review

**Spec coverage:** §1 reranker → Task 1+2. §2 trace + persistence → Task 2 (models + `recall_with_trace`) + Task 4 (prep + artifact). §3 routes + store browse → Task 3 (store) + Task 5 (routes); editor-client contracts are **Plan 3-B**. §5 error handling → degrade-not-raise in Task 1, soft-empty/best-effort in Tasks 4-5. §6 testing → each task's tests. §7 config → Task 1.

**Placeholder scan:** none — every step has runnable code/commands. The only narrative steps (Task 4's edits to existing files) carry the exact code to insert + the file/line context.

**Type consistency:** `recall_with_trace -> tuple[list[Memory], RecallTrace]` consistent across Tasks 2/4; `RecallTraceEntry.signals` keys (`semantic/lexical/structural/importance/recency`) consistent with the panel (3-B); `list_memories`/`get_supersede_chain` signatures match their Task-5 callers; `_SpyRecall` gains `recall_with_trace` in Task 4 (the careful-wiring guard).

**Note for implementer:** `recall()` MUST keep returning `list[Memory]` — verify `recall_grounded` and the `recall()` tool (`tool_source.py`) still pass after Task 2.
