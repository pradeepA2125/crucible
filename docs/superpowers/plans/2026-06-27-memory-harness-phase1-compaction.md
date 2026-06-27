# Memory Harness — Phase 1 (Within-Run Compaction) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the `ControllerLoop` and task `ToolLoop` from degrading when a run outgrows ~65% of the context window, by folding evicted history into a merged "anchored summary" while persisting raw segments to SQLite for later (Phase 2) recall.

**Architecture:** A new flag-gated `agentd/memory/` subpackage. `MemoryHarness` is a façade injected into both loops; each iteration the loop calls `harness.prepare_turn(history, run_id)` which delegates to a `Compactor`. The compactor keeps the last N turns verbatim ("hot"), merges everything older into a per-run anchored summary via one LLM call (never regenerated from scratch), and persists the evicted raw messages as `compaction_segments` rows in a dedicated SQLite DB. Recall is a Phase-2 no-op stub here. The whole subsystem is off unless `AI_EDITOR_MEMORY_ENABLED` is truthy.

**Tech Stack:** Python 3.13, Pydantic, stdlib `sqlite3`, pytest + pytest-asyncio. Reuses the existing `ScriptedReasoningEngine` testing pattern and the `_finalize_task_narrative` best-effort async pattern.

## Global Constraints

- Python target: 3.13. Use `asyncio.run(...)` or `@pytest.mark.asyncio`, never `get_event_loop().run_until_complete`.
- Strict typing: no `any`, explicit return types. Mirror existing `agentd/` style.
- All imports at top of file.
- The harness is **best-effort**: no compaction/store failure may ever propagate out of a loop iteration. On any internal failure, fall back to leaving history untouched (or hard-truncating) and continue.
- Master kill switch `AI_EDITOR_MEMORY_ENABLED` (default **off**). When off, `MemoryHarness` is a no-op pass-through and both loops behave byte-identically to today.
- New DB path env: `AI_EDITOR_MEMORY_DB_PATH` (default `.agentd/memory.sqlite3`). Separate file from task/chat DBs.
- Phase-1-specific simplification (decided, document in code): Phase 1 folds **all** evicted history into the anchor (no information cliff before recall exists). Segments are persisted with a `tier` label (`warm`/`cold`) for Phase 2 granularity, but Phase 1 summarizes uniformly.
- Default tuning constants (env-overridable): `MEMORY_COMPACT_TRIGGER_FRAC=0.65`, `MEMORY_HOT_TURNS=10`, `MEMORY_WINDOW_TOKENS=128000`.
- Run the suite with `pytest` and read the actual `FAILED`/summary lines — never trust a piped exit code.

---

## File Structure

- `agentd/memory/__init__.py` — exports `MemoryHarness`, `build_memory_harness`, `NO_OP_HARNESS`.
- `agentd/memory/models.py` — `MemoryKind`, `CompactionSegment`, `AnchoredSummary`, `CompactionResult`, `TurnPreparation`.
- `agentd/memory/config.py` — `MemoryConfig` + `from_env`.
- `agentd/memory/store.py` — `MemoryStore` (SQLite: `compaction_segments`, `anchored_summaries`).
- `agentd/memory/compactor.py` — `Compactor`, `estimate_tokens`, `AnchorSummarizer` type, `make_engine_summarizer`.
- `agentd/memory/harness.py` — `MemoryHarness`, `build_memory_harness`, `NO_OP_HARNESS`.
- `agentd/chat/controller_loop.py` — MODIFY: inject + call harness at top of `_iterate`.
- `agentd/tools/loop.py` — MODIFY: inject + call harness at top of the iteration loop.
- Tests under `tests/memory/`.

---

### Task 1: Subpackage scaffold — models + config

**Files:**
- Create: `agentd/memory/__init__.py`
- Create: `agentd/memory/models.py`
- Create: `agentd/memory/config.py`
- Test: `tests/memory/test_config.py`

**Interfaces:**
- Produces:
  - `MemoryKind(str, Enum)` = `EPISODIC|SEMANTIC|PROCEDURAL` (defined now for Phase-2 forward-compat).
  - `CompactionSegment(BaseModel)`: `id: str, run_id: str, seq: int, tier: Literal["warm","cold"], content: str, created_at: datetime`.
  - `AnchoredSummary(BaseModel)`: `run_id: str, summary_md: str, version: int, updated_at: datetime`.
  - `CompactionResult(BaseModel)`: `compacted: bool, history: list[dict[str, object]], anchor: str | None, degraded: bool = False`.
  - `TurnPreparation(BaseModel)`: `history: list[dict[str, object]], recalled_memories: list[dict[str, object]] = [], compacted: bool = False`.
  - `MemoryConfig(BaseModel)`: `enabled: bool, db_path: str, trigger_frac: float, hot_turns: int, window_tokens: int`; classmethod `from_env(env: Mapping[str,str]) -> MemoryConfig`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_config.py
from agentd.memory.config import MemoryConfig

def test_from_env_defaults_disabled():
    cfg = MemoryConfig.from_env({})
    assert cfg.enabled is False
    assert cfg.db_path.endswith("memory.sqlite3")
    assert cfg.trigger_frac == 0.65
    assert cfg.hot_turns == 10
    assert cfg.window_tokens == 128000

