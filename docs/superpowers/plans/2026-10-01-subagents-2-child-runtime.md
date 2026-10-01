# Sub-agents Plan 2 — Child Runtime, Dispatch & Write Guard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the chat controller dispatch parallel sub-agents that explore and edit the shared workspace, each with its own context window, its own permissions, and a full report returned to the dispatcher — with a thread-wide write log that refuses (with attribution) any edit to a file another agent changed after the editor's last read.

**Architecture:** A child is a `ControllerLoop` in the `AGENT` phase (Plan 1A shipped its schema, prompts and payload). A process-wide `SubAgentRuntime` owns a bounded semaphore, an `AgentRegistry` and per-thread `WorkspaceWriteLog`s; `SubAgentToolSource` exposes `dispatch_agents`. Children run against the real workspace through their own `TurnEditSession` shadows, raise gates through Plan 1B's multi-gate machinery tagged with their agent, and stream to their own broadcaster channel; their transcripts persist to a new `chat_agents` table. The UI (Phase 4) is out of scope — this plan ships the backend plus the minimum TypeScript contract changes that keep thread reads parsing.

**Tech Stack:** Python 3.13, pytest + pytest-asyncio, SQLite (`chat.sqlite3`), FastAPI; TypeScript + Zod + vitest for the contract tail of Part E.

**Spec:** `docs/superpowers/specs/2026-09-29-subagents-design.md` **rev 11** — Phase 2 of §16: §5 (child runtime), §6 (dispatch), §7 (write guard), §8 (child gates), §11.3–§11.7 (storage, stop, restart, rewind, failures), §12 flags, §13 logs. Built-in agents only (`explore`, `general-purpose`, §9.4); definition discovery is Phase 3.

**Prerequisites:** Plans 1A and 1B are merged on `feat/subagents` (tagged templates, `RenderContext`, AGENT schema/payload, multi-gate `pending_controller_gates` + `gate_id` routes, `ApprovalOutcome`, `StaleWriteError`, the `TurnEditSession` re-seed fix).

## How this plan is organized

One file, five parts. Each part is one reviewable patch series that leaves the suite green on its own; later parts only build on earlier ones.

| Part | Scope | Spec |
|---|---|---|
| **A — Write guard** | flag resolver, `WorkspaceWriteLog`, read tracking, both staleness checks, the loop's `"stale"` branch, `ToolOutput.workspace_changes`, parent wiring | §7, §6.4, §12 |
| **B — Child building blocks** | `AgentContext`, `RenderContext.for_agent`, config, permissions + tool filtering, VCS guard, child memory/skills modes | §3, §5.2, §5.3, §5.6, §4.6.6 |
| **C — AGENT loop runtime** | `ControllerLoop(agent=…)`: `report` terminal, final-iteration narrowing, ledger bypass + "Unfinished:", child channel `seq`, fallback report | §5.1, §5.5, §6.5 |
| **D — Runtime & dispatch** | `chat_agents` table, `SubAgentRuntime` + registry + slot token, child-scoped callbacks & gates, `dispatch_agents`, built-ins, parent teaching, thread events | §5.5, §6, §8, §9.4, §11.2, §11.3 |
| **E — Lifecycle, API & contracts** | routes, `/live agents`, `/v1/config`, stop cascade + synthetic dispatch result, restart reap, rewind, editor-client/webview contract tail | §11.1, §11.3–§11.7, §12 |

## Global Constraints

- **`CRUCIBLE_SUBAGENTS_ENABLED` stays OFF by default** through this phase (spec §14, §16). Every sub-agent test opts in with `monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")`. With the flag off, the parent's prompts, tools, history strings and persistence must be **byte-identical** to today: `tests/test_prompt_goldens.py` (Plan 1A) stays green on every commit, unmodified.
- **Never truncate a child's report** (D8). Only `/live`'s `report_preview` is cut, and it is UI-only.
- **Single-process asyncio discipline:** every check-then-act on shared state (write log, gate list, slot token, registry) happens with **no `await` between the check and the act**.
- **Degrade, never raise, on best-effort paths** (artifacts, memory, broadcasts), matching the controller's existing conventions; **raise explicitly** on programming errors (unregistered agent ids, accounting bugs — `BoundedSemaphore` over-release is a loud `ValueError` by design).
- **Test hygiene (repo rules):** never pass `-q` to pytest; never pipe pytest (redirect to a file, check `$?`); `@pytest.mark.asyncio` or `asyncio.run`, never `get_event_loop().run_until_complete`; use `ChatThreadStore(tmp_path / …)` (real SQLite), not in-memory stores, for anything that could hide object-identity bugs.
- **Pre-existing failure:** `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` fails before this work; compare failure sets, never counts.
- **Lint:** new files must be ruff-clean; touched modules must add **no** new findings — compare per-file/per-rule counts against the commit before the part starts (procedure in each part's verification).
- Backend commands run from `services/agentd-py` with `.venv/bin/python`; TypeScript from the repo root; rebuild `apps/editor-client` before the extension typecheck.

## Review Focus

1. **Catch order around `accept()`** — `StaleWriteError` is a `RuntimeError` (rev 11 §7.4); the `"stale"` branch must catch it before anything broader. Pinned in Part A.
2. **Parent byte-identity with the flag off** — no write log, no read observer, no new history string. Pinned by the goldens plus Part A's flag-off wiring test.
3. **Slot accounting under cancellation** — release only if held; a cancel during re-acquire must never over-release. Pinned in Part D.
4. **Permission inheritance** — read-only-ness propagates down the tree; `allow_all` never widens `plan`. Pinned in Part B.
5. **Child events never touch the parent's channel or transcript** except the three low-volume thread events and gate pokes. Pinned in Parts C/D.

---

# Part A — Shared-workspace write guard

Everything in this part is inert unless `CRUCIBLE_SUBAGENTS_ENABLED` is on. With it on and no children yet, the parent is guarded against nothing (no other agent writes), so the observable change is zero — the tests inject a sibling write directly into the log.

### File structure (Part A)

| File | Responsibility |
|---|---|
| `agentd/chat/controller_factory.py` | `is_subagents_enabled()` |
| `agentd/subagents/__init__.py` | new package marker |
| `agentd/subagents/write_log.py` | `canonical_path`, `WriteRecord`, `WorkspaceWriteLog`, `WriteGuard`, `MAIN_AGENT_ID` |
| `agentd/chat/edit_session.py` | `StaleWriteError.writer_label`; `write_guard` param; Check 1 in `apply`, Check 2 in `accept`, own-promote recording |
| `agentd/tools/registry.py` | `ToolOutput.workspace_changes` |
| `agentd/tools/sources.py` | `BuiltinToolSource(read_observer=…)` |
| `agentd/chat/controller_loop.py` | `workspace_changes` bookkeeping; the `"stale"` accept branch |
| `agentd/chat/controller.py` | per-thread write logs; guard + observer wiring; `"stale"` in `_edit_record_cb` |

### Task A1: The feature flag resolver

**Files:**
- Modify: `agentd/chat/controller_factory.py`
- Test: `tests/test_subagents_flag.py`

**Interfaces:**
- Produces: `is_subagents_enabled() -> bool` — truthy only for `1/true/yes/on` (case-insensitive, stripped); unset → False (OFF through Phases 1–4; Phase 5 flips the default).

- [ ] **Step 1: Write the failing test**

Create `tests/test_subagents_flag.py`:

```python
"""CRUCIBLE_SUBAGENTS_ENABLED (spec §12): default OFF until Phase 5 flips it."""
import pytest

from agentd.chat.controller_factory import is_subagents_enabled


def test_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED", raising=False)
    assert is_subagents_enabled() is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), (" YES ", True), ("on", True),
    ("0", False), ("false", False), ("off", False), ("", False), ("maybe", False),
])
def test_parsing(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", raw)
    assert is_subagents_enabled() is expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_subagents_flag.py > /tmp/pa1.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/pa1.txt`
Expected: `exit=2`, `ImportError` (`is_subagents_enabled`).

- [ ] **Step 3: Implement**

In `agentd/chat/controller_factory.py`, directly after `is_skills_enabled`:

```python
def is_subagents_enabled() -> bool:
    """Whether the controller can dispatch sub-agents and guards shared-workspace writes
    (spec §12). Default OFF through Phases 1–4 — Phase 5 flips it after the live smoke.
    Opt in with CRUCIBLE_SUBAGENTS_ENABLED=1."""
    return os.getenv("CRUCIBLE_SUBAGENTS_ENABLED", "0").strip().lower() in _TRUTHY
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_subagents_flag.py > /tmp/pa1.txt 2>&1; echo exit=$?; tail -1 /tmp/pa1.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_factory.py tests/test_subagents_flag.py
git commit -m "feat(subagents): CRUCIBLE_SUBAGENTS_ENABLED flag resolver (default off)"
```

---

### Task A2: `WorkspaceWriteLog` and the canonical path key

**Files:**
- Create: `agentd/subagents/__init__.py`, `agentd/subagents/write_log.py`
- Test: `tests/test_write_log.py`

**Interfaces:**
- Produces:
  - `MAIN_AGENT_ID = "main"`
  - `canonical_path(workspace_root: Path, raw: str) -> str | None` — workspace-relative POSIX key; `None` for empty, `.`, or anything resolving outside the workspace.
  - `@dataclass(frozen=True) WriteRecord(agent_id: str, label: str, name: str, seq: int)`
  - `WorkspaceWriteLog` with: `seq` (property), `register_agent(agent_id)`, `note_read(agent_id, path)`, `note_promote(agent_id, label, name, paths) -> int`, `stale_writer(agent_id, path) -> WriteRecord | None`, `note_stale_refusal(agent_id) -> int`, `stale_refusals(agent_id) -> int`, `read_observer(workspace_root, agent_id) -> Callable[[str], None]`.
  - `@dataclass(frozen=True) WriteGuard(log: WorkspaceWriteLog, agent_id: str, label: str, name: str)`
- Rules (spec §7.3): a read records `last_seen[path] = seq`; an own promote bumps `seq` once and records every promoted path (`last_write` + the promoter's `last_seen`); **stale** ⇔ `last_write[path].agent_id != me and last_write[path].seq > max(spawn_seq, last_seen.get(path, -1))`. `main` is pre-registered with `spawn_seq = 0`; any other agent must be registered (spawn_seq = the current seq) before use — using an unregistered id raises `KeyError` (a wiring bug, never a runtime condition).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_write_log.py`:

```python
"""WorkspaceWriteLog (spec §7): attributed staleness for the shared workspace."""
from pathlib import Path

import pytest

from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, canonical_path


def test_canonical_path_normalizes_inside_and_rejects_outside(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    assert canonical_path(ws, "a/b.py") == "a/b.py"
    assert canonical_path(ws, "./a/b.py") == "a/b.py"
    assert canonical_path(ws, "a/../a/b.py") == "a/b.py"
    assert canonical_path(ws, str(ws / "a" / "b.py")) == "a/b.py"
    assert canonical_path(ws, "../outside.py") is None
    assert canonical_path(ws, str(tmp_path / "x.py")) is None
    assert canonical_path(ws, "") is None
    assert canonical_path(ws, ".") is None


def test_a_sibling_write_after_spawn_is_stale_until_reread() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.register_agent("a2")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    record = log.stale_writer("a2", "x.py")
    assert record is not None
    assert (record.agent_id, record.label, record.name) == ("a1", "impl", "general-purpose")
    log.note_read("a2", "x.py")
    assert log.stale_writer("a2", "x.py") is None


def test_own_writes_are_never_stale() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    assert log.stale_writer("a1", "x.py") is None


def test_writes_before_an_agent_spawned_are_not_stale_for_it() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    log.register_agent("late")
    assert log.stale_writer("late", "x.py") is None


def test_main_is_guarded_and_its_reads_persist_across_turns() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_read(MAIN_AGENT_ID, "x.py")
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    assert log.stale_writer(MAIN_AGENT_ID, "x.py") is not None
    log.note_read(MAIN_AGENT_ID, "x.py")
    assert log.stale_writer(MAIN_AGENT_ID, "x.py") is None


def test_one_promote_is_one_sequence_step() -> None:
    log = WorkspaceWriteLog()
    before = log.seq
    assert log.note_promote(MAIN_AGENT_ID, "main", "main", ["a.py", "b.py"]) == before + 1
    assert log.seq == before + 1


def test_an_unregistered_agent_is_a_wiring_error() -> None:
    log = WorkspaceWriteLog()
    with pytest.raises(KeyError, match="ghost"):
        log.note_read("ghost", "x.py")
    with pytest.raises(KeyError, match="ghost"):
        log.note_promote("ghost", "g", "g", ["x.py"])


def test_stale_refusals_are_counted_per_agent() -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    assert log.note_stale_refusal("a1") == 1
    assert log.note_stale_refusal("a1") == 2
    assert log.stale_refusals("a1") == 2
    assert log.stale_refusals(MAIN_AGENT_ID) == 0


def test_read_observer_canonicalizes_and_ignores_outside_paths(tmp_path: Path) -> None:
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    observe = log.read_observer(tmp_path, MAIN_AGENT_ID)
    log.note_promote("a1", "impl", "general-purpose", ["x.py"])
    observe("./x.py")
    assert log.stale_writer(MAIN_AGENT_ID, "x.py") is None
    observe("../elsewhere.py")  # outside the workspace: silently not tracked
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_write_log.py > /tmp/pa2.txt 2>&1; echo exit=$?; grep -m1 "ModuleNotFoundError\|ImportError" /tmp/pa2.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/__init__.py`:

```python
"""Sub-agents (P5): child agents dispatched by the chat controller (spec 2026-09-29)."""
```

Create `agentd/subagents/write_log.py`:

```python
"""Thread-wide write log + attributed staleness guard for the shared workspace (spec §7).

Every promote — the main agent's and every sub-agent's — bumps one monotonic sequence and
records who wrote each file; every successful read_file records what the reader last saw.
An agent editing a file that ANOTHER agent promoted after the editor last saw it (or after
the editor spawned) is refused with that agent named, so it re-reads instead of silently
overwriting a sibling's work.

In memory only, one log per thread: after a backend restart the log is empty, which can
only ever miss a refusal, never produce a false one (spec §7.1).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAIN_AGENT_ID = "main"


def canonical_path(workspace_root: Path, raw: str) -> str | None:
    """The workspace-relative POSIX key for `raw` (relative, `./`-prefixed, or absolute
    inside the workspace), or None when it is empty or resolves outside the workspace.

    Reads and patch-op `file` values arrive in every one of those spellings; keying the
    log on one canonical form is what makes "read ./a.py, then edit a.py" count."""
    text = raw.strip()
    if not text:
        return None
    root = workspace_root.resolve()
    candidate = Path(text)
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = absolute.resolve().relative_to(root)
    except ValueError:
        return None
    key = PurePosixPath(*relative.parts).as_posix()
    return None if key in ("", ".") else key


@dataclass(frozen=True)
class WriteRecord:
    agent_id: str
    label: str
    name: str
    seq: int


@dataclass
class _AgentView:
    spawn_seq: int
    last_seen: dict[str, int] = field(default_factory=dict)
    stale_refusals: int = 0


class WorkspaceWriteLog:
    """Sequence of promotes + per-agent read watermarks for one thread.

    Every method is synchronous and await-free, so a check (`stale_writer`) and the
    promote that follows it can never interleave with another agent's promote in
    single-process asyncio."""

    def __init__(self) -> None:
        self._seq = 0
        self._last_write: dict[str, WriteRecord] = {}
        # main's spawn_seq is 0 and its watermarks persist across turns: a parent editing
        # a file a child changed in an EARLIER turn, without re-reading, is refused too.
        self._agents: dict[str, _AgentView] = {MAIN_AGENT_ID: _AgentView(spawn_seq=0)}

    @property
    def seq(self) -> int:
        return self._seq

    def register_agent(self, agent_id: str) -> None:
        """A sub-agent sees the workspace as of its spawn: writes before it are not stale."""
        self._agents[agent_id] = _AgentView(spawn_seq=self._seq)

    def _view(self, agent_id: str) -> _AgentView:
        view = self._agents.get(agent_id)
        if view is None:
            raise KeyError(f"agent {agent_id!r} is not registered with this write log")
        return view

    def note_read(self, agent_id: str, path: str) -> None:
        self._view(agent_id).last_seen[path] = self._seq

    def note_promote(self, agent_id: str, label: str, name: str, paths: list[str]) -> int:
        """Record one promote of `paths` by `agent_id`; returns the new sequence number."""
        view = self._view(agent_id)
        self._seq += 1
        for path in paths:
            self._last_write[path] = WriteRecord(agent_id, label, name, self._seq)
            view.last_seen[path] = self._seq
        return self._seq

    def stale_writer(self, agent_id: str, path: str) -> WriteRecord | None:
        """The other agent whose promote `agent_id` has not seen, or None when it is safe."""
        record = self._last_write.get(path)
        if record is None or record.agent_id == agent_id:
            return None
        view = self._view(agent_id)
        if record.seq > max(view.spawn_seq, view.last_seen.get(path, -1)):
            return record
        return None

    def note_stale_refusal(self, agent_id: str) -> int:
        view = self._view(agent_id)
        view.stale_refusals += 1
        return view.stale_refusals

    def stale_refusals(self, agent_id: str) -> int:
        return self._view(agent_id).stale_refusals

    def read_observer(self, workspace_root: Path, agent_id: str) -> Callable[[str], None]:
        """A callback for BuiltinToolSource: records a successful read_file of `raw`.
        Paths outside the workspace are not tracked (they can never be promoted)."""
        def observe(raw: str) -> None:
            key = canonical_path(workspace_root, raw)
            if key is not None:
                self.note_read(agent_id, key)
        return observe


@dataclass(frozen=True)
class WriteGuard:
    """What a TurnEditSession needs to check and record writes for one agent."""
    log: WorkspaceWriteLog
    agent_id: str
    label: str
    name: str
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_write_log.py > /tmp/pa2.txt 2>&1; echo exit=$?; tail -1 /tmp/pa2.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents tests/test_write_log.py
git commit -m "feat(subagents): thread-wide write log with attributed staleness"
```

---

### Task A3: Guarded `TurnEditSession` — Check 1 (apply) and Check 2 (accept)

**Files:**
- Modify: `agentd/chat/edit_session.py`
- Test: `tests/test_write_guard_session.py`

**Interfaces:**
- Consumes: `WriteGuard`, `canonical_path` (A2); `StaleWriteError` (Plan 1B).
- Changes:
  - `StaleWriteError(path, message, *, writer_label: str = "")` — gains `.writer_label` (the breadcrumb and the loop need who wrote it).
  - `TurnEditSession(..., write_guard: WriteGuard | None = None)`.
  - `apply()` — **before** any patching or checkpoint capture, raises `StaleWriteError` for the first touched file with a stale writer (Check 1).
  - `accept()` — immediately before `promote_files`, re-checks every pending file (Check 2); on a stale file it calls `reject()` (restoring the shadow from real) and raises. The check and the synchronous promote have **no `await` between them**. After promoting, records the promote in the log.
  - Every refusal increments the agent's `stale_refusals` and logs `[subagent] stale-refusal path=… by=…`.
  - Message (spec §7.4): "`` `{path}` was modified by agent `{label}` ({name}) after your last read — read it again before editing. ``"
- Unguarded sessions (`write_guard=None`, the flag-off parent and every existing caller) behave exactly as before.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_write_guard_session.py`:

```python
"""TurnEditSession + WriteGuard (spec §7.4): Check 1 at apply, Check 2 at accept."""
from pathlib import Path

import pytest

from agentd.chat.edit_session import StaleWriteError, TurnEditSession
from agentd.patch.engine import PatchEngine
from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, WriteGuard
from agentd.workspace.shadow import ShadowWorkspaceManager


def _sr(file: str, search: str, replace: str) -> list[dict[str, object]]:
    return [{"op": "search_replace", "file": file, "search": search, "replace": replace,
             "reason": "r"}]


def _session(tmp_path: Path, real: Path, log: WorkspaceWriteLog) -> TurnEditSession:
    return TurnEditSession(
        turn_id="guarded", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
        write_guard=WriteGuard(log, MAIN_AGENT_ID, "main", "main"))


def _workspace(tmp_path: Path) -> Path:
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    return real


@pytest.mark.asyncio
async def test_apply_refuses_a_file_a_sibling_changed_after_the_last_read(tmp_path: Path) -> None:
    real = _workspace(tmp_path)
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])
    sess = _session(tmp_path, real, log)
    with pytest.raises(StaleWriteError) as caught:
        await sess.apply(_sr("./f.py", "x = 1", "x = 2"))
    assert caught.value.path == "f.py"
    assert caught.value.writer_label == "impl"
    assert str(caught.value) == ("`f.py` was modified by agent `impl` (general-purpose) "
                                 "after your last read — read it again before editing.")
    assert log.stale_refusals(MAIN_AGENT_ID) == 1
    assert (real / "f.py").read_text() == "x = 1\n"
    await sess.close()


@pytest.mark.asyncio
async def test_after_a_reread_the_edit_lands_and_is_recorded(tmp_path: Path) -> None:
    real = _workspace(tmp_path)
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])
    log.note_read(MAIN_AGENT_ID, "f.py")
    sess = _session(tmp_path, real, log)
    await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 2\n"
    # main's promote is now unseen by a1: a1 editing f.py would be refused.
    record = log.stale_writer("a1", "f.py")
    assert record is not None and record.agent_id == MAIN_AGENT_ID
    await sess.close()


@pytest.mark.asyncio
async def test_accept_refuses_when_a_sibling_promoted_while_the_edit_was_held(
    tmp_path: Path,
) -> None:
    real = _workspace(tmp_path)
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    sess = _session(tmp_path, real, log)
    await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    # A sibling promotes f.py while this edit sits at a review gate.
    (real / "f.py").write_text("x = 1\ny = 9\n")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])
    with pytest.raises(StaleWriteError) as caught:
        await sess.accept()
    assert caught.value.writer_label == "impl"
    assert (real / "f.py").read_text() == "x = 1\ny = 9\n"  # the sibling's work survives
    # accept() restored the shadow; after a re-read the next edit builds on the sibling's.
    log.note_read(MAIN_AGENT_ID, "f.py")
    await sess.apply(_sr("f.py", "y = 9", "y = 10"))
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 1\ny = 10\n"
    await sess.close()


@pytest.mark.asyncio
async def test_an_unguarded_session_never_checks(tmp_path: Path) -> None:
    real = _workspace(tmp_path)
    sess = TurnEditSession(
        turn_id="plain", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine())
    await sess.apply(_sr("f.py", "x = 1", "x = 2"))
    await sess.accept()
    assert (real / "f.py").read_text() == "x = 2\n"
    await sess.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_write_guard_session.py > /tmp/pa3.txt 2>&1; echo exit=$?; grep -m1 TypeError /tmp/pa3.txt`
Expected: `exit=1`; `TypeError: TurnEditSession.__init__() got an unexpected keyword argument 'write_guard'`.

- [ ] **Step 3: Implement**

In `agentd/chat/edit_session.py`:

1. Imports: add `import logging` (above `import shutil`) and `from agentd.subagents.write_log import WriteGuard, canonical_path` (after the `agentd.patch…` imports, keeping the block sorted), and below the imports add `logger = logging.getLogger(__name__)`.
2. Replace `StaleWriteError` with:

```python
class StaleWriteError(PatchPreflightFailed):
    """An agent tried to edit a file another agent changed after its last read
    (spec §7.4). Carries a STALE_READ issue so the loop's PATCH FAILED branch gives the
    re-read guidance, and `writer_label` so the durable record can name the writer."""

    def __init__(self, path: str, message: str, *, writer_label: str = "") -> None:
        super().__init__(message, [PatchPreflightIssue(
            code=PatchFailureCode.STALE_READ, file=path, message=message)])
        self.path = path
        self.writer_label = writer_label
```

3. `TurnEditSession.__init__`: add the keyword parameter `write_guard: WriteGuard | None = None,` after `checkpoint_cb`, and store it: `self._write_guard = write_guard`.
4. Add this method to `TurnEditSession` (above `_ensure_shadow`):

```python
    def _raise_if_stale(self, paths: list[str]) -> None:
        """Refuse the first path another agent promoted after this agent last saw it.
        Synchronous on purpose: callers pair it with the promote/patch that follows
        with no await in between (spec §7.4)."""
        guard = self._write_guard
        if guard is None:
            return
        for raw in paths:
            key = canonical_path(self._real, raw)
            if key is None:
                continue
            writer = guard.log.stale_writer(guard.agent_id, key)
            if writer is None:
                continue
            guard.log.note_stale_refusal(guard.agent_id)
            logger.info("[subagent] stale-refusal path=%s by=%s agent=%s",
                        key, writer.label, guard.agent_id)
            raise StaleWriteError(
                key,
                f"`{key}` was modified by agent `{writer.label}` ({writer.name}) after "
                "your last read — read it again before editing.",
                writer_label=writer.label)
```

5. `apply()`: insert `self._raise_if_stale(touched)` on the line after `touched = [...]` (before the checkpoint callback — a refused edit touches nothing).
6. Replace `accept()` with:

```python
    async def accept(self) -> None:
        assert self._shadow is not None
        if self._write_guard is not None:
            try:
                # Check 2: a sibling may have promoted one of these files while this edit
                # was held at a review gate. Check + promote below are await-free.
                self._raise_if_stale(self._pending_touched)
            except StaleWriteError:
                await self.reject()  # restore the shadow from real (shadow == real)
                raise
        promote_files(self._shadow, self._real, self._pending_touched)
        if self._write_guard is not None:
            guard = self._write_guard
            keys = [k for k in (canonical_path(self._real, p) for p in self._pending_touched)
                    if k is not None]
            guard.log.note_promote(guard.agent_id, guard.label, guard.name, keys)
        self._pending_touched = []
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_write_guard_session.py tests/test_turn_edit_session.py tests/test_stale_write_error.py tests/test_rewind_integration.py > /tmp/pa3.txt 2>&1; echo exit=$?; tail -1 /tmp/pa3.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/edit_session.py tests/test_write_guard_session.py
git commit -m "feat(subagents): guard turn edits against unseen sibling writes"
```

---

### Task A4: `ToolOutput.workspace_changes` and the loop's bookkeeping

**Files:**
- Modify: `agentd/tools/registry.py`, `agentd/chat/controller_loop.py`
- Test: `tests/test_workspace_changes_loop.py`

**Interfaces:**
- Produces: `ToolOutput.workspace_changes: list[str]` (default empty). A tool that promoted files on the agent's behalf (Part D's `dispatch_agents`) sets it.
- Loop behavior (spec §6.4), in the `tool_call` branch after the result is appended: when `out.workspace_changes` is non-empty → `seen.clear()`, `self._edit_applied = True`, and `retrieval_delta_cb(files)` whose non-empty note is appended as a `retrieval_refresh` tool_result — the same bookkeeping as the loop's own accepted edit. Empty (every existing tool) → nothing changes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_workspace_changes_loop.py`:

```python
"""ToolOutput.workspace_changes (spec §6.4): a tool that promoted files on the agent's
behalf gets the same bookkeeping as the loop's own accepted edit."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.registry import ToolDefinition, ToolOutput
from agentd.tools.sources import AggregatingToolRegistry


class _ChangingSource:
    name = "fake"

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="fake_dispatch", description="d",
                               parameters={"type": "object", "properties": {}})]

    def owns(self, tool: str) -> bool:
        return tool == "fake_dispatch"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        return ToolOutput(output="children done", workspace_changes=["api/a.py"])


@pytest.mark.asyncio
async def test_workspace_changes_clear_dedup_mark_edited_and_refresh(tmp_path: Path) -> None:
    call = {"type": "tool_call", "thought": "t", "tool": "fake_dispatch", "args": {}}
    steps = [call, call, {"type": "submit_changes", "thought": "d", "summary": "ok"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([_ChangingSource()]), EventBroadcaster(),
        channel_id="c1", phase_sm=ControllerPhaseSM())
    deltas: list[list[str]] = []

    async def delta_cb(files: list[str]) -> str | None:
        deltas.append(files)
        return f"refreshed {files}"

    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6,
                         retrieval_delta_cb=delta_cb)
    assert out.kind == "submit_changes"
    contents = [str(m.get("content", "")) for m in out.history or []]
    assert not any("DUPLICATE BLOCKED" in c for c in contents)  # the repeat was allowed
    assert deltas == [["api/a.py"], ["api/a.py"]]
    assert contents.count("refreshed ['api/a.py']") == 2
    assert loop._edit_applied is True


@pytest.mark.asyncio
async def test_plain_tools_are_untouched(tmp_path: Path) -> None:
    assert ToolOutput(output="x").workspace_changes == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_workspace_changes_loop.py > /tmp/pa4.txt 2>&1; echo exit=$?; grep -m1 TypeError /tmp/pa4.txt`
Expected: `exit=1`; `TypeError: ToolOutput.__init__() got an unexpected keyword argument 'workspace_changes'`.

- [ ] **Step 3: Implement**

`agentd/tools/registry.py`: change `from dataclasses import dataclass` to `from dataclasses import dataclass, field` and replace `ToolOutput` with:

```python
@dataclass
class ToolOutput:
    output: str
    is_error: bool = False
    # Files a tool promoted on the agent's behalf (spec §6.4 — a sub-agent dispatch). The
    # loop treats a non-empty list like its own accepted edit; never parsed from `output`.
    workspace_changes: list[str] = field(default_factory=list)
```

`agentd/chat/controller_loop.py`, `tool_call` branch: replace

```python
                history.append(assistant_turn(resp))
                history.append({"role": "tool_result", "tool": tool, "content": out.output})
                continue
            if atype == "clarify":
```

with

```python
                history.append(assistant_turn(resp))
                history.append({"role": "tool_result", "tool": tool, "content": out.output})
                if out.workspace_changes:
                    # Another agent promoted files on this agent's behalf (a dispatch,
                    # spec §6.4): the same bookkeeping as this loop's own accepted edit —
                    # repeats are no longer duplicates, the turn has edited, and the tail
                    # gets a compact refresh note (never a seed rewrite).
                    seen.clear()
                    self._edit_applied = True
                    if retrieval_delta_cb is not None:
                        delta = await retrieval_delta_cb(list(out.workspace_changes))
                        if delta:
                            history.append({
                                "role": "tool_result", "tool": "retrieval_refresh",
                                "content": delta})
                continue
            if atype == "clarify":
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_workspace_changes_loop.py tests/test_tools_registry.py tests/test_prompt_goldens.py > /tmp/pa4.txt 2>&1; echo exit=$?; tail -1 /tmp/pa4.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/tools/registry.py agentd/chat/controller_loop.py tests/test_workspace_changes_loop.py
git commit -m "feat(controller): ToolOutput.workspace_changes drives edit bookkeeping"
```

