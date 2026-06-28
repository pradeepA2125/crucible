# Memory Harness Phase 2B — Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Distill conversation history into durable, deduplicated, lifecycle-aware `memories` — via a background `Consolidator` (LLM proposes, Python disposes) and a deliberate `remember()` tool.

**Architecture:** A `Consolidator` runs one structured `generate_json` call to propose `CandidateMemory` objects, then a deterministic Python post-process embeds, dedupes, supersedes, and inserts them via `MemoryStore` (Plan 2A). The `MemoryHarness` schedules consolidation fire-and-forget at three triggers (compaction / task-terminal / edit-promoting chat turn). All best-effort: a failure never touches the turn.

**Tech Stack:** Python 3.13, `generate_json` structured-output transport, `asyncio.create_task`, `sentence-transformers` (via Plan 2A `Embedder`), pytest-asyncio.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-28-memory-harness-phase2-recall-design.md`.
- Depends on **Plan 2A** — uses `MemoryStore.insert_memory/get_memory/similar_memories/supersede`, `Memory`, `CandidateMemory`, `Embedder`.
- LLM proposes only `{kind, content, entities, importance, contradicts?}`; **Python assigns** id/scope/source/lifecycle/embedding/seq-link.
- Consolidation defaults to `workspace` scope; `remember()` may pass `workspace` or `thread`; **never `global`**.
- **Episodic never supersedes** — always insert.
- All paths **best-effort**: consolidation throws → nothing written, log once, turn unaffected (segments survive as re-distill source).
- The consolidator uses **`generate_json`** (schema-constrained) — not free-text — so the JSON-echo failure is structurally prevented; input is a **single-key** `{transcript}` payload (existing memories folded into that text) per the spec's hardening.
- Lints clean (`ruff`, line 100); `mypy agentd/memory` clean. Tests use `ScriptedReasoningEngine`-style stubs + real `tmp_path` SQLite.

---

### Task 1: Consolidation prompt, schema, and distill callable

**Files:**
- Create: `services/agentd-py/agentd/memory/consolidator.py`
- Test: `services/agentd-py/tests/test_consolidator_distill.py` (create)

**Interfaces:**
- Consumes: `CandidateMemory`, `Memory` (2A); a transport with `generate_json(...)`.
- Produces: `DistillFn = Callable[[str, list[Memory]], Awaitable[list[CandidateMemory]]]`. `make_engine_consolidator(transport, model) -> DistillFn`. `CANDIDATE_MEMORY_SCHEMA: dict`. `_CONSOLIDATION_SYSTEM: str`. The distill fn returns `[]` (best-effort) on any transport/parse failure.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_consolidator_distill.py
import pytest

from agentd.memory.consolidator import make_engine_consolidator


class _FakeTransport:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_thinking=None):
        self.calls.append((model, system_instructions, user_payload))
        return self._payload


@pytest.mark.asyncio
async def test_distill_parses_candidates():
    t = _FakeTransport({"memories": [
        {"kind": "semantic", "content": "patch ops in patch/engine.py",
         "entities": ["patch/engine.py"], "importance": 8, "contradicts": None}]})
    distill = make_engine_consolidator(t, "m1")
    out = await distill("transcript text", [])
    assert len(out) == 1 and out[0].kind == "semantic" and out[0].importance == 8
    # single-key transcript payload (echo-hardening)
    _model, _sys, payload = t.calls[0]
    assert list(payload.keys()) == ["transcript"]


@pytest.mark.asyncio
async def test_distill_best_effort_on_garbage():
    class Boom:
        async def generate_json(self, **kw):
            raise RuntimeError("provider down")

    distill = make_engine_consolidator(Boom(), "m1")
    assert await distill("x", []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_consolidator_distill.py -v`
Expected: FAIL — module `agentd.memory.consolidator` missing.

- [ ] **Step 3: Write the prompt, schema, and distill callable**