def test_from_env_overrides():
    cfg = MemoryConfig.from_env({
        "AI_EDITOR_MEMORY_ENABLED": "1",
        "AI_EDITOR_MEMORY_DB_PATH": "/tmp/m.sqlite3",
        "AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC": "0.5",
        "AI_EDITOR_MEMORY_HOT_TURNS": "4",
        "AI_EDITOR_MEMORY_WINDOW_TOKENS": "8000",
    })
    assert cfg.enabled is True
    assert cfg.db_path == "/tmp/m.sqlite3"
    assert cfg.trigger_frac == 0.5
    assert cfg.hot_turns == 4
    assert cfg.window_tokens == 8000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.memory'`

- [ ] **Step 3: Write minimal implementation**

```python
# agentd/memory/models.py
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field

class MemoryKind(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"

class CompactionSegment(BaseModel):
    id: str
    run_id: str
    seq: int
    tier: Literal["warm", "cold"]
    content: str
    created_at: datetime

class AnchoredSummary(BaseModel):
    run_id: str
    summary_md: str
    version: int
    updated_at: datetime

class CompactionResult(BaseModel):
    compacted: bool
    history: list[dict[str, object]]
    anchor: str | None = None
    degraded: bool = False

class TurnPreparation(BaseModel):
    history: list[dict[str, object]]
    recalled_memories: list[dict[str, object]] = Field(default_factory=list)
    compacted: bool = False
```

```python
# agentd/memory/config.py
from __future__ import annotations
from collections.abc import Mapping
from pydantic import BaseModel

_TRUTHY = {"1", "true", "yes", "on"}

class MemoryConfig(BaseModel):
    enabled: bool
    db_path: str
    trigger_frac: float
    hot_turns: int
    window_tokens: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "MemoryConfig":
        return cls(
            enabled=env.get("AI_EDITOR_MEMORY_ENABLED", "").lower() in _TRUTHY,
            db_path=env.get("AI_EDITOR_MEMORY_DB_PATH", ".agentd/memory.sqlite3"),
            trigger_frac=float(env.get("AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC", "0.65")),
            hot_turns=int(env.get("AI_EDITOR_MEMORY_HOT_TURNS", "10")),
            window_tokens=int(env.get("AI_EDITOR_MEMORY_WINDOW_TOKENS", "128000")),
        )
```

```python
# agentd/memory/__init__.py
from agentd.memory.models import (
    AnchoredSummary,
    CompactionResult,
    CompactionSegment,
    MemoryKind,
    TurnPreparation,
)

__all__ = [
    "AnchoredSummary",
    "CompactionResult",
    "CompactionSegment",
    "MemoryKind",
    "TurnPreparation",
]
```

Also create empty `tests/memory/__init__.py` if the test layout requires packages (match the existing `tests/` convention — add only if other `tests/` subdirs have one).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/__init__.py services/agentd-py/agentd/memory/models.py services/agentd-py/agentd/memory/config.py services/agentd-py/tests/memory/
git commit -m "feat(memory): scaffold memory subpackage with models + config"
```

---

### Task 2: MemoryStore (SQLite — segments + anchored summaries)

**Files:**
- Create: `agentd/memory/store.py`
- Test: `tests/memory/test_store.py`

**Interfaces:**
- Consumes: `CompactionSegment`, `AnchoredSummary` (Task 1).
- Produces `MemoryStore`:
  - `__init__(self, db_path: str | Path)` — opens/creates DB, runs migrations.
  - `add_segments(self, segments: list[CompactionSegment]) -> None`
  - `get_segments(self, run_id: str) -> list[CompactionSegment]` — ordered by `seq`.
  - `upsert_anchor(self, run_id: str, summary_md: str) -> AnchoredSummary` — inserts at version 1 or bumps version + updates text.
  - `get_anchor(self, run_id: str) -> AnchoredSummary | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_store.py
from datetime import datetime, timezone
from agentd.memory.models import CompactionSegment
from agentd.memory.store import MemoryStore

def _seg(run_id: str, seq: int, tier: str, content: str) -> CompactionSegment:
    return CompactionSegment(
        id=f"{run_id}-{seq}", run_id=run_id, seq=seq, tier=tier,
        content=content, created_at=datetime.now(timezone.utc),
    )