---

### Task A5: Read tracking in `BuiltinToolSource`

**Files:**
- Modify: `agentd/tools/sources.py`
- Test: `tests/test_builtin_read_observer.py`

**Interfaces:**
- Produces: `BuiltinToolSource(..., read_observer: Callable[[str], None] | None = None)` — called with the **raw** requested path (after alias normalization: `file`/`filepath`/`file_path` → `path`) after every `read_file` whose result is not an error. `search_code` never counts (spec §7.3). Canonicalization is the observer's job (`WorkspaceWriteLog.read_observer`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_builtin_read_observer.py`:

```python
"""BuiltinToolSource read tracking (spec §7.3): only a SUCCESSFUL read_file counts."""
from pathlib import Path

import pytest

from agentd.tools.sources import BuiltinToolSource


@pytest.mark.asyncio
async def test_only_successful_reads_are_observed(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    seen: list[str] = []
    src = BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path,
                            read_observer=seen.append)
    ok = await src.execute("read_file", {"path": "./f.py"})
    alias = await src.execute("read_file", {"file_path": "f.py"})
    missing = await src.execute("read_file", {"path": "nope.py"})
    await src.execute("search_code", {"pattern": "x"})
    assert not ok.is_error and not alias.is_error and missing.is_error
    assert seen == ["./f.py", "f.py"]


@pytest.mark.asyncio
async def test_no_observer_is_the_default(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    src = BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)
    assert not (await src.execute("read_file", {"path": "f.py"})).is_error
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_builtin_read_observer.py > /tmp/pa5.txt 2>&1; echo exit=$?; grep -m1 TypeError /tmp/pa5.txt`
Expected: `exit=1`; `TypeError: … unexpected keyword argument 'read_observer'`.

- [ ] **Step 3: Implement**

In `agentd/tools/sources.py`: add `from collections.abc import Callable` to the imports and `from agentd.tools.arg_aliases import normalize_tool_args` (sorted, before `agentd.tools.registry`). In `BuiltinToolSource.__init__` add the keyword parameter `read_observer: Callable[[str], None] | None = None,` after `render_ctx`, store `self._read_observer = read_observer`, and replace `execute` with:

```python
    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        out = await self._inner.execute(tool, args)
        if tool == "read_file" and not out.is_error and self._read_observer is not None:
            # The write guard's read watermark (spec §7.3): only a read that actually
            # returned content counts; an errored read proves nothing was seen.
            raw = normalize_tool_args(tool, args).get("path")
            if isinstance(raw, str):
                self._read_observer(raw)
        return out
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_builtin_read_observer.py tests/test_tools_registry.py > /tmp/pa5.txt 2>&1; echo exit=$?; tail -1 /tmp/pa5.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/tools/sources.py tests/test_builtin_read_observer.py
git commit -m "feat(tools): observe successful read_file calls for the write guard"
```

---

### Task A6: The loop's `"stale"` branch and its durable record

**Files:**
- Modify: `agentd/chat/controller_loop.py`, `agentd/chat/controller.py`
- Test: `tests/test_stale_edit_branch.py`

**Interfaces:**
- `EditRecordCb`'s decision argument gains a third value, `"stale"` (with `"accept"`/`"reject"`).
- Loop (spec §7.4): the edit branch resolves the decision first (auto-accept or the gate), then calls `accept()`/`reject()`. `StaleWriteError` from `accept()` is caught **first and narrowly** → `edit_record_cb(diff, "stale", "<path> changed since it was read (by <label>)", was_gated)`, then the edit intent (no `patch_ops`) + `PATCH FAILED: <message> <guidance>` tool_result, then `continue`. This holds even when the user clicked Accept.
- `ChatController._edit_record_cb` for `"stale"`: the inert `diff_card` is `resolved: "discarded"` and the breadcrumb "`✗ Not applied: <reason>`" is written **whether or not the edit was gated**.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stale_edit_branch.py`:

```python
"""The loop's "stale" accept branch + its durable record (spec §7.4)."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.storage import ChatThreadStore
from agentd.chat.turn_control import ChatTurnControl
from agentd.domain.models import DiffEntry
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, WriteGuard
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


def _edit(search: str, replace: str) -> dict[str, object]:
    return {"type": "edit", "thought": "t", "patch_ops": [
        {"op": "search_replace", "file": "f.py", "search": search, "replace": replace,
         "reason": "r"}]}


@pytest.mark.asyncio
async def test_an_accepted_edit_that_went_stale_at_the_gate_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    log = WorkspaceWriteLog()
    log.register_agent("a1")
    session = TurnEditSession(
        turn_id="t", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"), patch_engine=PatchEngine(),
        write_guard=WriteGuard(log, MAIN_AGENT_ID, "main", "main"))
    registry = AggregatingToolRegistry([BuiltinToolSource(
        shadow_root=real, real_workspace_path=real,
        read_observer=log.read_observer(real, MAIN_AGENT_ID))])
    steps = [
        _edit("x = 1", "x = 2"),
        {"type": "tool_call", "thought": "reread", "tool": "read_file",
         "args": {"path": "f.py"}},
        _edit("y = 9", "y = 10"),
        {"type": "submit_changes", "thought": "d", "summary": "ok"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        registry, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        edit_session_factory=lambda: session)
    records: list[tuple[str, str, bool]] = []
    decisions = 0

    async def decide(diff: list[DiffEntry]) -> dict[str, object]:
        nonlocal decisions
        decisions += 1
        if decisions == 1:
            # A sibling promotes f.py while the first edit waits for the user.
            (real / "f.py").write_text("x = 1\ny = 9\n")
            log.note_promote("a1", "impl", "general-purpose", ["f.py"])
        return {"decision": "accept", "reason": ""}

    async def record(diff: list[DiffEntry], decision: str, reason: str, gated: bool) -> None:
        records.append((decision, reason, gated))

    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=8,
        turn_control=ChatTurnControl(auto_accept_edits=False),
        edit_decision_cb=decide, edit_record_cb=record)
    assert out.kind == "submit_changes"
    assert records == [("stale", "f.py changed since it was read (by impl)", True),
                       ("accept", "", True)]
    contents = [str(m.get("content", "")) for m in out.history or []]
    failed = next(c for c in contents if c.startswith("PATCH FAILED: `f.py`"))
    assert "was modified by agent `impl` (general-purpose)" in failed
    assert "read_file it again" in failed  # the STALE_READ guidance
    assert (real / "f.py").read_text() == "x = 1\ny = 10\n"


@pytest.mark.asyncio
async def test_the_stale_record_is_discarded_and_always_breadcrumbed(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    diff = [DiffEntry(path="f.py", additions=1, deletions=0, temp_path="/s/f.py",
                      unified_diff="@@\n+x = 2")]
    await ctrl._edit_record_cb(tid, f"chat:{tid}", diff, "stale",
                               "f.py changed since it was read (by impl)", False)
    thread = store.get_thread(tid)
    assert thread is not None
    card = next(m for m in thread.messages if m.type == "diff_card")
    assert card.metadata["resolved"] == "discarded"
    assert any(m.content == "✗ Not applied: f.py changed since it was read (by impl)"
               for m in thread.messages)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stale_edit_branch.py > /tmp/pa6.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pa6.txt`
Expected: `exit=1`; both tests fail (the first with an uncaught `StaleWriteError` ending the run; the second because no "Not applied" breadcrumb exists).

- [ ] **Step 3: Implement the loop branch**

In `agentd/chat/controller_loop.py`:
- add a runtime import next to the others: `from agentd.chat.edit_session import StaleWriteError` (the existing `TurnEditSession` import stays under `TYPE_CHECKING`; `edit_session` does not import the loop, so there is no cycle);
- update the comment above `EditRecordCb` with one line: `# decision is "accept" | "reject" | "stale" (a promote refused by the write guard, spec §7.4).`
- in the edit branch, replace from `was_gated = not turn_control.auto_accept_edits and edit_decision_cb is not None` through the `edit_record_cb(...)` call (the block ending `diff, "accept" if accepted else "reject", reason, was_gated)`) with:

```python
                was_gated = not turn_control.auto_accept_edits and edit_decision_cb is not None
                reason = ""
                if was_gated:
                    decision = await edit_decision_cb(diff)
                    accepted = decision.get("decision") == "accept"
                    reason = str(decision.get("reason", ""))
                else:
                    accepted = True
                stale: StaleWriteError | None = None
                if accepted:
                    try:
                        await self._edit.accept()
                    # Narrow on purpose and FIRST: StaleWriteError is a RuntimeError, so
                    # anything broader would swallow it (rev 11 §7.4). Check 2 refused the
                    # promote — a sibling changed a file while this edit was in flight or
                    # held at the gate; accept() already restored the shadow from real.
                    except StaleWriteError as exc:
                        stale = exc
                else:
                    await self._edit.reject()  # restore shadow from real (shadow==real)
                if stale is not None:
                    if edit_record_cb is not None:
                        await edit_record_cb(
                            diff, "stale",
                            f"{stale.path} changed since it was read (by {stale.writer_label})",
                            was_gated)
                    history.append(assistant_turn(
                        {k: v for k, v in resp.items() if k != "patch_ops"}))
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"PATCH FAILED: {stale} {_edit_failure_guidance(stale)}"})
                    continue
                if edit_record_cb is not None:
                    await edit_record_cb(
                        diff, "accept" if accepted else "reject", reason, was_gated)
```

(The accept/reject order and the record for the non-stale paths are unchanged from before; only the decision is now resolved before the promote in both modes, which is what makes the single `try` possible.)

- [ ] **Step 4: Implement the record**

In `ChatController._edit_record_cb`, directly after the `self._broadcaster.broadcast(channel_id, {"type": "diff_ready", ...})` call and before `files = ", ".join(...)`, insert:

```python
        if decision == "stale":
            # Refused by the write guard (spec §7.4): always recorded, gated or not — the
            # user may have clicked Accept on an edit that nevertheless did not land.
            self._write_breadcrumb(thread_id, channel_id, f"✗ Not applied: {reason}")
            return
```

(`resolved` is already `"discarded"` for any decision other than `"accept"`.)

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_stale_edit_branch.py tests/test_controller_durable_edit.py tests/test_edit_gate_controller.py tests/test_live_edit_review_pref.py tests/test_prompt_goldens.py > /tmp/pa6.txt 2>&1; echo exit=$?; tail -1 /tmp/pa6.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/controller_loop.py agentd/chat/controller.py tests/test_stale_edit_branch.py
git commit -m "feat(controller): refuse and record edits that went stale before promote"
```

---

### Task A7: Wire the guard into the parent (flag-gated)

**Files:**
- Modify: `agentd/chat/controller.py`
- Test: `tests/test_write_guard_wiring.py`

**Interfaces:**
- Produces: `ChatController._write_logs: dict[str, WorkspaceWriteLog]` and `ChatController._write_log_for(thread_id) -> WorkspaceWriteLog | None` — `None` when the flag is off; otherwise one log per thread for the process lifetime (the parent's watermarks persist across turns, spec §7.1). Part D's runtime reads the same map.
- `_build_registry(..., read_observer=None)` passes it to `BuiltinToolSource`.
- `_run_loop`: when a log exists, the parent's edit session gets `WriteGuard(log, MAIN_AGENT_ID, "main", "main")` and the builtin source gets `log.read_observer(workspace, MAIN_AGENT_ID)`. Flag off → both `None`: byte-identical to Part A's predecessor.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_write_guard_wiring.py`:

```python
"""The parent is guarded by its thread's write log when sub-agents are enabled."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.subagents.write_log import MAIN_AGENT_ID
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore,
                steps: list[dict[str, object]]) -> ChatController:
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        thread_store=store, orchestrator=orchestrator, broadcaster=EventBroadcaster(),
        retrieval_client=None)


def _edit() -> dict[str, object]:
    return {"type": "edit", "thought": "t", "patch_ops": [
        {"op": "search_replace", "file": "f.py", "search": "x = 1", "replace": "x = 2",
         "reason": "r"}]}


def test_no_log_when_the_flag_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED", raising=False)
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = _controller(tmp_path, tmp_path, store, [])
    assert ctrl._write_log_for("t1") is None


def test_one_log_per_thread_when_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = _controller(tmp_path, tmp_path, store, [])
    first = ctrl._write_log_for("t1")
    assert first is not None and ctrl._write_log_for("t1") is first
    assert ctrl._write_log_for("t2") is not first


@pytest.mark.asyncio
async def test_the_parent_is_refused_until_it_rereads(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    steps = [
        _edit(),
        {"type": "tool_call", "thought": "reread", "tool": "read_file",
         "args": {"path": "f.py"}},
        _edit(),
        {"type": "submit_changes", "thought": "d", "summary": "ok"},
    ]
    ctrl = _controller(ws, tmp_path, store, steps)
    log = ctrl._write_log_for(tid)
    assert log is not None
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])  # a sibling's earlier write

    await ctrl.handle_message(tid, "bump x", channel_id=f"chat:{tid}")

    assert (ws / "f.py").read_text() == "x = 2\n"
    assert log.stale_refusals(MAIN_AGENT_ID) == 1
    history = store.get_thread(tid).controller_conversation_history or []
    assert any(str(m.get("content", "")).startswith("PATCH FAILED: `f.py` was modified")
               for m in history)


@pytest.mark.asyncio
async def test_flag_off_the_same_turn_is_never_refused(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    ctrl = _controller(ws, tmp_path, store,
                       [_edit(), {"type": "submit_changes", "thought": "d", "summary": "ok"}])
    await ctrl.handle_message(tid, "bump x", channel_id=f"chat:{tid}")
    assert (ws / "f.py").read_text() == "x = 2\n"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_write_guard_wiring.py > /tmp/pa7.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pa7.txt`
Expected: `exit=1`; the three tests that touch `_write_log_for` fail with `AttributeError` (the flag-off turn test passes already).

- [ ] **Step 3: Implement**

In `agentd/chat/controller.py`:
1. Imports: add `is_subagents_enabled` to the existing `from agentd.chat.controller_factory import ...` line (sorted), and `from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, WriteGuard` (sorted among the `agentd.*` imports).
2. In `__init__`, after `self._exec_sessions = exec_session_manager` add:

```python
        # One write log per thread for the process lifetime (spec §7.1) — only when
        # sub-agents are enabled; the parent's read watermarks persist across turns.
        self._write_logs: dict[str, WorkspaceWriteLog] = {}
```

3. Add the method (next to `_build_registry`):

```python
    def _write_log_for(self, thread_id: str) -> WorkspaceWriteLog | None:
        """The thread's write log, created on first use; None when sub-agents are off
        (no guard, no read tracking — the parent behaves exactly as before P5)."""
        if not is_subagents_enabled():
            return None
        return self._write_logs.setdefault(thread_id, WorkspaceWriteLog())
```

4. `_build_registry`: add the keyword parameter `read_observer: Callable[[str], None] | None = None,` (after `thread_id`), and pass `read_observer=read_observer,` to `BuiltinToolSource(...)`.
5. `_run_loop`: directly before `edit_session_factory = (`, add `write_log = self._write_log_for(thread_id)`; in the `TurnEditSession(...)` call add
   ```python
                   write_guard=(
                       WriteGuard(write_log, MAIN_AGENT_ID, "main", "main")
                       if write_log is not None else None),
   ```
   after the `checkpoint_cb=(...)` argument; and in the `self._build_registry(...)` call add
   ```python
                                     read_observer=(
                                         write_log.read_observer(
                                             Path(self._workspace_path), MAIN_AGENT_ID)
                                         if write_log is not None else None),
   ```
   after `exec_session_source=exec_source`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_write_guard_wiring.py tests/test_prompt_goldens.py tests/test_controller_durable_edit.py > /tmp/pa7.txt 2>&1; echo exit=$?; tail -1 /tmp/pa7.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller.py tests/test_write_guard_wiring.py
git commit -m "feat(controller): guard the parent's edits with the thread write log"
```

---

### Part A verification

- [ ] **Step 1: Full suite** — `.venv/bin/python -m pytest tests/ --color=no > /tmp/pA.txt 2>&1; echo exit=$?; grep '^FAILED' /tmp/pA.txt; tail -1 /tmp/pA.txt` → `exit=1`, the only `FAILED` line is the pre-existing `test_command_only_step` one.
- [ ] **Step 2: Lint** — new files: `.venv/bin/ruff check agentd/subagents tests/test_subagents_flag.py tests/test_write_log.py tests/test_write_guard_session.py tests/test_workspace_changes_loop.py tests/test_builtin_read_observer.py tests/test_stale_edit_branch.py tests/test_write_guard_wiring.py` → `All checks passed!`. Touched modules add nothing — with `BASE` = the commit before Task A1:

```bash
F=(agentd/chat/controller_factory.py agentd/chat/edit_session.py agentd/tools/registry.py agentd/tools/sources.py agentd/chat/controller_loop.py agentd/chat/controller.py)
counts() { sed $'s/\x1b\\[[0-9;]*m//g' | grep -E "^agentd" | sed -E 's/:[0-9]+:[0-9]+: ([A-Z0-9]+).*/ \1/' | sort | uniq -c; }
.venv/bin/ruff check "${F[@]}" --output-format concise | counts > /tmp/lint-after.txt
for f in "${F[@]}"; do git show "${BASE}:services/agentd-py/$f" | .venv/bin/ruff check --stdin-filename "$f" --output-format concise -; done | counts > /tmp/lint-base.txt
comm -13 /tmp/lint-base.txt /tmp/lint-after.txt > /tmp/lint-new.txt; echo new=$(wc -l < /tmp/lint-new.txt); cat /tmp/lint-new.txt
```

Expected: `new=0` (a finding that disappears is fine).

---

# Part B — Child building blocks

Pure pieces the runtime (Part D) assembles into a child: identity/config, permissions and tool filtering, the VCS guard, and the two per-child modes of existing sources (memory, skills). Nothing here runs a child yet; every change is inert for the parent.

### File structure (Part B)

| File | Responsibility |
|---|---|
| `agentd/subagents/config.py` | `subagent_max_depth()`, `subagent_max_concurrent()`, `subagent_max_iters()` |
| `agentd/subagents/context.py` | `AgentContext`, `new_agent_id()` |
| `agentd/subagents/definitions.py` | `AgentDefinition`, `BUILTIN_AGENTS` (`explore`, `general-purpose`) |
| `agentd/prompting/tagged.py` | `RenderContext.for_agent(...)` |
| `agentd/subagents/permissions.py` | effective permission, AGENT type set, child tool-name filter |
| `agentd/tools/sources.py` | `AggregatingToolRegistry(allowed_tools=…)`; `BuiltinToolSource(command_guard=…)` |
| `agentd/tools/registry.py` | `ToolRegistry(command_guard=…)` checked before the approval callback |
| `agentd/subagents/vcs_guard.py` | `vcs_refusal(command_line)`, `VCS_REFUSAL` |
| `agentd/chat/controller_loop.py` | `_permission_correction` (read-only defense in depth) |
| `agentd/memory/harness.py`, `agentd/memory/tool_source.py` | `consolidate=False`, recall-only source, per-run recall key, `release_run` |
| `agentd/skills/tool_source.py` | `SkillToolSource(additive=True)` |

### Task B1: Config, `AgentContext`, built-in definitions, `RenderContext.for_agent`

**Files:**
- Create: `agentd/subagents/config.py`, `agentd/subagents/context.py`, `agentd/subagents/definitions.py`
- Modify: `agentd/prompting/tagged.py`
- Test: `tests/test_subagent_context.py`

**Interfaces:**
- `subagent_max_depth() -> int` (env `CRUCIBLE_SUBAGENT_MAX_DEPTH`, default 2, min 1), `subagent_max_concurrent() -> int` (`CRUCIBLE_SUBAGENT_MAX_CONCURRENT`, 8, min 1), `subagent_max_iters() -> int` (`CRUCIBLE_SUBAGENT_MAX_ITERS`, 100, min 1). A non-integer value logs a warning and uses the default; a value below the minimum logs a warning and uses the minimum (degrade, never crash startup).
- `@dataclass(frozen=True) AgentContext(agent_id, name, label, depth, parent_agent_id: str | None, permission: Permission, allowed_types: tuple[str, ...], persona: str, max_iters: int)` — `permission` is the **effective** permission (Task B2). `new_agent_id() -> str` → `"agent-" + 12 hex chars`.
- `@dataclass(frozen=True) AgentDefinition(name, description, permission: Permission, tools: frozenset[str] | None, persona: str, model: str = "inherit", max_turns: int | None = None)`; `tools=None` means "inherit every tool". `BUILTIN_AGENTS: dict[str, AgentDefinition]` holds `explore` (`plan`; tools `search_code, read_file, list_directory, query_graph, search_semantic`) and `general-purpose` (`default`; all tools) — spec §9.4. Phase 3 adds discovered definitions in front of these.
- `RenderContext.for_agent(agent, *, tools: frozenset[str], shell_policy: ShellPolicy) -> RenderContext` — `audience="child"`, `permission=agent.permission`, `base_types=frozenset(agent.allowed_types)`, `agent_id`, `agent_label=agent.label` (rev 11 §4.7.2).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_context.py`:

```python
"""Sub-agent identity, config and the child RenderContext (spec §3, §4.7.2, §9.4, §12)."""
import pytest

from agentd.prompting.tagged import RenderContext
from agentd.subagents.config import (
    subagent_max_concurrent,
    subagent_max_depth,
    subagent_max_iters,
)
from agentd.subagents.context import AgentContext, new_agent_id
from agentd.subagents.definitions import BUILTIN_AGENTS


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CRUCIBLE_SUBAGENT_MAX_DEPTH", "CRUCIBLE_SUBAGENT_MAX_CONCURRENT",
                 "CRUCIBLE_SUBAGENT_MAX_ITERS"):
        monkeypatch.delenv(name, raising=False)
    assert (subagent_max_depth(), subagent_max_concurrent(), subagent_max_iters()) == (2, 8, 100)


def test_config_degrades_on_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_DEPTH", "three")
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_CONCURRENT", "0")
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_ITERS", " 40 ")
    assert (subagent_max_depth(), subagent_max_concurrent(), subagent_max_iters()) == (2, 1, 40)


def test_agent_ids_are_unique_and_prefixed() -> None:
    first, second = new_agent_id(), new_agent_id()
    assert first.startswith("agent-") and len(first) == len("agent-") + 12
    assert first != second


def test_builtins() -> None:
    explore = BUILTIN_AGENTS["explore"]
    general = BUILTIN_AGENTS["general-purpose"]
    assert explore.permission == "plan"
    assert explore.tools == frozenset(
        {"search_code", "read_file", "list_directory", "query_graph", "search_semantic"})
    assert general.permission == "default" and general.tools is None
    assert explore.description and general.description and explore.persona


def test_render_context_for_an_agent() -> None:
    agent = AgentContext(
        agent_id="agent-abc", name="explore", label="auth survey", depth=1,
        parent_agent_id=None, permission="plan", allowed_types=("tool_call", "progress", "report"),
        persona="p", max_iters=20)
    ctx = RenderContext.for_agent(agent, tools=frozenset({"read_file"}), shell_policy="ask")
    assert not ctx.is_main
    assert (ctx.permission, ctx.shell_policy, ctx.agent_id, ctx.agent_label) == (
        "plan", "ask", "agent-abc", "auth survey")
    assert ctx.tools == frozenset({"read_file"})
    assert ctx.base_types == frozenset({"tool_call", "progress", "report"})
    hash(ctx)  # stays usable as a render-cache key
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subagent_context.py > /tmp/pb1.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pb1.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.config'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/config.py`:

```python
"""Sub-agent limits (spec §12). Read per call, so a test's monkeypatch takes effect."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[subagent] %s=%r is not an integer — using %d", name, raw, default)
        return default
    if value < minimum:
        logger.warning("[subagent] %s=%d is below %d — using %d", name, value, minimum, minimum)
        return minimum
    return value


def subagent_max_depth() -> int:
    """Max nesting below the parent turn: children are depth 1, theirs depth 2, …"""
    return _int_env("CRUCIBLE_SUBAGENT_MAX_DEPTH", 2, 1)


def subagent_max_concurrent() -> int:
    """Process-wide running-agent cap (protects the provider's rate limit, spec §6.2)."""
    return _int_env("CRUCIBLE_SUBAGENT_MAX_CONCURRENT", 8, 1)


def subagent_max_iters() -> int:
    """A child's loop budget when its definition sets no maxTurns (spec §6.5)."""
    return _int_env("CRUCIBLE_SUBAGENT_MAX_ITERS", 100, 1)
```

Create `agentd/subagents/context.py`:

```python
"""AgentContext: the one object that carries a sub-agent's configuration (spec §3)."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agentd.prompting.tagged import Permission


def new_agent_id() -> str:
    return f"agent-{uuid4().hex[:12]}"


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    name: str            # the definition name ("explore", "general-purpose", …)
    label: str           # unique within its dispatch; what the user and siblings see
    depth: int           # 1 = dispatched by the main agent
    parent_agent_id: str | None  # None when the main agent dispatched it
    permission: Permission       # EFFECTIVE permission (read-only-ness inherited, §5.6)
    allowed_types: tuple[str, ...]  # the AGENT base type set for this permission
    persona: str         # the definition body ("" = none)
    max_iters: int
```

Create `agentd/subagents/definitions.py`:

```python
"""Agent definitions (spec §9). Phase 2 ships the built-ins only; Phase 3 adds discovery
of `.crucible/agents` / `.claude/agents` files in front of them."""
from __future__ import annotations

from dataclasses import dataclass

from agentd.prompting.tagged import Permission


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str          # one line for the dispatcher's catalog
    permission: Permission
    tools: frozenset[str] | None  # None = every tool the dispatcher's child may have
    persona: str
    model: str = "inherit"
    max_turns: int | None = None


_EXPLORE_PERSONA = (
    "Locate, then read. Find the code your task names with search_code (and query_graph "
    "when it is available), read the relevant ranges with read_file, and stop as soon as "
    "more reading would not change your findings. Your report is dense but complete: "
    "every claim cites path:line, and it says plainly what you did not find.")

BUILTIN_AGENTS: dict[str, AgentDefinition] = {
    "explore": AgentDefinition(
        name="explore",
        description=("Read-only investigator: locates and reads code, returns findings "
                     "with path:line citations. Never edits files or runs commands."),
        permission="plan",
        tools=frozenset({"search_code", "read_file", "list_directory", "query_graph",
                         "search_semantic"}),
        persona=_EXPLORE_PERSONA),
    "general-purpose": AgentDefinition(
        name="general-purpose",
        description=("Implements one self-contained part of a change: reads, edits the "
                     "files it is assigned, runs checks, and reports what it changed."),
        permission="default",
        tools=None,
        persona=""),
}
```

In `agentd/prompting/tagged.py`: change `from typing import Literal` to `from typing import TYPE_CHECKING, Literal`, add after the imports

```python
if TYPE_CHECKING:
    from agentd.subagents.context import AgentContext
```

and add to `RenderContext`, directly after `main()`:

```python
    @classmethod
    def for_agent(
        cls, agent: AgentContext, *, tools: frozenset[str], shell_policy: ShellPolicy,
    ) -> RenderContext:
        """A sub-agent's context (rev 11 §4.7.2): flat and hashable, built once per loop
        from the AgentContext plus the child's final tool names."""
        return cls(
            audience="child", permission=agent.permission, shell_policy=shell_policy,
            tools=tools, base_types=frozenset(agent.allowed_types),
            agent_id=agent.agent_id, agent_label=agent.label)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_context.py tests/test_tagged_prompt.py tests/test_prompt_goldens.py > /tmp/pb1.txt 2>&1; echo exit=$?; tail -1 /tmp/pb1.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents agentd/prompting/tagged.py tests/test_subagent_context.py
git commit -m "feat(subagents): agent context, config limits and built-in definitions"
```

---

### Task B2: Permissions and the child tool set

**Files:**
- Create: `agentd/subagents/permissions.py`
- Modify: `agentd/tools/sources.py` (`AggregatingToolRegistry(allowed_tools=…)`)
- Test: `tests/test_subagent_permissions.py`

**Interfaces:**
- `effective_permission(own: Permission, dispatcher: Permission | None) -> Permission` — `plan` if either is `plan`, else `own` (spec §5.6: read-only-ness propagates, the rest of a mode does not; `dispatcher` is the dispatching agent's *effective* permission, `None` for the main agent).
- `AGENT_BASE_TYPES = ("tool_call", "edit", "progress", "report")`; `child_allowed_types(permission) -> tuple[str, ...]` drops `edit` for `plan`.
- `CHILD_EXCLUDED_TOOLS` = the PTY session tools + `remember`; `DISPATCH_TOOL = "dispatch_agents"`.
- `child_tool_names(available, *, definition_tools, permission, may_dispatch) -> frozenset[str]`: drop the exclusions; intersect with `definition_tools` when not `None`; for `plan` drop `run_command` and every `mcp__*`; drop `dispatch_agents` unless `may_dispatch` (depth < max depth, spec §5.3).
- `follows_live_review(permission) -> bool` — only `default` children share the parent turn's `ChatTurnControl` (§5.1).
- `AggregatingToolRegistry(sources, allowed_tools: frozenset[str] | None = None)` — when set, `definitions()` lists only those names and `execute()` of any other name answers the same "unknown tool" error as today. Duplicate-name checking still covers every source.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_permissions.py`:

```python
"""Effective permission, the AGENT type set and the child tool set (spec §5.3, §5.6)."""
from pathlib import Path

import pytest

from agentd.subagents.permissions import (
    AGENT_BASE_TYPES,
    child_allowed_types,
    child_tool_names,
    effective_permission,
    follows_live_review,
)
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

_AVAILABLE = ["search_code", "read_file", "list_directory", "run_command", "write_todos",
              "recall", "remember", "read_skill", "mcp__gh__create_issue", "start_session",
              "write_stdin", "kill_session", "list_sessions", "dispatch_agents"]


@pytest.mark.parametrize("own,dispatcher,expected", [
    ("default", None, "default"), ("acceptEdits", None, "acceptEdits"),
    ("dontAsk", "default", "dontAsk"), ("default", "plan", "plan"),
    ("acceptEdits", "plan", "plan"), ("plan", "default", "plan"), ("plan", None, "plan"),
])
def test_read_only_ness_propagates_nothing_else_does(own, dispatcher, expected) -> None:
    assert effective_permission(own, dispatcher) == expected


def test_type_sets() -> None:
    assert AGENT_BASE_TYPES == ("tool_call", "edit", "progress", "report")
    assert child_allowed_types("default") == AGENT_BASE_TYPES
    assert child_allowed_types("plan") == ("tool_call", "progress", "report")


def test_a_default_child_gets_everything_but_sessions_and_remember() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=None, permission="default",
                             may_dispatch=True)
    assert names == frozenset({"search_code", "read_file", "list_directory", "run_command",
                               "write_todos", "recall", "read_skill", "mcp__gh__create_issue",
                               "dispatch_agents"})


def test_a_read_only_child_loses_commands_and_mcp() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=None, permission="plan",
                             may_dispatch=False)
    assert "run_command" not in names and not any(n.startswith("mcp__") for n in names)
    assert "dispatch_agents" not in names