```python
# services/agentd-py/agentd/memory/consolidator.py
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agentd.memory.models import CandidateMemory, Memory

logger = logging.getLogger(__name__)

DistillFn = Callable[[str, list[Memory]], Awaitable[list[CandidateMemory]]]

CANDIDATE_MEMORY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["episodic", "semantic", "procedural"]},
                    "content": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer"},
                    "contradicts": {"type": ["string", "null"]},
                },
                "required": ["kind", "content", "entities", "importance"],
            },
        }
    },
    "required": ["memories"],
}

_CONSOLIDATION_SYSTEM = (
    "You distill an AI coding session into a few durable memory notes for your future self. "
    "You are given the recent transcript and any EXISTING MEMORIES (each with an id). Propose "
    "only NEW, durable notes worth recalling in a later session.\n"
    "\n"
    "Each note has a kind:\n"
    "- episodic: a specific thing that happened this session "
    "(e.g. \"User rejected the first plan and asked to keep the change minimal\"). "
    "Episodic notes are immutable — NEVER set contradicts on them.\n"
    "- semantic: a durable fact about the code/project/user "
    "(e.g. \"Patch ops are applied in patch/engine.py; it supports 7 op types\").\n"
    "- procedural: a reusable how-to / process "
    "(e.g. \"Run the backend via start-backend.sh, always quoting --workspace\"). "
    "Procedural is the hardest to spot — only record a genuinely reusable method.\n"
    "\n"
    "Rules:\n"
    "- One atomic fact per note. Keep entities exact: list the verbatim file paths and "
    "path:Symbol tokens the note is about.\n"
    "- importance: rate 1-10 how much this would help a future session (a project-wide fact = "
    "high; a one-off detail = low).\n"
    "- contradicts: set to an EXISTING MEMORY id only when your note directly conflicts with it "
    "(a fact that changed). Never for episodic.\n"
    "- Do NOT record: ephemeral chit-chat, tool mechanics, or anything obvious from the code. "
    "If nothing is worth keeping, return an empty list."
)


def _render_existing(existing: list[Memory]) -> str:
    if not existing:
        return "(none)"
    return "\n".join(f"[{m.id}] ({m.kind}) {m.content}" for m in existing)


def make_engine_consolidator(transport: object, model: str) -> DistillFn:
    async def _distill(transcript: str, existing: list[Memory]) -> list[CandidateMemory]:
        payload: dict[str, object] = {
            "transcript": f"{transcript}\n\nEXISTING MEMORIES (with ids):\n"
                          f"{_render_existing(existing)}"
        }
        try:
            raw = await transport.generate_json(  # type: ignore[attr-defined]
                model=model, schema_name="consolidated_memories",
                schema=CANDIDATE_MEMORY_SCHEMA, system_instructions=_CONSOLIDATION_SYSTEM,
                user_payload=payload,
            )
            items = raw.get("memories", []) if isinstance(raw, dict) else []
            out: list[CandidateMemory] = []
            for it in items:
                if isinstance(it, dict):
                    out.append(CandidateMemory.model_validate(it))
            return out
        except Exception:  # noqa: BLE001 — best-effort: never break the turn
            logger.warning("[memory] consolidation distill failed for model=%s", model)
            return []

    return _distill
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_consolidator_distill.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/consolidator.py services/agentd-py/tests/test_consolidator_distill.py
git commit -m "feat(memory): consolidation prompt + schema + distill callable"
```

---

### Task 2: `Consolidator` — deterministic dispose + write_explicit

**Files:**
- Modify: `services/agentd-py/agentd/memory/consolidator.py`
- Test: `services/agentd-py/tests/test_consolidator_dispose.py` (create)

**Interfaces:**
- Consumes: `MemoryStore` (2A: `similar_memories`, `insert_memory`, `supersede`, `get_memory`), `Embedder` (2A), `DistillFn` (Task 1).
- Produces: `Consolidator(store, embedder, distill, *, similar_k=5, dedup_threshold=0.92)`. `async consolidate(run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi) -> int` (count inserted). `async write_explicit(content, kind, entities, scope_kind, scope_id) -> str` (memory id). New-memory ids are `uuid4().hex`. `valid_from`/`created_at` = `datetime.now(UTC)`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_consolidator_dispose.py
import pytest