def test_segments_round_trip_ordered(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.add_segments([_seg("r1", 1, "cold", "first"), _seg("r1", 0, "warm", "zeroth")])
    got = store.get_segments("r1")
    assert [s.seq for s in got] == [0, 1]
    assert got[0].content == "zeroth"
    assert got[0].tier == "warm"

def test_segments_scoped_by_run(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.add_segments([_seg("r1", 0, "cold", "a"), _seg("r2", 0, "cold", "b")])
    assert len(store.get_segments("r1")) == 1
    assert store.get_segments("r1")[0].content == "a"

def test_anchor_insert_then_bump_version(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    a1 = store.upsert_anchor("r1", "summary v1")
    assert a1.version == 1 and a1.summary_md == "summary v1"
    a2 = store.upsert_anchor("r1", "summary v2")
    assert a2.version == 2 and a2.summary_md == "summary v2"
    assert store.get_anchor("r1").summary_md == "summary v2"

def test_get_anchor_missing_returns_none(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    assert store.get_anchor("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.memory.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# agentd/memory/store.py
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from agentd.memory.models import AnchoredSummary, CompactionSegment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS compaction_segments (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    tier TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_run ON compaction_segments(run_id, seq);
CREATE TABLE IF NOT EXISTS anchored_summaries (
    run_id TEXT PRIMARY KEY,
    summary_md TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""

class MemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_segments(self, segments: list[CompactionSegment]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO compaction_segments "
            "(id, run_id, seq, tier, content, created_at) VALUES (?,?,?,?,?,?)",
            [(s.id, s.run_id, s.seq, s.tier, s.content, s.created_at.isoformat())
             for s in segments],
        )
        self._conn.commit()

    def get_segments(self, run_id: str) -> list[CompactionSegment]:
        rows = self._conn.execute(
            "SELECT * FROM compaction_segments WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
        return [
            CompactionSegment(
                id=r["id"], run_id=r["run_id"], seq=r["seq"], tier=r["tier"],
                content=r["content"], created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def upsert_anchor(self, run_id: str, summary_md: str) -> AnchoredSummary:
        now = datetime.now(timezone.utc)
        existing = self._conn.execute(
            "SELECT version FROM anchored_summaries WHERE run_id=?", (run_id,)
        ).fetchone()
        version = (existing["version"] + 1) if existing else 1
        self._conn.execute(
            "INSERT OR REPLACE INTO anchored_summaries "
            "(run_id, summary_md, version, updated_at) VALUES (?,?,?,?)",
            (run_id, summary_md, version, now.isoformat()),
        )
        self._conn.commit()
        return AnchoredSummary(run_id=run_id, summary_md=summary_md, version=version, updated_at=now)

    def get_anchor(self, run_id: str) -> AnchoredSummary | None:
        r = self._conn.execute(
            "SELECT * FROM anchored_summaries WHERE run_id=?", (run_id,)
        ).fetchone()
        if r is None:
            return None
        return AnchoredSummary(
            run_id=r["run_id"], summary_md=r["summary_md"], version=r["version"],
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/store.py services/agentd-py/tests/memory/test_store.py
git commit -m "feat(memory): SQLite store for compaction segments + anchored summaries"
```

---

### Task 3: Compactor — token estimation + below-threshold no-op

**Files:**
- Create: `agentd/memory/compactor.py`
- Test: `tests/memory/test_compactor.py`

**Interfaces:**
- Consumes: `MemoryStore` (Task 2), `CompactionResult` (Task 1).
- Produces:
  - `estimate_tokens(text: str) -> int` — char/4 heuristic, min 1.
  - `AnchorSummarizer = Callable[[str, str], Awaitable[str]]` — `(old_anchor, evicted_text) -> new_anchor`.
  - `Compactor.__init__(self, store: MemoryStore, summarize: AnchorSummarizer, *, window_tokens: int, trigger_frac: float = 0.65, hot_turns: int = 10)`
  - `async Compactor.maybe_compact(self, history: list[dict], run_id: str) -> CompactionResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_compactor.py
import pytest
from agentd.memory.compactor import Compactor, estimate_tokens
from agentd.memory.store import MemoryStore

async def _never_called(old: str, new: str) -> str:  # summarizer must NOT run below threshold
    raise AssertionError("summarize called below threshold")

def _msgs(n: int, size: int = 4) -> list[dict]:
    return [{"role": "user", "content": "x" * size} for _ in range(n)]

def test_estimate_tokens_charsdiv4():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("") == 1

@pytest.mark.asyncio
async def test_below_threshold_is_noop(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(store, _never_called, window_tokens=10000, trigger_frac=0.65, hot_turns=10)
    history = _msgs(3)
    result = await comp.maybe_compact(history, "r1")
    assert result.compacted is False
    assert result.history == history
    assert store.get_anchor("r1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_compactor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.memory.compactor'`

- [ ] **Step 3: Write minimal implementation**

```python
# agentd/memory/compactor.py
from __future__ import annotations
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from agentd.memory.models import CompactionResult, CompactionSegment
from agentd.memory.store import MemoryStore

logger = logging.getLogger(__name__)

AnchorSummarizer = Callable[[str, str], Awaitable[str]]

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _history_tokens(history: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) for m in history)

def _render(messages: list[dict]) -> str:
    return "\n".join(f"{m.get('role', '')}: {m.get('content', '')}" for m in messages)

class Compactor:
    def __init__(
        self,
        store: MemoryStore,
        summarize: AnchorSummarizer,
        *,
        window_tokens: int,
        trigger_frac: float = 0.65,
        hot_turns: int = 10,
    ) -> None:
        self._store = store
        self._summarize = summarize
        self._window_tokens = window_tokens
        self._trigger_frac = trigger_frac
        self._hot_turns = hot_turns

    async def maybe_compact(self, history: list[dict], run_id: str) -> CompactionResult:
        budget = self._window_tokens * self._trigger_frac
        if _history_tokens(history) < budget or len(history) <= self._hot_turns:
            anchor = self._store.get_anchor(run_id)
            return CompactionResult(
                compacted=False, history=history,
                anchor=anchor.summary_md if anchor else None,
            )
        # Compaction logic added in Task 4.
        return CompactionResult(compacted=False, history=history)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_compactor.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/compactor.py services/agentd-py/tests/memory/test_compactor.py
git commit -m "feat(memory): compactor token estimation + below-threshold no-op"
```

---

### Task 4: Compactor — over-threshold compaction (hot/evict split, anchor merge, persist)

**Files:**
- Modify: `agentd/memory/compactor.py` (replace the Task-3 placeholder return in `maybe_compact`)
- Test: `tests/memory/test_compactor.py` (add cases)

**Interfaces:**
- Consumes: `AnchorSummarizer`, `MemoryStore.add_segments`, `MemoryStore.upsert_anchor`, `MemoryStore.get_anchor`.
- Produces: `maybe_compact` now returns `compacted=True` with `history = [anchor_message] + hot`, persists evicted as segments, and merges into the anchor via the injected summarizer. Anchor message shape: `{"role": "user", "content": "[MEMORY] Summary of earlier conversation that was compacted:\n<anchor>"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_compactor.py  (append)
@pytest.mark.asyncio
async def test_over_threshold_compacts(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    captured = {}
    async def summ(old: str, evicted: str) -> str:
        captured["old"] = old
        captured["evicted"] = evicted
        return "MERGED ANCHOR"
    comp = Compactor(store, summ, window_tokens=100, trigger_frac=0.1, hot_turns=2)
    history = [{"role": "user", "content": f"msg{i}" * 20} for i in range(6)]
    result = await comp.maybe_compact(history, "r1")
    assert result.compacted is True
    # last 2 stay verbatim
    assert result.history[-2:] == history[-2:]
    # first element is the anchor message carrying the merged summary
    assert result.history[0]["content"].startswith("[MEMORY]")
    assert "MERGED ANCHOR" in result.history[0]["content"]
    # evicted (first 4) persisted as segments
    assert len(store.get_segments("r1")) == 4
    # anchor stored + versioned
    assert store.get_anchor("r1").summary_md == "MERGED ANCHOR"

@pytest.mark.asyncio
async def test_anchor_merges_not_regenerates(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.upsert_anchor("r1", "PRIOR")
    seen = {}
    async def summ(old: str, evicted: str) -> str:
        seen["old"] = old
        return old + " + NEW"
    comp = Compactor(store, summ, window_tokens=100, trigger_frac=0.1, hot_turns=2)
    history = [{"role": "user", "content": "z" * 80} for _ in range(6)]
    result = await comp.maybe_compact(history, "r1")
    assert seen["old"] == "PRIOR"          # prior anchor fed back in (anchored merge)
    assert store.get_anchor("r1").summary_md == "PRIOR + NEW"
    assert store.get_anchor("r1").version == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_compactor.py -v`
Expected: FAIL — `test_over_threshold_compacts` asserts `compacted is True` but placeholder returns `False`.

- [ ] **Step 3: Write minimal implementation**

Replace the `# Compaction logic added in Task 4.` block and its `return` in `maybe_compact` with:

```python
        hot = history[-self._hot_turns:]
        evicted = history[:-self._hot_turns]
        old = self._store.get_anchor(run_id)
        old_text = old.summary_md if old else ""
        new_anchor = await self._summarize(old_text, _render(evicted))
        now = datetime.now(timezone.utc)
        # Warm = the band nearest hot; cold = the rest. Persisted for Phase-2 recall;
        # Phase 1 summarizes uniformly (see plan Global Constraints).
        warm_start = max(0, len(evicted) - self._hot_turns)
        segments = [
            CompactionSegment(
                id=f"{run_id}-{i}-{int(now.timestamp() * 1000)}",
                run_id=run_id, seq=i,
                tier="warm" if i >= warm_start else "cold",
                content=str(m.get("content", "")), created_at=now,
            )
            for i, m in enumerate(evicted)
        ]
        self._store.add_segments(segments)
        self._store.upsert_anchor(run_id, new_anchor)
        anchor_message = {
            "role": "user",
            "content": f"[MEMORY] Summary of earlier conversation that was compacted:\n{new_anchor}",
        }
        return CompactionResult(
            compacted=True, history=[anchor_message, *hot], anchor=new_anchor,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_compactor.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/compactor.py services/agentd-py/tests/memory/test_compactor.py
git commit -m "feat(memory): compactor evicts+merges over-threshold history into anchor"
```

---

### Task 5: Compactor — summarizer-failure fallback

**Files:**
- Modify: `agentd/memory/compactor.py` (wrap the summarize call)
- Test: `tests/memory/test_compactor.py` (add case)

**Interfaces:**
- Produces: on summarizer exception, `maybe_compact` returns `compacted=True, degraded=True` with `history = [old_anchor_message?] + hot` (evicted dropped from window but still persisted as segments), never raising.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_compactor.py  (append)
@pytest.mark.asyncio
async def test_summarizer_failure_falls_back(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    async def boom(old: str, evicted: str) -> str:
        raise RuntimeError("provider down")
    comp = Compactor(store, boom, window_tokens=100, trigger_frac=0.1, hot_turns=2)
    history = [{"role": "user", "content": "y" * 80} for _ in range(6)]
    result = await comp.maybe_compact(history, "r1")
    assert result.degraded is True
    assert result.compacted is True
    assert result.history[-2:] == history[-2:]   # hot preserved
    assert len(store.get_segments("r1")) == 4     # evicted still persisted (lossless on disk)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_compactor.py::test_summarizer_failure_falls_back -v`
Expected: FAIL — `RuntimeError: provider down` propagates.

- [ ] **Step 3: Write minimal implementation**

In `maybe_compact`, wrap the summarize + persist sequence. Replace the body added in Task 4 from `new_anchor = await self._summarize(...)` onward with:

```python
        now = datetime.now(timezone.utc)
        warm_start = max(0, len(evicted) - self._hot_turns)
        segments = [
            CompactionSegment(
                id=f"{run_id}-{i}-{int(now.timestamp() * 1000)}",
                run_id=run_id, seq=i,
                tier="warm" if i >= warm_start else "cold",
                content=str(m.get("content", "")), created_at=now,
            )
            for i, m in enumerate(evicted)
        ]
        self._store.add_segments(segments)  # persist BEFORE summarize so a failure is still lossless
        try:
            new_anchor = await self._summarize(old_text, _render(evicted))
        except Exception:  # best-effort: never fail a loop iteration
            logger.warning("[memory] anchor summarize failed for run=%s; degrading", run_id, exc_info=True)
            keep = (
                [{"role": "user", "content": f"[MEMORY] (earlier context summary unavailable)\n{old_text}"}]
                if old_text else []
            )
            return CompactionResult(compacted=True, history=[*keep, *hot], anchor=old_text or None, degraded=True)
        self._store.upsert_anchor(run_id, new_anchor)
        anchor_message = {
            "role": "user",
            "content": f"[MEMORY] Summary of earlier conversation that was compacted:\n{new_anchor}",
        }
        return CompactionResult(compacted=True, history=[anchor_message, *hot], anchor=new_anchor)
```

(Move the `old = self._store.get_anchor(run_id); old_text = ...` lines to just before this block if Task 4 placed them after; they must precede the `try`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_compactor.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/compactor.py services/agentd-py/tests/memory/test_compactor.py
git commit -m "feat(memory): compactor degrades gracefully on summarizer failure"
```

---

### Task 6: MemoryHarness façade + no-op default + build factory + engine summarizer

**Files:**
- Create: `agentd/memory/harness.py`
- Modify: `agentd/memory/__init__.py` (export `MemoryHarness`, `build_memory_harness`, `NO_OP_HARNESS`)
- Modify: `agentd/memory/compactor.py` (add `make_engine_summarizer`)
- Test: `tests/memory/test_harness.py`

**Interfaces:**
- Consumes: `Compactor`, `MemoryStore`, `MemoryConfig`, `TurnPreparation`.
- Produces:
  - `MemoryHarness.__init__(self, *, enabled: bool, compactor: Compactor | None)`
  - `async MemoryHarness.prepare_turn(self, history: list[dict], run_id: str) -> TurnPreparation` — disabled or no compactor ⇒ returns history untouched; else delegates to `compactor.maybe_compact`.
  - `async MemoryHarness.recall(self, query: str, run_id: str) -> list[dict]` — Phase-2 stub, returns `[]`.
  - `NO_OP_HARNESS: MemoryHarness` — module singleton, `enabled=False`, used as the default injected value in both loops.
  - `make_engine_summarizer(reasoning_engine) -> AnchorSummarizer` — builds the production summarizer from the engine's text generation (the same entrypoint `ChatAgent` uses for QA answers — confirm the method name when wiring; it is the engine's plain-text generation call).
  - `build_memory_harness(config: MemoryConfig, reasoning_engine) -> MemoryHarness` — if `config.enabled`: construct `MemoryStore(config.db_path)`, `Compactor(...)` with `make_engine_summarizer`, return enabled harness; else return `NO_OP_HARNESS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_harness.py
import pytest
from agentd.memory.harness import MemoryHarness, NO_OP_HARNESS
from agentd.memory.compactor import Compactor
from agentd.memory.store import MemoryStore

@pytest.mark.asyncio
async def test_disabled_harness_is_passthrough():
    history = [{"role": "user", "content": "hi"}]
    prep = await NO_OP_HARNESS.prepare_turn(history, "r1")
    assert prep.history is history
    assert prep.compacted is False
    assert prep.recalled_memories == []

@pytest.mark.asyncio
async def test_enabled_harness_delegates_to_compactor(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    async def summ(old: str, evicted: str) -> str:
        return "A"
    comp = Compactor(store, summ, window_tokens=100, trigger_frac=0.1, hot_turns=2)
    harness = MemoryHarness(enabled=True, compactor=comp)
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    prep = await harness.prepare_turn(history, "r1")
    assert prep.compacted is True
    assert prep.history[0]["content"].startswith("[MEMORY]")

@pytest.mark.asyncio
async def test_recall_stub_returns_empty():
    assert await NO_OP_HARNESS.recall("anything", "r1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_harness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.memory.harness'`

- [ ] **Step 3: Write minimal implementation**

```python
# agentd/memory/harness.py
from __future__ import annotations
import logging
from agentd.memory.compactor import Compactor, make_engine_summarizer
from agentd.memory.config import MemoryConfig
from agentd.memory.models import TurnPreparation
from agentd.memory.store import MemoryStore

logger = logging.getLogger(__name__)

class MemoryHarness:
    def __init__(self, *, enabled: bool, compactor: Compactor | None) -> None:
        self._enabled = enabled
        self._compactor = compactor

    async def prepare_turn(self, history: list[dict], run_id: str) -> TurnPreparation:
        if not self._enabled or self._compactor is None:
            return TurnPreparation(history=history, recalled_memories=[], compacted=False)
        try:
            result = await self._compactor.maybe_compact(history, run_id)
        except Exception:  # best-effort: memory must never break a loop
            logger.warning("[memory] prepare_turn failed for run=%s", run_id, exc_info=True)
            return TurnPreparation(history=history, recalled_memories=[], compacted=False)
        return TurnPreparation(history=result.history, recalled_memories=[], compacted=result.compacted)

    async def recall(self, query: str, run_id: str) -> list[dict]:
        return []  # Phase 2

NO_OP_HARNESS = MemoryHarness(enabled=False, compactor=None)

def build_memory_harness(config: MemoryConfig, reasoning_engine: object) -> MemoryHarness:
    if not config.enabled:
        return NO_OP_HARNESS
    store = MemoryStore(config.db_path)
    compactor = Compactor(
        store, make_engine_summarizer(reasoning_engine),
        window_tokens=config.window_tokens, trigger_frac=config.trigger_frac,
        hot_turns=config.hot_turns,
    )
    return MemoryHarness(enabled=True, compactor=compactor)
```

Add to `agentd/memory/compactor.py`:

```python
_SUMMARY_SYSTEM = (
    "You maintain a running summary of an AI coding session. Merge the PRIOR SUMMARY and the "
    "NEW EVICTED MESSAGES into one updated summary. Preserve goals, decisions, file/symbol names, "
    "and unresolved threads. Do not drop facts from the prior summary. Be concise but lossless on "
    "decisions and identifiers. Return only the updated summary."
)

def make_engine_summarizer(reasoning_engine: object) -> AnchorSummarizer:
    async def _summarize(old_anchor: str, evicted_text: str) -> str:
        prompt = f"PRIOR SUMMARY:\n{old_anchor or '(none)'}\n\nNEW EVICTED MESSAGES:\n{evicted_text}"
        # Uses the engine's plain-text generation (same entrypoint ChatAgent uses for QA answers).
        return await reasoning_engine.generate_text(  # type: ignore[attr-defined]
            system_instructions=_SUMMARY_SYSTEM, user_payload=prompt,
        )
    return _summarize
```

> **Wiring note for the implementer:** confirm the exact text-generation method/signature on the reasoning engine (grep `generate_text` in `agentd/reasoning/` and `agentd/chat/agent.py`). Adjust the call in `make_engine_summarizer` to match; the unit tests inject their own summarizer and do not exercise this adapter, so verify it live in Task 9's manual check.

Update `agentd/memory/__init__.py` to also export `MemoryHarness`, `build_memory_harness`, `NO_OP_HARNESS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/ -v`
Expected: PASS (all memory tests green)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/harness.py services/agentd-py/agentd/memory/__init__.py services/agentd-py/agentd/memory/compactor.py services/agentd-py/tests/memory/test_harness.py
git commit -m "feat(memory): MemoryHarness facade + build factory + engine summarizer"
```

---

### Task 7: Wire MemoryHarness into ControllerLoop

**Files:**
- Modify: `agentd/chat/controller_loop.py` (constructor + top of `_iterate` loop)
- Test: `tests/memory/test_controller_loop_compaction.py`

**Interfaces:**
- Consumes: `MemoryHarness`, `NO_OP_HARNESS`.
- Produces: `ControllerLoop.__init__` gains `memory_harness: MemoryHarness = NO_OP_HARNESS` (keyword, defaulted — existing constructions unaffected). At the top of each `for iteration` in `_iterate`, before `create_controller_step`, compact in place:
  ```python
  run_id = str(plan_context.get("run_id", "chat"))
  prep = await self._memory_harness.prepare_turn(history, run_id)
  history[:] = prep.history
  ```
  `history[:]` mutates the same list `partial_history()` and downstream `.append()` calls reference.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_controller_loop_compaction.py
import pytest
from agentd.memory.harness import MemoryHarness
from agentd.memory.compactor import Compactor
from agentd.memory.store import MemoryStore

@pytest.mark.asyncio
async def test_controller_loop_accepts_and_invokes_harness(tmp_path):
    # The harness is invoked at the top of each iteration with the live history + run_id.
    store = MemoryStore(tmp_path / "m.sqlite3")
    calls = []
    async def summ(old, evicted):
        return "A"
    class SpyCompactor(Compactor):
        async def maybe_compact(self, history, run_id):
            calls.append((len(history), run_id))
            return await super().maybe_compact(history, run_id)
    comp = SpyCompactor(store, summ, window_tokens=100, trigger_frac=0.1, hot_turns=2)
    harness = MemoryHarness(enabled=True, compactor=comp)
    # Construct ControllerLoop with the project's existing scripted test fixtures, passing
    # memory_harness=harness, run one turn with seed_history of >hot_turns large messages,
    # and assert calls is non-empty and calls[0][1] == "<thread_id>".
    assert harness is not None  # replace with the real loop drive using existing fixtures
```

> **Implementer:** replace the placeholder assert with the project's standard `ControllerLoop` construction (copy the fixture wiring from an existing `tests/test_controller_loop*.py`), inject `memory_harness=harness` and `plan_context={"run_id": "thread-x", ...}`, drive one `run()` whose `seed_history` has > `hot_turns` oversized messages, then assert `calls` is non-empty and `calls[0][1] == "thread-x"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_controller_loop_compaction.py -v`
Expected: FAIL — `ControllerLoop.__init__` has no `memory_harness` param (after the implementer wires the real construction).

- [ ] **Step 3: Write minimal implementation**

In `agentd/chat/controller_loop.py`:
1. Add import: `from agentd.memory.harness import MemoryHarness, NO_OP_HARNESS`.
2. Add to `__init__` signature: `memory_harness: MemoryHarness = NO_OP_HARNESS,` and store `self._memory_harness = memory_harness`.
3. At the very top of the `for iteration in range(max_iters + 1):` body in `_iterate` (before the `if iteration == 0:` block), insert:

```python
            run_id = str(plan_context.get("run_id", "chat"))
            _prep = await self._memory_harness.prepare_turn(history, run_id)
            history[:] = _prep.history
```

4. In `ChatController` (the constructor of `ControllerLoop`), pass `memory_harness=self._memory_harness` and ensure `plan_context["run_id"] = thread_id` is set before `loop.run(...)`. Grep `ControllerLoop(` in `agentd/chat/controller.py` to find the construction site; thread the harness from `build_memory_harness` created at app startup.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_controller_loop_compaction.py -v && pytest tests/ -k controller -q`
Expected: PASS (new test) and existing controller tests still green.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/memory/test_controller_loop_compaction.py
git commit -m "feat(memory): wire MemoryHarness compaction into ControllerLoop"
```

---

### Task 8: Wire MemoryHarness into the task ToolLoop

**Files:**
- Modify: `agentd/tools/loop.py` (constructor + top of iteration loop ~line 359)
- Test: `tests/memory/test_tool_loop_compaction.py`

**Interfaces:**
- Consumes: `MemoryHarness`, `NO_OP_HARNESS`.
- Produces: `ToolLoop.__init__` gains `memory_harness: MemoryHarness = NO_OP_HARNESS` (keyword, defaulted). At the top of `for iteration in range(total_budget):` (before building `history_tail`/`create_tool_step`), compact in place using `run_id = f"{self._task_id}:{step_id}"` (or `self._task_id` if step id unavailable in scope).

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_tool_loop_compaction.py
import pytest
from agentd.memory.harness import MemoryHarness
from agentd.memory.compactor import Compactor
from agentd.memory.store import MemoryStore

@pytest.mark.asyncio
async def test_tool_loop_invokes_harness_each_iteration(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    calls = []
    async def summ(old, evicted): return "A"
    class SpyCompactor(Compactor):
        async def maybe_compact(self, history, run_id):
            calls.append(run_id)
            return await super().maybe_compact(history, run_id)
    comp = SpyCompactor(store, summ, window_tokens=100, trigger_frac=0.1, hot_turns=2)
    harness = MemoryHarness(enabled=True, compactor=comp)
    # Construct ToolLoop with existing scripted fixtures (copy from tests/test_tool_loop*.py),
    # inject memory_harness=harness, run one step whose history grows beyond hot_turns,
    # assert calls is non-empty and each entry startswith the task id.
    assert harness is not None  # replace with real loop drive
```

> **Implementer:** replace the placeholder with the project's standard `ToolLoop` construction from an existing `tests/test_tool_loop*.py`, inject `memory_harness=harness`, drive a step, and assert `calls` is non-empty.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_tool_loop_compaction.py -v`
Expected: FAIL — `ToolLoop.__init__` has no `memory_harness` param.

- [ ] **Step 3: Write minimal implementation**

In `agentd/tools/loop.py`:
1. Add import: `from agentd.memory.harness import MemoryHarness, NO_OP_HARNESS`.
2. Add to `__init__` (near the `broadcast_key`/`skip_verify` params, ~line 214): `memory_harness: MemoryHarness = NO_OP_HARNESS,` and store `self._memory_harness = memory_harness`.
3. At the top of `for iteration in range(total_budget):` (~line 359, before `history_tail=history[-8:]` is built), insert:

```python
            _run_id = f"{self._task_id}:{step_id}" if "step_id" in dir() else str(self._task_id)
            _prep = await self._memory_harness.prepare_turn(history, _run_id)
            history[:] = _prep.history
```

(Use whatever step identifier is in scope at that point; if none, `str(self._task_id)` alone is acceptable for Phase 1 — segments are still correctly scoped per task.)

4. Thread the harness from the orchestrator that constructs `ToolLoop` (grep `ToolLoop(` in `agentd/orchestrator/engine.py`), passing the same `build_memory_harness` instance created at startup.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/memory/test_tool_loop_compaction.py -v && pytest tests/ -k tool_loop -q`
Expected: PASS (new test) and existing tool-loop tests still green.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/tools/loop.py services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/memory/test_tool_loop_compaction.py
git commit -m "feat(memory): wire MemoryHarness compaction into task ToolLoop"
```

---

### Task 9: Integration test + kill-switch parity + live manual check

**Files:**
- Test: `tests/memory/test_integration_compaction.py`
- (No new source — exercises the wired system end to end.)

**Interfaces:**
- Consumes: everything above.
- Produces: an acceptance test proving (a) a long run crosses the threshold, persists segments, versions the anchor, and keeps hot turns verbatim; (b) with `enabled=False` the loop history is untouched (parity).

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_integration_compaction.py
import pytest
from agentd.memory.harness import MemoryHarness, NO_OP_HARNESS
from agentd.memory.compactor import Compactor
from agentd.memory.store import MemoryStore

@pytest.mark.asyncio
async def test_long_run_compacts_and_persists(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    async def summ(old, evicted):
        return (old + " | " if old else "") + f"summarized {len(evicted)} chars"
    comp = Compactor(store, summ, window_tokens=200, trigger_frac=0.1, hot_turns=3)
    harness = MemoryHarness(enabled=True, compactor=comp)
    history = [{"role": "user", "content": "m" * 50} for _ in range(12)]
    prep = await harness.prepare_turn(history, "run-A")
    assert prep.compacted is True
    assert prep.history[-3:] == history[-3:]            # hot verbatim
    assert len(store.get_segments("run-A")) == 9        # 12 - 3 evicted
    assert store.get_anchor("run-A").version == 1
    # second compaction merges, not regenerates
    history2 = list(prep.history) + [{"role": "user", "content": "n" * 200} for _ in range(6)]
    prep2 = await harness.prepare_turn(history2, "run-A")
    assert store.get_anchor("run-A").version == 2
    assert "|" in store.get_anchor("run-A").summary_md  # prior anchor carried forward

@pytest.mark.asyncio
async def test_disabled_is_byte_identical():
    history = [{"role": "user", "content": "x" * 9999} for _ in range(50)]
    prep = await NO_OP_HARNESS.prepare_turn(history, "run-A")
    assert prep.history is history
    assert prep.compacted is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/memory/test_integration_compaction.py -v`
Expected: FAIL until the implementations from Tasks 1–6 are present (should PASS immediately if they are — this test exercises the harness directly, so if it fails, read the assertion).

- [ ] **Step 3: Write minimal implementation**

No new source needed. If an assertion fails, fix the responsible unit (most likely the anchor-merge carry-forward in Task 4/5).

- [ ] **Step 4: Run full suite + live manual check**

```bash
cd services/agentd-py && pytest -q          # whole suite green (read FAILED lines, not exit code)
mypy agentd/memory                          # types clean
ruff check agentd/memory                    # lint clean
```

Live check of the production summarizer adapter (the one path unit tests don't cover):
```bash
# From repo root, with a provider configured:
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
AI_EDITOR_MEMORY_ENABLED=1 AI_EDITOR_MEMORY_WINDOW_TOKENS=4000 AI_EDITOR_MEMORY_HOT_TURNS=4 \
  bash scripts/stress/start-backend.sh --backend gemini --workspace "$PWD/workspaces/shadow-forge-stress" --validation-profile none
# Drive a long chat turn; confirm in logs that compaction fires and
# .agentd/memory.sqlite3 gains compaction_segments + an anchored_summaries row.
sqlite3 workspaces/shadow-forge-stress/.agentd/memory.sqlite3 \
  "SELECT run_id, version, length(summary_md) FROM anchored_summaries;"
```
Expected: at least one `anchored_summaries` row with `version >= 1`; `compaction_segments` populated; the chat turn completes coherently with the compacted history.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/tests/memory/test_integration_compaction.py
git commit -m "test(memory): end-to-end compaction + kill-switch parity"
```

---

## Self-Review

**Spec coverage (Phase 1 scope only):**
- §1 component boundaries → Tasks 1–6 (one unit per file, store is the only DB-aware unit). ✓
- §2 data model (`compaction_segments`, `anchored_summaries`) → Task 2. The `memories` table is Phase 2 — correctly absent. ✓
- §5 compaction (0.65 trigger, hot/warm/cold, anchored merge not regenerate, fallback) → Tasks 3/4/5, asserted by `test_anchor_merges_not_regenerates` + `test_summarizer_failure_falls_back`. ✓
- §7 error handling (best-effort, kill switch) → harness try/except (Task 6) + `NO_OP_HARNESS` parity (Task 9). ✓
- §8 testing (store, compactor, integration, kill switch) → Tasks 2–9. KV-cache byte-position guard is **deferred to Phase 2** (recalled-memories injection doesn't exist yet in Phase 1 — the anchor message is a normal history entry, not a tail slot). Noted, not a gap.
- §9 phasing (Phase 1 standalone, FTS5/embeddings absent) → no `sqlite-vec` dependency in this plan. ✓
- Recall stub present for Phase 2 seam (Task 6). ✓

**Placeholder scan:** the two loop-wiring tests (Tasks 7/8) intentionally defer the fixture wiring to the implementer with explicit instructions to copy existing `tests/test_controller_loop*.py` / `tests/test_tool_loop*.py` construction — this is because the exact fixture signatures live in the codebase and must be read at implementation time, not guessed here. Every source step contains complete code.

**Type consistency:** `prepare_turn → TurnPreparation.history`, `maybe_compact → CompactionResult.history`, `summarize(old, evicted) -> str` consistent across Tasks 3–9. `run_id` is a `str` everywhere. `memory_harness: MemoryHarness = NO_OP_HARNESS` identical in both loops.

**One known soft spot:** `make_engine_summarizer` calls `reasoning_engine.generate_text(...)` — the exact method name/signature must be confirmed against `agentd/reasoning/` during Task 6 (flagged inline). This is the only place the plan references a codebase method it hasn't pinned, and it is covered by Task 9's live check rather than unit tests by design.