def test_a_definition_narrows_and_never_widens() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=frozenset({"read_file", "query_graph"}),
                             permission="plan", may_dispatch=True)
    assert names == frozenset({"read_file"})  # query_graph was never available


def test_only_default_follows_the_live_review_toggle() -> None:
    assert follows_live_review("default")
    assert not any(follows_live_review(p) for p in ("acceptEdits", "dontAsk", "plan"))


@pytest.mark.asyncio
async def test_the_registry_allow_list(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)],
        allowed_tools=frozenset({"read_file"}))
    assert [d.name for d in reg.definitions()] == ["read_file"]
    assert not (await reg.execute("read_file", {"path": "f.py"})).is_error
    refused = await reg.execute("run_command", {"command": "ls"})
    assert refused.is_error and refused.output == "Error: unknown tool 'run_command'"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subagent_permissions.py > /tmp/pb2.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pb2.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.permissions'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/permissions.py`:

```python
"""Sub-agent permissions and tool sets (spec §5.3, §5.6)."""
from __future__ import annotations

from collections.abc import Iterable

from agentd.prompting.tagged import Permission

AGENT_BASE_TYPES: tuple[str, ...] = ("tool_call", "edit", "progress", "report")
DISPATCH_TOOL = "dispatch_agents"
# PTY sessions are thread-scoped (one child could kill a sibling's process), and
# `remember` writes durable memory — children get recall only (spec §5.2, §5.3).
CHILD_EXCLUDED_TOOLS = frozenset({
    "start_session", "write_stdin", "kill_session", "list_sessions", "remember"})
_READ_ONLY_EXCLUDED = frozenset({"run_command"})


def effective_permission(own: Permission, dispatcher: Permission | None) -> Permission:
    """What propagates down a dispatch tree is read-only-ness, not the whole mode: the
    modes are not one restrictiveness scale (dontAsk is stricter than default on commands
    and looser on edits), so "most restrictive" would be ill-defined (spec §5.6)."""
    if own == "plan" or dispatcher == "plan":
        return "plan"
    return own


def child_allowed_types(permission: Permission) -> tuple[str, ...]:
    if permission == "plan":
        return tuple(t for t in AGENT_BASE_TYPES if t != "edit")
    return AGENT_BASE_TYPES


def child_tool_names(
    available: Iterable[str], *, definition_tools: frozenset[str] | None,
    permission: Permission, may_dispatch: bool,
) -> frozenset[str]:
    names = set(available) - CHILD_EXCLUDED_TOOLS
    if definition_tools is not None:
        names &= definition_tools
    if permission == "plan":
        # Filtered out of the tool list entirely, so the MCP teaching block is not
        # appended either; _permission_correction is the defense in depth.
        names = {n for n in names if n not in _READ_ONLY_EXCLUDED and not n.startswith("mcp__")}
    if not may_dispatch:
        names.discard(DISPATCH_TOOL)
    return frozenset(names)


def follows_live_review(permission: Permission) -> bool:
    """Only an effective-`default` child shares the parent turn's ChatTurnControl, so a
    mid-dispatch "Review each edit" flip reaches it; acceptEdits/dontAsk auto-accept."""
    return permission == "default"
```

In `agentd/tools/sources.py`, change `AggregatingToolRegistry`:

```python
    def __init__(
        self, sources: list[ToolSource], allowed_tools: frozenset[str] | None = None,
    ) -> None:
        seen: set[str] = set()
        for src in sources:
            for d in src.definitions():
                if d.name in seen:
                    raise ValueError(f"Duplicate tool name across sources: {d.name!r}")
                seen.add(d.name)
        self._sources = sources
        # A sub-agent's tool set (spec §5.3): None = every tool the sources offer.
        self._allowed = allowed_tools

    def definitions(self) -> list[ToolDefinition]:
        return [d for s in self._sources for d in s.definitions()
                if self._allowed is None or d.name in self._allowed]

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if self._allowed is None or tool in self._allowed:
            for s in self._sources:
                if s.owns(tool):
                    return await s.execute(tool, args)
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_permissions.py tests/test_tools_registry.py tests/test_mcp_tool_source.py > /tmp/pb2.txt 2>&1; echo exit=$?; tail -1 /tmp/pb2.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/permissions.py agentd/tools/sources.py tests/test_subagent_permissions.py
git commit -m "feat(subagents): effective permissions and the child tool set"
```

---

### Task B3: The VCS guard

**Files:**
- Create: `agentd/subagents/vcs_guard.py`
- Modify: `agentd/tools/registry.py` (`ToolRegistry(command_guard=…)`), `agentd/tools/sources.py` (`BuiltinToolSource(command_guard=…)`)
- Test: `tests/test_vcs_guard.py`

**Interfaces:**
- `vcs_refusal(command_line: str) -> str | None` — `VCS_REFUSAL` when the line would mutate version control, else `None` (spec §4.6.6 steps 1–7; fails **closed** on anything it cannot tokenize).
- `ToolRegistry(..., command_guard: Callable[[str], str | None] | None = None)` — in `run_command`, **before** the approval callback, the guard receives the exact shell line `run_command` would execute (`_build_shell_command_line(*_split_command(command, args, real_workspace))`); a non-`None` result is returned as `ToolOutput(output=<refusal>, is_error=True)` and no card is ever raised. `BuiltinToolSource` passes it through. Parent sources never set it.
- Algorithm: (1) every `$(…)`/backtick substitution — found by a quote-aware balanced scan — is checked recursively; (2) the line is tokenized with `shlex.shlex(posix=True, punctuation_chars=True, whitespace_split=True)`; (3) split into segments on operator tokens (`&& || ; | & ( )`); (4) in each segment, **any** token whose basename is `git` starts an invocation: skip `-C <p>`, `-c <k=v>`, `--git-dir[=]`, `--work-tree[=]`, `--no-pager`, `-P`, `--exec-path[=]`, then the subcommand must be in the read-only allow-list, or be `config` with only `--get/--get-all/--list` flags; (5) any `sh`/`bash`/`zsh` token followed by a `-…c…` flag has its script argument checked recursively, and any `eval` token has the rest of its segment checked recursively. A bare `git` (usage text) is allowed. False positives such as `echo git commit` are accepted by design (fails closed).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vcs_guard.py`:

```python
"""The sub-agent VCS guard (spec §4.6.6, §15): mutations refused before any card."""
from pathlib import Path

import pytest

from agentd.domain.models import ApprovalOutcome, CommandDecision
from agentd.subagents.vcs_guard import VCS_REFUSAL, vcs_refusal
from agentd.tools.registry import ToolRegistry

REFUSED = [
    "git commit -am x", "git checkout .", "git stash", "cd x && git commit", "ls ; git stash",
    "git -C . commit", "git -c user.name=x commit", "env GIT_DIR=. git reset",
    "/usr/bin/git checkout .", 'bash -c "git stash"', "git ci", "a&&git commit",
    "( git commit )", "$(git commit -am x)", "xargs git checkout", r"find . -exec git rm {} \;",
    "timeout 10 git commit", 'eval "git stash"', 'timeout 5 bash -c "git commit"',
    "xargs sh -c 'git stash'", "echo 'unbalanced", "git config user.name x",
    'echo "$(git push)"', "git --version", "nice git reset --hard", "bash -lc 'git stash'",
]
ALLOWED = [
    "git status", "git diff", "git log", "git -C sub status", "echo $(date)",
    "git config --get user.name", "git config --list", "git show HEAD:README.md",
    "pytest tests/test_x.py -x", 'grep "don\'t" notes.md', "echo '$(git commit)'", "git",
    "ls | wc -l",
]


@pytest.mark.parametrize("line", REFUSED)
def test_refused(line: str) -> None:
    assert vcs_refusal(line) == VCS_REFUSAL


@pytest.mark.parametrize("line", ALLOWED)
def test_allowed(line: str) -> None:
    assert vcs_refusal(line) is None


def test_the_refusal_text() -> None:
    assert VCS_REFUSAL == (
        "Version-control changes are blocked for sub-agents: other agents are editing this "
        "workspace, and the main agent commits after the work is done. Read-only git "
        "(status/diff/log/show) is fine.")