from agentd.memory.consolidator import Consolidator
from agentd.memory.embedder import Embedder
from agentd.memory.models import CandidateMemory
from agentd.memory.store import MemoryStore


def _store(tmp_path):
    return MemoryStore(tmp_path / "m.sqlite3")


def _embedder():
    # deterministic: distinct unit vectors per distinct content
    table = {}
    def enc(texts):
        out = []
        for t in texts:
            if t not in table:
                v = [0.0] * 384
                v[len(table) % 384] = 1.0
                table[t] = v
            out.append(table[t])
        return out
    return Embedder(encoder=enc)


async def _distill_returning(cands):
    async def d(transcript, existing):
        return cands
    return d


@pytest.mark.asyncio
async def test_consolidate_inserts_new(tmp_path):
    store = _store(tmp_path)
    cands = [CandidateMemory(kind="semantic", content="uses bge-small",
                             entities=["agentd/memory/embedder.py"], importance=7)]
    con = Consolidator(store, _embedder(), await _distill_returning(cands))
    n = await con.consolidate("thread-x", "workspace", "/ws", "transcript", 0, 8)
    assert n == 1
    live = store.get_live_memories("workspace", "/ws")
    assert len(live) == 1 and live[0].source_kind == "consolidation"
    assert live[0].source_seq_lo == 0 and live[0].source_seq_hi == 8


@pytest.mark.asyncio
async def test_consolidate_dedupes_near_identical(tmp_path):
    store = _store(tmp_path)
    c = CandidateMemory(kind="semantic", content="uses bge-small", entities=[], importance=7)
    con = Consolidator(store, _embedder(), await _distill_returning([c]))
    await con.consolidate("t", "workspace", "/ws", "tx", 0, 1)
    await con.consolidate("t", "workspace", "/ws", "tx", 2, 3)  # same content again
    assert len(store.get_live_memories("workspace", "/ws")) == 1  # deduped


@pytest.mark.asyncio
async def test_consolidate_supersedes_on_contradicts(tmp_path):
    store = _store(tmp_path)
    first = CandidateMemory(kind="semantic", content="uses openai embeddings",
                            entities=[], importance=6)
    con1 = Consolidator(store, _embedder(), await _distill_returning([first]))
    await con1.consolidate("t", "workspace", "/ws", "tx", 0, 1)
    old_id = store.get_live_memories("workspace", "/ws")[0].id
    second = CandidateMemory(kind="semantic", content="uses bge-small embeddings",
                             entities=[], importance=7, contradicts=old_id)
    con2 = Consolidator(store, _embedder(), await _distill_returning([second]))
    await con2.consolidate("t", "workspace", "/ws", "tx", 2, 3)
    live = store.get_live_memories("workspace", "/ws")
    assert len(live) == 1 and live[0].content == "uses bge-small embeddings"
    assert store.get_memory(old_id).superseded_by == live[0].id


@pytest.mark.asyncio
async def test_episodic_never_supersedes(tmp_path):
    store = _store(tmp_path)
    e1 = CandidateMemory(kind="episodic", content="user asked X", entities=[], importance=5)
    con = Consolidator(store, _embedder(), await _distill_returning([e1]))
    await con.consolidate("t", "workspace", "/ws", "tx", 0, 1)
    old_id = store.get_live_memories("workspace", "/ws")[0].id
    e2 = CandidateMemory(kind="episodic", content="user asked Y", entities=[], importance=5,
                         contradicts=old_id)  # should be IGNORED for episodic
    con2 = Consolidator(store, _embedder(), await _distill_returning([e2]))
    await con2.consolidate("t", "workspace", "/ws", "tx", 2, 3)
    assert len(store.get_live_memories("workspace", "/ws")) == 2  # both kept