@pytest.mark.asyncio
async def test_a_refused_command_never_reaches_the_approval_card(tmp_path: Path) -> None:
    asked: list[tuple[str, list[str]]] = []

    async def approve(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        asked.append((command, args))
        return ApprovalOutcome.from_command(CommandDecision(approve=True))

    reg = ToolRegistry(tmp_path, tmp_path, command_approval_callback=approve,
                       command_guard=vcs_refusal)
    refused = await reg.execute("run_command", {"command": "git", "args": ["stash"]})
    packed = await reg.execute("run_command", {"command": "cd sub && git commit -am x"})
    assert refused.is_error and refused.output == VCS_REFUSAL
    assert packed.is_error and packed.output == VCS_REFUSAL
    assert asked == []
    await reg.execute("run_command", {"command": "git", "args": ["status"]})
    assert asked == [("git", ["status"])]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vcs_guard.py > /tmp/pb3.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pb3.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.vcs_guard'`.

- [ ] **Step 3: Implement the guard**

Create `agentd/subagents/vcs_guard.py`:

```python
"""VCS guard for sub-agents (spec §4.6.6).

Siblings edit one shared workspace and the main agent commits after the batch, so a
child must not mutate version control. This runs inside the child's run_command BEFORE
the approval callback, so nobody is ever shown a card for a command that will be refused.
It parses the exact shell line run_command would execute and fails CLOSED: anything it
cannot tokenize is refused. VCS calls hidden inside scripts, make targets or npm hooks are
invisible to it (accepted residual gap); the role block's rule is the backstop.
"""
from __future__ import annotations

import os
import shlex

VCS_REFUSAL = (
    "Version-control changes are blocked for sub-agents: other agents are editing this "
    "workspace, and the main agent commits after the work is done. Read-only git "
    "(status/diff/log/show) is fine.")

_READONLY_SUBCOMMANDS = frozenset({
    "status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse",
    "describe", "shortlog", "cat-file"})
_CONFIG_READ_FLAGS = frozenset({"--get", "--get-all", "--list"})
_GLOBAL_OPTS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree"})
_GLOBAL_OPTS_EQUALS = ("--git-dir=", "--work-tree=", "--exec-path=")
_GLOBAL_FLAGS = frozenset({"--no-pager", "-P", "--exec-path"})
_SHELLS = frozenset({"sh", "bash", "zsh"})
_OPERATOR_CHARS = frozenset(";&|()")
_MAX_NESTING = 8


class _Unparseable(ValueError):
    """The line cannot be analysed reliably — the guard refuses it."""


def vcs_refusal(command_line: str) -> str | None:
    try:
        return VCS_REFUSAL if _mutates(command_line, 0) else None
    except _Unparseable:
        return VCS_REFUSAL


def _mutates(line: str, depth: int) -> bool:
    if depth > _MAX_NESTING:
        raise _Unparseable("nesting too deep")
    for inner in _substitutions(line):
        if _mutates(inner, depth + 1):
            return True
    return any(_segment_mutates(segment, depth) for segment in _segments(_tokens(line)))


def _substitutions(line: str) -> list[str]:
    """Inner text of every $(…) and `…` that the shell would execute: single-quoted text
    is literal, double-quoted text still substitutes."""
    found: list[str] = []
    in_double = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not in_double:
            end = line.find("'", i + 1)
            if end == -1:
                raise _Unparseable("unbalanced single quote")
            i = end + 1
            continue
        if ch == '"':
            in_double = not in_double
            i += 1
            continue
        if line.startswith("$(", i):
            level, j = 1, i + 2
            while j < n and level:
                if line[j] == "(":
                    level += 1
                elif line[j] == ")":
                    level -= 1
                j += 1
            if level:
                raise _Unparseable("unbalanced $(")
            found.append(line[i + 2:j - 1])
            i = j
            continue
        if ch == "`":
            end = line.find("`", i + 1)
            if end == -1:
                raise _Unparseable("unbalanced backtick")
            found.append(line[i + 1:end])
            i = end + 1
            continue
        i += 1
    if in_double:
        raise _Unparseable("unbalanced double quote")
    return found


def _tokens(line: str) -> list[str]:
    # punctuation_chars splits operators even without spaces (a&&git commit) and makes
    # subshell parens their own tokens (( git commit )).
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError as exc:
        raise _Unparseable(str(exc)) from exc


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and set(token) <= _OPERATOR_CHARS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _segment_mutates(segment: list[str], depth: int) -> bool:
    # ANY token counts, not just the first: `timeout 60 git …`, `xargs git …`,
    # `find -exec git …` and `VAR=x git …` are all git invocations.
    for i, token in enumerate(segment):
        base = os.path.basename(token)
        if base == "git" and _git_mutates(segment[i + 1:]):
            return True
        if base in _SHELLS:
            script = _shell_script(segment[i + 1:])
            if script is not None and _mutates(script, depth + 1):
                return True
        if base == "eval" and _mutates(" ".join(segment[i + 1:]), depth + 1):
            return True
    return False


def _shell_script(rest: list[str]) -> str | None:
    """The -c script of `sh|bash|zsh [flags] -c SCRIPT` (combined flags like -lc count)."""
    for j, token in enumerate(rest):
        if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
            return rest[j + 1] if j + 1 < len(rest) else None
        if not token.startswith("-"):
            return None  # a script file, not -c
    return None


def _git_mutates(rest: list[str]) -> bool:
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
        elif token.startswith(_GLOBAL_OPTS_EQUALS) or token in _GLOBAL_FLAGS:
            i += 1
        else:
            break
    if i >= len(rest):
        return False  # bare `git`: prints usage
    subcommand = rest[i]
    if subcommand == "config":
        flags = [a for a in rest[i + 1:] if a.startswith("-")]
        return not (flags and all(f in _CONFIG_READ_FLAGS for f in flags))
    # An allow-list, not a deny-list: user aliases (`git ci`) can never be enumerated.
    return subcommand not in _READONLY_SUBCOMMANDS
```

- [ ] **Step 4: Hook it into `run_command`**

`agentd/tools/registry.py`: add `from collections.abc import Callable` to the imports; add the keyword parameter `command_guard: Callable[[str], str | None] | None = None,` after `render_ctx` in `ToolRegistry.__init__` and store `self._command_guard = command_guard` (below `self._command_approval_callback = ...`). In the `run_command` branch, directly above `if self._command_approval_callback is not None:`, insert:

```python
            if self._command_guard is not None:
                # Sub-agent guards (the VCS guard, spec §4.6.6) see the exact line
                # run_command would execute and refuse BEFORE any approval card.
                from agentd.tools.shell import _build_shell_command_line, _split_command
                line = _build_shell_command_line(
                    *_split_command(command, cmd_args, self._real_workspace_path))
                refusal = self._command_guard(line)
                if refusal is not None:
                    return ToolOutput(output=refusal, is_error=True)
```

`agentd/tools/sources.py`: `BuiltinToolSource.__init__` gains `command_guard: Callable[[str], str | None] | None = None,` (after `read_observer`) and passes `command_guard=command_guard,` to `ToolRegistry(...)`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_vcs_guard.py tests/test_tools_registry.py tests/test_approval_outcome.py > /tmp/pb3.txt 2>&1; echo exit=$?; tail -1 /tmp/pb3.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/subagents/vcs_guard.py agentd/tools/registry.py agentd/tools/sources.py tests/test_vcs_guard.py
git commit -m "feat(subagents): VCS guard refuses mutations before any approval card"
```

---

### Task B4: The read-only correction (defense in depth)

**Files:**
- Modify: `agentd/chat/controller_loop.py`
- Test: `tests/test_permission_correction.py`

**Interfaces:**
- `_permission_correction(resp, atype, render_ctx) -> str | None` — for a child whose render context is `plan`, a `tool_call` naming `run_command` or any `mcp__*` tool is corrected with: "`You are read-only for this task: `{tool}` isn't available to you. Investigate with the read tools and put your findings in `report`.`" (spec §5.6). It sits in the correction chain directly after `_decide_state_change_correction`. The main agent and non-`plan` children are never affected.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permission_correction.py`:

```python
"""A read-only child naming a command/MCP tool is corrected, not executed (spec §5.6)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, _permission_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

READONLY = RenderContext(audience="child", permission="plan", tools=frozenset({"read_file"}),
                         base_types=frozenset({"tool_call", "progress", "report"}),
                         agent_id="agent-1", agent_label="survey")
DEFAULT = RenderContext(audience="child", permission="default", tools=frozenset({"run_command"}),
                        base_types=frozenset({"tool_call", "edit", "progress", "report"}))


def test_only_read_only_children_are_corrected() -> None:
    call = {"type": "tool_call", "tool": "run_command", "args": {"command": "ls"}}
    mcp = {"type": "tool_call", "tool": "mcp__gh__create_issue", "args": {}}
    assert _permission_correction(call, "tool_call", READONLY) == (
        "You are read-only for this task: `run_command` isn't available to you. Investigate "
        "with the read tools and put your findings in `report`.")
    assert _permission_correction(mcp, "tool_call", READONLY) is not None
    assert _permission_correction(call, "tool_call", DEFAULT) is None
    assert _permission_correction(call, "tool_call", RenderContext.main()) is None
    read = {"type": "tool_call", "tool": "read_file", "args": {"path": "f.py"}}
    assert _permission_correction(read, "tool_call", READONLY) is None


@pytest.mark.asyncio
async def test_the_correction_is_in_the_chain(tmp_path: Path) -> None:
    steps = [
        {"type": "tool_call", "thought": "t", "tool": "run_command", "args": {"command": "ls"}},
        {"type": "submit_changes", "thought": "d", "summary": "ok"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([BuiltinToolSource(shadow_root=tmp_path,
                                                   real_workspace_path=tmp_path)]),
        EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        render_ctx=READONLY)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    contents = [str(m.get("content", "")) for m in out.history or []]
    assert any("You are read-only for this task: `run_command`" in c for c in contents)
```

(The loop test runs in `ACTIVE` only because `report` becomes a terminal in Part C; the chain is phase-independent.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_permission_correction.py > /tmp/pb4.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/pb4.txt`
Expected: `exit=2`, `ImportError` (`_permission_correction`).

- [ ] **Step 3: Implement**

In `agentd/chat/controller_loop.py`, directly above `def _decide_state_change_correction(`, add:

```python
def _permission_correction(
    resp: dict[str, object], atype: str, ctx: RenderContext,
) -> str | None:
    """Defense in depth for a read-only (effective `plan`) sub-agent (spec §5.6): its tool
    list already omits run_command and every mcp__ tool, so reaching this means the model
    named a tool it was never offered. Routed through the normal correction chain."""
    if ctx.is_main or ctx.permission != "plan" or atype != "tool_call":
        return None
    tool = str(resp.get("tool", ""))
    if tool == "run_command" or tool.startswith("mcp__"):
        return (f"You are read-only for this task: `{tool}` isn't available to you. "
                "Investigate with the read tools and put your findings in `report`.")
    return None
```

and in the correction chain replace `                or _decide_state_change_correction(resp, self._sm.phase)` with

```python
                or _decide_state_change_correction(resp, self._sm.phase)
                or _permission_correction(resp, atype, self._render_ctx)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_permission_correction.py tests/test_controller_child_corrections.py tests/test_prompt_goldens.py tests/test_prompt_leak_lint.py > /tmp/pb4.txt 2>&1; echo exit=$?; tail -1 /tmp/pb4.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_loop.py tests/test_permission_correction.py
git commit -m "feat(controller): read-only sub-agents are corrected off commands and MCP"
```

---

### Task B5: Child memory modes

**Files:**
- Modify: `agentd/memory/harness.py`, `agentd/memory/tool_source.py`
- Test: `tests/test_memory_child_modes.py`

**Interfaces:**
- `MemoryHarness.prepare_turn(history, run_id, query="", observed=None, *, consolidate: bool = True)` — `consolidate=False` never schedules consolidation, even when compaction evicts (children, spec §5.2).
- The recall de-dup key becomes **per run** (`_recall_keys: dict[str, str]`), so two concurrent runs alternating queries no longer thrash one shared slot. `release_run(run_id)` drops a finished child's recall/trace cache and key.
- `MemoryHarness.memory_tool_source(run_id="", *, allow_remember: bool = True)` — with `allow_remember=False` it returns a **recall-only** source whenever a recall engine exists (independent of the consolidator), else `None`. The default path is unchanged.
- `MemoryToolSource(consolidator: object | None, …, allow_remember: bool = True)` — `allow_remember=False` offers and owns only `recall`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_child_modes.py`:

```python
"""Child memory modes (spec §5.2): no consolidation, recall-only, per-run recall key."""
import asyncio

import pytest

from agentd.memory.harness import MemoryHarness
from agentd.memory.models import CompactionResult, RecallTrace


class _Compactor:
    _store = None

    async def maybe_compact(self, history, run_id, observed=None):
        return CompactionResult(compacted=True, history=history, evicted_count=1,
                                evicted_seq_lo=1, evicted_seq_hi=2)

    def set_window_tokens(self, window_tokens):  # pragma: no cover — protocol filler
        pass


class _Consolidator:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def consolidate(self, run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi):
        self.runs.append(run_id)


class _SpyRecall:
    def __init__(self) -> None:
        self.calls = 0

    async def recall(self, query, scope_kind, scope_id, k):
        return []

    async def recall_with_trace(self, query, scope_kind, scope_id, k):
        self.calls += 1
        return [], RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id, k=k,
                               floor=0.0, reranked=False, entries=[])


@pytest.mark.asyncio
async def test_children_never_schedule_consolidation() -> None:
    cons = _Consolidator()
    harness = MemoryHarness(enabled=True, compactor=_Compactor(), consolidator=cons,
                            scope_kind="workspace", scope_id="/ws")
    await harness.prepare_turn([], "t1:agent-1", consolidate=False)
    await asyncio.sleep(0)
    assert cons.runs == []
    await harness.prepare_turn([], "t1")
    for _ in range(3):
        await asyncio.sleep(0)
    assert cons.runs == ["t1"]


@pytest.mark.asyncio
async def test_the_recall_key_is_per_run_and_released() -> None:
    spy = _SpyRecall()
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=spy,
                            scope_kind="workspace", scope_id="/ws")
    await harness.prepare_turn([], "run-a", query="q1")
    await harness.prepare_turn([], "run-b", query="q2")
    await harness.prepare_turn([], "run-a", query="q1")  # cached per run — no thrash
    assert spy.calls == 2
    harness.release_run("run-a")
    await harness.prepare_turn([], "run-a", query="q1")
    assert spy.calls == 3


def test_a_recall_only_source_needs_no_consolidator() -> None:
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=_SpyRecall(),
                            scope_kind="workspace", scope_id="/ws")
    assert harness.memory_tool_source("t1") is None  # unchanged default path
    child = harness.memory_tool_source("t1:agent-1", allow_remember=False)
    assert child is not None
    assert [d.name for d in child.definitions()] == ["recall"]
    assert child.owns("recall") and not child.owns("remember")


def test_no_recall_engine_means_no_child_source() -> None:
    harness = MemoryHarness(enabled=True, compactor=None, consolidator=_Consolidator(),
                            scope_kind="workspace", scope_id="/ws")
    assert harness.memory_tool_source("t1:agent-1", allow_remember=False) is None
    assert harness.memory_tool_source("t1").owns("remember")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_memory_child_modes.py > /tmp/pb5.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pb5.txt`
Expected: `exit=1`; all four tests fail (`unexpected keyword argument 'consolidate'`, `no attribute 'release_run'`, `unexpected keyword argument 'allow_remember'`).

- [ ] **Step 3: Implement the harness**

In `agentd/memory/harness.py`:
1. `__init__`: replace `self._recall_key: str | None = None  # last (run_id::query) recalled, to dedup per turn` with
   ```python
           # Last query recalled PER RUN (dedup per turn). A dict, not one slot: concurrent
           # sub-agent runs alternating queries would otherwise evict each other every turn.
           self._recall_keys: dict[str, str] = {}
   ```
2. `prepare_turn` signature: `observed: ObservedPrompt | None = None,` → `observed: ObservedPrompt | None = None, *, consolidate: bool = True,`, and the consolidation condition `if (result and result.compacted and self._consolidator is not None` → `if (consolidate and result and result.compacted and self._consolidator is not None`.
3. `_fill_recall`: `if key != self._recall_key:` → `if key != self._recall_keys.get(run_id):`, and `self._recall_key = key` → `self._recall_keys[run_id] = key`.
4. Add after `_fill_recall`:
   ```python
       def release_run(self, run_id: str) -> None:
           """Drop a finished run's recall caches (a sub-agent run ends for good)."""
           self._recall_cache.pop(run_id, None)
           self._trace_cache.pop(run_id, None)
           self._recall_keys.pop(run_id, None)
   ```
5. Replace `memory_tool_source` with:
   ```python
       def memory_tool_source(
           self, run_id: str = "", *, allow_remember: bool = True,
       ) -> object | None:
           """A MemoryToolSource for the controller registry. The default (remember + recall)
           needs a consolidator; a sub-agent's recall-only source (allow_remember=False,
           spec §5.2) needs only a recall engine. None when the needed piece is absent."""
           from agentd.memory.tool_source import MemoryToolSource
           if allow_remember:
               if self._consolidator is None:
                   return None
               return MemoryToolSource(
                   self._consolidator, self._scope_kind, self._scope_id,
                   recall_engine=self._recall_engine, store=self._store, run_id=run_id,
               )
           if self._recall_engine is None:
               return None
           return MemoryToolSource(
               None, self._scope_kind, self._scope_id,
               recall_engine=self._recall_engine, store=self._store, run_id=run_id,
               allow_remember=False,
           )
   ```

- [ ] **Step 4: Implement the source**

In `agentd/memory/tool_source.py`, `MemoryToolSource.__init__`: `consolidator: object` → `consolidator: object | None`, add the keyword parameter `allow_remember: bool = True,` after `run_id`, and store `self._allow_remember = allow_remember`. Replace `definitions`, `owns`, and the first lines of `execute`:

```python
    def definitions(self) -> list[ToolDefinition]:
        defs = [_REMEMBER_DEF] if self._allow_remember else []
        if self._recall_engine is not None:
            defs.append(_RECALL_DEF)
        return defs

    def owns(self, tool: str) -> bool:
        return tool == "recall" or (tool == "remember" and self._allow_remember)

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool == "recall":
            return await self._recall(args)
        if tool != "remember" or not self._allow_remember:
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
```

(the rest of `execute` — the `remember` body — is unchanged).

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_memory_child_modes.py tests/test_memory_recall_wiring.py tests/test_memory_harness.py tests/test_memory_terminal_trigger.py > /tmp/pb5.txt 2>&1; echo exit=$?; tail -1 /tmp/pb5.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/memory/harness.py agentd/memory/tool_source.py tests/test_memory_child_modes.py
git commit -m "feat(memory): child modes — no consolidation, recall-only, per-run recall key"
```

---

### Task B6: Additive skills for children

**Files:**
- Modify: `agentd/skills/tool_source.py`
- Test: `tests/test_skills_additive.py`

**Interfaces:**
- `SkillToolSource(loader, active_skills, on_activate=None, *, additive: bool = False)` — `additive=True` (children) adds each `read_skill` body instead of replacing the single active skill (spec §5.2). Children pass no `on_activate`, so nothing is persisted to the thread.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_additive.py`:

```python
"""Children accumulate skills; the parent keeps exactly one active skill (spec §5.2)."""
from pathlib import Path

import pytest

from agentd.skills.loader import SkillCatalogLoader
from agentd.skills.tool_source import SkillToolSource


def _write_skill(ws: Path, name: str, body: str) -> None:
    d = ws / ".crucible" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_additive_keeps_every_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tdd", "TDD body")
    _write_skill(tmp_path, "debugging", "Debug body")
    active: dict[str, str] = {}
    src = SkillToolSource(SkillCatalogLoader(str(tmp_path)), active, additive=True)
    await src.execute("read_skill", {"name": "tdd"})
    await src.execute("read_skill", {"name": "debugging"})
    assert sorted(active) == ["debugging", "tdd"]


@pytest.mark.asyncio
async def test_the_default_still_replaces(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tdd", "TDD body")
    _write_skill(tmp_path, "debugging", "Debug body")
    active: dict[str, str] = {}
    src = SkillToolSource(SkillCatalogLoader(str(tmp_path)), active)
    await src.execute("read_skill", {"name": "tdd"})
    await src.execute("read_skill", {"name": "debugging"})
    assert list(active) == ["debugging"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_skills_additive.py > /tmp/pb6.txt 2>&1; echo exit=$?; grep -m1 TypeError /tmp/pb6.txt`
Expected: `exit=1`; `TypeError: … unexpected keyword argument 'additive'`.

- [ ] **Step 3: Implement**

In `agentd/skills/tool_source.py`, `SkillToolSource.__init__` gains the keyword-only parameter `*, additive: bool = False` after `on_activate` and stores `self._additive = additive`; in `execute`, replace `        self._active.clear()` with

```python
        if not self._additive:
            # The parent keeps exactly one active skill; a sub-agent accumulates every
            # skill it reads (spec §5.2) and persists none of them.
            self._active.clear()
```

and replace the last paragraph of the class docstring (from "Exactly one skill is active at a time:" to the closing quotes) with:

```python
    For the main agent exactly one skill is active at a time: a new read_skill REPLACES
    the previous entry rather than accumulating, and (when on_activate is given) persists
    the replacement immediately so it survives a mid-turn /stop, not just a clean
    turn-end. A sub-agent's source is `additive` instead (spec §5.2)."""
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_skills_additive.py tests/test_skills_thread_persistence.py > /tmp/pb6.txt 2>&1; echo exit=$?; tail -1 /tmp/pb6.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/skills/tool_source.py tests/test_skills_additive.py
git commit -m "feat(skills): additive read_skill for sub-agents"
```

---

### Part B verification

- [ ] **Step 1: Full suite** — as Part A Step 1 (only the pre-existing failure).
- [ ] **Step 2: Lint** — `.venv/bin/ruff check agentd/subagents tests/test_subagent_context.py tests/test_subagent_permissions.py tests/test_vcs_guard.py tests/test_permission_correction.py tests/test_memory_child_modes.py tests/test_skills_additive.py` → `All checks passed!`; then the Part A lint-diff procedure with `BASE` = the commit before Task B1 and `F=(agentd/prompting/tagged.py agentd/tools/sources.py agentd/tools/registry.py agentd/chat/controller_loop.py agentd/memory/harness.py agentd/memory/tool_source.py agentd/skills/tool_source.py)` → `new=0`.

---

# Part C — The AGENT loop runtime

`ControllerLoop` learns to be a child: an `agent: AgentContext` switches its allowed types to the agent's set (narrowing to `["report"]` on the final iteration), passes the persona to the engine, marks the payload read-only for `plan` agents, skips memory consolidation, and ends on `report`. A `SequencedBroadcaster` stamps every child-channel event with a monotonic `seq`. With `agent=None` (every existing caller) nothing changes.

### File structure (Part C)

| File | Responsibility |
|---|---|
| `agentd/subagents/events.py` | `SequencedBroadcaster` |
| `agentd/chat/controller_loop.py` | `agent` param; per-iteration allowed types; persona seam; `agent_readonly`; `consolidate=False`; the `report` terminal; `fallback_report()` |

### Task C1: `SequencedBroadcaster`

**Files:**
- Create: `agentd/subagents/events.py`
- Test: `tests/test_sequenced_broadcaster.py`

**Interfaces:**
- `SequencedBroadcaster(inner: EventBroadcaster, channel_id: str)` — a subclass of `EventBroadcaster` whose `broadcast` stamps `seq` (1, 2, 3, …) onto a **copy** of every event sent to `channel_id` and forwards everything to `inner`; events for any other channel pass through unstamped. `subscribe`/`unsubscribe`/`clear_replay` delegate to `inner`, so subscribers and the replay buffer are the process-wide ones. `last_seq` is the last stamped number (spec §5.5: the child loop's own events are the cursor's source of truth; Part D records it on every transcript message).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sequenced_broadcaster.py`:

```python
"""Child-channel events carry a monotonic seq (spec §5.5)."""
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.subagents.events import SequencedBroadcaster

CHILD = "chat:t1:agent:agent-1"


def test_stamps_only_its_own_channel() -> None:
    inner = EventBroadcaster()
    child_q, thread_q = inner.subscribe(CHILD), inner.subscribe("chat:t1")
    seq = SequencedBroadcaster(inner, CHILD)
    seq.broadcast(CHILD, {"type": "tool_call", "payload": {}})
    seq.broadcast("chat:t1", {"type": "agent_status", "payload": {}})
    seq.broadcast(CHILD, {"type": "tool_result", "payload": {}})
    assert [child_q.get_nowait()["seq"] for _ in range(2)] == [1, 2]
    assert "seq" not in thread_q.get_nowait()
    assert seq.last_seq == 2


def test_the_callers_event_is_not_mutated() -> None:
    seq = SequencedBroadcaster(EventBroadcaster(), CHILD)
    event = {"type": "progress", "payload": {}}
    seq.broadcast(CHILD, event)
    assert "seq" not in event


def test_subscribe_and_replay_are_the_inner_ones() -> None:
    inner = EventBroadcaster()
    seq = SequencedBroadcaster(inner, CHILD)
    seq.broadcast(CHILD, {"type": "a", "payload": {}})
    late = seq.subscribe(CHILD)  # replay comes from the shared buffer
    assert late.get_nowait()["seq"] == 1
    seq.clear_replay(CHILD)
    assert inner.subscribe(CHILD).empty()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sequenced_broadcaster.py > /tmp/pc1.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pc1.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.events'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/events.py`:

```python
"""Sub-agent channel events (spec §5.5)."""
from __future__ import annotations

from typing import Any

from agentd.orchestrator.broadcaster import EventBroadcaster


class SequencedBroadcaster(EventBroadcaster):
    """Stamps a monotonic `seq` on every event broadcast to ONE channel (a child's).

    `call_index` resets at every pills boundary, so it cannot identify an event; `seq`
    can. The UI backfills a child's transcript (whose messages record the seq that
    produced them) and then skips replayed events at or below that cursor, so
    backfill-then-subscribe never renders a duplicate. Other channels pass through
    untouched: the parent's channel never gains a field."""

    def __init__(self, inner: EventBroadcaster, channel_id: str) -> None:
        super().__init__()
        self._inner = inner
        self._channel = channel_id
        self._seq = 0

    @property
    def last_seq(self) -> int:
        return self._seq

    def broadcast(self, channel_id: str, event: dict[str, Any]) -> None:
        if channel_id == self._channel:
            self._seq += 1
            event = {**event, "seq": self._seq}
        self._inner.broadcast(channel_id, event)

    def subscribe(self, channel_id: str) -> Any:
        return self._inner.subscribe(channel_id)

    def unsubscribe(self, channel_id: str, queue: Any) -> None:
        self._inner.unsubscribe(channel_id, queue)

    def clear_replay(self, channel_id: str) -> None:
        self._inner.clear_replay(channel_id)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sequenced_broadcaster.py > /tmp/pc1.txt 2>&1; echo exit=$?; tail -1 /tmp/pc1.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/events.py tests/test_sequenced_broadcaster.py
git commit -m "feat(subagents): monotonic seq on child-channel events"
```

---

### Task C2: `ControllerLoop(agent=…)` — types, persona, read-only flag, memory

**Files:**
- Modify: `agentd/chat/controller_loop.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- `ControllerLoop(..., agent: AgentContext | None = None)` (keyword, after `render_ctx`). The caller builds the matching `render_ctx` with `RenderContext.for_agent` and starts the SM at `"AGENT"` (Part D does both).
- `_allowed_action_types()` with an agent: `list(agent.allowed_types)` — except on the **final iteration** (`iteration >= max_iters`), where it is `["report"]` (spec §6.5). The schema narrows; the system prompt does not (it renders from `render_ctx.base_types`, fixed for the loop — spec §15 "child system prompt identical across iterations").
- The engine gets `persona=agent.persona` through the same `accepts_kwarg` seam as `allowed_types`/`render_ctx`.
- `plan_context["agent_readonly"] = agent.permission == "plan"` (the Plan 1A AGENT payload branch already reads it).
- Memory: `prepare_turn(..., consolidate=False)` for an agent; the parent's call is unchanged (no new kwarg).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_loop.py`:

```python
"""ControllerLoop as a sub-agent (spec §5.1, §6.5)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.todo_ledger import TodoItem, TodoLedger
from agentd.memory.models import TurnPreparation
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.subagents.context import AgentContext
from agentd.subagents.permissions import child_allowed_types
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


class _Recording(ScriptedReasoningEngine):
    def __init__(self, steps: list[dict[str, object]]) -> None:
        super().__init__(None, [], controller_step_responses=steps)
        self.calls: list[dict[str, object]] = []

    async def create_controller_step(
        self, plan_context, history, tool_definitions, *, phase, on_thinking=None,
        on_retry=None, on_progress=None, on_salvage=None, on_usage=None,
        unconstrained=False, allowed_types=None, render_ctx=None, persona=None,
    ):
        self.calls.append({"allowed_types": list(allowed_types or []), "persona": persona,
                           "readonly": plan_context.get("agent_readonly"),
                           "iteration": plan_context.get("iteration")})
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, render_ctx=render_ctx)


class _SpyHarness:
    def __init__(self) -> None:
        self.consolidate: list[object] = []

    async def prepare_turn(self, history, run_id, query="", observed=None, **kwargs):
        self.consolidate.append(kwargs.get("consolidate", "absent"))
        return TurnPreparation(history=history)


def _agent(permission: str = "default") -> AgentContext:
    return AgentContext(
        agent_id="agent-1", name="general-purpose", label="impl", depth=1,
        parent_agent_id=None, permission=permission,
        allowed_types=child_allowed_types(permission), persona="Be terse.", max_iters=5)


def _loop(tmp_path: Path, steps: list[dict[str, object]], agent: AgentContext | None,
          *, ledger: TodoLedger | None = None, harness: object | None = None,
          ) -> tuple[ControllerLoop, _Recording]:
    engine = _Recording(steps)
    registry = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    kwargs: dict[str, object] = {}
    if harness is not None:
        kwargs["memory_harness"] = harness
    if agent is not None:
        kwargs["render_ctx"] = RenderContext.for_agent(
            agent, tools=frozenset(d.name for d in registry.definitions()), shell_policy="ask")
        kwargs["agent"] = agent
    loop = ControllerLoop(
        engine, registry, EventBroadcaster(), channel_id="chat:t:agent:agent-1",
        phase_sm=ControllerPhaseSM(start="AGENT" if agent else "ACTIVE"), todo_ledger=ledger,
        **kwargs)
    return loop, engine


REPORT = {"type": "report", "thought": "t", "summary": "Changed api/a.py. Tests pass."}
LIST = {"type": "tool_call", "thought": "t", "tool": "list_directory", "args": {"path": "."}}
SEARCH = {"type": "tool_call", "thought": "t", "tool": "search_code", "args": {"pattern": "x"}}


@pytest.mark.asyncio
async def test_agent_types_persona_and_readonly_flag(tmp_path: Path) -> None:
    loop, engine = _loop(tmp_path, [REPORT], _agent())
    await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert engine.calls[0] == {"allowed_types": ["tool_call", "edit", "progress", "report"],
                               "persona": "Be terse.", "readonly": False, "iteration": 0}


@pytest.mark.asyncio
async def test_a_read_only_agent_has_no_edit(tmp_path: Path) -> None:
    loop, engine = _loop(tmp_path, [REPORT], _agent("plan"))
    await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert engine.calls[0]["allowed_types"] == ["tool_call", "progress", "report"]
    assert engine.calls[0]["readonly"] is True


@pytest.mark.asyncio
async def test_the_final_iteration_narrows_to_report(tmp_path: Path) -> None:
    loop, engine = _loop(tmp_path, [LIST, SEARCH, REPORT], _agent())
    await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=2)
    assert [c["allowed_types"] for c in engine.calls] == [
        ["tool_call", "edit", "progress", "report"],
        ["tool_call", "edit", "progress", "report"],
        ["report"]]


@pytest.mark.asyncio
async def test_children_never_consolidate_and_the_parent_call_is_unchanged(
    tmp_path: Path,
) -> None:
    child_harness, parent_harness = _SpyHarness(), _SpyHarness()
    child, _ = _loop(tmp_path, [REPORT], _agent(), harness=child_harness)
    await child.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    parent, _ = _loop(tmp_path, [{"type": "submit_changes", "thought": "d", "summary": "ok"}],
                      None, harness=parent_harness)
    await parent.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert child_harness.consolidate == [False]
    assert parent_harness.consolidate == ["absent"]
```

(`REPORT` is a terminal only after Task C3; until then these tests fail at the first `report` with `NotImplementedError`, which is the expected red.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py > /tmp/pc2.txt 2>&1; echo exit=$?; grep -m1 TypeError /tmp/pc2.txt`
Expected: `exit=1`; `TypeError: ControllerLoop.__init__() got an unexpected keyword argument 'agent'`.

- [ ] **Step 3: Implement**

In `agentd/chat/controller_loop.py`:

1. Under `if TYPE_CHECKING:` add `    from agentd.subagents.context import AgentContext` (sorted with the other TYPE_CHECKING imports).
2. `ControllerLoop.__init__`: add the keyword parameter `agent: AgentContext | None = None,` after `render_ctx: RenderContext | None = None,`, and directly after `self._render_ctx = render_ctx or RenderContext.main()` add:

```python
        # A dispatched sub-agent (spec §5.1). None = the main agent, whose behavior is
        # unchanged by everything keyed off this.
        self._agent = agent
        # Set per iteration by _iterate; the final iteration narrows a child's types.
        self._iteration = 0
        self._max_iters = 0
```

3. Replace `_allowed_action_types` with:

```python
    def _allowed_action_types(self) -> list[str]:
        """The action types legal THIS iteration. The main agent: the phase SM's own set,
        plus a conditional propose_mode addition when in ACTIVE with the task subsystem on
        (I4: lets a big-enough ACTIVE-phase request escalate to a reviewed task without a
        detour through Plan Mode). A sub-agent: its own type set, narrowed to `report` on
        the final iteration so the budget always ends in a report (spec §6.5) — the system
        prompt keeps the full base set, only the schema narrows."""
        if self._agent is not None:
            if self._iteration >= self._max_iters:
                return ["report"]
            return list(self._agent.allowed_types)
        types = list(self._sm.allowed_types())
        if self._sm.phase == "ACTIVE" and self._task_subsystem_enabled:
            types.append("propose_mode")
        return types
```

4. `run()`: replace `        plan_context = {**plan_context, "max_iters": max_iters}` with

```python
        plan_context = {**plan_context, "max_iters": max_iters}
        self._max_iters = max_iters
        if self._agent is not None:
            # The Plan 1A AGENT payload branch reads this for its read-only hints.
            plan_context["agent_readonly"] = self._agent.permission == "plan"
```

5. `_iterate`: at the top of the `for iteration in range(max_iters + 1):` body (before `self._apply_pills_boundary()`) add `            self._iteration = iteration`.
6. The memory call: replace

```python
            _prep = await self._memory_harness.prepare_turn(
                history, run_id, query=str(plan_context.get("goal", "")),
                observed=self._observed_prompt)
```

with

```python
            _prep = await self._memory_harness.prepare_turn(
                history, run_id, query=str(plan_context.get("goal", "")),
                observed=self._observed_prompt,
                # A child's run never schedules consolidation (spec §5.2); the parent's
                # call is left exactly as it was.
                **({"consolidate": False} if self._agent is not None else {}))
```

7. The seam kwargs: after the `render_ctx` seam lines

```python
                if accepts_kwarg(step_fn, "render_ctx"):
                    seam_kwargs["render_ctx"] = self._render_ctx
```

add

```python
                if self._agent is not None and accepts_kwarg(step_fn, "persona"):
                    seam_kwargs["persona"] = self._agent.persona
```

- [ ] **Step 4: Confirm the remaining red is only the missing terminal**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py > /tmp/pc2.txt 2>&1; echo exit=$?; grep -c NotImplementedError /tmp/pc2.txt`
Expected: `exit=1`, and a non-zero count — every failure is now `NotImplementedError: report` (Task C3 adds the terminal).

Run: `.venv/bin/python -m pytest tests/test_prompt_goldens.py tests/test_controller_schema.py tests/test_controller_agent_payload.py tests/test_engine_agent_seams.py > /tmp/pc2b.txt 2>&1; echo exit=$?; tail -1 /tmp/pc2b.txt`
Expected: `exit=0` (the main agent is untouched).

(No commit yet — Task C3 completes this unit.)

---

### Task C3: The `report` terminal and the fallback report

**Files:**
- Modify: `agentd/chat/controller_loop.py`
- Test: `tests/test_agent_loop.py` (appended)

**Interfaces:**
- `ControllerOutcome.kind` gains `"report"`; its `payload` is `{"status": "completed" | "partial"}`. `text` is the full summary — **never truncated** (D8).
- The `report` branch (spec §5.1, §6.5): while the todo ledger has pending items and this is **not** the final iteration → a `report BLOCKED` redirect (not malformed; only `max_iters` bounds it, mirroring `submit_changes`). On the final iteration the block is bypassed, the still-open items are appended as `"\n\nUnfinished:\n- <title> (<status>)"`, and the status is `partial`; a report before the final iteration is `completed`. An empty `summary` is already corrected and retried by `_empty_action_correction` (Plan 1A).
- `ControllerLoop.fallback_report(error: str, files_changed: list[str], *, status: str = "failed") -> str` — the deterministic report for a child that produced none (malformed exhaustion, a crash, or a stop — spec §6.5, §11.4; Part D passes `status="stopped"` for a stop): the status and error, `files_changed`, the open todo items and the last 10 tool calls (name + JSON args). Synthesized from what the loop recorded, never from model text.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_agent_loop.py`:

```python
@pytest.mark.asyncio
async def test_a_voluntary_report_is_completed_and_verbatim(tmp_path: Path) -> None:
    long = "x" * 50_000
    loop, _ = _loop(tmp_path, [{"type": "report", "thought": "t", "summary": long}], _agent())
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert out.kind == "report" and out.payload == {"status": "completed"}
    assert out.text == long


@pytest.mark.asyncio
async def test_open_todos_block_a_report_until_the_final_iteration(tmp_path: Path) -> None:
    ledger = TodoLedger(items=[TodoItem(title="write the limiter"),
                               TodoItem(title="docs", status="done")])
    loop, _ = _loop(tmp_path, [REPORT, LIST, REPORT], _agent(), ledger=ledger)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=2)
    contents = [str(m.get("content", "")) for m in out.history or []]
    assert any(c.startswith("report BLOCKED — 1 todo item(s) still open: write the limiter")
               for c in contents)
    assert out.kind == "report" and out.payload == {"status": "partial"}
    assert out.text == ("Changed api/a.py. Tests pass.\n\nUnfinished:\n"
                        "- write the limiter (pending)")


@pytest.mark.asyncio
async def test_the_fallback_report_is_synthesized_from_the_record(tmp_path: Path) -> None:
    ledger = TodoLedger(items=[TodoItem(title="write the limiter", status="in_progress")])
    empty = {"type": "report", "thought": "t", "summary": ""}
    loop, _ = _loop(tmp_path, [LIST, empty, empty, empty, empty, empty], _agent(),
                    ledger=ledger)
    with pytest.raises(Exception, match="consecutive malformed"):
        await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=20)
    report = loop.fallback_report("the model stopped responding", ["api/a.py"])
    assert report == (
        "Status: failed — the model stopped responding\n\n"
        "Files changed: api/a.py\n\n"
        "Unfinished:\n- write the limiter (in_progress)\n\n"
        'Last tool calls:\n- list_directory {"path": "."}')
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py > /tmp/pc3.txt 2>&1; echo exit=$?; grep -cE "NotImplementedError|AttributeError" /tmp/pc3.txt`
Expected: `exit=1` with a non-zero count (`NotImplementedError: report`; `'ControllerLoop' object has no attribute 'fallback_report'`).

- [ ] **Step 3: Implement**

In `agentd/chat/controller_loop.py`:

1. `ControllerOutcome`: replace the `kind: str  # "answer" | "clarify" | "propose_mode" | "submit_changes"` line with:

```python
    # "answer" | "clarify" | "propose_mode" | "submit_changes" | "report" (a sub-agent's
    # terminal; its payload is {"status": "completed" | "partial"}).
    kind: str
```
2. Directly above `            if atype == "submit_changes":` insert:

```python
            if atype == "report":
                # A sub-agent's only terminal and its WHOLE deliverable (spec §5.1, D8): the
                # summary is returned verbatim, never truncated.
                still_open = self._ledger.pending()
                final = iteration >= max_iters
                if still_open and not final:
                    # Same contract as submit_changes: a redirect, NOT a malformed action,
                    # so it does not touch consecutive_malformed — only max_iters bounds it.
                    titles = ", ".join(i.title for i in still_open)
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "",
                        "content": (
                            f"report BLOCKED — {len(still_open)} todo item(s) still open: "
                            f"{titles}. Continue with the next item, then call write_todos to "
                            "mark it 'done' (cite evidence in 'note'). If one is genuinely "
                            "stuck, mark it 'blocked' (with the unblock reason) or 'cancelled' "
                            "(with why). Report once nothing is pending."),
                    })
                    continue
                history.append(assistant_turn(resp))
                summary = str(resp.get("summary", "")).strip()
                if still_open:
                    # Final iteration: the budget is spent, so the ledger block is bypassed
                    # and the dispatcher is told exactly what is left (spec §6.5).
                    summary += "\n\nUnfinished:\n" + "\n".join(
                        f"- {i.title} ({i.status})" for i in still_open)
                return ControllerOutcome(
                    kind="report", text=summary, history=history,
                    payload={"status": "partial" if final else "completed"})
```

3. Add the method to `ControllerLoop` (next to `partial_history`):

```python
    def fallback_report(
        self, error: str, files_changed: list[str], *, status: str = "failed",
    ) -> str:
        """The report for a sub-agent that produced none (malformed exhaustion, a crash,
        or a stop — spec §6.5, §11.4). Synthesized from what the loop recorded — never a
        truncation of anything the model wrote."""
        lines = [f"Status: {status} — {error}", "",
                 "Files changed: " + (", ".join(files_changed) or "none")]
        still_open = self._ledger.pending()
        if still_open:
            lines += ["", "Unfinished:", *(f"- {i.title} ({i.status})" for i in still_open)]
        recent = self._calls[-10:]
        if recent:
            lines += ["", "Last tool calls:", *(
                f"- {c.tool_name} {json.dumps(c.arguments, sort_keys=True)}" for c in recent)]
        return "\n".join(lines)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py tests/test_prompt_goldens.py tests/test_controller_agent_schema.py tests/test_controller_agent_payload.py tests/test_engine_agent_seams.py tests/test_controller_child_corrections.py > /tmp/pc3.txt 2>&1; echo exit=$?; tail -1 /tmp/pc3.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_loop.py tests/test_agent_loop.py
git commit -m "feat(controller): AGENT loop — per-iteration types, report terminal, fallback report"
```

---

### Part C verification

- [ ] **Step 1: Full suite** — as Part A Step 1 (only the pre-existing failure).
- [ ] **Step 2: Lint** — `.venv/bin/ruff check agentd/subagents tests/test_sequenced_broadcaster.py tests/test_agent_loop.py` → `All checks passed!`; the Part A lint-diff procedure with `BASE` = the commit before Task C1 and `F=(agentd/chat/controller_loop.py)` → `new=0`.

---

# Part D — Runtime, dispatch and child callbacks

This part makes children real: durable storage, the runtime that runs them under the concurrency cap, the `dispatch_agents` tool, child-scoped gates and transcripts, the controller method that builds and runs one child, and the parent's teaching. Everything is reachable only when `CRUCIBLE_SUBAGENTS_ENABLED` is on (the parent's registry gains `dispatch_agents` only then).

### File structure (Part D)

| File | Responsibility |
|---|---|
| `agentd/chat/models.py` | `ChatMessage.type` gains `agent_dispatch`; `AgentRecord` |
| `agentd/chat/storage.py` | `chat_agents` table; `insert_agent`, `update_agent`, `set_agent_transcript`, `get_agent`, `list_agents` |
| `agentd/subagents/transcript.py` | `AgentTranscript` — a child's durable message list |
| `agentd/subagents/write_log.py` | `files_changed_by(agent_ids)` |
| `agentd/subagents/runtime.py` | `DispatchRequest`, `ChildResult`, `AgentHandle`, `AgentRegistry`, `SubAgentRuntime`, `agent_channel` |
| `agentd/subagents/tool_source.py` | `SubAgentToolSource` (`dispatch_agents`) |
| `agentd/chat/controller.py` | runtime wiring, dispatch hook, child gates/callbacks, `_run_child` |
| `agentd/chat/controller_loop.py` | `plan_context["dispatch_available"]` for the parent |
| `agentd/chat/controller_prompts.py` | `_DISPATCH_BLOCK`; the entry-hint dispatch clause |

### Task D1: `chat_agents` storage and the `agent_dispatch` message type

**Files:**
- Modify: `agentd/chat/models.py`, `agentd/chat/storage.py`
- Test: `tests/test_chat_agents_store.py`

**Interfaces:**
- `ChatMessage.type` adds `"agent_dispatch"` (the parent transcript's roster anchor, spec §6.1, §11.3). An unknown type in this Literal would break every thread read, which is why the type lands before anything writes it.
- `AgentRecord(BaseModel)`: `agent_id, thread_id, turn_id, parent_agent_id: str | None, depth: int, name, label, prompt, status, report="", files_changed: list[str], stale_refusals=0, transcript: list[ChatMessage], started_at: datetime | None, ended_at: datetime | None`.
- `ChatThreadStore.insert_agent(record)`, `update_agent(agent_id, *, status=None, report=None, files_changed=None, stale_refusals=None, started_at=None, ended_at=None)` (only the given fields change; columns come from a fixed map and values are parameterized), `set_agent_transcript(agent_id, messages)`, `get_agent(agent_id) -> AgentRecord | None`, `list_agents(thread_id, turn_id=None) -> list[AgentRecord]` (insertion order).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chat_agents_store.py`:

```python
"""chat_agents rows (spec §11.3)."""
from datetime import UTC, datetime
from pathlib import Path

from agentd.chat.models import AgentRecord, ChatMessage
from agentd.chat.storage import ChatThreadStore


def _record(agent_id: str, turn_id: str = "turn-1", **kw: object) -> AgentRecord:
    base: dict[str, object] = dict(
        agent_id=agent_id, thread_id="t1", turn_id=turn_id, parent_agent_id=None, depth=1,
        name="general-purpose", label=agent_id, prompt="do it", status="queued")
    base.update(kw)
    return AgentRecord(**base)


def test_insert_get_update_and_list(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.insert_agent(_record("agent-a"))
    store.insert_agent(_record("agent-b", parent_agent_id="agent-a", depth=2))
    store.insert_agent(_record("agent-c", turn_id="turn-2"))
    now = datetime.now(UTC)
    store.update_agent("agent-a", status="completed", report="R" * 30_000,
                       files_changed=["a.py"], stale_refusals=2, started_at=now, ended_at=now)
    got = store.get_agent("agent-a")
    assert got is not None
    assert (got.status, got.files_changed, got.stale_refusals) == ("completed", ["a.py"], 2)
    assert got.report == "R" * 30_000  # never truncated
    assert got.started_at == now and got.ended_at == now
    assert [r.agent_id for r in store.list_agents("t1")] == ["agent-a", "agent-b", "agent-c"]
    assert [r.agent_id for r in store.list_agents("t1", "turn-1")] == ["agent-a", "agent-b"]
    assert store.get_agent("nope") is None


def test_the_transcript_round_trips(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.insert_agent(_record("agent-a"))
    store.set_agent_transcript("agent-a", [
        ChatMessage(role="agent", content="note", metadata={"progress": True, "seq": 3})])
    got = store.get_agent("agent-a")
    assert got is not None and got.transcript[0].content == "note"
    assert got.transcript[0].metadata["seq"] == 3


def test_agent_dispatch_is_a_thread_message_type(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.append_message(tid, ChatMessage(
        role="agent", content="", type="agent_dispatch",
        metadata={"agent_ids": ["agent-a"], "turn_id": "turn-1"}))
    thread = store.get_thread(tid)
    assert thread is not None and thread.messages[-1].type == "agent_dispatch"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chat_agents_store.py > /tmp/pd1.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/pd1.txt`
Expected: `exit=2`, `ImportError` (`AgentRecord`).

- [ ] **Step 3: Implement the models**

In `agentd/chat/models.py`, change the `ChatMessage.type` line to

```python
    type: Literal["text", "plan_card", "diff_card", "diff_summary", "task_card", "scope_card",
                  "agent_dispatch"] = "text"
```

and add after `ChatMessage`:

```python
class AgentRecord(BaseModel):
    """One sub-agent of a dispatch (spec §11.3) — a `chat_agents` row."""
    agent_id: str
    thread_id: str
    turn_id: str
    parent_agent_id: str | None = None
    depth: int
    name: str
    label: str
    prompt: str
    status: str  # queued | running | waiting | completed | partial | failed | stopped
    report: str = ""  # the full report, never truncated (D8)
    files_changed: list[str] = Field(default_factory=list)
    stale_refusals: int = 0
    transcript: list[ChatMessage] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
```

- [ ] **Step 4: Implement the store**

In `agentd/chat/storage.py`: add `AgentRecord` to the `agentd.chat.models` import — the line then exceeds 100 characters, so rewrite it as a parenthesized, one-name-per-line block (`AgentRecord, CapturedFile, ChatMessage, ChatThread, Checkpoint, PendingGate`). In `_migrate`, directly before the final `self._conn.commit()`, add:

```python
        # Sub-agents (spec §11.3): one row per dispatched agent, its transcript inline.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_agents (
                agent_id            TEXT PRIMARY KEY,
                thread_id           TEXT NOT NULL,
                turn_id             TEXT NOT NULL,
                parent_agent_id     TEXT,
                depth               INTEGER NOT NULL,
                name                TEXT NOT NULL,
                label               TEXT NOT NULL,
                prompt              TEXT NOT NULL,
                status              TEXT NOT NULL,
                report              TEXT NOT NULL DEFAULT '',
                files_changed_json  TEXT NOT NULL DEFAULT '[]',
                stale_refusals      INTEGER NOT NULL DEFAULT 0,
                transcript_json     TEXT NOT NULL DEFAULT '[]',
                started_at          TEXT,
                ended_at            TEXT
            );
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS chat_agents_by_turn ON chat_agents(thread_id, turn_id)")
```

and add these methods at the end of the class:

```python
    # ── Sub-agents (spec §11.3) ─────────────────────────────────────────────
    _AGENT_UPDATABLE = {
        "status": "status", "report": "report", "files_changed": "files_changed_json",
        "stale_refusals": "stale_refusals", "started_at": "started_at",
        "ended_at": "ended_at",
    }

    def insert_agent(self, record: AgentRecord) -> None:
        self._conn.execute(
            "INSERT INTO chat_agents (agent_id, thread_id, turn_id, parent_agent_id, depth, "
            "name, label, prompt, status, report, files_changed_json, stale_refusals, "
            "transcript_json, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.agent_id, record.thread_id, record.turn_id, record.parent_agent_id,
             record.depth, record.name, record.label, record.prompt, record.status,
             record.report, json.dumps(record.files_changed), record.stale_refusals,
             json.dumps([m.model_dump(mode="json") for m in record.transcript]),
             record.started_at.isoformat() if record.started_at else None,
             record.ended_at.isoformat() if record.ended_at else None))
        self._conn.commit()

    def update_agent(
        self, agent_id: str, *, status: str | None = None, report: str | None = None,
        files_changed: list[str] | None = None, stale_refusals: int | None = None,
        started_at: datetime | None = None, ended_at: datetime | None = None,
    ) -> None:
        """Change only the given fields. Column names come from a fixed map, values are
        always bound parameters."""
        given: dict[str, object] = {
            "status": status, "report": report,
            "files_changed": json.dumps(files_changed) if files_changed is not None else None,
            "stale_refusals": stale_refusals,
            "started_at": started_at.isoformat() if started_at else None,
            "ended_at": ended_at.isoformat() if ended_at else None,
        }
        pairs = [(self._AGENT_UPDATABLE[k], v) for k, v in given.items() if v is not None]
        if not pairs:
            return
        assignments = ", ".join(f"{column} = ?" for column, _ in pairs)
        self._conn.execute(
            f"UPDATE chat_agents SET {assignments} WHERE agent_id = ?",  # noqa: S608 — fixed map
            (*[v for _, v in pairs], agent_id))
        self._conn.commit()

    def set_agent_transcript(self, agent_id: str, messages: list[ChatMessage]) -> None:
        self._conn.execute(
            "UPDATE chat_agents SET transcript_json = ? WHERE agent_id = ?",
            (json.dumps([m.model_dump(mode="json") for m in messages]), agent_id))
        self._conn.commit()

    @staticmethod
    def _agent_from_row(row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"], thread_id=row["thread_id"], turn_id=row["turn_id"],
            parent_agent_id=row["parent_agent_id"], depth=row["depth"], name=row["name"],
            label=row["label"], prompt=row["prompt"], status=row["status"],
            report=row["report"], files_changed=json.loads(row["files_changed_json"]),
            stale_refusals=row["stale_refusals"],
            transcript=[ChatMessage.model_validate(m)
                        for m in json.loads(row["transcript_json"])],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None)

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        row = self._conn.execute(
            "SELECT * FROM chat_agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return self._agent_from_row(row) if row is not None else None

    def list_agents(self, thread_id: str, turn_id: str | None = None) -> list[AgentRecord]:
        if turn_id is None:
            rows = self._conn.execute(
                "SELECT * FROM chat_agents WHERE thread_id = ? ORDER BY rowid",
                (thread_id,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM chat_agents WHERE thread_id = ? AND turn_id = ? ORDER BY rowid",
                (thread_id, turn_id)).fetchall()
        return [self._agent_from_row(r) for r in rows]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_chat_agents_store.py tests/test_controller_gate_list.py tests/test_rewind_scenarios.py > /tmp/pd1.txt 2>&1; echo exit=$?; tail -1 /tmp/pd1.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/models.py agentd/chat/storage.py tests/test_chat_agents_store.py
git commit -m "feat(chat): chat_agents table and the agent_dispatch message type"
```

---

### Task D2: `AgentTranscript` and `files_changed_by`

**Files:**
- Create: `agentd/subagents/transcript.py`
- Modify: `agentd/subagents/write_log.py`
- Test: `tests/test_agent_transcript.py`

**Interfaces:**
- `AgentTranscript(persist: Callable[[list[ChatMessage]], None], current_seq: Callable[[], int])` — the child's durable message list (spec §5.5): `messages` (copy), `append(message)`, `async upsert_pills(tool_events, thinking_log)` (the loop awaits `on_pills_update`), `seal_pills()` (the loop's `pills_seal_cb`), `last_seq()`. Every write stamps `metadata["seq"]` with the child channel's latest seq and mints an `id` when missing, then persists the whole list. Pills segmentation mirrors the thread store: one in-flight pills message updated in place until sealed, then a fresh one.
- `WorkspaceWriteLog.files_changed_by(agent_ids: set[str]) -> list[str]` — sorted union of every path any of those agents ever promoted (spec §6.3: a child's `files_changed` is its whole subtree's).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_transcript.py`:

```python
"""A child's durable transcript (spec §5.5) and its subtree's files (spec §6.3)."""
import pytest

from agentd.chat.models import ChatMessage
from agentd.subagents.transcript import AgentTranscript
from agentd.subagents.write_log import WorkspaceWriteLog


@pytest.mark.asyncio
async def test_pills_update_in_place_until_sealed_and_every_write_is_stamped() -> None:
    saved: list[list[ChatMessage]] = []
    seq = {"n": 0}
    transcript = AgentTranscript(saved.append, lambda: seq["n"])
    seq["n"] = 2
    await transcript.upsert_pills([{"id": 1, "tool": "read_file"}], [])
    seq["n"] = 4
    await transcript.upsert_pills([{"id": 1, "tool": "read_file"}, {"id": 2, "tool": "ls"}],
                                  ["t"])
    assert len(transcript.messages) == 1
    assert transcript.messages[0].metadata["tool_events"][1]["tool"] == "ls"
    assert transcript.messages[0].metadata["seq"] == 4
    transcript.seal_pills()
    seq["n"] = 5
    transcript.append(ChatMessage(role="agent", content="note", metadata={"progress": True}))
    seq["n"] = 7
    await transcript.upsert_pills([{"id": 3, "tool": "search_code"}], [])
    assert [m.content for m in transcript.messages] == ["", "note", ""]
    assert transcript.last_seq() == 7
    assert all(m.id for m in transcript.messages)
    assert saved[-1] == transcript.messages  # every change persisted, whole list


def test_files_changed_by_is_the_union_over_the_given_agents() -> None:
    log = WorkspaceWriteLog()
    for agent in ("a", "b", "c"):
        log.register_agent(agent)
    log.note_promote("a", "A", "g", ["x.py"])
    log.note_promote("b", "B", "g", ["y.py", "x.py"])
    log.note_promote("c", "C", "g", ["z.py"])
    assert log.files_changed_by({"a", "b"}) == ["x.py", "y.py"]
    assert log.files_changed_by({"nobody"}) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_transcript.py > /tmp/pd2.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pd2.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.transcript'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/transcript.py`:

```python
"""A sub-agent's durable transcript (spec §5.5).

The same ChatMessage shapes and pills segmentation as a thread transcript, kept in memory
and persisted whole to `chat_agents.transcript_json` on every change. Each message records
the seq of the latest child-channel event when it was written: the UI backfills the
transcript, then skips replayed events at or below that cursor.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from agentd.chat.models import ChatMessage


class AgentTranscript:
    def __init__(
        self, persist: Callable[[list[ChatMessage]], None], current_seq: Callable[[], int],
    ) -> None:
        self._messages: list[ChatMessage] = []
        self._inflight: int | None = None  # index of the pills message still being updated
        self._persist = persist
        self._current_seq = current_seq

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def append(self, message: ChatMessage) -> None:
        self._messages.append(self._stamped(message))
        self._save()

    async def upsert_pills(
        self, tool_events: list[dict[str, object]], thinking_log: list[str],
    ) -> None:
        metadata: dict[str, object] = {"tool_events": tool_events}
        if thinking_log:
            metadata["thinking_log"] = thinking_log
        message = self._stamped(ChatMessage(role="agent", content="", metadata=metadata))
        if self._inflight is None:
            self._inflight = len(self._messages)
            self._messages.append(message)
        else:
            self._messages[self._inflight] = message.model_copy(
                update={"id": self._messages[self._inflight].id})
        self._save()

    def seal_pills(self) -> None:
        """A durable message landed: later pills start a fresh message after it."""
        self._inflight = None

    def last_seq(self) -> int:
        return max((int(m.metadata.get("seq", 0)) for m in self._messages), default=0)

    def _stamped(self, message: ChatMessage) -> ChatMessage:
        return message.model_copy(update={
            "id": message.id or uuid4().hex,
            "metadata": {**message.metadata, "seq": self._current_seq()},
        })

    def _save(self) -> None:
        self._persist(list(self._messages))
```

In `agentd/subagents/write_log.py`, `WorkspaceWriteLog.__init__` gains (after `self._agents = …`)

```python
        # Every path each agent ever promoted — a child's files_changed is its subtree's.
        self._promoted: dict[str, set[str]] = {}
```

`note_promote` gains, after `view = self._view(agent_id)`, the line `        self._promoted.setdefault(agent_id, set()).update(paths)`, and add the method:

```python
    def files_changed_by(self, agent_ids: set[str]) -> list[str]:
        """Sorted union of every path any of `agent_ids` promoted (spec §6.3)."""
        paths: set[str] = set()
        for agent_id in agent_ids:
            paths |= self._promoted.get(agent_id, set())
        return sorted(paths)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_agent_transcript.py tests/test_write_log.py > /tmp/pd2.txt 2>&1; echo exit=$?; tail -1 /tmp/pd2.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/transcript.py agentd/subagents/write_log.py tests/test_agent_transcript.py
git commit -m "feat(subagents): durable child transcripts and subtree files_changed"
```

---

### Task D3: `SubAgentRuntime` — registry, slot tokens, gather

**Files:**
- Create: `agentd/subagents/runtime.py`
- Test: `tests/test_subagent_runtime.py`

**Interfaces:**
- `agent_channel(thread_id, agent_id) -> str` — `"chat:{thread_id}:agent:{agent_id}"`.
- `TERMINAL_STATUSES = {"completed", "partial", "failed", "stopped"}`.
- `@dataclass(frozen=True) DispatchRequest(agent: AgentDefinition, prompt: str, label: str)`; `@dataclass(frozen=True) ChildResult(status, report, files_changed, stale_refusals=0)`.
- `@dataclass(eq=False) AgentHandle(context, definition, prompt, thread_id, turn_id, status="queued", held=False, task=None, loop=None, transcript=None, broadcaster=None, started_at=None, ended_at=None, result=None)` with `agent_id` property (spec §6.7).
- `AgentRegistry`: `add`, `get`, `for_turn(thread_id, turn_id)`, `subtree_ids(agent_id) -> set[str]` (self + every descendant).
- `SubAgentRuntime(max_concurrent, on_status=None)`:
  - `registry`; `set_status(handle, status)` (stamps `started_at` on the first `running`, `ended_at` on a terminal status, then calls `on_status(handle)`).
  - `async dispatch(handles, run_child, *, dispatcher=None) -> list[ChildResult]` — registers the handles; a dispatcher that holds a slot **releases** it before awaiting its children and **re-acquires** after they return (spec §6.2: a parent waiting on children must not pin a slot they may need); runs each child as a task under one `BoundedSemaphore`; `gather(..., return_exceptions=True)`, so one child's crash or stop never affects its siblings. If the dispatcher itself is cancelled while waiting, it never re-acquires (its `held` stays `False`, so nothing is over-released).
  - Contract for `run_child(handle)`: return a `ChildResult` (it catches its own failures); on cancellation, set `handle.result` (e.g. a `stopped` fallback report) and re-raise. The runtime's backstops: an exception becomes `failed`, a cancel without `handle.result` becomes `stopped` ("Stopped before it started.").
  - `async stop_agent(agent_id) -> bool` — cancels that child's task (its own dispatches are cancelled through their gather) and waits for it to unwind; `False` when the agent is unknown or already done.
- Logs: `[subagent] slot acquire|release id=… held=…`, `[subagent] finish id=… parent=… depth=… name=… status=… files=…` (spec §13).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_runtime.py`:

```python
"""SubAgentRuntime (spec §6.2, §6.7): cap, slot tokens, isolation, stop."""
import asyncio

import pytest

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.runtime import (
    AgentHandle,
    ChildResult,
    SubAgentRuntime,
    agent_channel,
)


def _handle(agent_id: str, parent: str | None = None, depth: int = 1) -> AgentHandle:
    ctx = AgentContext(agent_id=agent_id, name="general-purpose", label=agent_id, depth=depth,
                       parent_agent_id=parent, permission="default",
                       allowed_types=("tool_call", "edit", "progress", "report"),
                       persona="", max_iters=10)
    return AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"],
                       prompt="p", thread_id="t1", turn_id="turn-1")


def test_the_child_channel_name() -> None:
    assert agent_channel("t1", "agent-a") == "chat:t1:agent:agent-a"


@pytest.mark.asyncio
async def test_the_cap_queues_extra_children_and_statuses_flow() -> None:
    seen: list[tuple[str, str]] = []
    runtime = SubAgentRuntime(1, on_status=lambda h: seen.append((h.agent_id, h.status)))
    running = {"now": 0, "peak": 0}

    async def run_child(handle: AgentHandle) -> ChildResult:
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return ChildResult(status="completed", report=f"done {handle.agent_id}",
                           files_changed=[])

    results = await runtime.dispatch([_handle("a"), _handle("b")], run_child)
    assert [r.report for r in results] == ["done a", "done b"]
    assert running["peak"] == 1
    assert seen == [("a", "running"), ("a", "completed"), ("b", "running"), ("b", "completed")]
    handle = runtime.registry.get("a")
    assert handle is not None and handle.started_at and handle.ended_at and not handle.held


@pytest.mark.asyncio
async def test_a_dispatching_child_lends_its_slot_and_takes_it_back() -> None:
    runtime = SubAgentRuntime(1)
    parent = _handle("p")
    await runtime._acquire(parent)  # the dispatcher is running and holds the only slot

    async def run_child(handle: AgentHandle) -> ChildResult:
        return ChildResult(status="completed", report="ok", files_changed=[])

    results = await asyncio.wait_for(
        runtime.dispatch([_handle("c", parent="p", depth=2)], run_child, dispatcher=parent),
        timeout=1)
    assert results[0].status == "completed" and parent.held
    runtime._release(parent)
    with pytest.raises(ValueError):  # BoundedSemaphore: any over-release is loud
        runtime._slots.release()


@pytest.mark.asyncio
async def test_a_cancelled_dispatcher_never_takes_its_slot_back() -> None:
    runtime = SubAgentRuntime(1)
    parent = _handle("p")
    await runtime._acquire(parent)
    started = asyncio.Event()

    async def run_child(handle: AgentHandle) -> ChildResult:
        started.set()
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    waiting = asyncio.create_task(
        runtime.dispatch([_handle("c", parent="p", depth=2)], run_child, dispatcher=parent))
    await started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not parent.held
    child = runtime.registry.get("c")
    assert child is not None and child.status == "stopped"
    with pytest.raises(ValueError):  # every slot is back; one more release would overflow
        runtime._slots.release()


@pytest.mark.asyncio
async def test_one_child_failing_or_stopped_never_touches_its_siblings() -> None:
    runtime = SubAgentRuntime(4)
    started = asyncio.Event()

    async def run_child(handle: AgentHandle) -> ChildResult:
        if handle.agent_id == "boom":
            raise RuntimeError("crash")
        if handle.agent_id == "slow":
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                handle.result = ChildResult(status="stopped", report="stopped mid-way",
                                            files_changed=["s.py"])
                raise
        return ChildResult(status="completed", report="ok", files_changed=[])

    dispatching = asyncio.create_task(runtime.dispatch(
        [_handle("ok"), _handle("boom"), _handle("slow")], run_child))
    await started.wait()
    assert await runtime.stop_agent("slow") is True
    results = await dispatching
    assert [r.status for r in results] == ["completed", "failed", "stopped"]
    assert results[1].report == "Status: failed — crash"
    assert results[2].report == "stopped mid-way" and results[2].files_changed == ["s.py"]
    assert await runtime.stop_agent("slow") is False


def test_subtree_ids() -> None:
    runtime = SubAgentRuntime(2)
    for handle in (_handle("a"), _handle("b", parent="a", depth=2),
                   _handle("c", parent="b", depth=3), _handle("d")):
        runtime.registry.add(handle)
    assert runtime.registry.subtree_ids("a") == {"a", "b", "c"}
    assert runtime.registry.subtree_ids("d") == {"d"}
    assert [h.agent_id for h in runtime.registry.for_turn("t1", "turn-1")] == ["a", "b", "c", "d"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subagent_runtime.py > /tmp/pd3.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pd3.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.runtime'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/runtime.py`:

```python
"""SubAgentRuntime (spec §6.2, §6.7): runs dispatched children under one process-wide
concurrency cap, keeps the registry, and owns slot accounting.

It knows nothing about loops, stores or gates: the controller passes `run_child` (build and
run one child) and `on_status` (persist + broadcast a status change).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import AgentDefinition

if TYPE_CHECKING:
    from agentd.subagents.events import SequencedBroadcaster
    from agentd.subagents.transcript import AgentTranscript

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "stopped"})


def agent_channel(thread_id: str, agent_id: str) -> str:
    return f"chat:{thread_id}:agent:{agent_id}"


@dataclass(frozen=True)
class DispatchRequest:
    agent: AgentDefinition
    prompt: str
    label: str


@dataclass(frozen=True)
class ChildResult:
    status: str
    report: str  # the full report, never truncated (D8)
    files_changed: list[str]
    stale_refusals: int = 0


@dataclass(eq=False)
class AgentHandle:
    context: AgentContext
    definition: AgentDefinition
    prompt: str
    thread_id: str
    turn_id: str
    status: str = "queued"
    held: bool = False  # the slot token (spec §6.2)
    task: asyncio.Task[ChildResult] | None = None
    loop: object | None = None  # the child's ControllerLoop once built
    transcript: AgentTranscript | None = None
    broadcaster: SequencedBroadcaster | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result: ChildResult | None = None

    @property
    def agent_id(self) -> str:
        return self.context.agent_id


class AgentRegistry:
    def __init__(self) -> None:
        self._handles: dict[str, AgentHandle] = {}

    def add(self, handle: AgentHandle) -> None:
        self._handles[handle.agent_id] = handle

    def get(self, agent_id: str) -> AgentHandle | None:
        return self._handles.get(agent_id)

    def for_turn(self, thread_id: str, turn_id: str) -> list[AgentHandle]:
        return [h for h in self._handles.values()
                if h.thread_id == thread_id and h.turn_id == turn_id]

    def subtree_ids(self, agent_id: str) -> set[str]:
        ids, frontier = {agent_id}, [agent_id]
        while frontier:
            current = frontier.pop()
            for handle in self._handles.values():
                if handle.context.parent_agent_id == current and handle.agent_id not in ids:
                    ids.add(handle.agent_id)
                    frontier.append(handle.agent_id)
        return ids


RunChild = Callable[[AgentHandle], Awaitable[ChildResult]]
StatusSink = Callable[[AgentHandle], None]


class SubAgentRuntime:
    def __init__(self, max_concurrent: int, on_status: StatusSink | None = None) -> None:
        # Process-wide on purpose: the cap protects the provider's rate limit. `main` never
        # holds a slot. Bounded, so any accounting bug is a loud ValueError.
        self._slots = asyncio.BoundedSemaphore(max_concurrent)
        self._on_status = on_status
        self.registry = AgentRegistry()

    def set_status(self, handle: AgentHandle, status: str) -> None:
        handle.status = status
        if status == "running" and handle.started_at is None:
            handle.started_at = datetime.now(UTC)
        if status in TERMINAL_STATUSES:
            handle.ended_at = datetime.now(UTC)
        if self._on_status is not None:
            self._on_status(handle)

    async def dispatch(
        self, handles: list[AgentHandle], run_child: RunChild, *,
        dispatcher: AgentHandle | None = None,
    ) -> list[ChildResult]:
        for handle in handles:
            self.registry.add(handle)
        if dispatcher is not None and dispatcher.held:
            # A child waiting on its own children must not pin a slot they may need.
            self._release(dispatcher)
        tasks = [asyncio.create_task(self._run_one(h, run_child)) for h in handles]
        for handle, task in zip(handles, tasks, strict=True):
            handle.task = task
        # A cancel of THIS await (the dispatcher stopping) cancels every child and
        # propagates; the dispatcher then never re-acquires, so `held` stays False.
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if dispatcher is not None:
            await self._acquire(dispatcher)
        return [self._result_of(h, o) for h, o in zip(handles, outcomes, strict=True)]

    async def stop_agent(self, agent_id: str) -> bool:
        handle = self.registry.get(agent_id)
        if handle is None or handle.task is None or handle.task.done():
            return False
        handle.task.cancel()
        await asyncio.wait({handle.task})
        return True

    async def _run_one(self, handle: AgentHandle, run_child: RunChild) -> ChildResult:
        try:
            await self._acquire(handle)
            self.set_status(handle, "running")
            result = await run_child(handle)
        except asyncio.CancelledError:
            self._finish(handle, handle.result or ChildResult(
                status="stopped", report="Stopped before it started.", files_changed=[]))
            raise
        except Exception as exc:  # backstop: run_child is meant to catch its own failures
            logger.exception("[subagent] run_child raised id=%s", handle.agent_id)
            result = handle.result or ChildResult(
                status="failed", report=f"Status: failed — {exc}", files_changed=[])
        finally:
            if handle.held:
                self._release(handle)
        self._finish(handle, result)
        return result

    def _result_of(self, handle: AgentHandle, outcome: object) -> ChildResult:
        if isinstance(outcome, ChildResult):
            return outcome
        assert handle.result is not None  # _run_one finished every path
        return handle.result

    def _finish(self, handle: AgentHandle, result: ChildResult) -> None:
        handle.result = result
        self.set_status(handle, result.status)
        logger.info("[subagent] finish id=%s parent=%s depth=%d name=%s status=%s files=%d",
                    handle.agent_id, handle.context.parent_agent_id, handle.context.depth,
                    handle.context.name, result.status, len(result.files_changed))

    async def _acquire(self, handle: AgentHandle) -> None:
        await self._slots.acquire()
        handle.held = True  # only after acquire returns: a cancel above leaves it False
        logger.info("[subagent] slot acquire id=%s held=%s", handle.agent_id, handle.held)

    def _release(self, handle: AgentHandle) -> None:
        handle.held = False
        self._slots.release()
        logger.info("[subagent] slot release id=%s held=%s", handle.agent_id, handle.held)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_runtime.py > /tmp/pd3.txt 2>&1; echo exit=$?; tail -1 /tmp/pd3.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/runtime.py tests/test_subagent_runtime.py
git commit -m "feat(subagents): runtime with registry, slot tokens and isolated gather"
```

---

### Task D4: `SubAgentToolSource` — the `dispatch_agents` tool

**Files:**
- Create: `agentd/subagents/tool_source.py`
- Test: `tests/test_dispatch_tool_source.py`

**Interfaces:**
- `SubAgentToolSource(catalog: dict[str, AgentDefinition], dispatch: Callable[[list[DispatchRequest]], Awaitable[list[tuple[AgentHandle, ChildResult]]]])`, `name = "subagents"`, one tool `dispatch_agents` (spec §6.1). Its description carries the agent catalog (`- name: description`); the arg schema enumerates the agent names.
- Validation (`ToolOutput(is_error=True)`, never a dispatch): `agents` missing/empty/not a list, an item that is not an object, an unknown agent, an empty prompt, a duplicate label. Every error ends with `Valid agents: <comma-separated names>.`. `label` is optional; the default is `{agent}-{n}` (n counting per agent within the call).
- Result (spec §6.3): `output` is a JSON array, one entry per child in request order: `{"agent_id", "agent", "label", "status", "report", "files_changed", "stale_refusals"}` with the **full** report; `workspace_changes` is the sorted union of every child's `files_changed` (Part A's loop bookkeeping).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_tool_source.py`:

```python
"""dispatch_agents (spec §6.1, §6.3)."""
import json

import pytest

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.runtime import AgentHandle, ChildResult, DispatchRequest
from agentd.subagents.tool_source import SubAgentToolSource


def _fake_dispatch(results: dict[str, ChildResult]):
    calls: list[list[DispatchRequest]] = []

    async def dispatch(requests: list[DispatchRequest]):
        calls.append(requests)
        out = []
        for i, req in enumerate(requests):
            ctx = AgentContext(agent_id=f"agent-{i}", name=req.agent.name, label=req.label,
                               depth=1, parent_agent_id=None, permission=req.agent.permission,
                               allowed_types=("tool_call", "report"), persona="", max_iters=5)
            handle = AgentHandle(context=ctx, definition=req.agent, prompt=req.prompt,
                                 thread_id="t", turn_id="u")
            out.append((handle, results[req.label]))
        return out
    return dispatch, calls


def test_the_definition_carries_the_catalog() -> None:
    src = SubAgentToolSource(BUILTIN_AGENTS, _fake_dispatch({})[0])
    [definition] = src.definitions()
    assert definition.name == "dispatch_agents" and src.owns("dispatch_agents")
    assert "- explore: Read-only investigator" in definition.description
    assert "- general-purpose: Implements one self-contained part" in definition.description
    agent_schema = definition.parameters["properties"]["agents"]["items"]["properties"]["agent"]
    assert agent_schema["enum"] == ["explore", "general-purpose"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args,problem", [
    ({}, "'agents' must be a non-empty list"),
    ({"agents": []}, "'agents' must be a non-empty list"),
    ({"agents": ["x"]}, "agents[0] must be an object"),
    ({"agents": [{"agent": "nope", "prompt": "p"}]}, "agents[0]: unknown agent 'nope'"),
    ({"agents": [{"agent": "explore", "prompt": "  "}]}, "agents[0]: 'prompt' is empty"),
    ({"agents": [{"agent": "explore", "prompt": "p", "label": "x"},
                 {"agent": "explore", "prompt": "q", "label": "x"}]},
     "agents[1]: duplicate label 'x'"),
])
async def test_validation_errors_never_dispatch(args: dict[str, object], problem: str) -> None:
    dispatch, calls = _fake_dispatch({})
    out = await SubAgentToolSource(BUILTIN_AGENTS, dispatch).execute("dispatch_agents", args)
    assert out.is_error
    assert out.output == f"Error: {problem}. Valid agents: explore, general-purpose."
    assert calls == []


@pytest.mark.asyncio
async def test_default_labels_full_reports_and_workspace_changes() -> None:
    long_report = "R" * 40_000
    dispatch, calls = _fake_dispatch({
        "explore-1": ChildResult(status="completed", report="found it", files_changed=[]),
        "general-purpose-1": ChildResult(status="partial", report=long_report,
                                         files_changed=["b.py", "a.py"], stale_refusals=1),
    })
    out = await SubAgentToolSource(BUILTIN_AGENTS, dispatch).execute("dispatch_agents", {
        "agents": [{"agent": "explore", "prompt": "find X"},
                   {"agent": "general-purpose", "prompt": "fix Y"}]})
    assert not out.is_error
    assert [r.label for r in calls[0]] == ["explore-1", "general-purpose-1"]
    entries = json.loads(out.output)
    assert entries[1] == {"agent_id": "agent-1", "agent": "general-purpose",
                          "label": "general-purpose-1", "status": "partial",
                          "report": long_report, "files_changed": ["b.py", "a.py"],
                          "stale_refusals": 1}
    assert out.workspace_changes == ["a.py", "b.py"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatch_tool_source.py > /tmp/pd4.txt 2>&1; echo exit=$?; grep -m1 ModuleNotFoundError /tmp/pd4.txt`
Expected: `exit=2`, `ModuleNotFoundError: No module named 'agentd.subagents.tool_source'`.

- [ ] **Step 3: Implement**

Create `agentd/subagents/tool_source.py`:

```python
"""SubAgentToolSource: the dispatch_agents tool (spec §6.1, §6.3)."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from agentd.subagents.definitions import AgentDefinition
from agentd.subagents.runtime import AgentHandle, ChildResult, DispatchRequest
from agentd.tools.registry import ToolDefinition, ToolOutput

DISPATCH_TOOL_NAME = "dispatch_agents"

Dispatch = Callable[[list[DispatchRequest]], Awaitable[list[tuple[AgentHandle, ChildResult]]]]


class SubAgentToolSource:
    name = "subagents"

    def __init__(self, catalog: dict[str, AgentDefinition], dispatch: Dispatch) -> None:
        self._catalog = catalog
        self._dispatch = dispatch

    def definitions(self) -> list[ToolDefinition]:
        lines = "\n".join(f"- {d.name}: {d.description}" for d in self._catalog.values())
        return [ToolDefinition(
            name=DISPATCH_TOOL_NAME,
            description=(
                "Run sub-agents in parallel. Each starts with ONLY the prompt you write and "
                "returns one full report plus the files it changed; the call returns when "
                f"every agent has finished. Available agents:\n{lines}"),
            parameters={
                "type": "object",
                "properties": {"agents": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "enum": list(self._catalog)},
                        "prompt": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["agent", "prompt"],
                }}},
                "required": ["agents"],
            })]

    def owns(self, tool: str) -> bool:
        return tool == DISPATCH_TOOL_NAME

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != DISPATCH_TOOL_NAME:
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        requests, problem = self._parse(args)
        if problem is not None:
            valid = ", ".join(self._catalog)
            return ToolOutput(output=f"Error: {problem}. Valid agents: {valid}.", is_error=True)
        pairs = await self._dispatch(requests)
        entries = [{
            "agent_id": handle.agent_id, "agent": handle.context.name,
            "label": handle.context.label, "status": result.status,
            "report": result.report, "files_changed": result.files_changed,
            "stale_refusals": result.stale_refusals,
        } for handle, result in pairs]
        changed = sorted({f for _, result in pairs for f in result.files_changed})
        return ToolOutput(output=json.dumps(entries, indent=2), workspace_changes=changed)

    def _parse(self, args: dict[str, object]) -> tuple[list[DispatchRequest], str | None]:
        raw = args.get("agents")
        if not isinstance(raw, list) or not raw:
            return [], "'agents' must be a non-empty list"
        requests: list[DispatchRequest] = []
        labels: set[str] = set()
        counts: dict[str, int] = {}
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return [], f"agents[{i}] must be an object"
            name = str(item.get("agent", ""))
            definition = self._catalog.get(name)
            if definition is None:
                return [], f"agents[{i}]: unknown agent {name!r}"
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                return [], f"agents[{i}]: 'prompt' is empty"
            counts[name] = counts.get(name, 0) + 1
            label = str(item.get("label") or "").strip() or f"{name}-{counts[name]}"
            if label in labels:
                return [], f"agents[{i}]: duplicate label {label!r}"
            labels.add(label)
            requests.append(DispatchRequest(agent=definition, prompt=prompt, label=label))
        return requests, None
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_dispatch_tool_source.py > /tmp/pd4.txt 2>&1; echo exit=$?; tail -1 /tmp/pd4.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/tool_source.py tests/test_dispatch_tool_source.py
git commit -m "feat(subagents): dispatch_agents tool with validation and full reports"
```

---
### Task D5: Gates from children

**Files:**
- Modify: `agentd/chat/controller.py`
- Test: `tests/test_child_gates.py`

**Interfaces (spec §5.6, §8):**
- `_edit_decision_cb`, `_command_approval_cb`, `_mcp_approval_cb` gain a keyword-only `child: AgentHandle | None = None`. With a child: the gate carries `agent=GateAgent(id, label, name)`; an edit gate's payload also records `shadow_key = "chatturn-{thread_id}-{agent_id}"`; the instant-render poke's payload gains `"agent": {"id", "label", "name"}`; the child's status is `waiting` while the gate is open and back to `running` after; the decision breadcrumb goes to the **child's** transcript and channel, never the parent's. With `child=None` nothing changes.
- New wrappers the child's sources use:
  - `_child_command_approval_cb(handle, command, args, cwd)` — `allow_all` or a remembered rule → allow; `dontAsk` → `ApprovalOutcome.deny("policy")` without a gate; otherwise the gated path with `child=handle`.
  - `_child_mcp_approval_cb(handle, server, tool, args)` — a remembered MCP rule → allow; `dontAsk` → `deny("policy")`; otherwise gated.
  - `_child_edit_decision_cb(handle, diff)` — the gated edit path with `child=handle`.
- Helpers: `_gate_agent(child)`, `_set_child_status(child, status)`, `_gate_breadcrumb(thread_id, channel_id, text, child)`, `_child_breadcrumb(child, text)`.
- `ChatController.__init__` builds `self._subagents = SubAgentRuntime(subagent_max_concurrent(), on_status=self._on_agent_status)` when the flag is on (else `None`); `_on_agent_status(handle)` persists the status (and, at a terminal status, the report/files/stale count) and broadcasts `agent_status` — plus `agent_finished` at a terminal status — on the **thread** channel (spec §11.2).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_child_gates.py`:

```python
"""Gates raised by sub-agents (spec §5.6, §8)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, DiffEntry, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.events import SequencedBroadcaster
from agentd.subagents.permissions import child_allowed_types
from agentd.subagents.runtime import AgentHandle, agent_channel
from agentd.subagents.transcript import AgentTranscript
from tests.gate_helpers import first_gate


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permission: str = "default"):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None, shell_policy=ShellPolicy.ASK)
    assert ctrl._subagents is not None
    ctx = AgentContext(agent_id="agent-1", name="general-purpose", label="impl", depth=1,
                       parent_agent_id=None, permission=permission,
                       allowed_types=child_allowed_types(permission), persona="", max_iters=5)
    handle = AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"],
                         prompt="p", thread_id=tid, turn_id="turn-1", status="running")
    broadcaster = SequencedBroadcaster(ctrl._broadcaster, agent_channel(tid, "agent-1"))
    handle.broadcaster = broadcaster
    handle.transcript = AgentTranscript(lambda msgs: None, lambda: broadcaster.last_seq)
    store.insert_agent(AgentRecord(agent_id="agent-1", thread_id=tid, turn_id="turn-1",
                                   depth=1, name="general-purpose", label="impl",
                                   prompt="p", status="running"))
    ctrl._subagents.registry.add(handle)
    return ctrl, store, tid, handle


async def _gate(store: ChatThreadStore, tid: str):
    for _ in range(100):
        await asyncio.sleep(0)
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            return gate
    raise AssertionError("no gate")


@pytest.mark.asyncio
async def test_a_child_command_gate_is_tagged_and_recorded_on_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch)
    pokes = ctrl._broadcaster.subscribe(f"chat:{tid}")
    pending = asyncio.create_task(ctrl._child_command_approval_cb(handle, "ls", ["-la"], ""))
    gate = await _gate(store, tid)
    assert gate.kind == "command" and gate.agent is not None
    assert (gate.agent.id, gate.agent.label, gate.agent.name) == (
        "agent-1", "impl", "general-purpose")
    assert handle.status == "waiting" and store.get_agent("agent-1").status == "waiting"
    events = [pokes.get_nowait() for _ in range(pokes.qsize())]
    poke = next(e for e in events if e["type"] == "command_approval_requested")
    assert poke["payload"]["agent"] == {"id": "agent-1", "label": "impl",
                                        "name": "general-purpose"}
    assert await ctrl.resolve_command(tid, CommandDecision(approve=True),
                                      gate_id=gate.gate_id) is True
    assert (await pending).approved is True
    assert handle.status == "running"
    assert handle.transcript is not None
    assert [m.content for m in handle.transcript.messages] == ["✓ Command approved: ls"]
    thread = store.get_thread(tid)
    assert thread is not None
    assert not any("Command approved" in m.content for m in thread.messages)


@pytest.mark.asyncio
async def test_dont_ask_denies_by_policy_without_a_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch, permission="dontAsk")
    outcome = await ctrl._child_command_approval_cb(handle, "rm", ["-rf", "x"], "")
    assert outcome.approved is False and outcome.denied_by == "policy"
    mcp = await ctrl._child_mcp_approval_cb(handle, "gh", "create_issue", {})
    assert mcp.approved is False and mcp.denied_by == "policy"
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []


@pytest.mark.asyncio
async def test_a_child_edit_gate_records_its_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch)
    diff = [DiffEntry(path="a.py", additions=1, deletions=0, temp_path="/s/a.py",
                      unified_diff="@@\n+a")]
    pending = asyncio.create_task(ctrl._child_edit_decision_cb(handle, diff))
    gate = await _gate(store, tid)
    assert gate.kind == "edit" and gate.agent is not None
    assert gate.payload["shadow_key"] == f"chatturn-{tid}-agent-1"
    await ctrl.resolve_edit(tid, {"decision": "accept"}, gate_id=gate.gate_id)
    assert (await pending)["decision"] == "accept"


def test_status_changes_reach_the_store_and_the_thread_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch)
    events = ctrl._broadcaster.subscribe(f"chat:{tid}")
    from agentd.subagents.runtime import ChildResult
    handle.result = ChildResult(status="completed", report="done", files_changed=["a.py"],
                                stale_refusals=1)
    assert ctrl._subagents is not None
    ctrl._subagents.set_status(handle, "completed")
    row = store.get_agent("agent-1")
    assert row is not None
    assert (row.status, row.report, row.files_changed, row.stale_refusals) == (
        "completed", "done", ["a.py"], 1)
    assert row.ended_at is not None
    seen = [events.get_nowait() for _ in range(events.qsize())]
    assert [e["type"] for e in seen] == ["agent_status", "agent_finished"]
    assert seen[1]["payload"] == {"agent_id": "agent-1", "status": "completed",
                                  "files_changed": ["a.py"]}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_child_gates.py > /tmp/pd5.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pd5.txt`
Expected: `exit=1`; all four fail (`ctrl._subagents` does not exist).

- [ ] **Step 3: Implement the wiring and helpers**

In `agentd/chat/controller.py`:

1. Imports (module level, sorted into the existing blocks): add `AgentRecord` and `GateAgent` to the `agentd.chat.models` import (rewrite it as a parenthesized, one-name-per-line block — it no longer fits on one line); add

```python
from agentd.prompting.tagged import RenderContext
from agentd.subagents.config import subagent_max_concurrent
from agentd.subagents.runtime import (
    TERMINAL_STATUSES,
    AgentHandle,
    SubAgentRuntime,
    agent_channel,
)
```

2. In `__init__`, after the `self._write_logs` lines:

```python
        # The sub-agent runtime (spec §6.2): process-wide, built once when enabled.
        self._subagents: SubAgentRuntime | None = (
            SubAgentRuntime(subagent_max_concurrent(), on_status=self._on_agent_status)
            if is_subagents_enabled() else None)