@pytest.mark.asyncio
async def test_write_explicit_returns_id(tmp_path):
    store = _store(tmp_path)
    con = Consolidator(store, _embedder(), await _distill_returning([]))
    mid = await con.write_explicit("always quote --workspace", "procedural",
                                   ["scripts/stress/start-backend.sh"], "workspace", "/ws")
    assert store.get_memory(mid) is not None
    assert store.get_memory(mid).source_kind == "agent_tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_consolidator_dispose.py -v`
Expected: FAIL — `Consolidator` not defined.

- [ ] **Step 3: Write the implementation**

Append to `services/agentd-py/agentd/memory/consolidator.py` (add imports `from datetime import UTC, datetime`, `from uuid import uuid4`, `from agentd.memory.embedder import Embedder`, `from agentd.memory.store import MemoryStore`):

```python
def _cosine_from_l2(distance: float) -> float:
    # unit vectors: ||a-b||^2 = 2 - 2cos  =>  cos = 1 - d^2/2
    return 1.0 - (distance * distance) / 2.0


class Consolidator:
    """Async write path: LLM proposes candidates; Python disposes (embed/dedupe/supersede)."""

    def __init__(
        self, store: MemoryStore, embedder: Embedder, distill: DistillFn,
        *, similar_k: int = 5, dedup_threshold: float = 0.92,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._distill = distill
        self._k = similar_k
        self._dedup = dedup_threshold

    async def consolidate(
        self, run_id: str, scope_kind: str, scope_id: str, transcript: str,
        seq_lo: int | None, seq_hi: int | None,
    ) -> int:
        existing = self._store.get_live_memories(scope_kind, scope_id)
        candidates = await self._distill(transcript, existing)
        inserted = 0
        for c in candidates:
            vec = self._embedder.embed([c.content])
            emb = vec[0] if vec else []
            if self._dispose(c, emb, run_id, scope_kind, scope_id, "consolidation",
                             seq_lo, seq_hi):
                inserted += 1
        return inserted

    async def write_explicit(
        self, content: str, kind: str, entities: list[str], scope_kind: str, scope_id: str,
    ) -> str:
        c = CandidateMemory(kind=kind, content=content, entities=entities, importance=8)
        vec = self._embedder.embed([content])
        emb = vec[0] if vec else []
        mem = self._build_memory(c, run_id="", scope_kind=scope_kind, scope_id=scope_id,
                                 source_kind="agent_tool", seq_lo=None, seq_hi=None)
        self._store.insert_memory(mem, emb)
        return mem.id

    def _dispose(
        self, c: CandidateMemory, emb: list[float], run_id: str, scope_kind: str,
        scope_id: str, source_kind: str, seq_lo: int | None, seq_hi: int | None,
    ) -> bool:
        # Dedupe: drop a near-identical live memory of same kind+scope.
        if emb:
            for mem, dist in self._store.similar_memories(emb, c.kind, scope_kind, scope_id,
                                                          self._k):
                if _cosine_from_l2(dist) >= self._dedup:
                    return False
        new = self._build_memory(c, run_id, scope_kind, scope_id, source_kind, seq_lo, seq_hi)
        # Supersede: only when the LLM flagged a conflict AND the kind is not episodic.
        if c.kind != "episodic" and c.contradicts and self._store.get_memory(c.contradicts):
            self._store.supersede(c.contradicts, new, emb)
            return True
        self._store.insert_memory(new, emb)
        return True

    def _build_memory(
        self, c: CandidateMemory, run_id: str, scope_kind: str, scope_id: str,
        source_kind: str, seq_lo: int | None, seq_hi: int | None,
    ) -> Memory:
        now = datetime.now(UTC)
        return Memory(
            id=uuid4().hex, scope_kind=scope_kind, scope_id=scope_id, kind=c.kind,
            content=c.content, entities=c.entities, importance=c.importance,
            valid_from=now, valid_to=None, superseded_by=None, source_kind=source_kind,
            source_ref=run_id, source_seq_lo=seq_lo, source_seq_hi=seq_hi, created_at=now,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_consolidator_dispose.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/consolidator.py services/agentd-py/tests/test_consolidator_dispose.py
git commit -m "feat(memory): Consolidator dispose (dedupe/supersede/episodic-insert) + write_explicit"
```