```

3. Add these methods (next to `_write_breadcrumb`):

```python
    def _on_agent_status(self, handle: AgentHandle) -> None:
        """Persist a sub-agent status change and announce it on the THREAD channel — the
        low-volume events (spec §5.5, §11.2); everything else stays on the child's own."""
        self._store.update_agent(handle.agent_id, status=handle.status,
                                 started_at=handle.started_at, ended_at=handle.ended_at)
        thread_channel = f"chat:{handle.thread_id}"
        self._broadcaster.broadcast(thread_channel, {
            "type": "agent_status",
            "payload": {"agent_id": handle.agent_id, "status": handle.status}})
        if handle.status in TERMINAL_STATUSES and handle.result is not None:
            result = handle.result
            self._store.update_agent(handle.agent_id, report=result.report,
                                     files_changed=result.files_changed,
                                     stale_refusals=result.stale_refusals)
            self._broadcaster.broadcast(thread_channel, {
                "type": "agent_finished",
                "payload": {"agent_id": handle.agent_id, "status": result.status,
                            "files_changed": result.files_changed}})

    @staticmethod
    def _gate_agent(child: AgentHandle | None) -> GateAgent | None:
        if child is None:
            return None
        return GateAgent(id=child.agent_id, label=child.context.label, name=child.context.name)

    def _set_child_status(self, child: AgentHandle | None, status: str) -> None:
        if child is not None and self._subagents is not None:
            self._subagents.set_status(child, status)

    def _gate_breadcrumb(
        self, thread_id: str, channel_id: str, text: str, child: AgentHandle | None,
    ) -> None:
        if child is None:
            self._write_breadcrumb(thread_id, channel_id, text)
        else:
            self._child_breadcrumb(child, text)

    def _child_breadcrumb(self, child: AgentHandle, text: str) -> None:
        """A sub-agent's decision record goes to ITS transcript and channel, never the
        parent's (spec §5.5); the boundary is the child loop's, not the parent's."""
        if isinstance(child.loop, ControllerLoop):
            child.loop.mark_pills_boundary()
        if child.broadcaster is not None:
            child.broadcaster.broadcast(agent_channel(child.thread_id, child.agent_id), {
                "type": "chat_breadcrumb", "payload": {"text": text, "task_id": ""}})
        if child.transcript is not None:
            child.transcript.append(ChatMessage(
                role="agent", content=text, metadata={"breadcrumb": True}))

    async def _child_command_approval_cb(
        self, child: AgentHandle, command: str, args: list[str], cwd: str,
    ) -> ApprovalOutcome:
        if (self._shell_policy == ShellPolicy.ALLOW_ALL
                or CommandRuleStore(self._workspace_path).matches(command, args)):
            return ApprovalOutcome.allow()
        if child.context.permission == "dontAsk":
            # dontAsk: only a remembered rule or allow_all runs a command; nobody is asked
            # (spec §5.6), and the model is told the truth (rev 11 §4.6.4).
            return ApprovalOutcome.deny("policy")
        return await self._command_approval_cb(
            child.thread_id, f"chat:{child.thread_id}", command, args, cwd, child=child)

    async def _child_mcp_approval_cb(
        self, child: AgentHandle, server: str, tool: str, args: dict[str, object],
    ) -> ApprovalOutcome:
        from agentd.mcp.rules import McpRuleStore

        if McpRuleStore(self._workspace_path).matches(server, tool):
            return ApprovalOutcome.allow()
        if child.context.permission == "dontAsk":
            return ApprovalOutcome.deny("policy")
        return await self._mcp_approval_cb(
            child.thread_id, f"chat:{child.thread_id}", server, tool, args, child=child)

    async def _child_edit_decision_cb(
        self, child: AgentHandle, diff: list[DiffEntry],
    ) -> dict[str, object]:
        return await self._edit_decision_cb(
            child.thread_id, f"chat:{child.thread_id}", diff, child=child)
```

- [ ] **Step 4: Make the three gate callbacks child-aware**

`_edit_decision_cb`: signature becomes

```python
    async def _edit_decision_cb(
        self, thread_id: str, channel_id: str, diff: list[DiffEntry], *,
        child: AgentHandle | None = None,
    ) -> dict[str, object]:
```

and replace from `gate = self._store.add_controller_gate(thread_id, PendingGate.new("edit", {` through `self._pending_edit.pop(gate.gate_id, None)` / `self._store.remove_controller_gate(thread_id, gate.gate_id)` (the end of the method) with:

```python
        payload: dict[str, object] = {"diff_entries": [
            {"path": d.path, "additions": d.additions,
             "deletions": d.deletions, "unified_diff": d.unified_diff}
            for d in diff]}
        if child is not None:
            # A child edits in its own shadow (spec §7.5). Informational: a child gate is
            # never recovered after a restart (§11.5).
            payload["shadow_key"] = f"chatturn-{thread_id}-{child.agent_id}"
        gate = self._store.add_controller_gate(
            thread_id, PendingGate.new("edit", payload, agent=self._gate_agent(child)))
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending_edit[gate.gate_id] = fut
        self._set_child_status(child, "waiting")
        timeout = float(os.environ.get(_EDIT_DECISION_TIMEOUT_ENV, "0") or "0")
        try:
            if timeout > 0:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        except TimeoutError:
            return {"decision": "reject", "reason": "decision timed out"}
        finally:
            self._pending_edit.pop(gate.gate_id, None)
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._set_child_status(child, "running")
```

`_command_approval_cb`: signature gains `*, child: AgentHandle | None = None,` after `cwd: str,`. Replace the gate creation and poke with:

```python
        gate = self._store.add_controller_gate(thread_id, PendingGate.new(
            "command", {"command": command, "args": args, "cwd": cwd},
            agent=self._gate_agent(child)))
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[CommandDecision] = loop.create_future()
        self._pending_command[gate.gate_id] = fut
        # Instant-render poke (consistency with the task path's command_approval_requested):
        # the card still renders FROM /live (durable on reload) — this only nudges the FE
        # poll so it appears immediately instead of on the next 1s tick. Registered the
        # waiter first so a fast decision always finds it.
        poke: dict[str, object] = {
            "decision_id": uuid4().hex,
            "command": command,
            "args": args,
            "cwd": cwd,
            "step_id": "",
        }
        if child is not None:
            poke["agent"] = {"id": child.agent_id, "label": child.context.label,
                             "name": child.context.name}
        self._broadcaster.broadcast(channel_id, {
            "type": "command_approval_requested", "payload": poke})
        self._set_child_status(child, "waiting")
```

add `            self._set_child_status(child, "running")` as the last line of its `finally:` block, and change the breadcrumb call `self._write_breadcrumb(\n            thread_id, channel_id,` to `self._gate_breadcrumb(\n            thread_id, channel_id,` with `, child)` appended after the conditional text argument (the same two message strings).

`_mcp_approval_cb`: the same pattern — signature `*, child: AgentHandle | None = None,` after `args: dict[str, object],`; `PendingGate.new("mcp_tool", {...}, agent=self._gate_agent(child))`; the poke payload becomes a `poke` dict `{"server", "tool", "args"}` plus `"agent"` for a child; `self._set_child_status(child, "waiting")` after the poke and `"running"` at the end of `finally:`; the breadcrumb through `_gate_breadcrumb(..., child)`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_child_gates.py tests/test_controller_command_gate.py tests/test_controller_mcp_gate.py tests/test_edit_gate_controller.py tests/test_multi_gate_decisions.py tests/test_approval_outcome.py tests/test_gate_breadcrumbs.py > /tmp/pd5.txt 2>&1; echo exit=$?; tail -1 /tmp/pd5.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/controller.py tests/test_child_gates.py
git commit -m "feat(subagents): child gates tagged with their agent; dontAsk denies by policy"
```

---

### Task D6: Running a child, and the parent's `dispatch_agents`

**Files:**
- Modify: `agentd/chat/controller.py`, `agentd/chat/controller_loop.py`
- Test: `tests/test_dispatch_integration.py`

**Interfaces:**
- `_build_registry(..., extra_sources: list[object] | None = None)` appends them.
- `_run_loop`: when the runtime exists and the turn has an id, the parent's registry gains `self._dispatch_source(thread_id, turn_id, None)`.
- `_dispatch_source(thread_id, turn_id, dispatcher) -> SubAgentToolSource` over `BUILTIN_AGENTS` (Phase 3 swaps in discovered definitions).
- `_dispatch(thread_id, turn_id, dispatcher, requests)`: builds each child's `AgentContext` (effective permission from the dispatcher, depth = dispatcher depth + 1, `max_iters = maxTurns or CRUCIBLE_SUBAGENT_MAX_ITERS`), **registers it with the write log at that instant** (its spawn seq), calls `_on_dispatch_start`, then `runtime.dispatch(..., self._run_child, dispatcher=dispatcher)`.
- `_on_dispatch_start` (spec §6.1): the `agent_dispatch` roster message in the dispatcher's transcript (the thread for the main agent, the child's transcript for a nested dispatch) after a pills boundary; one `chat_agents` row per child (`queued`); one `agent_started` per child on the thread channel; `[subagent] start …` logs.
- `_run_child(handle) -> ChildResult` (spec §5): its own `SequencedBroadcaster` channel, `AgentTranscript`, todo ledger, additive skills, recall-only memory under `run_id = "{thread}:{agent}"`, MCP (dropped for `plan` by the filter), `dispatch_agents` while `depth < max depth`; the tool set from `child_tool_names`; `RenderContext.for_agent`; a guarded `TurnEditSession` at `chatturn-{thread}-{agent}` sharing the parent turn's rewind checkpoint; the parent turn's `ChatTurnControl` for an effective-`default` child, else a fixed auto-accept control; `engine.with_model` only for a non-`inherit` model; artifacts under `chat/<thread>/<turn>/agents/<agent>/` (via `artifact_turn_id`); no retrieval delta (spec §4.6.4). A `report` → its status; anything else → `failed` with `fallback_report`; a cancel → `stopped` with a fallback report, set on `handle.result`, then re-raised.
- `_close_child`: computes the subtree's `files_changed` and the stale count, appends the report as the transcript's last message, clears the child's gates, releases its recall caches, and clears its channel's replay buffer (spec §5.5, §8).
- `_child_edit_record_cb`, `_child_progress_note` — the child-scoped durable writers (spec §5.5).
- `ControllerLoop.run` sets `plan_context["dispatch_available"] = True` for the **parent** when `dispatch_agents` is in its tools (Task D7 reads it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_integration.py`:

```python
"""End to end: the parent dispatches children that edit the shared workspace (spec §5, §6)."""
import json
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore,
                engine: ScriptedReasoningEngine) -> ChatController:
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)


def _creates(file: str, text: str) -> list[dict[str, object]]:
    return [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "create_file", "file": file, "content": text, "reason": "r"}]},
        {"type": "report", "thought": "t", "summary": f"Created {file}."},
    ]


def _dispatch(*agents: tuple[str, str, str]) -> dict[str, object]:
    return {"type": "tool_call", "thought": "fan out", "tool": "dispatch_agents",
            "args": {"agents": [{"agent": a, "label": label, "prompt": prompt}
                                for a, label, prompt in agents]}}


DONE = {"type": "submit_changes", "thought": "d", "summary": "All parts done."}


@pytest.mark.asyncio
async def test_two_children_edit_disjoint_files(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(
        None, [], controller_step_responses=[
            _dispatch(("general-purpose", "impl-a", "Create a.py"),
                      ("general-purpose", "impl-b", "Create b.py")), DONE],
        agent_scripts={"impl-a": _creates("a.py", "A = 1\n"),
                       "impl-b": _creates("b.py", "B = 2\n")})
    ctrl = _controller(ws, tmp_path, store, engine)
    thread_events = ctrl._broadcaster.subscribe(f"chat:{tid}")

    await ctrl.handle_message(tid, "build both parts", channel_id=f"chat:{tid}")

    assert (ws / "a.py").read_text() == "A = 1\n"
    assert (ws / "b.py").read_text() == "B = 2\n"
    rows = {r.label: r for r in store.list_agents(tid)}
    assert {k: (r.status, r.files_changed, r.depth) for k, r in rows.items()} == {
        "impl-a": ("completed", ["a.py"], 1), "impl-b": ("completed", ["b.py"], 1)}
    for label, file in (("impl-a", "a.py"), ("impl-b", "b.py")):
        transcript = rows[label].transcript
        assert transcript[-1].content == f"Created {file}."
        assert transcript[-1].metadata["report"] is True
        assert any(m.type == "diff_card" for m in transcript)
        assert all("seq" in m.metadata for m in transcript)
    thread = store.get_thread(tid)
    assert thread is not None
    [roster] = [m for m in thread.messages if m.type == "agent_dispatch"]
    assert sorted(roster.metadata["agent_ids"]) == sorted(r.agent_id for r in rows.values())
    assert not any(m.type == "diff_card" for m in thread.messages)  # children's stay theirs
    history = thread.controller_conversation_history or []
    result = next(m for m in history if m.get("tool") == "dispatch_agents")
    entries = json.loads(str(result["content"]))
    assert [(e["label"], e["status"], e["report"]) for e in entries] == [
        ("impl-a", "completed", "Created a.py."), ("impl-b", "completed", "Created b.py.")]
    types = [thread_events.get_nowait()["type"] for _ in range(thread_events.qsize())]
    assert types.count("agent_started") == 2 and types.count("agent_finished") == 2


@pytest.mark.asyncio
async def test_nested_dispatch_stops_at_the_depth_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_CONCURRENT", "1")  # the lead must lend its slot
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(
        None, [], controller_step_responses=[
            _dispatch(("general-purpose", "lead", "Split the work")), DONE],
        agent_scripts={
            "lead": [_dispatch(("general-purpose", "leaf", "Create leaf.py")),
                     {"type": "report", "thought": "t", "summary": "Lead done."}],
            "leaf": _creates("leaf.py", "LEAF = 1\n")})
    ctrl = _controller(ws, tmp_path, store, engine)

    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    rows = {r.label: r for r in store.list_agents(tid)}
    assert rows["leaf"].depth == 2 and rows["leaf"].parent_agent_id == rows["lead"].agent_id
    assert rows["lead"].files_changed == ["leaf.py"]  # the subtree's files
    assert any(m.type == "agent_dispatch" for m in rows["lead"].transcript)
    assert ctrl._subagents is not None
    lead = ctrl._subagents.registry.get(rows["lead"].agent_id)
    leaf = ctrl._subagents.registry.get(rows["leaf"].agent_id)
    assert lead is not None and leaf is not None
    assert "dispatch_agents" in [d.name for d in lead.loop._registry.definitions()]
    assert "dispatch_agents" not in [d.name for d in leaf.loop._registry.definitions()]


@pytest.mark.asyncio
async def test_flag_off_the_parent_has_no_dispatch_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED", raising=False)
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = _controller(tmp_path, tmp_path, store, ScriptedReasoningEngine(None, []))
    assert ctrl._subagents is None
    names = [d.name for d in ctrl._build_registry().definitions()]
    assert "dispatch_agents" not in names
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatch_integration.py > /tmp/pd6.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pd6.txt`
Expected: `exit=1`; the two dispatch tests fail (the parent's `dispatch_agents` tool call answers `Error: unknown tool 'dispatch_agents'`, so no rows/files appear); the flag-off test passes.

- [ ] **Step 3: Implement the controller**

In `agentd/chat/controller.py`:

1. Extend the imports: add `ChildResult` and `DispatchRequest` to the `agentd.subagents.runtime` import, and add (sorted)

```python
from agentd.subagents.config import subagent_max_concurrent, subagent_max_depth, subagent_max_iters
from agentd.subagents.context import AgentContext, new_agent_id
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.events import SequencedBroadcaster
from agentd.subagents.permissions import (
    child_allowed_types,
    child_tool_names,
    effective_permission,
    follows_live_review,
)
from agentd.subagents.tool_source import SubAgentToolSource
from agentd.subagents.transcript import AgentTranscript
from agentd.subagents.vcs_guard import vcs_refusal
```

(merging `subagent_max_concurrent` into that one `config` import line — wrap it if it exceeds 100 characters).

2. `_build_registry`: add the keyword parameter `extra_sources: list[object] | None = None,` (after `read_observer`) and, directly before `return AggregatingToolRegistry(sources)`, add `        sources.extend(extra_sources or [])`.
3. `_run_loop`: directly before `loop = ControllerLoop(`, add

```python
        # The parent's dispatch_agents (spec §6.1) — only when sub-agents are enabled.
        dispatch_sources: list[object] = (
            [self._dispatch_source(thread_id, turn_id, None)]
            if self._subagents is not None and turn_id else [])
```

and in its `self._build_registry(...)` call add `extra_sources=dispatch_sources,` after the `read_observer=(...)` argument.

4. Add the methods (after `_child_edit_decision_cb`):

```python
    def _dispatch_source(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
    ) -> SubAgentToolSource:
        return SubAgentToolSource(
            BUILTIN_AGENTS, partial(self._dispatch, thread_id, turn_id, dispatcher))

    async def _dispatch(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
        requests: list[DispatchRequest],
    ) -> list[tuple[AgentHandle, ChildResult]]:
        runtime = self._subagents
        log = self._write_log_for(thread_id)
        if runtime is None or log is None:
            raise RuntimeError("dispatch_agents was offered while sub-agents are disabled")
        parent_permission = dispatcher.context.permission if dispatcher is not None else None
        depth = dispatcher.context.depth + 1 if dispatcher is not None else 1
        handles: list[AgentHandle] = []
        for request in requests:
            permission = effective_permission(request.agent.permission, parent_permission)
            context = AgentContext(
                agent_id=new_agent_id(), name=request.agent.name, label=request.label,
                depth=depth,
                parent_agent_id=dispatcher.agent_id if dispatcher is not None else None,
                permission=permission, allowed_types=child_allowed_types(permission),
                persona=request.agent.persona,
                max_iters=request.agent.max_turns or subagent_max_iters())
            # Writes before this instant are not stale for the new agent (spec §7.3).
            log.register_agent(context.agent_id)
            handles.append(AgentHandle(
                context=context, definition=request.agent, prompt=request.prompt,
                thread_id=thread_id, turn_id=turn_id))
        self._on_dispatch_start(thread_id, turn_id, dispatcher, handles)
        results = await runtime.dispatch(handles, self._run_child, dispatcher=dispatcher)
        return list(zip(handles, results, strict=True))

    def _on_dispatch_start(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
        handles: list[AgentHandle],
    ) -> None:
        """Make a dispatch durable before any child runs (spec §6.1), so a mid-run reload
        shows the roster: the anchor message, one row and one agent_started per child."""
        roster = ChatMessage(
            role="agent", content="", type="agent_dispatch",
            metadata={"agent_ids": [h.agent_id for h in handles], "turn_id": turn_id})
        if dispatcher is None:
            self._mark_pills_boundary(thread_id)
            self._store.append_message(thread_id, roster)
        else:
            if isinstance(dispatcher.loop, ControllerLoop):
                dispatcher.loop.mark_pills_boundary()
            if dispatcher.transcript is not None:
                dispatcher.transcript.append(roster)
        thread_channel = f"chat:{thread_id}"
        for handle in handles:
            ctx = handle.context
            self._store.insert_agent(AgentRecord(
                agent_id=ctx.agent_id, thread_id=thread_id, turn_id=turn_id,
                parent_agent_id=ctx.parent_agent_id, depth=ctx.depth, name=ctx.name,
                label=ctx.label, prompt=handle.prompt, status=handle.status))
            self._broadcaster.broadcast(thread_channel, {
                "type": "agent_started",
                "payload": {"agent_id": ctx.agent_id, "parent_agent_id": ctx.parent_agent_id,
                            "depth": ctx.depth, "name": ctx.name, "label": ctx.label}})
            logger.info("[subagent] start id=%s parent=%s depth=%d name=%s",
                        ctx.agent_id, ctx.parent_agent_id, ctx.depth, ctx.name)

    async def _run_child(self, handle: AgentHandle) -> ChildResult:
        """Build and run one sub-agent (spec §5): its own context window, tool set,
        shadow, channel and transcript, on the shared workspace guarded by the thread's
        write log."""
        ctx = handle.context
        thread_id, turn_id = handle.thread_id, handle.turn_id
        log = self._write_log_for(thread_id)
        assert log is not None and self._subagents is not None
        channel = agent_channel(thread_id, ctx.agent_id)
        broadcaster = SequencedBroadcaster(self._broadcaster, channel)
        transcript = AgentTranscript(
            partial(self._store.set_agent_transcript, ctx.agent_id),
            lambda: broadcaster.last_seq)
        handle.broadcaster, handle.transcript = broadcaster, transcript
        workspace = Path(self._workspace_path)
        run_id = f"{thread_id}:{ctx.agent_id}"
        ledger = TodoLedger()  # in memory only: never written to thread columns (§5.2)
        active_skills: dict[str, str] = {}
        may_dispatch = ctx.depth < subagent_max_depth()

        def sources(render_ctx: RenderContext | None) -> list[object]:
            built: list[object] = [
                BuiltinToolSource(
                    shadow_root=workspace, real_workspace_path=workspace,
                    semantic_index=getattr(self._retrieval, "_semantic_index", None),
                    command_approval_callback=partial(self._child_command_approval_cb, handle),
                    render_ctx=render_ctx,
                    read_observer=log.read_observer(workspace, ctx.agent_id),
                    command_guard=vcs_refusal),
                TodoToolSource(ledger, render_ctx=render_ctx),
            ]
            memory_source = self._memory_harness.memory_tool_source(
                run_id, allow_remember=False)
            if memory_source is not None:
                built.append(memory_source)
            if is_skills_enabled():
                built.append(SkillToolSource(
                    SkillCatalogLoader(self._workspace_path), active_skills, additive=True))
            if self._mcp_manager is not None:
                from agentd.mcp.tool_source import McpToolSource

                built.append(McpToolSource(
                    self._mcp_manager, partial(self._child_mcp_approval_cb, handle),
                    render_ctx=render_ctx))
            if may_dispatch:
                built.append(self._dispatch_source(thread_id, turn_id, handle))
            return built

        available = [d.name for d in AggregatingToolRegistry(sources(None)).definitions()]
        names = child_tool_names(available, definition_tools=handle.definition.tools,
                                 permission=ctx.permission, may_dispatch=may_dispatch)
        render_ctx = RenderContext.for_agent(
            ctx, tools=names,
            shell_policy="allow_all" if self._shell_policy == ShellPolicy.ALLOW_ALL else "ask")
        registry = AggregatingToolRegistry(sources(render_ctx), allowed_tools=names)
        edit_session_factory = (
            (lambda: TurnEditSession(
                turn_id=f"{thread_id}-{ctx.agent_id}", real_path=workspace,
                workspace_manager=self._orchestrator._workspace_manager,
                patch_engine=self._orchestrator._patch_engine,
                # Children's edits land in the parent turn's checkpoint (first-seen-wins),
                # so rewinding before the turn reverts the whole tree (spec §7.5).
                checkpoint_cb=(
                    partial(self._rewind.capture, thread_id)
                    if self._rewind is not None else None),
                write_guard=WriteGuard(log, ctx.agent_id, ctx.label, ctx.name)))
            if self._orchestrator is not None else None)
        parent_control = self._turn_controls.get(thread_id)
        control = (parent_control
                   if follows_live_review(ctx.permission) and parent_control is not None
                   else ChatTurnControl(auto_accept_edits=True))
        engine = (self._reasoning if handle.definition.model == "inherit"
                  else self._reasoning.with_model(handle.definition.model))
        loop = ControllerLoop(
            engine, registry, broadcaster, channel_id=channel,
            phase_sm=ControllerPhaseSM(start="AGENT"),
            edit_session_factory=edit_session_factory, todo_ledger=ledger,
            memory_harness=self._memory_harness, active_skills=active_skills,
            progress_note_cb=partial(self._child_progress_note, handle),
            pills_seal_cb=transcript.seal_pills, render_ctx=render_ctx, agent=ctx)
        handle.loop = loop
        plan_context: dict[str, object] = {
            "goal": handle.prompt, "workspace_path": self._workspace_path, "run_id": run_id,
            # Nests this child's controller-turn-NN / memory-recall-NN dumps under
            # chat/<thread>/<turn>/agents/<agent>/ (spec §5.2) with no engine change.
            "artifact_thread_id": thread_id,
            "artifact_turn_id": f"{turn_id}/agents/{ctx.agent_id}",
            "artifact_seed_len": 0, "edit_is_resume": False}

        def subtree_files() -> list[str]:
            assert self._subagents is not None
            return log.files_changed_by(self._subagents.registry.subtree_ids(ctx.agent_id))

        status, report = "failed", ""
        try:
            # No retrieval_delta_cb: the delta references a seed children never had.
            outcome = await loop.run(
                plan_context, max_iters=ctx.max_iters, turn_control=control,
                edit_decision_cb=partial(self._child_edit_decision_cb, handle),
                edit_record_cb=partial(self._child_edit_record_cb, handle),
                on_pills_update=transcript.upsert_pills)
            if outcome.kind == "report":
                status = str((outcome.payload or {}).get("status", "completed"))
                report = outcome.text
            else:
                report = loop.fallback_report(
                    f"ended without a report ({outcome.text or outcome.kind})",
                    subtree_files())
        except asyncio.CancelledError:
            handle.result = self._close_child(handle, "stopped", loop.fallback_report(
                "stopped before reporting", subtree_files(), status="stopped"))
            raise
        except Exception as exc:
            logger.exception("[subagent] child failed id=%s", ctx.agent_id)
            report = loop.fallback_report(str(exc), subtree_files())
        return self._close_child(handle, status, report)

    def _close_child(self, handle: AgentHandle, status: str, report: str) -> ChildResult:
        """The child's final bookkeeping on every exit (spec §5.5, §6.3, §8)."""
        ctx = handle.context
        log = self._write_log_for(handle.thread_id)
        files = (log.files_changed_by(self._subagents.registry.subtree_ids(ctx.agent_id))
                 if log is not None and self._subagents is not None else [])
        result = ChildResult(
            status=status, report=report, files_changed=files,
            stale_refusals=log.stale_refusals(ctx.agent_id) if log is not None else 0)
        if handle.transcript is not None:
            handle.transcript.append(ChatMessage(
                role="agent", content=report, metadata={"report": True, "status": status}))
        # Its gates can never be answered now; its recall caches and replay buffer are
        # done — the replay map is otherwise never pruned (late viewers backfill instead).
        self._store.clear_controller_gates(handle.thread_id, agent_id=ctx.agent_id)
        self._memory_harness.release_run(f"{handle.thread_id}:{ctx.agent_id}")
        self._broadcaster.clear_replay(agent_channel(handle.thread_id, ctx.agent_id))
        return result

    async def _child_edit_record_cb(
        self, child: AgentHandle, diff: list[DiffEntry], decision: str, reason: str,
        was_gated: bool,
    ) -> None:
        """The child twin of _edit_record_cb: the inert diff card and breadcrumbs go to the
        child's transcript and channel (spec §5.5). The event is broadcast first, so the
        persisted message records the seq of the event that produced it."""
        diff_payload = [
            {"path": d.path, "additions": d.additions,
             "deletions": d.deletions, "unified_diff": d.unified_diff}
            for d in diff]
        resolved = "applied" if decision == "accept" else "discarded"
        if isinstance(child.loop, ControllerLoop):
            child.loop.mark_pills_boundary()
        if child.broadcaster is not None:
            child.broadcaster.broadcast(agent_channel(child.thread_id, child.agent_id), {
                "type": "diff_ready",
                "payload": {"diff_entries": diff_payload, "resolved": resolved}})
        if child.transcript is not None:
            child.transcript.append(ChatMessage(
                role="agent", content="", type="diff_card",
                metadata={"diff_entries": diff_payload, "resolved": resolved}))
        files = ", ".join(d.path for d in diff) or "(no files)"
        if decision == "stale":
            self._child_breadcrumb(child, f"✗ Not applied: {reason}")
        elif was_gated and decision == "accept":
            self._child_breadcrumb(child, f"✓ Edit accepted: {files}")
        elif was_gated:
            self._child_breadcrumb(
                child, f"✗ Edit rejected: {files}" + (f" — {reason}" if reason else ""))

    async def _child_progress_note(self, child: AgentHandle, note: str) -> None:
        """Persist a child's progress note to ITS transcript (the loop already broadcast
        chat_progress on the child's channel)."""
        if child.transcript is not None:
            child.transcript.append(ChatMessage(
                role="agent", content=note, metadata={"progress": True}))
```

- [ ] **Step 4: The loop's `dispatch_available`**

In `agentd/chat/controller_loop.py`, `run()`, directly after the `if self._agent is not None:` block that sets `agent_readonly`, add:

```python
        if self._agent is None and any(
                d.get("name") == "dispatch_agents" for d in tool_defs):
            # The parent's entry hint offers parallel dispatch (spec §6.6).
            plan_context["dispatch_available"] = True
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_dispatch_integration.py tests/test_child_gates.py tests/test_write_guard_wiring.py tests/test_prompt_goldens.py > /tmp/pd6.txt 2>&1; echo exit=$?; tail -1 /tmp/pd6.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/controller.py agentd/chat/controller_loop.py tests/test_dispatch_integration.py
git commit -m "feat(subagents): run children and give the parent dispatch_agents"
```

---

### Task D7: Parent teaching — the dispatch block and the entry-hint clause

**Files:**
- Modify: `agentd/chat/controller_prompts.py`, `tests/test_prompt_leak_lint.py`
- Test: `tests/test_dispatch_teaching.py`

**Interfaces (spec §6.6):**
- `_DISPATCH_BLOCK` — a tagged template appended by `format_controller_system_prompt` when any tool definition is named `dispatch_agents` (the same keyed-off-tools pattern as the MCP and sessions blocks; the catalog itself rides the tool description). Main-only lines (commit after the batch, dispatch-as-first-action) sit in a `<<main>>` region, so a child that may dispatch gets the rules without them. With no dispatch tool the prompt is unchanged, so the main-agent goldens stay byte-identical.
- The ACTIVE entry hint gains, only when `plan_context["dispatch_available"]`: "` Or, for independent parts touching disjoint files, dispatch them in parallel with dispatch_agents (a tool_call — see SUB-AGENTS).`" right after the BIG bullet.
- The leak lint covers the new template (`TEMPLATES`) and the rendered child prompt with the dispatch tool present.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch_teaching.py`:

```python
"""Parent teaching for dispatch_agents (spec §6.6)."""
from agentd.chat.controller_prompts import (
    build_controller_step_payload,
    format_controller_system_prompt,
)
from agentd.prompting.tagged import RenderContext

DISPATCH = {"name": "dispatch_agents", "description": "d",
            "parameters": {"type": "object"}}
CHILD = RenderContext(audience="child", permission="default",
                      tools=frozenset({"dispatch_agents", "read_file"}),
                      base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                      agent_id="agent-1", agent_label="lead")


def _prompt(tools: list[dict[str, object]], ctx: RenderContext | None = None) -> str:
    return format_controller_system_prompt(
        tools, task_subsystem_enabled=False, memory_enabled=False, render_ctx=ctx)


def test_the_block_appears_only_with_the_tool() -> None:
    assert "SUB-AGENTS (dispatch_agents)" not in _prompt([])
    main = _prompt([DISPATCH])
    assert "SUB-AGENTS (dispatch_agents)" in main
    assert "Commit after the batch yourself" in main


def test_a_child_that_may_dispatch_gets_the_rules_without_main_only_lines() -> None:
    child = _prompt([DISPATCH], CHILD)
    assert "SUB-AGENTS (dispatch_agents)" in child
    assert "Commit after the batch yourself" not in child
    assert "first action instead of write_todos" not in child


def _entry_hint(dispatch_available: bool) -> str:
    context: dict[str, object] = {"goal": "g", "workspace_path": "/w", "active_entry": True,
                                  "iteration": 0, "max_iters": 10}
    if dispatch_available:
        context["dispatch_available"] = True
    return str(build_controller_step_payload(context, [], [], phase="ACTIVE")["instruction"])


def test_the_entry_hint_clause_is_conditional() -> None:
    clause = ("Or, for independent parts touching disjoint files, dispatch them in parallel "
              "with dispatch_agents (a tool_call — see SUB-AGENTS).")
    assert clause in _entry_hint(True)
    assert clause not in _entry_hint(False)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatch_teaching.py > /tmp/pd7.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pd7.txt`
Expected: `exit=1`; the three tests that look for the block or the clause fail.

- [ ] **Step 3: Implement**

In `agentd/chat/controller_prompts.py`, directly after the `_SESSIONS_BLOCK` template, add:

```python
# Appended when dispatch_agents is offered (spec §6.6). The agent catalog rides the tool's
# own description in tools_json; this block teaches how to use it.
_DISPATCH_BLOCK = tagged("_DISPATCH_BLOCK", """

SUB-AGENTS (dispatch_agents)
dispatch_agents runs sub-agents in parallel — each in its own context window — and
returns when all of them are done, with one full report per agent and the files each
one changed.
- A sub-agent starts with ONLY the prompt you write. Make each prompt self-contained:
  the goal, the exact files that agent owns, the constraints, and how to verify.
- Give each agent its OWN files. An agent editing a file another agent changed is
  refused until it re-reads that file, which wastes its budget.
- Sub-agents cannot ask you anything: put every decision in the prompt. Their reports
  list the assumptions they made and their open questions.
- explore is read-only (fast, parallel investigation); general-purpose can edit.
<<main>>
- Don't tell sub-agents to commit or use version control — they are blocked from it.
  Commit after the batch yourself, using each result's files_changed.
- When the parts are independent and touch disjoint files, dispatch_agents may be your
  first action instead of write_todos; the todo list stays yours to reconcile afterwards.
<</main>>
- Check each result: read its report, and re-read any file in files_changed before you
  edit it yourself.
Example (two independent parts):
{"type":"tool_call","thought":"two independent parts on disjoint files","tool":"dispatch_agents","args":{"agents":[{"agent":"general-purpose","label":"limiter","prompt":"Add a token-bucket limiter in api/limiter.py (you own only that file). Verify with pytest tests/test_limiter.py."},{"agent":"explore","label":"auth survey","prompt":"Find every caller of check_token under api/ and report each with path:line."}]}}
""")
```

In `format_controller_system_prompt`, directly after the sessions-block `if`, add:

```python
    # dispatch teaching block: keyed off the merged tool definitions, like the two above.
    if any(str((d or {}).get("name", "")) == "dispatch_agents"
           for d in tool_definitions if isinstance(d, dict)):
        base += render_prompt(_DISPATCH_BLOCK, ctx)
```

In `build_controller_step_payload`, in the ACTIVE branch, directly above `if plan_context.get("active_entry") or not history:`, add

```python
        dispatch_clause = (
            " Or, for independent parts touching disjoint files, dispatch them in parallel "
            "with dispatch_agents (a tool_call — see SUB-AGENTS)."
            if plan_context.get("dispatch_available") else "")
```

and in that branch's `hint = (...)` replace

```python
                "none are pending).\n"
```

with

```python
                "none are pending)." + dispatch_clause + "\n"
```

In `tests/test_prompt_leak_lint.py`: add `"_DISPATCH_BLOCK"` to the `cp` names tuple inside `TEMPLATES`; add `_DISPATCH = {"name": "dispatch_agents", "description": "d", "parameters": {"type": "object"}}` next to `_MCP`; change `format_controller_system_prompt([_MCP], …)` in `test_everything_a_child_is_shown_is_leak_free` to `format_controller_system_prompt([_MCP, _DISPATCH], …)`; and add to that test's `shown` list `SubAgentToolSource(BUILTIN_AGENTS, None).definitions()[0].description` (imports: `from agentd.subagents.definitions import BUILTIN_AGENTS`, `from agentd.subagents.tool_source import SubAgentToolSource`).

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_dispatch_teaching.py tests/test_prompt_leak_lint.py tests/test_prompt_goldens.py tests/test_controller_payload.py tests/test_controller_agent_payload.py > /tmp/pd7.txt 2>&1; echo exit=$?; tail -1 /tmp/pd7.txt`
Expected: `exit=0` — the goldens prove the main agent is byte-identical without the dispatch tool.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_prompts.py tests/test_prompt_leak_lint.py tests/test_dispatch_teaching.py
git commit -m "feat(prompts): teach dispatch_agents to agents that have it"
```

---

### Part D verification

- [ ] **Step 1: Full suite** — as Part A Step 1 (only the pre-existing failure).
- [ ] **Step 2: Lint** — `.venv/bin/ruff check agentd/subagents tests/test_chat_agents_store.py tests/test_agent_transcript.py tests/test_subagent_runtime.py tests/test_dispatch_tool_source.py tests/test_child_gates.py tests/test_dispatch_integration.py tests/test_dispatch_teaching.py` → `All checks passed!`; the Part A lint-diff procedure with `BASE` = the commit before Task D1 and `F=(agentd/chat/models.py agentd/chat/storage.py agentd/chat/controller.py agentd/chat/controller_loop.py agentd/chat/controller_prompts.py)` → `new=0`.

---

# Part E — Lifecycle, API and the contract tail

Part E makes children observable and safe across stops, restarts and rewinds: the `/live` roster and the agent routes, the stop cascade with the parent's memory of an interrupted dispatch, the startup reap, rewind cleanup, and the minimum TypeScript contract changes so every client keeps parsing threads that now contain `agent_dispatch` messages. The roster/floating-window UI itself is Phase 4.

### File structure (Part E)

| File | Responsibility |
|---|---|
| `agentd/chat/controller_loop.py` | `ControllerLoop.tool_calls` (read-only view for `/live`) |
| `agentd/subagents/tool_source.py` | `format_dispatch_result(pairs)` (shared by the tool and the stop path) |
| `agentd/chat/controller.py` | `live_agents`, `stop_agent`, in-flight dispatch tracking + synthetic result on stop, `reap_subagents`, `forget_rewound_agents` |
| `agentd/chat/models.py` | `ThreadLiveState.agents`; `AgentRecord.summary()` |
| `agentd/chat/storage.py` | `reap_agents`, `remove_child_gates`, `delete_agents_for_turns` |
| `agentd/memory/store.py`, `agentd/memory/harness.py` | `delete_segments`, `forget_run` |
| `agentd/chat/rewind.py` | preview counts children's commands |
| `agentd/api/routes.py` | agent routes, `/live` agents, `/v1/config` flag, rewind cleanup |
| `agentd/main.py`, `agentd/chat/controller_factory.py` | startup reap; the incoherent-flag warning |
| `apps/editor-client/…`, `apps/vscode-extension/webview-ui/…` | `agent_dispatch` type, agent schemas/methods, stream events, config flag |

### Task E1: Live roster, single-agent stop, and the parent's memory of a stopped dispatch

**Files:**
- Modify: `agentd/chat/controller_loop.py`, `agentd/subagents/tool_source.py`, `agentd/chat/controller.py`
- Test: `tests/test_subagent_lifecycle.py`

**Interfaces:**
- `ControllerLoop.tool_calls -> list[ToolCall]` — a copy of the calls recorded so far (read-only view for `/live`).
- `format_dispatch_result(pairs: list[tuple[AgentHandle, ChildResult]]) -> str` — the exact JSON array `dispatch_agents` returns; `SubAgentToolSource.execute` now uses it.
- `ChatController.live_agents(thread_id) -> list[dict]` (spec §11.1) — every agent of the **in-flight** turn's dispatch tree, running and finished: `{agent_id, parent_agent_id, depth, name, label, status, now, tool_count, files_changed_count, started_at, ended_at, report_preview}`; `now` is the latest tool name plus its `path`/`command` argument; `report_preview` is the first 200 characters (UI only, D8). Empty when no turn is in flight — the turn id is registered for exactly `loop.run`'s lifetime, like `_active_loops`.
- `ChatController.stop_agent(thread_id, agent_id) -> bool` — cancels that child (and its subtree); siblings continue (spec §11.4). `False` for an unknown/foreign/finished agent.
- Turn `/stop` (spec §11.4): cancellation already reaches every child through the gather; additionally `_run_loop`'s `CancelledError` branch appends, **before persisting the partial history**, a synthetic assistant `dispatch_agents` tool_call (rebuilt from the handles) and its tool_result (`format_dispatch_result`, each child with its `stopped` fallback report and `files_changed`). The next turn therefore knows what the children already promoted. The main agent's in-flight dispatch is tracked in `_inflight_dispatch[thread_id]` and cleared on normal completion.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_lifecycle.py`:

```python
"""/live roster, single-agent stop, and a stopped dispatch remembered (spec §11.1, §11.4)."""
import asyncio
import json
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager
from tests.gate_helpers import first_gate


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore,
                engine: ScriptedReasoningEngine) -> ChatController:
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)


DISPATCH = {"type": "tool_call", "thought": "fan out", "tool": "dispatch_agents",
            "args": {"agents": [
                {"agent": "general-purpose", "label": "waiter", "prompt": "Run ls"},
                {"agent": "general-purpose", "label": "quick", "prompt": "Report now"}]}}
WAIT = [{"type": "tool_call", "thought": "t", "tool": "run_command",
         "args": {"command": "ls"}},
        {"type": "report", "thought": "t", "summary": "Listed."}]
QUICK = [{"type": "report", "thought": "t", "summary": "Quick done."}]
DONE = {"type": "submit_changes", "thought": "d", "summary": "ok"}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[DISPATCH, DONE],
                                     agent_scripts={"waiter": WAIT, "quick": QUICK})
    return _controller(ws, tmp_path, store, engine), store, tid


async def _wait_for_gate(store: ChatThreadStore, tid: str):
    for _ in range(400):
        await asyncio.sleep(0.005)
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            return gate
    raise AssertionError("the waiter never raised its command gate")


@pytest.mark.asyncio
async def test_live_roster_then_stopping_one_agent(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch)
    turn = asyncio.create_task(ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"))
    gate = await _wait_for_gate(store, tid)
    assert gate.agent is not None and gate.agent.label == "waiter"
    roster = {a["label"]: a for a in ctrl.live_agents(tid)}
    assert roster["waiter"]["status"] == "waiting"
    assert roster["waiter"]["depth"] == 1 and roster["waiter"]["report_preview"] == ""
    assert set(roster["waiter"]) == {
        "agent_id", "parent_agent_id", "depth", "name", "label", "status", "now",
        "tool_count", "files_changed_count", "started_at", "ended_at", "report_preview"}
    assert await ctrl.stop_agent(tid, gate.agent.id) is True
    await turn
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []
    history = thread.controller_conversation_history or []
    result = next(m for m in history if m.get("tool") == "dispatch_agents")
    statuses = {e["label"]: e["status"] for e in json.loads(str(result["content"]))}
    assert statuses == {"waiter": "stopped", "quick": "completed"}
    assert ctrl.live_agents(tid) == []  # the turn is over
    assert await ctrl.stop_agent(tid, gate.agent.id) is False


@pytest.mark.asyncio
async def test_a_stopped_turn_remembers_its_dispatch(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch)
    ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"),
                     channel_id=f"chat:{tid}")
    await _wait_for_gate(store, tid)
    assert await ctrl.stop_turn(tid) is True
    rows = {r.label: r for r in store.list_agents(tid)}
    assert rows["waiter"].status == "stopped"
    assert rows["waiter"].report.startswith("Status: stopped — stopped before reporting")
    history = store.get_thread(tid).controller_conversation_history or []
    assert json.loads(str(history[-2]["content"]))["tool"] == "dispatch_agents"
    assert history[-1]["tool"] == "dispatch_agents"
    entries = json.loads(str(history[-1]["content"]))
    assert {e["label"]: e["status"] for e in entries}["waiter"] == "stopped"
    assert ctrl._inflight_dispatch == {}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subagent_lifecycle.py > /tmp/pe1.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pe1.txt`
Expected: `exit=1`; both fail (`'ChatController' object has no attribute 'live_agents'` / `_inflight_dispatch`).

- [ ] **Step 3: Implement**

`agentd/chat/controller_loop.py` — add next to `partial_history`:

```python
    @property
    def tool_calls(self) -> list[ToolCall]:
        """The tool calls recorded so far (a copy) — what /live's roster reads (§11.1)."""
        return list(self._calls)
```

`agentd/subagents/tool_source.py` — add above the class:

```python
def format_dispatch_result(pairs: list[tuple[AgentHandle, ChildResult]]) -> str:
    """The dispatch_agents tool result (spec §6.3): one entry per child, full reports."""
    return json.dumps([{
        "agent_id": handle.agent_id, "agent": handle.context.name,
        "label": handle.context.label, "status": result.status,
        "report": result.report, "files_changed": result.files_changed,
        "stale_refusals": result.stale_refusals,
    } for handle, result in pairs], indent=2)
```

and in `execute` replace the `entries = [...]` comprehension and `json.dumps(entries, indent=2)` with `output=format_dispatch_result(pairs)`.

`agentd/chat/controller.py`:

1. Imports: `from agentd.reasoning.react_common import assistant_turn` and `from agentd.subagents.tool_source import SubAgentToolSource, format_dispatch_result` (extend the existing `tool_source` import).
2. `__init__`, after `self._subagents …`:

```python
        # The turn whose dispatch tree /live reports (spec §11.1) — registered for exactly
        # loop.run's lifetime, like _active_loops.
        self._live_turns: dict[str, str] = {}
        # The main agent's dispatch that is awaiting its children, so a /stop can still
        # tell the next turn what they did (spec §11.4).
        self._inflight_dispatch: dict[str, list[AgentHandle]] = {}
```

3. `_run_loop`: next to `self._active_loops[thread_id] = loop` add

```python
        if turn_id:
            self._live_turns[thread_id] = turn_id
```

in its `finally:` add `            self._live_turns.pop(thread_id, None)`; in the `except asyncio.CancelledError:` branch, directly after `partial_hist = loop.partial_history()`, add

```python
            # A dispatch the stop interrupted (spec §11.4): its tool_call never returned,
            # so append it and its result — each child's status, files and report — or the
            # next turn would not know what the children already promoted.
            interrupted = self._inflight_dispatch.pop(thread_id, None)
            if interrupted:
                partial_hist.append(assistant_turn({
                    "type": "tool_call", "thought": "(dispatch interrupted by stop)",
                    "tool": "dispatch_agents",
                    "args": {"agents": [
                        {"agent": h.context.name, "label": h.context.label, "prompt": h.prompt}
                        for h in interrupted]}}))
                partial_hist.append({
                    "role": "tool_result", "tool": "dispatch_agents",
                    "content": format_dispatch_result([
                        (h, h.result or ChildResult(
                            status="stopped", report="Stopped before it started.",
                            files_changed=[]))
                        for h in interrupted])})
```

and in the generic `except Exception as exc:` branch add `            self._inflight_dispatch.pop(thread_id, None)` as its first line.
4. `_dispatch`: replace `        results = await runtime.dispatch(handles, self._run_child, dispatcher=dispatcher)` with

```python
        if dispatcher is None:
            self._inflight_dispatch[thread_id] = handles
        results = await runtime.dispatch(handles, self._run_child, dispatcher=dispatcher)
        if dispatcher is None:
            # Not in a finally: a cancel must leave it for _run_loop's stop branch.
            self._inflight_dispatch.pop(thread_id, None)
```

5. Add the methods (next to `_dispatch_source`):

```python
    def live_agents(self, thread_id: str) -> list[dict[str, object]]:
        """/live's roster (spec §11.1): every agent of the in-flight turn's dispatch tree,
        running and finished. Empty once the turn ends (the UI then reads the routes)."""
        turn_id = self._live_turns.get(thread_id)
        if self._subagents is None or turn_id is None:
            return []
        log = self._write_log_for(thread_id)
        roster: list[dict[str, object]] = []
        for handle in self._subagents.registry.for_turn(thread_id, turn_id):
            calls = handle.loop.tool_calls if isinstance(handle.loop, ControllerLoop) else []
            now = ""
            if calls:
                last = calls[-1]
                target = last.arguments.get("path") or last.arguments.get("command") or ""
                now = f"{last.tool_name} {target}".strip()
            if handle.result is not None:
                files = len(handle.result.files_changed)
            elif log is not None:
                files = len(log.files_changed_by(
                    self._subagents.registry.subtree_ids(handle.agent_id)))
            else:
                files = 0
            roster.append({
                "agent_id": handle.agent_id, "parent_agent_id": handle.context.parent_agent_id,
                "depth": handle.context.depth, "name": handle.context.name,
                "label": handle.context.label, "status": handle.status, "now": now,
                "tool_count": len(calls), "files_changed_count": files,
                "started_at": handle.started_at.isoformat() if handle.started_at else None,
                "ended_at": handle.ended_at.isoformat() if handle.ended_at else None,
                # UI-only preview; the model always gets the full report (D8).
                "report_preview": handle.result.report[:200] if handle.result else "",
            })
        return roster

    async def stop_agent(self, thread_id: str, agent_id: str) -> bool:
        """Stop one sub-agent and its subtree; siblings continue (spec §11.4)."""
        if self._subagents is None:
            return False
        handle = self._subagents.registry.get(agent_id)
        if handle is None or handle.thread_id != thread_id:
            return False
        return await self._subagents.stop_agent(agent_id)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_lifecycle.py tests/test_dispatch_integration.py tests/test_dispatch_tool_source.py tests/test_controller_durable_turn.py > /tmp/pe1.txt 2>&1; echo exit=$?; tail -1 /tmp/pe1.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_loop.py agentd/subagents/tool_source.py agentd/chat/controller.py tests/test_subagent_lifecycle.py