---

### Task 3: Harness wiring + compaction trigger

**Files:**
- Modify: `services/agentd-py/agentd/memory/harness.py`
- Modify: `services/agentd-py/agentd/memory/models.py` (add evicted seq span to `CompactionResult`/`TurnPreparation`)
- Modify: `services/agentd-py/agentd/memory/compactor.py` (set the evicted seq span)
- Test: `services/agentd-py/tests/test_memory_consolidation_trigger.py` (create)

**Interfaces:**
- Consumes: `Consolidator` (Task 2), `MemoryStore.get_segments` (Phase 1).
- Produces: `MemoryHarness` gains `_consolidator` + `schedule_consolidation(run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi)` (fire-and-forget `asyncio.create_task`, best-effort). `prepare_turn` schedules consolidation of the just-evicted span when `compacted`. `CompactionResult`/`TurnPreparation` gain `evicted_seq_lo: int | None`, `evicted_seq_hi: int | None`. `build_memory_harness` builds the consolidator from `(transport, model)` + a workspace path for default scope.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_memory_consolidation_trigger.py
import asyncio

import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.harness import MemoryHarness
from agentd.memory.store import MemoryStore


class _SpyConsolidator:
    def __init__(self):
        self.calls = []

    async def consolidate(self, run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi):
        self.calls.append((run_id, scope_kind, scope_id, seq_lo, seq_hi))
        return 0


@pytest.mark.asyncio
async def test_compaction_schedules_consolidation(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    comp = Compactor(store, summ, window_tokens=100, trigger_frac=0.1,
                     hot_token_frac=0.4, hot_turns=2)
    spy = _SpyConsolidator()
    harness = MemoryHarness(enabled=True, compactor=comp, consolidator=spy,
                            scope_kind="workspace", scope_id="/ws")
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    await harness.prepare_turn(history, "thread-x")
    await asyncio.sleep(0)  # let the fire-and-forget task run
    assert spy.calls and spy.calls[0][0] == "thread-x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_consolidation_trigger.py -v`
Expected: FAIL — `MemoryHarness.__init__` rejects `consolidator`/`scope_*`.

- [ ] **Step 3: Add the evicted span to the compaction result**

In `models.py`, add to `CompactionResult` and `TurnPreparation`:
```python
    evicted_seq_lo: int | None = None
    evicted_seq_hi: int | None = None
```
In `compactor.py` `maybe_compact`, where segments are built (`base = self._store.next_seq(run_id)`), capture the span and pass it on every `compacted=True` return:
```python
        seq_lo = base
        seq_hi = base + evicted_count - 1 if evicted_count else None
```
and add `evicted_seq_lo=seq_lo, evicted_seq_hi=seq_hi` to each `CompactionResult(compacted=True, ...)` return.

- [ ] **Step 4: Wire the harness**

In `harness.py`, extend `MemoryHarness.__init__` to accept `consolidator=None, scope_kind="workspace", scope_id=""` and store them. In `prepare_turn`, after building the result, schedule consolidation:

```python
        prep = TurnPreparation(
            history=result.history, recalled_memories=[], compacted=result.compacted,
            evicted_count=result.evicted_count, anchor_version=result.anchor_version,
        )
        if result.compacted and self._consolidator is not None and result.evicted_seq_hi is not None:
            self.schedule_consolidation(
                run_id, self._scope_kind, self._scope_id,
                transcript=self._render_segments(run_id, result.evicted_seq_lo,
                                                 result.evicted_seq_hi),
                seq_lo=result.evicted_seq_lo, seq_hi=result.evicted_seq_hi,
            )
        return prep

    def schedule_consolidation(self, run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi):
        async def _run():
            try:
                await self._consolidator.consolidate(
                    run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi)
            except Exception:  # noqa: BLE001
                logger.warning("[memory] scheduled consolidation failed for run=%s", run_id)
        asyncio.create_task(_run())

    def _render_segments(self, run_id, seq_lo, seq_hi):
        segs = [s for s in self._store_segments(run_id) if seq_lo <= s.seq <= seq_hi]
        return "\n".join(s.content for s in segs)
```

Give the harness access to the store: pass `store` into `MemoryHarness` (or read it from the compactor — add `self._store_segments = compactor._store.get_segments` in `__init__` when a compactor is present). Add `import asyncio` at top. Update `build_memory_harness` to construct a `Consolidator` (via `make_engine_consolidator(transport, model)` + `Embedder(config.embedding_model)` + the store) and pass it + the workspace scope into `MemoryHarness`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_consolidation_trigger.py tests/test_memory_harness.py -v`
Expected: PASS (trigger test + existing harness tests still green).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/memory/harness.py services/agentd-py/agentd/memory/models.py services/agentd-py/agentd/memory/compactor.py services/agentd-py/tests/test_memory_consolidation_trigger.py
git commit -m "feat(memory): schedule consolidation on compaction (fire-and-forget)"
```

---

### Task 4: `remember()` tool

**Files:**
- Create: `services/agentd-py/agentd/memory/tool_source.py`
- Test: `services/agentd-py/tests/test_memory_tool_source.py` (create)

**Interfaces:**
- Consumes: `Consolidator.write_explicit` (Task 2), `ToolDefinition`/`ToolOutput` (`agentd.tools.registry`).
- Produces: `MemoryToolSource(consolidator, scope_kind, scope_id)` implementing `ToolSource` — `name="memory"`, exposes `remember`. `recall` is added in Plan 2C. `execute("remember", {content, kind, entities?, scope?})` → calls `write_explicit`, returns `ToolOutput`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_memory_tool_source.py
import pytest

from agentd.memory.tool_source import MemoryToolSource


class _SpyConsolidator:
    def __init__(self):
        self.explicit = []

    async def write_explicit(self, content, kind, entities, scope_kind, scope_id):
        self.explicit.append((content, kind, entities, scope_kind, scope_id))
        return "mem-1"


@pytest.mark.asyncio
async def test_remember_tool_writes_and_reports():
    spy = _SpyConsolidator()
    src = MemoryToolSource(spy, "workspace", "/ws")
    assert src.owns("remember")
    out = await src.execute("remember", {"content": "quote --workspace",
                                         "kind": "procedural", "entities": ["start-backend.sh"]})
    assert not out.is_error and "mem-1" in out.output
    assert spy.explicit[0][1] == "procedural"


@pytest.mark.asyncio
async def test_remember_rejects_bad_kind():
    src = MemoryToolSource(_SpyConsolidator(), "workspace", "/ws")
    out = await src.execute("remember", {"content": "x", "kind": "nonsense"})
    assert out.is_error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_tool_source.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the implementation**

```python
# services/agentd-py/agentd/memory/tool_source.py
from __future__ import annotations

from agentd.tools.registry import ToolDefinition, ToolOutput

_KINDS = {"episodic", "semantic", "procedural"}

_REMEMBER_DEF = ToolDefinition(
    name="remember",
    description=(
        "Store a durable memory for future sessions. Use for a fact/decision/how-to worth "
        "recalling later; SKIP for transient detail. kind is one of episodic (something that "
        "happened), semantic (a durable fact), procedural (a reusable how-to)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "kind": {"type": "string", "enum": ["episodic", "semantic", "procedural"]},
            "entities": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "enum": ["workspace", "thread"]},
        },
        "required": ["content", "kind"],
    },
)


class MemoryToolSource:
    """ToolSource exposing the deliberate memory write path (and recall, in Plan 2C)."""

    name = "memory"

    def __init__(self, consolidator: object, scope_kind: str, scope_id: str) -> None:
        self._consolidator = consolidator
        self._scope_kind = scope_kind
        self._scope_id = scope_id

    def definitions(self) -> list[ToolDefinition]:
        return [_REMEMBER_DEF]

    def owns(self, tool: str) -> bool:
        return tool == "remember"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != "remember":
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        content = str(args.get("content", "")).strip()
        kind = str(args.get("kind", ""))
        if not content:
            return ToolOutput(output="remember needs non-empty 'content'.", is_error=True)
        if kind not in _KINDS:
            return ToolOutput(output=f"invalid kind {kind!r}; use {sorted(_KINDS)}.",
                              is_error=True)
        entities = [str(e) for e in args.get("entities", []) if isinstance(args.get("entities"), list)]
        scope = str(args.get("scope", "workspace"))
        scope_kind = "thread" if scope == "thread" else self._scope_kind
        scope_id = self._scope_id
        mid = await self._consolidator.write_explicit(  # type: ignore[attr-defined]
            content, kind, entities, scope_kind, scope_id)
        return ToolOutput(output=f"Remembered ({kind}): {content}  [{mid}]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_tool_source.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/tool_source.py services/agentd-py/tests/test_memory_tool_source.py
git commit -m "feat(memory): remember() tool source"
```

---

### Task 5: Task-terminal and edit-promote triggers

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (task-terminal hook ~ line 1849-1881 finally)
- Modify: `services/agentd-py/agentd/chat/controller.py` (edit-promote hook — locate the promote-on-accept site)
- Test: `services/agentd-py/tests/test_memory_terminal_trigger.py` (create)

**Interfaces:**
- Consumes: `MemoryHarness.schedule_consolidation` (Task 3).
- Produces: at task terminal, the orchestrator schedules consolidation of the run (`run_id=task_id`, transcript = the run's segments or final history, seq span = full run span or `None`). At an edit-promoting controller turn, the controller schedules consolidation of the turn (`run_id=thread_id`). QnA/answer/clarify turns do NOT schedule.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_memory_terminal_trigger.py
import pytest

from agentd.memory.harness import MemoryHarness


class _SpyConsolidator:
    def __init__(self):
        self.calls = []

    async def consolidate(self, run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi):
        self.calls.append(run_id)
        return 0


@pytest.mark.asyncio
async def test_schedule_consolidation_runs_for_terminal(tmp_path):
    import asyncio
    spy = _SpyConsolidator()
    harness = MemoryHarness(enabled=True, compactor=None, consolidator=spy,
                            scope_kind="workspace", scope_id="/ws")
    harness.schedule_consolidation("task-1", "workspace", "/ws", "final transcript", None, None)
    await asyncio.sleep(0)
    assert spy.calls == ["task-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_terminal_trigger.py -v`
Expected: FAIL — `MemoryHarness` may not allow `compactor=None` with a consolidator; adjust `__init__` so the consolidator path works without a compactor (terminal/edit triggers have no compaction). Make `schedule_consolidation` independent of `_store_segments`.

- [ ] **Step 3: Make the harness terminal-trigger-capable, then wire the call sites**

In `harness.py`, ensure `schedule_consolidation` works with `compactor=None` (it already takes the transcript directly — no segment read needed). Guard: if `self._consolidator is None`, `schedule_consolidation` is a no-op.

In `orchestrator/engine.py`, inside `_execute_plan`'s terminal `finally` (near `_finalize_run_summary`/`_finalize_task_narrative`, ~line 1860-1876), add:
```python
            if self._memory_harness is not None:
                self._memory_harness.schedule_consolidation(
                    run_id=task.task_id, scope_kind="workspace",
                    scope_id=str(self._workspace_path), transcript=self._run_transcript(task),
                    seq_lo=None, seq_hi=None,
                )
```
(`_run_transcript(task)` renders the run's history/segments to text — reuse whatever the narrative path already assembles; if none, render `task.execution_state.run_events`.) The orchestrator must hold the harness (constructor injection, mirroring how it already takes other collaborators).

In `chat/controller.py`, locate the edit-promote site (search: `grep -n "promote" agentd/chat/controller.py` — the `submit_changes`/EditGate accept path that writes to the real workspace). After a successful promote, add:
```python
            if self._memory_harness is not None:
                self._memory_harness.schedule_consolidation(
                    run_id=thread_id, scope_kind="workspace",
                    scope_id=str(self._workspace_path), transcript=turn_transcript,
                    seq_lo=None, seq_hi=None,
                )
```
Do NOT add this to the `answer`/`clarify` branches.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_terminal_trigger.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/harness.py services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_memory_terminal_trigger.py
git commit -m "feat(memory): task-terminal + edit-promote consolidation triggers"
```

---

### Task 6: End-to-end write integration

**Files:**
- Test: `services/agentd-py/tests/test_memory_write_integration.py` (create)

**Interfaces:**
- Consumes: everything in 2A + 2B.

- [ ] **Step 1: Write the test**

```python
# services/agentd-py/tests/test_memory_write_integration.py
import pytest

from agentd.memory.consolidator import Consolidator
from agentd.memory.embedder import Embedder
from agentd.memory.models import CandidateMemory
from agentd.memory.store import MemoryStore


class _Engine:
    """Stub transport: returns a fixed candidate set from generate_json."""
    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_thinking=None):
        return {"memories": [
            {"kind": "semantic", "content": "memory harness lives in agentd/memory",
             "entities": ["agentd/memory"], "importance": 9, "contradicts": None}]}


@pytest.mark.asyncio
async def test_consolidate_via_engine_distill(tmp_path):
    from agentd.memory.consolidator import make_engine_consolidator
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = Embedder(encoder=lambda ts: [[1.0] + [0.0] * 383 for _ in ts])
    con = Consolidator(store, emb, make_engine_consolidator(_Engine(), "m1"))
    n = await con.consolidate("thread-x", "workspace", "/ws", "we built the memory harness", 0, 5)
    assert n == 1
    live = store.get_live_memories("workspace", "/ws")
    assert live[0].entities == ["agentd/memory"] and live[0].importance == 9
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_memory_write_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Full memory suite + lint + types**

Run:
```bash
python -m pytest tests/ -k "memory or consolidator" -q
ruff check agentd/memory/
mypy agentd/memory
```
Expected: all green; ruff + mypy clean for `agentd/memory`.

- [ ] **Step 4: Commit**

```bash
git add services/agentd-py/tests/test_memory_write_integration.py
git commit -m "test(memory): end-to-end consolidation write integration"
```

---

## Self-Review

**Spec coverage (§2 write path + §4 prompt):**
- Deliberate `remember()` tool → Task 4. Background `Consolidator` (distill → embed → dedupe → supersede → insert) → Tasks 1-2. Async fire-and-forget at all three triggers → Tasks 3 (compaction) + 5 (terminal, edit-promote, QnA excluded).
- LLM proposes minimal `{kind, content, entities, importance, contradicts?}`; Python assigns bookkeeping → Tasks 1-2.
- Dedupe by cosine ≥ 0.92, supersede on `contradicts` (episodic never), workspace default, A+link span → Task 2.
- Consolidation prompt: taxonomy + 4-part teaching + importance + structured `generate_json` + single-key payload → Task 1.
- Best-effort everywhere → Tasks 1 (distill), 3/5 (scheduling).

**Placeholder scan:** the only non-literal steps are the two call-site locates in Task 5 (orchestrator terminal `finally`, controller promote) — each carries the exact code to insert and a `grep` to find the line; this is unavoidable (the sites are large pre-existing functions) and is concrete, not a placeholder.

**Type consistency:** `CandidateMemory{kind,content,entities,importance,contradicts}` identical across Tasks 1/2/4; `Consolidator(store, embedder, distill, *, similar_k, dedup_threshold)` and `consolidate(...)`/`write_explicit(...)` signatures match their callers; `schedule_consolidation(run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi)` identical in Tasks 3/5.