git commit -m "feat(subagents): live roster, single-agent stop, and stopped dispatches remembered"
```

---

### Task E2: The agent routes, `/live` agents and `/v1/config`

**Files:**
- Modify: `agentd/chat/models.py`, `agentd/api/routes.py`
- Test: `tests/test_subagent_routes.py`

**Interfaces (spec §11.1, §11.3, §12):**
- `ThreadLiveState.agents: list[dict[str, Any]] | None = None` (filled from `live_agents`; `None` when empty — the same convention as `sessions`).
- `AgentRecord.summary() -> dict` — `{agent_id, turn_id, parent_agent_id, depth, name, label, status, files_changed_count, started_at, ended_at, report_preview}`.
- `GET /v1/chat/threads/{id}/agents[?turn_id=]` → `{"agents": [summary…]}` (404 unknown thread).
- `GET /v1/chat/threads/{id}/agents/{agent_id}` → the full record (`transcript`, the full `report`, `files_changed`, …) plus `last_seq` = the highest `seq` on a persisted transcript message (spec §5.5); 404 when the agent is unknown or belongs to another thread.
- `POST /v1/chat/threads/{id}/agents/{agent_id}/stop` → `{"ok": bool}` (`ok: false` is benign — already finished, unknown, or a handler without sub-agents).
- `GET /v1/config` gains `"subagents_enabled"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_routes.py`:

```python
"""Agent routes, /live agents and the config flag (spec §11.1, §11.3, §12)."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord, ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _client(tmp_path: Path, ctrl: ChatController) -> AsyncClient:
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_get_stop_live_and_config(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    other = store.create_thread(str(tmp_path), title="o").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    store.insert_agent(AgentRecord(
        agent_id="agent-a", thread_id=tid, turn_id="turn-1", depth=1,
        name="general-purpose", label="impl", prompt="p", status="completed",
        report="R" * 500, files_changed=["a.py"]))
    store.set_agent_transcript("agent-a", [
        ChatMessage(role="agent", content="x", metadata={"seq": 4}),
        ChatMessage(role="agent", content="R", metadata={"report": True, "seq": 9})])
    async with _client(tmp_path, ctrl) as client:
        listed = (await client.get(f"/v1/chat/threads/{tid}/agents")).json()["agents"]
        by_turn = (await client.get(
            f"/v1/chat/threads/{tid}/agents", params={"turn_id": "nope"})).json()["agents"]
        detail = (await client.get(f"/v1/chat/threads/{tid}/agents/agent-a")).json()
        foreign = await client.get(f"/v1/chat/threads/{other}/agents/agent-a")
        missing_thread = await client.get("/v1/chat/threads/nope/agents")
        stopped = (await client.post(f"/v1/chat/threads/{tid}/agents/agent-a/stop")).json()
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        config = (await client.get("/v1/config")).json()
    assert listed == [{
        "agent_id": "agent-a", "turn_id": "turn-1", "parent_agent_id": None, "depth": 1,
        "name": "general-purpose", "label": "impl", "status": "completed",
        "files_changed_count": 1, "started_at": None, "ended_at": None,
        "report_preview": "R" * 200}]
    assert by_turn == []
    assert detail["report"] == "R" * 500 and detail["last_seq"] == 9
    assert [m["content"] for m in detail["transcript"]] == ["x", "R"]
    assert foreign.status_code == 404 and missing_thread.status_code == 404
    assert stopped == {"ok": False}  # already finished
    assert live["agents"] is None
    assert config["subagents_enabled"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_subagent_routes.py > /tmp/pe2.txt 2>&1; echo exit=$?; grep -E "assert|Error" /tmp/pe2.txt | head -3`
Expected: `exit=1` (the agent routes answer 404/405; `agents`/`subagents_enabled` are missing).

- [ ] **Step 3: Implement**

`agentd/chat/models.py`: in `ThreadLiveState`, after the `sessions` field, add

```python
    # The in-flight turn's sub-agent tree (spec §11.1); None when there is none.
    agents: list[dict[str, Any]] | None = None
```

and add to `AgentRecord`:

```python
    def summary(self) -> dict[str, Any]:
        """The list view (spec §11.3): no transcript, a 200-character UI-only preview."""
        return {
            "agent_id": self.agent_id, "turn_id": self.turn_id,
            "parent_agent_id": self.parent_agent_id, "depth": self.depth,
            "name": self.name, "label": self.label, "status": self.status,
            "files_changed_count": len(self.files_changed),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "report_preview": self.report[:200],
        }
```

`agentd/api/routes.py`:
1. `/config`: add `is_subagents_enabled` to the lazy `from agentd.chat.controller_factory import (...)` list and `"subagents_enabled": is_subagents_enabled(),` after `"exec_sessions_enabled"`.
2. `/live`: directly after the exec-sessions block (before `return live.model_dump()`), add

```python
            # The in-flight turn's sub-agent tree (spec §11.1). Flag-tolerant: the legacy
            # ChatAgent has no live_agents.
            _live_agents = getattr(_chat_agent, "live_agents", None)
            if _live_agents is not None:
                live.agents = _live_agents(thread_id) or None
```

3. Directly after the `post_stop_turn` route, add:

```python
        @router.get("/chat/threads/{thread_id}/agents")
        async def list_thread_agents(thread_id: str, turn_id: str | None = None) -> dict:
            if _chat_agent._store.get_thread(thread_id) is None:
                raise HTTPException(status_code=404, detail="Thread not found")
            rows = _chat_agent._store.list_agents(thread_id, turn_id)
            return {"agents": [r.summary() for r in rows]}

        @router.get("/chat/threads/{thread_id}/agents/{agent_id}")
        async def get_thread_agent(thread_id: str, agent_id: str) -> dict:
            record = _chat_agent._store.get_agent(agent_id)
            if record is None or record.thread_id != thread_id:
                raise HTTPException(status_code=404, detail="Agent not found")
            # The backfill cursor (spec §5.5): the highest seq on a PERSISTED message, so an
            # event broadcast but not yet persisted is never skipped by the client.
            last_seq = max((int(m.metadata.get("seq", 0)) for m in record.transcript),
                           default=0)
            return {**record.model_dump(mode="json"), "last_seq": last_seq}

        @router.post("/chat/threads/{thread_id}/agents/{agent_id}/stop")
        async def post_stop_agent(thread_id: str, agent_id: str) -> dict:
            stop = getattr(_chat_agent, "stop_agent", None)
            if stop is None:
                return {"ok": False}
            return {"ok": await stop(thread_id, agent_id)}  # type: ignore[misc]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_routes.py tests/test_chat_live_route.py tests/test_thread_live_state.py > /tmp/pe2.txt 2>&1; echo exit=$?; tail -1 /tmp/pe2.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/models.py agentd/api/routes.py tests/test_subagent_routes.py
git commit -m "feat(api): agent routes, /live agents and the subagents_enabled flag"
```

---
### Task E3: Restart reap and the incoherent-flag warning

**Files:**
- Modify: `agentd/chat/storage.py`, `agentd/chat/controller.py`, `agentd/main.py`, `agentd/chat/controller_factory.py`
- Test: `tests/test_subagent_reap.py`

**Interfaces (spec §11.5, §12):**
- `ChatThreadStore.reap_agents(reason) -> int` — every `queued|running|waiting` row becomes `failed` with report `"Status: failed — <reason>"` and an `ended_at`; returns the count.
- `ChatThreadStore.remove_child_gates() -> list[str]` — drops every gate whose `agent` is set from every thread (child gates are **not** recoverable: the write log is gone, so promoting would bypass the guard); returns the affected thread ids. Main-agent gates stay (their orphan recovery is unchanged).
- `ChatController.reap_subagents()` — the startup reap: the two store calls, a "✗ Sub-agent approvals were cleared — the backend restarted." breadcrumb on each affected thread, and deleting leftover `chatturn-*-agent-*` shadow directories under the orchestrator's shadow root. Registered in `main.py` as a startup handler next to the exec-session reap.
- `warn_if_incoherent_flags` also warns when `CRUCIBLE_SUBAGENTS_ENABLED` is **explicitly** set truthy while `CRUCIBLE_CHAT_CONTROLLER` is off (sub-agents are controller-only).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_reap.py`:

```python
"""Startup reap after a restart (spec §11.5) and the flag-coherence warning (§12)."""
import logging
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_factory import warn_if_incoherent_flags
from agentd.chat.models import AgentRecord, GateAgent, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _row(agent_id: str, status: str) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id="t", turn_id="u", depth=1,
                       name="general-purpose", label=agent_id, prompt="p", status=status)


def test_the_startup_reap(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    for agent_id, status in (("a", "running"), ("b", "waiting"), ("c", "queued"),
                             ("d", "completed")):
        store.insert_agent(_row(agent_id, status))
    parent_gate = store.add_controller_gate(tid, PendingGate.new("edit", {}))
    store.add_controller_gate(tid, PendingGate.new(
        "command", {"command": "ls"}, agent=GateAgent(id="a", label="a", name="g")))
    shadows = tmp_path / "shadows"
    (shadows / f"chatturn-{tid}-agent-0123456789ab").mkdir(parents=True)
    (shadows / f"chatturn-{tid}").mkdir()
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, broadcaster=EventBroadcaster(), retrieval_client=None,
        orchestrator=AgentOrchestrator(
            store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(),
            validator=_Validator(), patch_engine=PatchEngine(),
            workspace_manager=ShadowWorkspaceManager(root_path=shadows)))

    ctrl.reap_subagents()

    statuses = {r.agent_id: (r.status, r.report) for r in store.list_agents("t")}
    assert statuses["a"] == ("failed", "Status: failed — backend restarted")
    assert statuses["b"][0] == statuses["c"][0] == "failed"
    assert statuses["d"] == ("completed", "")
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [parent_gate.gate_id]
    assert thread.messages[-1].content == (
        "✗ Sub-agent approvals were cleared — the backend restarted.")
    assert not (shadows / f"chatturn-{tid}-agent-0123456789ab").exists()
    assert (shadows / f"chatturn-{tid}").exists()  # the parent's orphan recovery needs it


def test_explicit_subagents_without_the_controller_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "1")
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "0")
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("t"))
    assert any("CRUCIBLE_SUBAGENTS_ENABLED" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("t"))
    assert not any("CRUCIBLE_SUBAGENTS_ENABLED" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subagent_reap.py > /tmp/pe3.txt 2>&1; echo exit=$?; grep -E "^(FAILED|ERROR)" /tmp/pe3.txt`
Expected: `exit=1`; both fail (`reap_subagents` missing; no sub-agent warning).

- [ ] **Step 3: Implement the store**

Add `UTC` to the module's `from datetime import …` line (`from datetime import UTC, datetime, timezone`), then add to `ChatThreadStore` (after `list_agents`):

```python
    def reap_agents(self, reason: str) -> int:
        """Fail every row whose process is gone (spec §11.5); returns how many."""
        cursor = self._conn.execute(
            "UPDATE chat_agents SET status = 'failed', report = ?, ended_at = ? "
            "WHERE status IN ('queued', 'running', 'waiting')",
            (f"Status: failed — {reason}", datetime.now(UTC).isoformat()))
        self._conn.commit()
        return cursor.rowcount

    def remove_child_gates(self) -> list[str]:
        """Drop every sub-agent gate from every thread; returns the affected thread ids.
        A child gate is never recoverable after a restart: the write log is gone, so
        promoting its edit would bypass the staleness guard (spec §11.5)."""
        rows = self._conn.execute(
            "SELECT thread_id, controller_gate_json FROM chat_threads "
            "WHERE controller_gate_json IS NOT NULL").fetchall()
        affected: list[str] = []
        for row in rows:
            gates = self._gates_from_row(row)
            kept = [g for g in gates if g.agent is None]
            if len(kept) != len(gates):
                self._write_gates(row["thread_id"], kept)
                affected.append(row["thread_id"])
        return affected
```

- [ ] **Step 4: Implement the controller, startup hook and warning**

`agentd/chat/controller.py`: add `import shutil` to the stdlib imports, and the method (next to `stop_turn`):

```python
    def reap_subagents(self) -> None:
        """Startup reap (spec §11.5): children never survive a restart. Fail their rows,
        drop their gates (with a breadcrumb), and delete their leftover shadows. The
        parent's orphaned-edit recovery (_promote_orphaned_edit) is unchanged."""
        reaped = self._store.reap_agents("backend restarted")
        for thread_id in self._store.remove_child_gates():
            self._store.append_message(thread_id, ChatMessage(
                role="agent",
                content="✗ Sub-agent approvals were cleared — the backend restarted.",
                metadata={"breadcrumb": True}))
        if self._orchestrator is not None:
            root = self._orchestrator._workspace_manager._root_path
            for shadow in root.glob("chatturn-*-agent-*"):
                shutil.rmtree(shadow, ignore_errors=True)
        logger.info("[subagent] reap rows=%d", reaped)
```

`agentd/main.py`, directly after the exec-session `if _exec_manager is not None:` block:

```python
# Sub-agents never survive a restart (spec §11.5): fail their rows, drop their gates,
# delete their shadows. Flag-tolerant: the legacy ChatAgent has no reap.
_reap_subagents = getattr(_chat_agent, "reap_subagents", None)
if _reap_subagents is not None:
    app.router.add_event_handler("startup", _reap_subagents)
```

`agentd/chat/controller_factory.py`, at the end of `warn_if_incoherent_flags`:

```python
    # Sub-agents are controller-only. Warn only when explicitly turned on (spec §12) —
    # the default is not a user decision.
    explicit = os.getenv("CRUCIBLE_SUBAGENTS_ENABLED", "").strip().lower() in _TRUTHY
    if explicit and not is_controller_enabled():
        logger.warning(
            "incoherent flags: CRUCIBLE_SUBAGENTS_ENABLED is on but CRUCIBLE_CHAT_CONTROLLER "
            "is off — sub-agents are controller-only. Set CRUCIBLE_CHAT_CONTROLLER=1.")
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_reap.py tests/test_chat_agents_store.py > /tmp/pe3.txt 2>&1; echo exit=$?; tail -1 /tmp/pe3.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/storage.py agentd/chat/controller.py agentd/main.py agentd/chat/controller_factory.py tests/test_subagent_reap.py
git commit -m "feat(subagents): startup reap and the controller-only flag warning"
```

---

### Task E4: Rewind removes what the rewound turns' children left

**Files:**
- Modify: `agentd/chat/storage.py`, `agentd/memory/store.py`, `agentd/memory/harness.py`, `agentd/chat/controller.py`, `agentd/chat/rewind.py`, `agentd/api/routes.py`
- Test: `tests/test_subagent_rewind.py`

**Interfaces (spec §11.6):**
- `ChatThreadStore.delete_agents_for_turns(thread_id, turn_ids) -> list[str]` — deletes those turns' rows, returns their agent ids.
- `MemoryStore.delete_segments(run_id)`; `MemoryHarness.forget_run(run_id)` — clears a run's compaction anchor and segments; a no-op without a store.
- `ChatController.forget_rewound_agents(thread_id, turn_ids) -> int` — the rows above, each child's `{thread}:{agent}` memory run (best-effort, never fails the rewind), and the thread's write log is **reset** (an empty log is always safe; a stale one would draw refusals attributed to agents the rewind just erased).
- `RewindStore.preview(...)` — `commands_run` also counts `run_command`/`session_start` pills in the rewound turns' child transcripts.
- `POST /rewind`: computes the rewound turn ids **before** `restore()` (which deletes the checkpoints), then calls `forget_rewound_agents` best-effort; the response gains `removed_agents`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subagent_rewind.py`:

```python
"""Rewind removes the rewound turns' children (spec §11.6)."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord, ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.memory.harness import MemoryHarness
from agentd.memory.models import CompactionSegment
from agentd.memory.store import MemoryStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _Compactor:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store


def _row(agent_id: str, turn_id: str, tid: str) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id=tid, turn_id=turn_id, depth=1,
                       name="general-purpose", label=agent_id, prompt="p", status="completed",
                       transcript=[ChatMessage(role="agent", content="", metadata={
                           "tool_events": [{"tool": "run_command"}, {"tool": "read_file"}]})])


@pytest.mark.asyncio
async def test_rewind_deletes_children_memory_and_the_write_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    rewind = RewindStore(store, tmp_path)
    memory = MemoryStore(tmp_path / "m.sqlite3")
    harness = MemoryHarness(enabled=True, compactor=_Compactor(memory))
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None, rewind_store=rewind, memory_harness=harness)
    first = store.append_message(tid, ChatMessage(role="user", content="one"))
    rewind.open_checkpoint(tid, first, "turn-1", thread=store.get_thread(tid),
                           memory_anchor_md=None)
    second = store.append_message(tid, ChatMessage(role="user", content="two"))
    rewind.open_checkpoint(tid, second, "turn-2", thread=store.get_thread(tid),
                           memory_anchor_md=None)
    store.insert_agent(_row("agent-keep", "turn-1", tid))
    store.insert_agent(_row("agent-gone", "turn-2", tid))
    run = f"{tid}:agent-gone"
    memory.upsert_anchor(run, "summary")
    memory.add_segments([CompactionSegment(id="s1", run_id=run, seq=0, content="x",
                                              created_at=datetime.now(UTC))])
    log = ctrl._write_log_for(tid)
    assert log is not None
    log.register_agent("agent-gone")

    preview = rewind.preview(tid, second)
    assert preview is not None and preview.commands_run == 1  # the child's run_command

    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = (await client.post(f"/v1/chat/threads/{tid}/rewind",
                                      json={"message_id": second})).json()
    assert response["removed_agents"] == 1
    assert [r.agent_id for r in store.list_agents(tid)] == ["agent-keep"]
    assert memory.get_anchor(run) is None and memory.get_segments(run) == []
    assert ctrl._write_log_for(tid) is not log  # reset: a fresh, empty log
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_subagent_rewind.py > /tmp/pe4.txt 2>&1; echo exit=$?; grep -E "assert|Error" /tmp/pe4.txt | head -3`
Expected: `exit=1` (`commands_run` is 0 — the child transcripts are not counted yet).

- [ ] **Step 3: Implement**

`agentd/chat/storage.py` (after `remove_child_gates`):

```python
    def delete_agents_for_turns(self, thread_id: str, turn_ids: list[str]) -> list[str]:
        """Delete the rows of rewound turns (spec §11.6); returns their agent ids."""
        if not turn_ids:
            return []
        placeholders = ", ".join("?" for _ in turn_ids)
        rows = self._conn.execute(
            f"SELECT agent_id FROM chat_agents WHERE thread_id = ? "  # noqa: S608
            f"AND turn_id IN ({placeholders})", (thread_id, *turn_ids)).fetchall()
        ids = [r["agent_id"] for r in rows]
        self._conn.execute(
            f"DELETE FROM chat_agents WHERE thread_id = ? "  # noqa: S608
            f"AND turn_id IN ({placeholders})", (thread_id, *turn_ids))
        self._conn.commit()
        return ids
```

(Only `?` placeholders are interpolated; every value is bound.)

`agentd/memory/store.py` (after `clear_anchor`):

```python
    def delete_segments(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM compaction_segments WHERE run_id=?", (run_id,))
        self._conn.commit()
```

`agentd/memory/harness.py` (after `restore_anchor`):

```python
    def forget_run(self, run_id: str) -> None:
        """Drop a run's compaction anchor and segments (a rewound sub-agent, §11.6)."""
        if self._store is None:
            return
        self._store.clear_anchor(run_id)
        self._store.delete_segments(run_id)
```

`agentd/chat/controller.py` (next to `reap_subagents`):

```python
    def forget_rewound_agents(self, thread_id: str, turn_ids: list[str]) -> int:
        """Remove what the rewound turns' children left (spec §11.6): their rows, their
        memory runs (best-effort), and the thread's write log — reset, because a stale
        log would refuse edits citing agents the rewind just erased; an empty one is
        always safe (§7.1)."""
        agent_ids = self._store.delete_agents_for_turns(thread_id, turn_ids)
        for agent_id in agent_ids:
            try:
                self._memory_harness.forget_run(f"{thread_id}:{agent_id}")
            except Exception:  # noqa: BLE001 — memory never fails a rewind
                logger.warning("[subagent] memory cleanup failed id=%s", agent_id,
                               exc_info=True)
        self._write_logs.pop(thread_id, None)
        return len(agent_ids)
```

`agentd/chat/rewind.py`, `preview`: directly before `return RewindPreview(`, add

```python
        # Children of the rewound turns ran commands too (spec §11.6); count them so the
        # confirm dialog states everything a rewind cannot undo.
        span_turns = {cp.turn_id for cp in span}
        for record in self._store.list_agents(thread_id):
            if record.turn_id not in span_turns:
                continue
            for m in record.transcript:
                events = m.metadata.get("tool_events") or []
                commands += sum(
                    1 for e in events
                    if isinstance(e, dict)
                    and str(e.get("tool", "")) in {"run_command", "session_start"})
```

`agentd/api/routes.py`, `post_rewind`: directly after `anchor_md = checkpoint.memory_anchor_md if checkpoint else None`, add

```python
            # The rewound turns, read BEFORE restore() deletes their checkpoints.
            rewound_turns = (
                [cp.turn_id for cp in _chat_agent._store.list_checkpoints(thread_id)
                 if cp.seq >= checkpoint.seq] if checkpoint is not None else [])
```

and directly before the final `return {**outcome.model_dump(mode="json"), "retired_memories": retired}`, add

```python
            removed_agents = 0
            forget = getattr(_chat_agent, "forget_rewound_agents", None)
            if forget is not None:
                try:
                    removed_agents = forget(thread_id, rewound_turns)
                except Exception:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "[rewind] sub-agent cleanup failed", exc_info=True)
```

and add `"removed_agents": removed_agents` to that returned dict.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_subagent_rewind.py tests/test_rewind_scenarios.py tests/test_rewind_integration.py tests/test_memory_rewind.py > /tmp/pe4.txt 2>&1; echo exit=$?; tail -1 /tmp/pe4.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/storage.py agentd/memory/store.py agentd/memory/harness.py agentd/chat/controller.py agentd/chat/rewind.py agentd/api/routes.py tests/test_subagent_rewind.py
git commit -m "feat(subagents): rewind deletes rewound children, their memory and the write log"
```

---

### Task E5: The TypeScript contract tail

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`, `apps/vscode-extension/webview-ui/src/types.ts`, `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`
- Test: `apps/editor-client/test/subagent-contracts.test.ts`, `apps/vscode-extension/webview-ui/src/test/components.test.tsx` (appended)

**Interfaces (spec §11.2, §11.3):**
- `ChatMessageSchema.type` and the webview `ChatMsg.type` gain `"agent_dispatch"` — without it, the first thread containing a roster message would fail `getChatThread`'s Zod parse (the `PendingGate.kind` footgun class).
- `MessageRow` renders `agent_dispatch` as nothing until Phase 4's roster card (never an empty agent bubble).
- `AgentSummarySchema` (camelCase: `agentId, turnId?, parentAgentId, depth, name, label, status, now, toolCount, filesChangedCount, startedAt, endedAt, reportPreview`) and `AgentDetailSchema` (+ `prompt, report, filesChanged, staleRefusals, transcript: ChatMessage[], lastSeq`).
- `ThreadLiveStateSchema.agents: AgentSummary[] | null` (optional); `BackendConfigSchema.subagentsEnabled` (default `false`).
- `StreamEvent` gains `agent_started`, `agent_status`, `agent_finished` (payloads as spec §11.2).
- `BackendTaskClient.listAgents(threadId, turnId?)`, `getAgent(threadId, agentId)`, `stopAgent(threadId, agentId)`; `HttpBackendClient` implements them with explicit snake→camel mapping, and its per-message mapping is factored into one `toChatMessage` helper shared by `getChatThread` and `getAgent`.

- [ ] **Step 1: Write the failing tests**

Create `apps/editor-client/test/subagent-contracts.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";

function respond(body: unknown) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body });
}

const SUMMARY = {
  agent_id: "agent-a", turn_id: "turn-1", parent_agent_id: null, depth: 1,
  name: "general-purpose", label: "impl", status: "completed", files_changed_count: 1,
  started_at: null, ended_at: null, report_preview: "done",
};

describe("sub-agent contracts", () => {
  it("parses a thread containing an agent_dispatch message", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      thread_id: "t", workspace_path: "/w", title: "t", touched_files: [],
      messages: [{ role: "agent", content: "", type: "agent_dispatch",
                   timestamp: "2026-10-01T00:00:00Z",
                   metadata: { agent_ids: ["agent-a"], turn_id: "turn-1" } }],
    }) });
    const thread = await c.getChatThread("t");
    expect(thread.messages[0].type).toBe("agent_dispatch");
  });

  it("lists, gets and stops agents with camelCase mapping", async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ agents: [SUMMARY] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        ...SUMMARY, prompt: "p", report: "full report", files_changed: ["a.py"],
        stale_refusals: 2, last_seq: 9, transcript: [{
          role: "agent", content: "full report", type: "text",
          timestamp: "2026-10-01T00:00:00Z", metadata: { report: true, seq: 9 } }] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const [agent] = await c.listAgents("t", "turn-1");
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/chat/threads/t/agents?turn_id=turn-1");
    expect(agent).toMatchObject({ agentId: "agent-a", filesChangedCount: 1, reportPreview: "done" });
    const detail = await c.getAgent("t", "agent-a");
    expect(detail).toMatchObject({ report: "full report", filesChanged: ["a.py"],
                                   staleRefusals: 2, lastSeq: 9 });
    expect(detail.transcript[0].metadata).toEqual({ report: true, seq: 9 });
    expect(await c.stopAgent("t", "agent-a")).toEqual({ ok: true });
    expect(fetchFn.mock.calls[2][0]).toBe("http://x/v1/chat/threads/t/agents/agent-a/stop");
  });

  it("maps /live agents and the config flag", async () => {
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null, turn_active: true,
      agents: [{ ...SUMMARY, status: "running", now: "read_file a.py", tool_count: 3 }],
    }) });
    const state = await live.getThreadLiveState("t");
    expect(state.agents?.[0]).toMatchObject({ status: "running", now: "read_file a.py",
                                               toolCount: 3 });
    const cfg = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: false,
      skills_enabled: false, mcp_enabled: false, subagents_enabled: true, provider: null }) });
    expect((await cfg.getConfig()).subagentsEnabled).toBe(true);
  });
});
```

Append to `apps/vscode-extension/webview-ui/src/test/components.test.tsx` (it already imports `render`, `describe`/`it`/`expect` and `MessageRow`; every other `MessageRow` prop is optional):

```tsx
describe("MessageRow — agent_dispatch", () => {
  it("renders nothing until the roster card ships (Phase 4)", () => {
    const { container } = render(
      <MessageRow msg={{ role: "agent", content: "", type: "agent_dispatch",
                         timestamp: "2026-10-01T00:00:00Z",
                         metadata: { agent_ids: ["agent-a"] } }} />,
    );
    expect(container.textContent).toBe("");
  });
});
```


- [ ] **Step 2: Run to verify they fail**

Run: `npm run -w @crucible/editor-client test > /tmp/pe5.txt 2>&1; echo exit=$?; grep -cE "×|FAIL" /tmp/pe5.txt`
Expected: `exit=1` with failures in `subagent-contracts.test.ts`.

- [ ] **Step 3: Implement the contracts**

`apps/editor-client/src/contracts/task-contracts.ts`:
1. `ChatMessageSchema.type` enum: append `"agent_dispatch"`.
2. Add `{ type: "agent_started"; payload: { agent_id: string; parent_agent_id: string | null; depth: number; name: string; label: string } }`, `{ type: "agent_status"; payload: { agent_id: string; status: string } }` and `{ type: "agent_finished"; payload: { agent_id: string; status: string; files_changed: string[] } }` to the `StreamEvent` union (after `memory_compacted`).
3. After `ChatMessageSchema`, add:

```ts
// A sub-agent of a dispatch (spec §11.1, §11.3). `now`/`toolCount` exist only on /live
// (an in-flight turn); the routes' list view omits them.
export const AgentSummarySchema = z.object({
  agentId: z.string(),
  turnId: z.string().optional(),
  parentAgentId: z.string().nullable(),
  depth: z.number(),
  name: z.string(),
  label: z.string(),
  status: z.string(),
  now: z.string().default(""),
  toolCount: z.number().default(0),
  filesChangedCount: z.number(),
  startedAt: z.string().nullable(),
  endedAt: z.string().nullable(),
  reportPreview: z.string().default(""),
});
export type AgentSummary = z.infer<typeof AgentSummarySchema>;

export const AgentDetailSchema = AgentSummarySchema.extend({
  prompt: z.string(),
  report: z.string(),
  filesChanged: z.array(z.string()),
  staleRefusals: z.number(),
  transcript: z.array(ChatMessageSchema),
  lastSeq: z.number(),
});
export type AgentDetail = z.infer<typeof AgentDetailSchema>;
```

4. `ThreadLiveStateSchema`: add `agents: z.array(AgentSummarySchema).nullable().optional(),` after `sessions`.
5. `BackendConfigSchema`: add `subagentsEnabled: z.boolean().default(false),` after `mcpEnabled`.
6. `BackendTaskClient` interface, after `stopChatTurn`:

```ts
  // Sub-agents (spec §11.3).
  listAgents(threadId: string, turnId?: string): Promise<AgentSummary[]>;
  getAgent(threadId: string, agentId: string): Promise<AgentDetail>;
  stopAgent(threadId: string, agentId: string): Promise<{ ok: boolean }>;
```

`apps/editor-client/src/client/http-backend-client.ts`:
1. Import `AgentDetailSchema`, `AgentSummarySchema` and the `AgentDetail`/`AgentSummary` types with the other contract imports.
2. Factor the per-message mapping out of `getChatThread` into

```ts
  private static toChatMessage(m: Record<string, unknown>): Record<string, unknown> {
    return {
      role: m["role"],
      content: m["content"],
      type: m["type"] ?? "text",
      // The rewind anchor. This mapping is explicit, not passthrough — omitting
      // the field here silently drops it no matter what the schema allows.
      id: m["id"] ?? null,
      taskId: m["task_id"] ?? null,
      timestamp: typeof m["timestamp"] === "string"
        ? m["timestamp"]
        : new Date(m["timestamp"] as string).toISOString(),
      metadata: (typeof m["metadata"] === "object" && m["metadata"] !== null)
        ? m["metadata"]
        : {},
    };
  }

  private static toAgentSummary(a: Record<string, unknown>): Record<string, unknown> {
    return {
      agentId: a["agent_id"], turnId: a["turn_id"] ?? undefined,
      parentAgentId: a["parent_agent_id"] ?? null, depth: a["depth"],
      name: a["name"], label: a["label"], status: a["status"],
      now: a["now"] ?? "", toolCount: a["tool_count"] ?? 0,
      filesChangedCount: a["files_changed_count"] ?? 0,
      startedAt: a["started_at"] ?? null, endedAt: a["ended_at"] ?? null,
      reportPreview: a["report_preview"] ?? "",
    };
  }
```

and make `getChatThread` use `messages: (messages as Record<string, unknown>[]).map((m) => HttpBackendClient.toChatMessage(m)),`.
3. `getThreadLiveState`: add `agents: Array.isArray(raw["agents"]) ? (raw["agents"] as Record<string, unknown>[]).map((a) => HttpBackendClient.toAgentSummary(a)) : null,` after `sessions`.
4. `getConfig`: add `subagentsEnabled: raw["subagents_enabled"] ?? false,` after `mcpEnabled`.
5. Add the methods (after `stopChatTurn`):

```ts
  async listAgents(threadId: string, turnId?: string): Promise<AgentSummary[]> {
    const query = turnId !== undefined ? `?turn_id=${encodeURIComponent(turnId)}` : "";
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents${query}`
    ) as Record<string, unknown>;
    const agents = Array.isArray(raw["agents"]) ? raw["agents"] as Record<string, unknown>[] : [];
    return agents.map((a) => AgentSummarySchema.parse(HttpBackendClient.toAgentSummary(a)));
  }

  async getAgent(threadId: string, agentId: string): Promise<AgentDetail> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents/${encodeURIComponent(agentId)}`
    ) as Record<string, unknown>;
    const transcript = Array.isArray(raw["transcript"])
      ? raw["transcript"] as Record<string, unknown>[] : [];
    return AgentDetailSchema.parse({
      ...HttpBackendClient.toAgentSummary({
        ...raw,
        files_changed_count: Array.isArray(raw["files_changed"]) ? raw["files_changed"].length : 0,
        report_preview: String(raw["report"] ?? "").slice(0, 200),
      }),
      prompt: raw["prompt"], report: raw["report"],
      filesChanged: raw["files_changed"] ?? [], staleRefusals: raw["stale_refusals"] ?? 0,
      transcript: transcript.map((m) => HttpBackendClient.toChatMessage(m)),
      lastSeq: raw["last_seq"] ?? 0,
    });
  }

  async stopAgent(threadId: string, agentId: string): Promise<{ ok: boolean }> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/agents/${encodeURIComponent(agentId)}/stop`,
      { method: "POST" }
    ) as Record<string, unknown>;
    return { ok: raw["ok"] === true };
  }
```

`apps/vscode-extension/webview-ui/src/types.ts`: add `| "agent_dispatch"` to `ChatMsg.type`.

`apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`: directly before `// diff_summary falls through to text/role-based dispatch`, add

```tsx
    // The dispatch roster anchor (spec §6.1). Its card is Phase 4; until then it
    // renders nothing rather than an empty agent bubble.
    case "agent_dispatch":
      return null;
```

- [ ] **Step 4: Run everything**

Run (repo root): `npm run -w @crucible/editor-client test > /tmp/pe5.txt 2>&1; echo exit=$?; npm run -w @crucible/editor-client build > /tmp/pe5b.txt 2>&1; echo build=$?; npm run typecheck > /tmp/pe5t.txt 2>&1; echo tc=$?; npm run test > /tmp/pe5v.txt 2>&1; echo test=$?`
Expected: all four `0`. (If the extension typecheck reports another `BackendTaskClient` implementation missing the three methods, add them there with the same signatures.)

Run: `(cd apps/vscode-extension/webview-ui && npx tsc --noEmit > /tmp/pe5w.txt 2>&1; echo wtc=$?; npx vitest run > /tmp/pe5wv.txt 2>&1; echo wv=$?)`
Expected: `wtc=0`, `wv=0`.

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client apps/vscode-extension/webview-ui
git commit -m "feat(editor-client): sub-agent contracts, agent_dispatch type and agent routes"
```

---

### Part E verification

- [ ] **Step 1: Full backend suite** — as Part A Step 1 (only the pre-existing failure).
- [ ] **Step 2: Lint** — `.venv/bin/ruff check agentd/subagents tests/test_subagent_lifecycle.py tests/test_subagent_routes.py tests/test_subagent_reap.py tests/test_subagent_rewind.py` → `All checks passed!`; the Part A lint-diff procedure with `BASE` = the commit before Task E1 and `F=(agentd/chat/controller_loop.py agentd/chat/controller.py agentd/chat/models.py agentd/chat/storage.py agentd/api/routes.py agentd/main.py agentd/chat/controller_factory.py agentd/memory/store.py agentd/memory/harness.py agentd/chat/rewind.py)` → `new=0`.
- [ ] **Step 3: TypeScript** — Task E5 Step 4's commands, all `0`.

---

## Phase 2 completion

- [ ] **Full verification** — the backend suite (only the pre-existing failure), every part's lint check, and the TypeScript suites, from a clean checkout of the final commit.
- [ ] **CLAUDE.md** — add a "Sub-agents (P5)" section under "Reactive controller" summarizing: the flag (still default OFF until Phase 5); `dispatch_agents` and the built-ins; the write guard (`WorkspaceWriteLog`, Check 1/Check 2, the `"stale"` record); child channels `chat:{thread}:agent:{agent}` with `seq`; `chat_agents` + the three routes; `/live agents`; the startup reap and rewind cleanup; and the GOTCHAs this plan found: `StaleWriteError` must be caught narrowly before anything broad, `RenderContext.for_agent` (not `agent=`) is what the engine receives, and a fake engine without `render_ctx` silently behaves as the main agent.
- [ ] **Manual live smoke (recommended before Phase 3)** — `CRUCIBLE_SUBAGENTS_ENABLED=1` on a real provider: ask for a two-part change on disjoint files; confirm both children run concurrently in `agentd.log` (`[subagent] start|finish`), their edits land, the parent's next step cites `files_changed`, and `GET /v1/chat/threads/{id}/agents` lists both with full reports.
