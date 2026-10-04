# Sub-agents v2 — Phase 2 implementation plan: Background agents, resume and notices

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Spec:** `docs/superpowers/specs/2026-10-02-subagents-v2-design.md` (rev 11) — §4, §5, §6 and the §8.10
rewind rule; §11.3 phase 2. Builds on Phase 1 (`docs/superpowers/plans/2026-10-02-subagents-v2-phase1.md`),
which is implemented on `feat/subagents-v2`.

**Goal:** Agents run in the background. `dispatch_agents` returns at once; the dispatcher waits with
`wait_agents` when it wants results, follows up with `message_agent`, and stops agents with `stop_agent`.
Reports that arrive after the user's turn become notices that either wait for the next message or start a
notice turn. Preferences the user sets in the extension reach background work, and the UI shows it all.

**Tech Stack:** Python 3.13, FastAPI backend (`services/agentd-py`), SQLite, pytest + pytest-asyncio +
pytest-timeout; TypeScript `apps/editor-client` (Zod), the VS Code extension host, and the React webview
(vitest).

## How this phase is divided

One document per spec phase. Each part was researched and written as if it were its own plan, then appended
here in order; tasks are numbered per part (Task 2A.3, Task 2B.1, …). Execute the parts in order. The whole plan was implemented once in a throwaway worktree (2026-10-04) and the
30 defects that run found are folded in.

| Part | Title | Spec sections | Status |
|---|---|---|---|
| **2A** | Background dispatch — `dispatch_agents` returns at once; `wait_agents`, `message_agent`, `stop_agent`; report routing to child dispatchers; unwaited-dispatch redirect; stop-all; teaching | §4.1–§4.5 | written |
| **2B** | Notices — `agent_notices`, the router (exactly once, by persisted history), the first-turn seed, the fold and its size guard, notice turns, queued user messages, wake suppression and cap | §5.1–§5.3 | written |
| **2C** | Preferences and rewind — process-level review control and Plan Mode with push points, rewind refusal and preview, notice un-delivery and notes to surviving agents on rewind | §5.4, §8.10 | written |
| **2D** | UI — message types, summaries, re-follow, `agents_running`, composer rules, `turn_kind`, queued send, attention poll, transcript reconcile, rewind dialog, Stop all | §6 | written |

## Global Constraints

- Everything is behind `CRUCIBLE_SUBAGENTS_ENABLED` (default on) **except** the unconditional changes the spec
  lists in §11.1 (first-turn seed, process-level preferences); with the flag off the main agent's prompt and
  tools stay byte-identical to `tests/goldens/controller_prompt_main.json`.
- Tests run with sub-agents off by default (`tests/conftest.py`); sub-agent tests opt in with
  `monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")`.
- Reports are never truncated (v1 D8) and always reach another agent framed (`agentd.subagents.framing.frame`).
- Pytest: never `-q` (already in `addopts`); never pipe — redirect and read `$?`; **always pass
  `--timeout=120`** to full or broad runs (a test waiting on an approval card otherwise hangs the run, and
  `-k` subsets can miss the file — Phase 1's dry run hung at 6%).
- Python: strict typing, imports at the top (unless an import cycle forces otherwise, with a comment), no
  boolean flags that switch a function's behavior, specific exception types, comments explain why. Run
  `ruff --fix` only on files you changed.
- A fresh worktree needs `npm ci` at the repo root **and** in `apps/vscode-extension/webview-ui` (a
  separate npm package); without the second, `npm run typecheck` fails on `@testing-library/react`. The
  Python venv can be the main checkout's (`ln -s <main>/services/agentd-py/.venv`): `pythonpath = ["."]`
  in `pyproject.toml` makes the worktree's own `agentd` win.
- **Tests of background behavior must let the agents run.** Scripted engine steps never suspend, so a
  background activation does not run while a scripted main turn iterates. A test that needs an agent to
  finish mid-turn wraps `create_controller_step` with `await asyncio.sleep(0.02)`; one that needs reports
  after `handle_message` adds a `wait_agents` step to the main script. Identical consecutive scripted
  tool calls are blocked as duplicates — vary their arguments.
- Commit format `type(scope): short description`, ending with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz
  ```

## Review Focus (whole phase)

1. **A dispatch followed by `answer` in the same turn** must not silently hold the results until the user's
   next message: the unwaited-dispatch redirect fires once, and a second `answer` upgrades the agents to
   `wake` (Task 2A.5).
2. **A child dispatcher whose agent finishes after it stopped waiting** must still see that report before it
   reports — the inbox delivery plus Phase 1's report guard (Task 2A.4).
3. **`wait_agents` on an agent whose report was already read** returns "already delivered", never the report
   twice (Task 2A.4).
4. **Stopping the user's turn** must leave background agents running (Task 2A.4); **Stop all** must stop them
   (Task 2A.4).
5. **A notice is delivered only after the history holding it is written** (Task 2B.2). Releasing claims in
   `_run_loop`'s `finally` runs before the normal path persists, so nothing is ever delivered — the dry run
   hit exactly this. Release only in the cancel branch.
6. **A report already in a dispatcher's inbox must not also come back from `wait_agents`** (Tasks 2A.4,
   2B.2): the wait discards the inbox copies of what it returned.
7. **A just-resumed agent is not "already delivered"** (Task 2A.4): its row keeps the previous
   activation's stamp until the new activation starts.

---

# Part 2A — Background dispatch

**Goal:** `dispatch_agents` enqueues and returns; three new tools — `wait_agents`, `message_agent`,
`stop_agent` — let the dispatcher collect, follow up and stop. A child dispatcher's agents report into its
inbox when it is not waiting on them. The main agent is redirected once if it ends a turn without waiting on
agents it just dispatched. A thread-level stop-all route stops every agent.

**Architecture:** `SubAgentToolSource` grows from one tool to four, backed by a small `SubAgentOps` bundle of
callables the controller provides per caller (main or a child). The controller keeps two pieces of per-thread
state: which agents a waiter is currently waiting on (so a finished agent's report goes either to the wait or
to the dispatcher's inbox, never both) and the current main turn's dispatches (for the redirect). Phase 1's
`resume_agent` and `AgentSupervisor.stop` do the actual work.

## 2A file map

| File | Change |
|---|---|
| `services/agentd-py/agentd/subagents/runtime.py` | `AgentSupervisor.wait_for(handles, *, dispatcher, timeout)` |
| `services/agentd-py/agentd/chat/storage.py` | `update_agent(on_finish=)`; `agents_dispatched_by` |
| `services/agentd-py/agentd/subagents/permissions.py` | `DISPATCH_GROUP`; group mapping in `child_tool_names` |
| `services/agentd-py/agentd/subagents/tool_source.py` | four tools; `SubAgentOps`; result formatting |
| `services/agentd-py/agentd/chat/controller.py` | background `_dispatch`; `_wait_agents`, `_message_agent`, `_stop_own_agent`; report routing; drain marks delivery; `stop_all_agents`; remove `_inflight_dispatch`; turn dispatch state |
| `services/agentd-py/agentd/chat/controller_loop.py` | `terminal_guard` seam |
| `services/agentd-py/agentd/chat/controller_prompts.py` | `_DISPATCH_BLOCK` rewrite; child role line |
| `services/agentd-py/agentd/api/routes.py` | `POST /v1/chat/threads/{id}/agents/stop-all` |
| `services/agentd-py/tests/goldens/controller_prompt_subagents.json` | **new** golden (sub-agents on) |

---

### Task 2A.1: Supervisor `wait_for` with a timeout

**Files:**
- Modify: `services/agentd-py/agentd/subagents/runtime.py`
- Test: `services/agentd-py/tests/test_agent_supervisor.py` (extend)

**Interfaces:**
- Produces: `AgentSupervisor.wait_for(handles: list[AgentHandle], *, dispatcher: AgentHandle | None = None,
  timeout: float | None = None) -> list[ChildResult | None]` — the result for each handle that finished
  (`None` for one still running when the timeout expired). Like `wait`, the dispatcher gives up its slot
  while waiting and takes it back after.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_agent_supervisor.py`:

```python
@pytest.mark.asyncio
async def test_wait_for_returns_what_finished_by_the_timeout() -> None:
    sup = AgentSupervisor(max_concurrent=4)
    fast, slow = _handle("fast"), _handle("slow")
    never = asyncio.Event()

    async def run(handle: AgentHandle) -> ChildResult:
        if handle.agent_id == "slow":
            await never.wait()
        return _done()

    sup.enqueue(fast, run)
    sup.enqueue(slow, run)
    results = await sup.wait_for([fast, slow], timeout=0.05)
    assert results[0] is not None and results[0].status == "completed"
    assert results[1] is None and sup.is_active("slow")
    await sup.stop("slow")


@pytest.mark.asyncio
async def test_wait_for_without_timeout_waits_for_all() -> None:
    sup = AgentSupervisor(max_concurrent=4)
    handles = [_handle("a"), _handle("b")]

    async def run(handle: AgentHandle) -> ChildResult:
        await asyncio.sleep(0)
        return _done()

    for h in handles:
        sup.enqueue(h, run)
    assert [r.status for r in await sup.wait_for(handles) if r is not None] == [
        "completed", "completed"]
```

- [ ] **Step 2: Run to verify failure** — `cd services/agentd-py && pytest tests/test_agent_supervisor.py --timeout=60 > /tmp/2a1.txt 2>&1; echo exit=$?`; expected exit=1 (`wait_for` missing).

- [ ] **Step 3: Implement** — add to `AgentSupervisor` (after `wait`):

```python
    async def wait_for(
        self, handles: list[AgentHandle], *, dispatcher: AgentHandle | None = None,
        timeout: float | None = None,
    ) -> list[ChildResult | None]:
        """`wait_agents` (spec §4.2): the results that are in by the timeout; None for an
        agent still running. The dispatcher lends its slot while it waits, as in `wait`."""
        if dispatcher is not None and dispatcher.held:
            self._release(dispatcher)
        tasks = [h.task for h in handles if h.task is not None and not h.task.done()]
        # Not try/finally: a cancel of THIS await (the dispatcher stopping) propagates and
        # the dispatcher never re-acquires — the same contract as `wait`.
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        if dispatcher is not None:
            await self._acquire(dispatcher)
        return [h.result if h.task is not None and h.task.done() else None for h in handles]
```

- [ ] **Step 4: Run** — same command; expected exit=0.

- [ ] **Step 5: Commit** — `feat(subagents): wait for agents with a timeout` (+ trailers)

---

### Task 2A.2: Storage — `on_finish` updates and dispatcher queries

**Files:**
- Modify: `services/agentd-py/agentd/chat/storage.py`
- Test: `services/agentd-py/tests/test_agent_store_v2.py` (extend)

**Interfaces:**
- Produces: `update_agent(..., on_finish: str | None = None)` (added to `_AGENT_UPDATABLE`);
  `agents_dispatched_by(thread_id: str, dispatcher_id: str) -> list[AgentRecord]` (in dispatch order);
  `live_agent_ids(thread_id: str) -> list[str]` (status `queued`, `running` or `waiting`, top-level first —
  ordered by `depth`, then `rowid`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_agent_store_v2.py`:

```python
def test_on_finish_and_dispatcher_queries(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    # _record defaults to "queued" (a live status): set every status explicitly.
    store.insert_agent(_record("a1", tid, dispatcher_id="main", on_finish="notify").model_copy(
        update={"status": "completed"}))
    store.insert_agent(_record("a2", tid, parent="a1", dispatcher_id="a1").model_copy(
        update={"status": "completed"}))
    store.insert_agent(_record("a3", tid, dispatcher_id="main").model_copy(
        update={"status": "running"}))
    store.update_agent("a1", on_finish="wake")
    assert store.get_agent("a1").on_finish == "wake"  # type: ignore[union-attr]
    assert [r.agent_id for r in store.agents_dispatched_by(tid, "main")] == ["a1", "a3"]
    assert [r.agent_id for r in store.agents_dispatched_by(tid, "a1")] == ["a2"]
    assert store.live_agent_ids(tid) == ["a3"]
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Implement** — add `"on_finish": "on_finish"` to `_AGENT_UPDATABLE`, the keyword
`on_finish: str | None = None` to `update_agent` and `"on_finish": on_finish` to its `given` dict, then:

```python
    def agents_dispatched_by(self, thread_id: str, dispatcher_id: str) -> list[AgentRecord]:
        rows = self._conn.execute(
            "SELECT * FROM chat_agents WHERE thread_id = ? AND dispatcher_id = ? ORDER BY rowid",
            (thread_id, dispatcher_id)).fetchall()
        return [self._agent_from_row(r) for r in rows]

    def live_agent_ids(self, thread_id: str) -> list[str]:
        """Queued, running or parked at a gate — top-level first, so a stop-all reaches the
        dispatchers (whose stop cascades) before their children."""
        rows = self._conn.execute(
            "SELECT agent_id FROM chat_agents WHERE thread_id = ? "
            "AND status IN ('queued', 'running', 'waiting') ORDER BY depth, rowid",
            (thread_id,)).fetchall()
        return [r["agent_id"] for r in rows]
```

- [ ] **Step 4: Run** — expected exit=0.

- [ ] **Step 5: Commit** — `feat(chat): store queries for dispatchers and live agents` (+ trailers)

---

### Task 2A.3: Four sub-agent tools

**Files:**
- Modify: `services/agentd-py/agentd/subagents/permissions.py`
- Modify: `services/agentd-py/agentd/subagents/tool_source.py`
- Test: `services/agentd-py/tests/test_dispatch_tool_source.py` (rewrite the dispatch tests),
  `services/agentd-py/tests/test_subagent_permissions.py` (extend); update
  `tests/test_dispatch_caps.py`, `tests/test_agent_trust.py`, `tests/test_framing.py` (Step 5)

**Interfaces:**
- Produces (`permissions.py`): `DISPATCH_GROUP = frozenset({"dispatch_agents", "wait_agents",
  "message_agent", "stop_agent"})`; `child_tool_names` adds the whole group present in `available` whenever
  `dispatch_agents` survives the definition filter, and removes the whole group when `may_dispatch` is false.
- Produces (`tool_source.py`):

```python
@dataclass(frozen=True)
class QueuedAgent:
    agent_id: str
    label: str
    name: str
    warning: str = ""   # e.g. a workspace definition overriding a built-in (spec §3.12)


@dataclass(frozen=True)
class AgentResultEntry:
    agent_id: str
    label: str
    name: str
    status: str          # an idle status, "still running", or "already delivered"
    report: str = ""     # full, never truncated; framed when written into the result
    files_changed: list[str] = field(default_factory=list)
    stale_refusals: int = 0


@dataclass(frozen=True)
class SubAgentOps:
    dispatch: Callable[[list[DispatchRequest], dict[str, str]], Awaitable[list[QueuedAgent]]]
    wait: Callable[[list[str] | None, float | None], Awaitable[list[AgentResultEntry]]]
    message: Callable[[str, str, str | None], Awaitable[QueuedAgent]]
    stop: Callable[[str], Awaitable[bool]]
```

  `SubAgentToolSource(catalog, ops: SubAgentOps)`; `format_wait_result(entries) -> str`;
  `format_queued(agents) -> str`. `dispatch`'s second argument maps label → `on_finish`.
- The four tools return errors as `ToolOutput(is_error=True)` with the exception's message for
  `DispatchCapExceeded`, `AgentNotFoundError`, `AgentNotYoursError`, `AgentBusyError`.

- [ ] **Step 1: Write the failing tests** — replace the dispatch tests in `tests/test_dispatch_tool_source.py`
with these (keep any parse-error tests that still apply, adapting them to the new constructor):

```python
"""The four sub-agent tools (spec §4.1)."""
import json

import pytest

from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.framing import frame
from agentd.subagents.runtime import AgentBusyError, DispatchRequest
from agentd.subagents.tool_source import (
    AgentResultEntry, QueuedAgent, SubAgentOps, SubAgentToolSource)


class _Ops:
    def __init__(self) -> None:
        self.dispatched: list[tuple[list[DispatchRequest], dict[str, str]]] = []
        self.waited: list[tuple[list[str] | None, float | None]] = []

    async def dispatch(self, requests, on_finish):  # type: ignore[no-untyped-def]
        self.dispatched.append((requests, on_finish))
        return [QueuedAgent(agent_id=f"agent-{i}", label=r.label, name=r.agent.name)
                for i, r in enumerate(requests)]

    async def wait(self, agent_ids, timeout):  # type: ignore[no-untyped-def]
        self.waited.append((agent_ids, timeout))
        return [AgentResultEntry(agent_id="agent-0", label="a", name="explore",
                                 status="completed", report="R" * 40_000,
                                 files_changed=["b.py", "a.py"], stale_refusals=1),
                AgentResultEntry(agent_id="agent-1", label="b", name="explore",
                                 status="already delivered")]

    async def message(self, agent_id, message, on_finish):  # type: ignore[no-untyped-def]
        raise AgentBusyError("agent 'a' is still running — wait for its report or stop it")

    async def stop(self, agent_id):  # type: ignore[no-untyped-def]
        return agent_id == "agent-0"


def _source(ops: _Ops) -> SubAgentToolSource:
    return SubAgentToolSource(BUILTIN_AGENTS, SubAgentOps(
        dispatch=ops.dispatch, wait=ops.wait, message=ops.message, stop=ops.stop))


def test_four_tools() -> None:
    names = [d.name for d in _source(_Ops()).definitions()]
    assert names == ["dispatch_agents", "wait_agents", "message_agent", "stop_agent"]


@pytest.mark.asyncio
async def test_dispatch_returns_at_once() -> None:
    ops = _Ops()
    out = await _source(ops).execute("dispatch_agents", {"agents": [
        {"agent": "explore", "prompt": "find X", "on_finish": "wake"},
        {"agent": "general-purpose", "prompt": "fix Y"}]})
    assert not out.is_error
    assert json.loads(out.output) == [
        {"agent_id": "agent-0", "label": "explore-1", "agent": "explore", "status": "queued"},
        {"agent_id": "agent-1", "label": "general-purpose-1", "agent": "general-purpose",
         "status": "queued"}]
    assert ops.dispatched[0][1] == {"explore-1": "wake", "general-purpose-1": "notify"}


@pytest.mark.asyncio
async def test_wait_frames_full_reports_and_reports_changed_files() -> None:
    ops = _Ops()
    out = await _source(ops).execute("wait_agents", {"agent_ids": ["agent-0"], "timeout_sec": 30})
    entries = json.loads(out.output)
    assert entries[0]["report"] == frame("a (explore)", "report", "R" * 40_000)
    assert entries[1] == {"agent_id": "agent-1", "label": "b", "agent": "explore",
                          "status": "already delivered — see the earlier message"}
    assert out.workspace_changes == ["a.py", "b.py"]
    assert ops.waited == [(["agent-0"], 30.0)]


@pytest.mark.asyncio
async def test_refusals_become_tool_errors() -> None:
    out = await _source(_Ops()).execute("message_agent", {"agent_id": "agent-0", "message": "go"})
    assert out.is_error and "still running" in out.output


@pytest.mark.asyncio
async def test_stop_agent() -> None:
    src = _source(_Ops())
    assert json.loads((await src.execute("stop_agent", {"agent_id": "agent-0"})).output) == {
        "agent_id": "agent-0", "stopped": True}
```

Append to `tests/test_subagent_permissions.py`:

```python
def test_dispatch_brings_its_whole_group() -> None:
    from agentd.subagents.permissions import DISPATCH_GROUP, child_tool_names

    available = ["read_file", *sorted(DISPATCH_GROUP)]
    names = child_tool_names(available, definition_tools=frozenset({"read_file",
                                                                    "dispatch_agents"}),
                             permission="default", may_dispatch=True)
    assert DISPATCH_GROUP <= names
    names = child_tool_names(available, definition_tools=None, permission="default",
                             may_dispatch=False)
    assert not (DISPATCH_GROUP & names)
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_dispatch_tool_source.py tests/test_subagent_permissions.py --timeout=60` (redirected); expected exit=1.

- [ ] **Step 3: Permissions** — in `permissions.py`:

```python
DISPATCH_GROUP = frozenset({"dispatch_agents", "wait_agents", "message_agent", "stop_agent"})
```

and in `child_tool_names`, replace the final `if not may_dispatch: names.discard(DISPATCH_TOOL)` with:

```python
    if DISPATCH_TOOL in names:
        # A definition that may dispatch (Claude Code's Agent/Task map to dispatch_agents)
        # gets the whole group, so it can always wait — and so satisfy the report guard.
        names |= DISPATCH_GROUP & set(available)
    if not may_dispatch:
        names -= DISPATCH_GROUP
```

- [ ] **Step 4: The tool source** — rewrite `tool_source.py` around the interfaces above. Its body:

```python
"""The sub-agent tools (spec §4.1): dispatch_agents, wait_agents, message_agent, stop_agent."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agentd.subagents.config import DispatchCapExceeded, subagent_max_per_dispatch
from agentd.subagents.definitions import AgentDefinition
from agentd.subagents.framing import frame
from agentd.subagents.runtime import (
    AgentBusyError, AgentNotFoundError, AgentNotYoursError, DispatchRequest)
from agentd.tools.registry import ToolDefinition, ToolOutput

DISPATCH_TOOL_NAME = "dispatch_agents"
WAIT_TOOL_NAME = "wait_agents"
MESSAGE_TOOL_NAME = "message_agent"
STOP_TOOL_NAME = "stop_agent"
_TOOLS = (DISPATCH_TOOL_NAME, WAIT_TOOL_NAME, MESSAGE_TOOL_NAME, STOP_TOOL_NAME)
_ON_FINISH = ("notify", "wake")
_REFUSALS = (DispatchCapExceeded, AgentNotFoundError, AgentNotYoursError, AgentBusyError)

# … QueuedAgent, AgentResultEntry, SubAgentOps exactly as in the Interfaces block …


def format_queued(agents: list[QueuedAgent]) -> str:
    out: list[dict[str, str]] = []
    for a in agents:
        entry = {"agent_id": a.agent_id, "label": a.label, "agent": a.name, "status": "queued"}
        if a.warning:
            entry["warning"] = a.warning
        out.append(entry)
    return json.dumps(out, indent=2)


def format_wait_result(entries: list[AgentResultEntry]) -> str:
    """One entry per agent (spec §4.2): full reports, framed as data (§3.10)."""
    out: list[dict[str, object]] = []
    for e in entries:
        base: dict[str, object] = {"agent_id": e.agent_id, "label": e.label, "agent": e.name}
        if e.status == "already delivered":
            out.append({**base, "status": "already delivered — see the earlier message"})
        elif e.status == "still running":
            out.append({**base, "status": "still running"})
        else:
            out.append({**base, "status": e.status,
                        "report": frame(f"{e.label} ({e.name})", "report", e.report),
                        "files_changed": e.files_changed,
                        "stale_refusals": e.stale_refusals})
    return json.dumps(out, indent=2)


class SubAgentToolSource:
    name = "subagents"

    def __init__(self, catalog: dict[str, AgentDefinition], ops: SubAgentOps) -> None:
        self._catalog = catalog
        self._ops = ops

    def definitions(self) -> list[ToolDefinition]:
        def line(d: AgentDefinition) -> str:
            if d.trust == "capped":
                return f"- {d.name}:\n" + frame(f"definition {d.source} (untrusted)",
                                                  "agent description", d.description)
            return f"- {d.name}: {d.description}"
        lines = "\n".join(line(d) for d in self._catalog.values())
        return [
            ToolDefinition(
                name=DISPATCH_TOOL_NAME,
                description=(
                    "Start sub-agents in the background. Each starts with ONLY the prompt you "
                    "write. Returns at once with each agent's id; call wait_agents to collect "
                    "their reports. on_finish: \"notify\" (default) holds a report finished "
                    "after your turn until the user's next message; \"wake\" starts a turn for "
                    f"you when it arrives. Available agents:\n{lines}"),
                parameters={"type": "object", "properties": {"agents": {
                    "type": "array", "items": {"type": "object", "properties": {
                        "agent": {"type": "string", "enum": list(self._catalog)},
                        "prompt": {"type": "string"},
                        "label": {"type": "string"},
                        "on_finish": {"type": "string", "enum": list(_ON_FINISH)},
                    }, "required": ["agent", "prompt"]}}},
                    "required": ["agents"]}),
            ToolDefinition(
                name=WAIT_TOOL_NAME,
                description=(
                    "Wait for agents you dispatched and get their full reports and changed "
                    "files. agent_ids defaults to every agent of yours still running. With "
                    "timeout_sec, returns what has finished and lists the rest as still "
                    "running."),
                parameters={"type": "object", "properties": {
                    "agent_ids": {"type": "array", "items": {"type": "string"}},
                    "timeout_sec": {"type": "number"}}}),
            ToolDefinition(
                name=MESSAGE_TOOL_NAME,
                description=(
                    "Send a follow-up to an agent you dispatched that has finished (or "
                    "failed, or was stopped). It continues with everything it already read "
                    "and did. Returns at once; collect the new report with wait_agents."),
                parameters={"type": "object", "properties": {
                    "agent_id": {"type": "string"}, "message": {"type": "string"},
                    "on_finish": {"type": "string", "enum": list(_ON_FINISH)}},
                    "required": ["agent_id", "message"]}),
            ToolDefinition(
                name=STOP_TOOL_NAME,
                description="Stop an agent you dispatched, and every agent it started.",
                parameters={"type": "object", "properties": {"agent_id": {"type": "string"}},
                            "required": ["agent_id"]}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in _TOOLS

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == DISPATCH_TOOL_NAME:
                return await self._dispatch(args)
            if tool == WAIT_TOOL_NAME:
                return await self._wait(args)
            if tool == MESSAGE_TOOL_NAME:
                return await self._message(args)
            if tool == STOP_TOOL_NAME:
                agent_id = str(args.get("agent_id", ""))
                return ToolOutput(output=json.dumps(
                    {"agent_id": agent_id, "stopped": await self._ops.stop(agent_id)}))
        except _REFUSALS as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)

    async def _dispatch(self, args: dict[str, object]) -> ToolOutput:
        requests, on_finish, problem = self._parse(args)
        if problem is not None:
            valid = ", ".join(self._catalog)
            return ToolOutput(output=f"Error: {problem}. Valid agents: {valid}.", is_error=True)
        return ToolOutput(output=format_queued(await self._ops.dispatch(requests, on_finish)))

    async def _wait(self, args: dict[str, object]) -> ToolOutput:
        raw_ids = args.get("agent_ids")
        ids = [str(i) for i in raw_ids] if isinstance(raw_ids, list) else None
        raw_timeout = args.get("timeout_sec")
        timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float)) else None
        entries = await self._ops.wait(ids, timeout)
        changed = sorted({f for e in entries for f in e.files_changed})
        return ToolOutput(output=format_wait_result(entries), workspace_changes=changed)

    async def _message(self, args: dict[str, object]) -> ToolOutput:
        on_finish = str(args.get("on_finish") or "")
        queued = await self._ops.message(
            str(args.get("agent_id", "")), str(args.get("message", "")),
            on_finish if on_finish in _ON_FINISH else None)
        return ToolOutput(output=format_queued([queued]))

    def _parse(
        self, args: dict[str, object],
    ) -> tuple[list[DispatchRequest], dict[str, str], str | None]:
        raw = args.get("agents")
        if not isinstance(raw, list) or not raw:
            return [], {}, "'agents' must be a non-empty list"
        if len(raw) > subagent_max_per_dispatch():
            return [], {}, f"at most {subagent_max_per_dispatch()} agents per call"
        requests: list[DispatchRequest] = []
        on_finish: dict[str, str] = {}
        labels: set[str] = set()
        counts: dict[str, int] = {}
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return [], {}, f"agents[{i}] must be an object"
            name = str(item.get("agent", ""))
            definition = self._catalog.get(name)
            if definition is None:
                return [], {}, f"agents[{i}]: unknown agent {name!r}"
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                return [], {}, f"agents[{i}]: 'prompt' is empty"
            counts[name] = counts.get(name, 0) + 1
            label = str(item.get("label") or "").strip() or f"{name}-{counts[name]}"
            if label in labels:
                return [], {}, f"agents[{i}]: duplicate label {label!r}"
            labels.add(label)
            choice = str(item.get("on_finish") or "notify")
            on_finish[label] = choice if choice in _ON_FINISH else "notify"
            requests.append(DispatchRequest(agent=definition, prompt=prompt, label=label))
        return requests, on_finish, None
```

`format_dispatch_result` goes with the rewrite, and `controller.py` still imports it until Task 2A.4: this
commit leaves the controller unimportable, so run only the tests named in Step 6 here (or fold 2A.3 and
2A.4 into one commit).

- [ ] **Step 5: Update the other tests that build the tool source.**
  - `tests/test_dispatch_caps.py`: `SubAgentToolSource(BUILTIN_AGENTS, dispatch=None)` →
    `SubAgentToolSource(BUILTIN_AGENTS, None)  # type: ignore[arg-type]`; the cap test raises from an
    ops bundle:
    ```python
    from agentd.subagents.tool_source import SubAgentOps

    async def dispatch(requests, on_finish):  # type: ignore[no-untyped-def]
        raise DispatchCapExceeded("this thread already has 16 agents running — wait for some "
                                  "to finish or stop them")

    source = SubAgentToolSource(BUILTIN_AGENTS, SubAgentOps(
        dispatch=dispatch, wait=None, message=None, stop=None))  # type: ignore[arg-type]
    ```
  - `tests/test_agent_trust.py`: `[tool] = SubAgentToolSource(loader.load(), dispatch=None).definitions()`
    → `tool = SubAgentToolSource(loader.load(), None).definitions()[0]` (there are four tools now).
  - `tests/test_framing.py`: the framed-report test uses the new formatter:
    ```python
    from agentd.subagents.tool_source import AgentResultEntry, format_wait_result

    [entry] = json.loads(format_wait_result([AgentResultEntry(
        agent_id="agent-a", label="survey", name="explore", status="completed",
        report="Found it.")]))
    assert entry["report"] == frame("survey (explore)", "report", "Found it.")
    ```
  - `tests/test_prompt_leak_lint.py` passes `None` positionally — no change.

- [ ] **Step 6: Run** — `pytest tests/test_dispatch_tool_source.py tests/test_subagent_permissions.py tests/test_dispatch_caps.py tests/test_agent_trust.py tests/test_framing.py tests/test_prompt_leak_lint.py --timeout=60`; expected exit=0. (Controller-level tests fail to import until Task 2A.4 — do not run them yet.)

- [ ] **Step 7: Commit** — `feat(subagents): wait_agents, message_agent and stop_agent tools` (+ trailers)

---

### Task 2A.4: The controller runs dispatch in the background

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Modify: `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_background_dispatch.py` (new); update
  `tests/test_dispatch_integration.py`, `tests/test_subagent_lifecycle.py`, `tests/test_agent_activation.py`,
  `tests/test_usage.py`, `tests/test_subagent_routes.py` where they assumed a blocking dispatch

**Interfaces:**
- Consumes: Task 2A.1 `wait_for`; Task 2A.2 store queries; Task 2A.3 `SubAgentOps`, `QueuedAgent`,
  `AgentResultEntry`.
- Produces (`AgentSupervisor`): `discard_reports(agent_id: str, source_ids: set[str]) -> None`.
- Produces (`ChatController`): `_agent_ops(thread_id, turn_id, caller: AgentHandle | None) -> SubAgentOps`;
  `async stop_all_agents(thread_id: str) -> int`; `_waiting: dict[tuple[str, str], set[str]]` keyed by
  `(thread_id, waiter_id)` — the waiter is `"main"` or an agent id; the thread is in the key because every
  thread's main agent is `"main"`; `_route_report(handle, result)`.
- Produces (route): `POST /v1/chat/threads/{thread_id}/agents/stop-all` → `{"stopped": int}`.

**Behavior:**
- `_dispatch` no longer waits: it creates the agents exactly as today (caps, context, `register_agent`,
  `_on_dispatch_start`), sets `activation_input`, stores `on_finish` on each **main**-dispatched row, enqueues
  each handle with `supervisor.enqueue(handle, self._activate)`, and returns `QueuedAgent`s. A workspace
  definition overriding a built-in name sets `warning` (spec §3.12).
- `_wait_agents(caller_id, caller_handle, agent_ids, timeout)`:
  1. ids default to the caller's agents (`agents_dispatched_by(thread, caller_id)`) that are live **or whose
     report has not been delivered yet** — an agent that finished just before the call must not be skipped;
     explicit ids must have that dispatcher (else `AgentNotYoursError`) and exist (else `AgentNotFoundError`);
  2. records `self._waiting[(thread_id, caller_id)] |= set(ids)` before awaiting and removes them in a `finally`;
  3. `wait_for(handles_of_active_ids, dispatcher=caller_handle, timeout=timeout)`;
  4. builds one `AgentResultEntry` per id from the **row** (status, report, `files_changed` of the subtree
     from the database): `still running` when live, `already delivered` when `report_delivered_at` was set
     **before** this call and the agent is not active (a just-resumed agent is enqueued but its row still
     carries the previous activation's stamp until the activation starts), else the report — and sets
     `report_delivered_at` now for those;
  5. drops the inbox copies of the reports it returned (`AgentSupervisor.discard_reports`) so a child
     dispatcher never reads one twice — once from the wait, once from its inbox.
- `_route_report(handle, result)` runs at the end of every activation (after `_close_child`): when the agent's
  dispatcher is a **child** (`dispatcher_id != "main"`) and that child is not waiting on it
  (`id not in self._waiting.get((thread_id, dispatcher_id), set())`), deliver
  `InboxItem(kind="report", text=result.report, wakes=True, source_id=agent_id, author=f"{label} ({name})")`
  to the dispatcher's inbox. (Main-dispatched reports become notices in Part 2B.)
- The inbox drain passed to the loop marks delivery: wrap `supervisor.drain` so every drained `report` item
  sets `report_delivered_at` on its `source_id`.
- `_message_agent(caller_id, agent_id, message, on_finish)` → `resume_agent(...)` (Phase 1) and, when given and
  the caller is main, `update_agent(agent_id, on_finish=on_finish)`.
- `_stop_own_agent(caller_id, agent_id)` → `AgentNotYoursError` unless `dispatcher_id == caller_id`, then
  `supervisor.stop(agent_id, "user")`.
- `stop_all_agents(thread_id)` stops every live agent (top-level first; the cascade covers the rest) and
  returns how many were stopped.
- `_run_loop` no longer tracks `_inflight_dispatch`: remove the attribute, both `pop` calls and the
  "dispatch interrupted by stop" history block. A stopped turn leaves background agents running.

- [ ] **Step 1: Write the failing tests** — create `tests/test_background_dispatch.py`:

```python
"""Dispatch returns at once; wait, message and stop (spec §4)."""
import asyncio
import json
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_agent_activation import _controller

DONE = {"type": "submit_changes", "thought": "d", "summary": "done"}


def _tool(tool: str, **args: object) -> dict[str, object]:
    return {"type": "tool_call", "thought": "t", "tool": tool, "args": args}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, main, kids):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=main,
                                     agent_scripts=kids)
    return _controller(ws, tmp_path, store, engine), store, tid


def _tool_results(store: ChatThreadStore, tid: str, tool: str) -> list[object]:
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    return [json.loads(str(m["content"])) for m in history if m.get("tool") == tool]


@pytest.mark.asyncio
async def test_dispatch_then_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [queued] = _tool_results(store, tid, "dispatch_agents")
    assert queued[0]["status"] == "queued"
    [waited] = _tool_results(store, tid, "wait_agents")
    assert waited[0]["status"] == "completed" and "found it" in waited[0]["report"]
    [row] = store.list_agents(tid)
    assert row.report_delivered_at is not None and row.on_finish == "notify"


@pytest.mark.asyncio
async def test_a_second_wait_says_already_delivered(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    entries = await ctrl._agent_ops(tid, "t2", None).wait([row.agent_id], None)
    assert [e.status for e in entries] == ["already delivered"]


@pytest.mark.asyncio
async def test_message_agent_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "one"},
                          {"type": "report", "thought": "t", "summary": "two"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    ops = ctrl._agent_ops(tid, "t2", None)
    queued = await ops.message(row.agent_id, "again", "wake")
    assert queued.agent_id == row.agent_id
    [entry] = await ops.wait([row.agent_id], None)
    assert entry.report == "two" and store.get_agent(row.agent_id).on_finish == "wake"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_childs_late_report_reaches_its_inbox(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from agentd.subagents.runtime import ChildResult

    ctrl, store, tid = _setup(tmp_path, monkeypatch, [DONE], {})
    delivered: list[tuple[str, str]] = []
    ctrl._subagents.deliver = lambda agent_id, item: delivered.append((agent_id, item.text))  # type: ignore[union-attr, method-assign]
    from agentd.chat.models import AgentRecord
    store.insert_agent(AgentRecord(agent_id="lead", thread_id=tid, turn_id="u", depth=1,
                                   name="general-purpose", label="lead", prompt="p",
                                   status="running", dispatcher_id="main"))
    store.insert_agent(AgentRecord(agent_id="kid", thread_id=tid, turn_id="u", depth=2,
                                   parent_agent_id="lead", name="explore", label="kid",
                                   prompt="p", status="completed", dispatcher_id="lead"))
    handle = ctrl._handle_from_record(tid, "kid")
    ctrl._route_report(handle, ChildResult(status="completed", report="kid done",
                                           files_changed=[]))
    assert delivered == [("lead", "kid done")]
    ctrl._waiting[(tid, "lead")] = {"kid"}
    ctrl._route_report(handle, ChildResult(status="completed", report="again",
                                           files_changed=[]))
    assert delivered == [("lead", "kid done")]   # a waiting dispatcher gets it from the wait


@pytest.mark.asyncio
async def test_stopping_the_turn_leaves_agents_running_and_stop_all_stops_them(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "general-purpose", "label": "slow",
                                          "prompt": "run"}]),
        _tool("wait_agents")],
        {"slow": [_tool("run_command", command="sleep", args=["30"])]})
    # Through launch_turn: stop_turn only finds turns registered in _active_turns.
    turn = ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"),
                            channel_id=f"chat:{tid}")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if store.list_agents(tid) and store.list_agents(tid)[0].status == "waiting":
            break
    assert await ctrl.stop_turn(tid)
    await asyncio.gather(turn, return_exceptions=True)
    [row] = store.list_agents(tid)
    assert row.status == "waiting"                 # still parked at its command card
    assert await ctrl.stop_all_agents(tid) == 1
    assert store.get_agent(row.agent_id).status == "stopped"  # type: ignore[union-attr]
```

Add to `tests/test_subagent_routes.py`:

```python
@pytest.mark.asyncio
async def test_stop_all_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    async with _client(tmp_path, ctrl) as client:
        response = await client.post(f"/v1/chat/threads/{tid}/agents/stop-all")
    assert response.status_code == 200 and response.json() == {"stopped": 0}
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_background_dispatch.py --timeout=60` (redirected); expected exit=1.

- [ ] **Step 3: Implement.** In `runtime.py`, next to `has_pending_report`:

```python
    def discard_reports(self, agent_id: str, source_ids: set[str]) -> None:
        """A wait_agents result already carried these reports (spec §4.2): drop the inbox
        copies so the dispatcher never reads one twice."""
        self._inboxes[agent_id] = [
            i for i in self._inboxes.get(agent_id, [])
            if not (i.kind == "report" and i.source_id in source_ids)]
```

with a test in `tests/test_agent_supervisor.py`:

```python
def test_discard_reports_keeps_notes_and_other_reports() -> None:
    sup = AgentSupervisor(max_concurrent=1)
    sup.deliver("lead", InboxItem(kind="report", text="a", wakes=True, source_id="a"))
    sup.deliver("lead", InboxItem(kind="report", text="b", wakes=True, source_id="b"))
    sup.deliver("lead", InboxItem(kind="note", text="n", wakes=False, source_id="a"))
    sup.discard_reports("lead", {"a"})
    assert [(i.kind, i.source_id) for i in sup.drain("lead")] == [("report", "b"), ("note", "a")]
```

Then in `controller.py`:

1. `__init__`: add `self._waiting: dict[tuple[str, str], set[str]] = {}`; delete `self._inflight_dispatch`.
2. Replace `_dispatch_source` with:

```python
    def _dispatch_source(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
    ) -> SubAgentToolSource:
        return SubAgentToolSource(self._agent_catalog(),
                                  self._agent_ops(thread_id, turn_id, dispatcher))

    def _agent_ops(
        self, thread_id: str, turn_id: str, caller: AgentHandle | None,
    ) -> SubAgentOps:
        caller_id = caller.agent_id if caller is not None else MAIN_AGENT_ID
        return SubAgentOps(
            dispatch=partial(self._dispatch, thread_id, turn_id, caller),
            wait=partial(self._wait_agents, thread_id, caller_id, caller),
            message=partial(self._message_agent, thread_id, caller_id),
            stop=partial(self._stop_own_agent, thread_id, caller_id))
```

3. `_dispatch(self, thread_id, turn_id, dispatcher, requests, on_finish) -> list[QueuedAgent]`: keep
   everything up to and including `self._on_dispatch_start(...)`; replace the tail (from
   `if dispatcher is None: self._inflight_dispatch…` to the `return`) with:

```python
        queued: list[QueuedAgent] = []
        for handle in handles:
            handle.activation_input = handle.prompt
            if dispatcher is None:
                self._store.update_agent(handle.agent_id,
                                         on_finish=on_finish.get(handle.context.label, "notify"))
            runtime.enqueue(handle, self._activate)
            warning = ""
            if (handle.definition.name in BUILTIN_AGENTS
                    and handle.definition.source != "built-in"):
                warning = (f"{handle.definition.name!r} is defined by "
                           f"{handle.definition.source} in this workspace, not the built-in")
            queued.append(QueuedAgent(agent_id=handle.agent_id, label=handle.context.label,
                                      name=handle.context.name, warning=warning))
        return queued
```

4. New methods:

```python
    async def _wait_agents(
        self, thread_id: str, caller_id: str, caller: AgentHandle | None,
        agent_ids: list[str] | None, timeout: float | None,
    ) -> list[AgentResultEntry]:
        assert self._subagents is not None
        mine = {r.agent_id: r for r in self._store.agents_dispatched_by(thread_id, caller_id)}
        if agent_ids is None:
            # Running agents, plus finished ones whose report has not reached the caller
            # yet — an agent that finished before this call must not be silently skipped.
            ids = [i for i, r in mine.items()
                   if r.status in LIVE_STATUSES or r.report_delivered_at is None]
        else:
            ids = list(agent_ids)
            for agent_id in ids:
                record = self._store.get_agent(agent_id)
                if record is None or record.thread_id != thread_id:
                    raise AgentNotFoundError(f"no agent {agent_id!r} in this thread")
                if agent_id not in mine:
                    raise AgentNotYoursError(f"agent {record.label!r} is not one you dispatched")
        # Read before waiting; an agent just resumed is active but its row still carries
        # the previous activation's delivery stamp until the activation starts.
        already = {i for i in ids if mine[i].report_delivered_at is not None
                   and mine[i].status not in LIVE_STATUSES
                   and not self._subagents.is_active(i)}
        waiting = self._waiting.setdefault((thread_id, caller_id), set())
        waiting.update(ids)
        try:
            handles = [h for h in (self._subagents.registry.get(i) for i in ids)
                       if h is not None and self._subagents.is_active(h.agent_id)]
            await self._subagents.wait_for(handles, dispatcher=caller, timeout=timeout)
        finally:
            waiting.difference_update(ids)
        entries: list[AgentResultEntry] = []
        now = datetime.now(UTC)
        for agent_id in ids:
            record = self._store.get_agent(agent_id)
            assert record is not None
            base = {"agent_id": agent_id, "label": record.label, "name": record.name}
            if self._subagents.is_active(agent_id) or record.status in LIVE_STATUSES:
                entries.append(AgentResultEntry(**base, status="still running"))
            elif agent_id in already:
                entries.append(AgentResultEntry(**base, status="already delivered"))
            else:
                entries.append(AgentResultEntry(
                    **base, status=record.status, report=record.report,
                    files_changed=self._subtree_files(agent_id),
                    stale_refusals=record.stale_refusals))
                self._store.update_agent(agent_id, report_delivered_at=now)
        delivered = {e.agent_id for e in entries
                     if e.status not in ("still running", "already delivered")}
        if caller is not None:
            self._subagents.discard_reports(caller_id, delivered)
        return entries

    def _subtree_files(self, agent_id: str) -> list[str]:
        files: set[str] = set()
        for i in self._store.subtree_agent_ids(agent_id):
            record = self._store.get_agent(i)
            if record is not None:
                files |= set(record.files_changed)
        return sorted(files)

    async def _message_agent(
        self, thread_id: str, caller_id: str, agent_id: str, message: str,
        on_finish: str | None,
    ) -> QueuedAgent:
        handle = self.resume_agent(thread_id, agent_id, message, caller_id=caller_id)
        if on_finish is not None and caller_id == MAIN_AGENT_ID:
            self._store.update_agent(agent_id, on_finish=on_finish)
        return QueuedAgent(agent_id=agent_id, label=handle.context.label,
                           name=handle.context.name)

    async def _stop_own_agent(self, thread_id: str, caller_id: str, agent_id: str) -> bool:
        assert self._subagents is not None
        record = self._store.get_agent(agent_id)
        if record is None or record.thread_id != thread_id:
            raise AgentNotFoundError(f"no agent {agent_id!r} in this thread")
        if record.dispatcher_id != caller_id:
            raise AgentNotYoursError(f"agent {record.label!r} is not one you dispatched")
        return await self._subagents.stop(agent_id, "user")

    async def stop_all_agents(self, thread_id: str) -> int:
        """Stop every agent in the thread (spec §4.4); a stopped dispatcher's stop cascades."""
        if self._subagents is None:
            return 0
        stopped = 0
        for agent_id in self._store.live_agent_ids(thread_id):
            if await self._subagents.stop(agent_id, "user"):
                stopped += 1
        return stopped

    def _route_report(self, handle: AgentHandle, result: ChildResult) -> None:
        """A child dispatcher that is not waiting on this agent gets its report in its
        inbox (spec §3.6, §4.2); the main agent's go to notices (Part 2B)."""
        assert self._subagents is not None
        record = self._store.get_agent(handle.agent_id)
        if record is None or record.dispatcher_id in (None, MAIN_AGENT_ID):
            return
        if handle.agent_id in self._waiting.get((record.thread_id, record.dispatcher_id), set()):
            return
        self._subagents.deliver(record.dispatcher_id, InboxItem(
            kind="report", text=result.report, wakes=True, source_id=handle.agent_id,
            author=f"{handle.context.label} ({handle.context.name})"))

    def _drain_and_mark(self, agent_id: str) -> list[InboxItem]:
        assert self._subagents is not None
        items = self._subagents.drain(agent_id)
        now = datetime.now(UTC)
        for item in items:
            if item.kind == "report" and item.source_id:
                self._store.update_agent(item.source_id, report_delivered_at=now)
        return items
```

5. In `_activate`: pass `inbox_drain=partial(self._drain_and_mark, ctx.agent_id)`; and replace the final
   `return self._close_child(handle, status, report)` with:

```python
        result = self._close_child(handle, status, report)
        self._route_report(handle, result)
        return result
```

   (The cancelled branch keeps its own `_close_child` + `raise`: a stopped agent's report is the fallback, and
   a cascade stop's dispatcher is stopping too.)
6. `_run_loop`: delete the `interrupted = self._inflight_dispatch.pop(...)` block and the
   `self._inflight_dispatch.pop(thread_id, None)` in the `except Exception` branch; remove the now-unused
   `format_dispatch_result` and `assistant_turn` imports (`ChildResult` is still used).
7. Imports: `QueuedAgent`, `AgentResultEntry`, `SubAgentOps` from `agentd.subagents.tool_source`.

`routes.py`, next to `post_stop_agent`:

```python
        @router.post("/chat/threads/{thread_id}/agents/stop-all")
        async def post_stop_all_agents(thread_id: str) -> dict:
            stop_all = getattr(_chat_agent, "stop_all_agents", None)
            if stop_all is None:
                return {"stopped": 0}
            return {"stopped": await stop_all(thread_id)}  # type: ignore[misc]
```

- [ ] **Step 4: Update the 13 tests that assumed a blocking dispatch.** Add a constant next to `DONE` in
`tests/test_agent_activation.py` and `tests/test_dispatch_integration.py`:

```python
# Dispatch returns at once (spec §4.1); reports are collected with wait_agents.
WAIT = {"type": "tool_call", "thought": "collect", "tool": "wait_agents", "args": {}}
```

  - `test_agent_activation.py::_setup`: main script `[_dispatch(...), WAIT, DONE]`.
  - `test_dispatch_integration.py`: both tests' main scripts get `WAIT` before `DONE`; the nested test's
    `lead` script gets `WAIT` before its report; the results are read from the `wait_agents` tool result
    (`next(m for m in history if m.get("tool") == "wait_agents")`).
  - `test_agent_definitions_wiring.py`: import `WAIT`; both dispatch scripts get it; `[before] = …definitions()`
    and `[after] = …` become `…definitions()[0]` (there are four tools).
  - `test_dispatch_roster_event.py` (nested roster): `WAIT` in the main and the `lead` scripts.
  - `test_subagent_lifecycle.py`: that file's `WAIT` is a child script, so add
    `COLLECT = {"type": "tool_call", "thought": "collect", "tool": "wait_agents", "args": {}}`, use
    `[DISPATCH, COLLECT, DONE]`, read statuses from the `wait_agents` result, and replace
    `test_a_stopped_turn_remembers_its_dispatch` (its subject is gone) with:
    ```python
    @pytest.mark.asyncio
    async def test_a_stopped_turn_leaves_its_agents_running(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec §4.4: /stop ends the turn only; the next turn sees what it dispatched."""
        ctrl, store, tid = _setup(tmp_path, monkeypatch)
        ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"),
                         channel_id=f"chat:{tid}")
        await _wait_for_gate(store, tid)
        assert await ctrl.stop_turn(tid) is True
        rows = {r.label: r for r in store.list_agents(tid)}
        assert rows["waiter"].status == "waiting"
        history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
        dispatched = next(m for m in history if m.get("tool") == "dispatch_agents")
        assert {e["label"] for e in json.loads(str(dispatched["content"]))} == {"waiter", "quick"}
        assert await ctrl.stop_all_agents(tid) == 1
    ```
  - `test_usage.py`: a `wait_agents` step before `submit_changes`; the thread now makes three main calls
    (`store.thread_usage(tid).requests == 3`).

- [ ] **Step 5: Run** — `pytest -k "agent or dispatch or subagent or child or usage or route" --timeout=120` (redirected); expected exit=0.

- [ ] **Step 6: Commit** — `feat(subagents): dispatch runs in the background` (+ trailers)

---

### Task 2A.5: The unwaited-dispatch redirect

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py` (`terminal_guard`)
- Modify: `services/agentd-py/agentd/chat/controller.py` (turn dispatch state)
- Test: `services/agentd-py/tests/test_unwaited_dispatch.py` (new)

**Interfaces:**
- Produces: `ControllerLoop.run(..., terminal_guard: Callable[[], str | None] | None = None)` — called on a
  non-final iteration before `answer` or `submit_changes` ends the turn; a string redirects (assistant turn +
  `tool_result` with the text, `continue`), not counted as malformed.
- Produces: `TurnDispatches` (in `controller.py`): `dispatched: dict[str, str]` (agent id → on_finish) and
  `waited: set[str]`, `redirected: bool`; `ChatController._turn_dispatches: dict[str, TurnDispatches]`.

**Behavior (spec §4.1):** when the main agent ends its turn while agents it dispatched **this turn** with
`on_finish="notify"` are still queued or running and it has not waited on them, redirect once:
`Agents <labels> are still running. Call wait_agents to include their results, or answer now and say they
continue in the background.` A second terminal action is accepted, and those agents are switched to `wake`.

- [ ] **Step 1: Write the failing tests**

```python
"""The main agent does not end its turn on unwaited agents by accident (spec §4.1)."""
import json
from pathlib import Path

import pytest

from tests.test_background_dispatch import _setup, _tool

ANSWER = {"type": "answer", "thought": "t", "answer": "They are working on it."}


@pytest.mark.asyncio
async def test_redirect_once_then_upgrade_to_wake(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "general-purpose", "label": "slow",
                                          "prompt": "run"}]),
        ANSWER, ANSWER],
        {"slow": [_tool("run_command", command="sleep", args=["30"])]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    redirects = [m for m in history if "are still running" in str(m.get("content", ""))]
    assert len(redirects) == 1
    [row] = store.list_agents(tid)
    assert row.on_finish == "wake"
    await ctrl.stop_all_agents(tid)


@pytest.mark.asyncio
async def test_no_redirect_after_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"), ANSWER],
        {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert not [m for m in history if "are still running" in str(m.get("content", ""))]
    assert json.loads(str([m for m in history if m.get("tool") == "wait_agents"][0]["content"]))
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Loop seam** — thread `terminal_guard` through `run` and `_iterate` like Task 1A.5's seams. In the
`answer` branch, right before `history.append(assistant_turn(resp))` + `return ControllerOutcome(kind="answer"…)`,
and in the `submit_changes` branch right after its open-todo block, insert:

```python
                if terminal_guard is not None and iteration < max_iters:
                    redirect = terminal_guard()
                    if redirect is not None:
                        # A redirect, not malformed (same contract as the open-todo block).
                        history.append(assistant_turn(resp))
                        history.append({"role": "tool_result", "tool": "", "content": redirect})
                        continue
```

- [ ] **Step 4: Controller**

```python
@dataclass
class TurnDispatches:
    """What the main agent dispatched and waited on in the current turn (spec §4.1)."""
    dispatched: dict[str, str] = field(default_factory=dict)   # agent id -> on_finish
    waited: set[str] = field(default_factory=set)
    redirected: bool = False
```

- `__init__`: `self._turn_dispatches: dict[str, TurnDispatches] = {}`.
- `_run_loop`: create `self._turn_dispatches[thread_id] = TurnDispatches()` next to
  `self._turn_controls[thread_id] = control`; pop it in the same `finally`; pass
  `terminal_guard=partial(self._unwaited_guard, thread_id)` to `loop.run`.
- `_dispatch` (main dispatcher only): record `state.dispatched[agent_id] = on_finish` for each queued agent.
- `_wait_agents` (caller main): `state.waited.update(ids)`.
- The guard:

```python
    def _unwaited_guard(self, thread_id: str) -> str | None:
        state = self._turn_dispatches.get(thread_id)
        if state is None or self._subagents is None:
            return None
        pending = [i for i, on_finish in state.dispatched.items()
                   if on_finish == "notify" and i not in state.waited
                   and self._subagents.is_active(i)]
        if not pending:
            return None
        if not state.redirected:
            state.redirected = True
            labels = ", ".join(
                (self._store.get_agent(i) or AgentRecord(agent_id=i, thread_id=thread_id,
                 turn_id="", depth=1, name="", label=i, prompt="", status="")).label
                for i in pending)
            return (f"Agents {labels} are still running. Call wait_agents to include their "
                    "results, or answer now and say they continue in the background.")
        for agent_id in pending:
            # The model chose to end the turn: deliver the results by waking it instead of
            # holding them silently until the user's next message.
            self._store.update_agent(agent_id, on_finish="wake")
        return None
```

  (`AgentRecord` is already imported in `controller.py`; if the label lookup reads awkwardly, fall back to the
  agent id when the row is missing — it never is in practice.)

- [ ] **Step 5: Run** — `pytest tests/test_unwaited_dispatch.py tests/test_background_dispatch.py --timeout=60`; expected exit=0.

- [ ] **Step 6: Commit** — `feat(chat): redirect a turn that ends on agents it never waited for` (+ trailers)

---

### Task 2A.6: Teaching and the sub-agents golden

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (`_DISPATCH_BLOCK`, `_AGENT_ROLE_BLOCK`)
- Create: `services/agentd-py/tests/test_prompt_goldens_subagents.py`,
  `services/agentd-py/tests/goldens/controller_prompt_subagents.json`

**Behavior:** the main agent's sub-agents block teaches the background model with two worked examples (spec
§4.1); the child role block's dispatch line names `wait_agents`. The existing main golden (sub-agents off)
must stay byte-identical; a new golden pins the sub-agents-on main prompt so later edits are deliberate.

- [ ] **Step 1: Rewrite `_DISPATCH_BLOCK`** (keep it a `tagged(...)` literal — no `<<<` inside it):

```python
_DISPATCH_BLOCK = tagged("_DISPATCH_BLOCK", """

SUB-AGENTS (dispatch_agents, wait_agents, message_agent, stop_agent)
dispatch_agents starts sub-agents in the background — each in its own context window — and
returns at once with their ids. wait_agents collects their reports (one full report per
agent, plus the files each changed). message_agent sends a finished agent a follow-up: it
continues with everything it already read. stop_agent stops one.
- A sub-agent starts with ONLY the prompt you write. Make each prompt self-contained:
  the goal, the exact files that agent owns, the constraints, and how to verify.
- Give each agent its OWN files. An agent editing a file another agent changed is
  refused until it re-reads that file, which wastes its budget.
- Sub-agents cannot ask you anything: put every decision in the prompt. Their reports
  list the assumptions they made and their open questions.
- explore is read-only (fast, parallel investigation); general-purpose can edit.
- When an agent failed or stopped part-way, message_agent it with what to do next: it keeps
  its context. Dispatch a new agent only when a fresh start is better.
<<main>>
- Don't tell sub-agents to commit or use version control — they are blocked from it.
  Commit after the batch yourself, using each result's files_changed.
- When the parts are independent and touch disjoint files, dispatch_agents may be your
  first action instead of write_todos; the todo list stays yours to reconcile afterwards.
- on_finish: "notify" (default) holds a report that finishes after your turn until the
  user's next message; "wake" starts a turn for you when it arrives.
<</main>>
- Check each result: read its report, and re-read any file in files_changed before you
  edit it yourself.
Example — results needed now (dispatch, then wait, then answer):
{"type":"tool_call","thought":"two independent parts on disjoint files","tool":"dispatch_agents","args":{"agents":[{"agent":"general-purpose","label":"limiter","prompt":"Add a token-bucket limiter in api/limiter.py (you own only that file). Verify with pytest tests/test_limiter.py."},{"agent":"explore","label":"auth survey","prompt":"Find every caller of check_token under api/ and report each with path:line."}]}}
{"type":"tool_call","thought":"collect both reports before answering","tool":"wait_agents","args":{}}
<<main>>
Example — long work the user need not wait for (dispatch with wake, then answer):
{"type":"tool_call","thought":"a long migration; the user can keep chatting","tool":"dispatch_agents","args":{"agents":[{"agent":"general-purpose","label":"migrate","prompt":"Migrate api/ to the new client (you own api/). Verify with pytest tests/api.","on_finish":"wake"}]}}
{"type":"answer","thought":"it runs in the background","answer":"I started a migration agent; it continues in the background and I'll report when it finishes."}
<</main>>
""")
```

- [ ] **Step 2: Child role line** — in `_AGENT_ROLE_BLOCK`, replace the
`<<tool:dispatch_agents>>` bullet's text with: "You may dispatch your own sub-agents with dispatch_agents when
parts of your task are independent and touch disjoint files. Call wait_agents before you report: you cannot
report while your agents are still running."

- [ ] **Step 3: The new golden test**

```python
"""Main-agent text with sub-agents on (Phase 2): changes to it must be deliberate."""
import json
from pathlib import Path

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.tool_source import SubAgentOps, SubAgentToolSource

GOLDEN = Path(__file__).parent / "goldens" / "controller_prompt_subagents.json"


async def _never(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


def _live() -> dict[str, str]:
    tools = [d.model_dump() for d in SubAgentToolSource(BUILTIN_AGENTS, SubAgentOps(
        dispatch=_never, wait=_never, message=_never, stop=_never)).definitions()]
    return {"system/subagents_on": format_controller_system_prompt(
        tools, task_subsystem_enabled=False, memory_enabled=False)}


def test_subagents_main_text_matches_golden() -> None:
    assert _live() == json.loads(GOLDEN.read_text(encoding="utf-8"))


if __name__ == "__main__":  # python -m tests.test_prompt_goldens_subagents  → re-capture
    GOLDEN.write_text(json.dumps(_live(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

  Capture it once after Steps 1–2: `cd services/agentd-py && python -m tests.test_prompt_goldens_subagents`.
  Read the captured prompt and check the two examples render (the main-only one appears; no `<<` remains).

- [ ] **Step 4: Run** — `pytest tests/test_prompt_goldens.py tests/test_prompt_goldens_subagents.py tests/test_prompt_leak_lint.py tests/test_dispatch_teaching.py --timeout=60`; expected exit=0. `tests/test_dispatch_teaching.py` asserts the old header: replace `"SUB-AGENTS (dispatch_agents)"` with
`"SUB-AGENTS (dispatch_agents, wait_agents"` (three places).

- [ ] **Step 5: Commit** — `feat(chat): teach background dispatch with worked examples` (+ trailers)

---

### Task 2A.7: Part 2A checkpoint

- [ ] **Step 1:** `cd services/agentd-py && pytest --color=no --timeout=120 > /tmp/2a-full.txt 2>&1; echo exit=$?; tail -5 /tmp/2a-full.txt` — all pass except known timing flakes (`test_command_only_step`; `test_memory_harness_warmup_no_race` under load).
- [ ] **Step 2:** `ruff check` the files this part changed; fix new findings only.

---

# Part 2B — Notices

**Goal:** A report from an agent the main agent dispatched reaches the main agent **exactly once**: in a
`wait_agents` result, at an iteration top of a running main turn, folded into the next main turn's first
message, or — for `wake` — by starting a notice turn. Delivery counts only once the text is in the main
agent's **persisted** history (spec §5.2).

**Architecture:** One table, `agent_notices`, is the record. A notice is *claimed* by the turn that appends its
text (`claimed_turn_id`, `claimed_checkpoint_seq`) and *delivered* when that turn's history is written
(`delivered_at`); a turn that never persists releases its claims. In memory, the controller keeps a per-thread
main inbox (reports and queued user messages for the running main turn), a per-thread wake timer, the wake
counter and the suppression flag. The first-turn seed fix (unconditional, spec §5.2) lands here because the
fold needs a user message in the seed.

## 2B file map

| File | Change |
|---|---|
| `services/agentd-py/agentd/chat/models.py` | `NoticeRecord`; `ChatMessage.type` gains `notice`, `agent_message`, `team_created`; `ThreadLiveState.turn_kind` |
| `services/agentd-py/agentd/chat/storage.py` | `agent_notices` table + notice methods; `set_message_metadata`; `move_message_to_end` |
| `services/agentd-py/agentd/subagents/inbox.py` | `InboxItem.kind` gains `"user"`; `notice_id` |
| `services/agentd-py/agentd/subagents/notices.py` | **new** — notice text, the fold builder, token estimate |
| `services/agentd-py/agentd/chat/controller_loop.py` | a drained `user` item is appended as the user's own message |
| `services/agentd-py/agentd/chat/controller.py` | router, main inbox, claims and delivery, fold, notice turns, queued messages |
| `services/agentd-py/agentd/api/routes.py` | `/message` 202 during a notice turn; `/live` `turn_kind` |
| `services/agentd-py/agentd/main.py` | startup releases unpersisted claims |
| `apps/editor-client/src/contracts/task-contracts.ts`, `apps/vscode-extension/webview-ui/src/types.ts` | the three message types, in lockstep (rendering is Part 2D) |

---

### Task 2B.1: Notice records and the new message types

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py`, `services/agentd-py/agentd/chat/storage.py`,
  `services/agentd-py/agentd/subagents/inbox.py`
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`ChatMessageSchema.type`),
  `apps/vscode-extension/webview-ui/src/types.ts` (`ChatMsg.type`)
- Test: `services/agentd-py/tests/test_agent_notices_store.py` (new)

**Interfaces:**
- Produces (`models.py`):

```python
class NoticeRecord(BaseModel):
    """A report or milestone waiting for the main agent (spec §5.1)."""
    notice_id: str
    thread_id: str
    source_kind: Literal["agent", "team"]
    source_id: str
    kind: str                       # "agent_finished" or a team milestone (Phase 4)
    payload: dict[str, Any]
    delivery: Literal["notify", "wake"]
    created_at: datetime
    claimed_turn_id: str | None = None
    claimed_checkpoint_seq: int | None = None
    delivered_at: datetime | None = None
```

- Produces (`ChatThreadStore`):
  - `insert_notice(record: NoticeRecord) -> None`
  - `unclaimed_notices(thread_id: str) -> list[NoticeRecord]` — `claimed_turn_id IS NULL`, oldest first
  - `claim_notices(notice_ids: list[str], turn_id: str, checkpoint_seq: int) -> None`
  - `deliver_claimed_notices(thread_id: str, turn_id: str) -> list[NoticeRecord]` — sets `delivered_at` on
    this turn's claimed, undelivered rows and `report_delivered_at` on their source agents; returns them
  - `release_claimed_notices(thread_id: str, turn_id: str) -> int` — clears the claim of this turn's
    claimed-but-undelivered rows
  - `release_all_unpersisted_notices() -> int` — the startup reset (spec §5.2)
  - `claimed_agent_sources(thread_id: str) -> set[str]` — agent ids with a claimed notice
  - `set_message_metadata(thread_id: str, message_id: str, updates: dict[str, Any]) -> bool`
  - `move_message_to_end(thread_id: str, message_id: str) -> bool`
- Produces (`inbox.py`): `InboxItem.kind: Literal["report", "note", "user"]`; `notice_id: str = ""`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_agent_notices_store.py`:

```python
"""agent_notices: claim, deliver on persist, release (spec §5.1, §5.2)."""
from datetime import UTC, datetime
from pathlib import Path

from agentd.chat.models import AgentRecord, ChatMessage, NoticeRecord
from agentd.chat.storage import ChatThreadStore


def _store(tmp_path: Path) -> tuple[ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    return store, store.create_thread(str(tmp_path), title="t").thread_id


def _notice(nid: str, tid: str, source: str = "a1") -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id=tid, source_kind="agent", source_id=source,
                        kind="agent_finished", payload={"report": "r"}, delivery="notify",
                        created_at=datetime.now(UTC))


def test_claim_deliver_release(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_agent(AgentRecord(agent_id="a1", thread_id=tid, turn_id="u", depth=1,
                                   name="explore", label="a", prompt="p", status="completed"))
    for nid in ("n1", "n2"):
        store.insert_notice(_notice(nid, tid))
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n1", "n2"]
    store.claim_notices(["n1"], "turn-1", 3)
    store.claim_notices(["n2"], "turn-2", 3)
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == []
    assert store.claimed_agent_sources(tid) == {"a1"}
    delivered = store.deliver_claimed_notices(tid, "turn-1")
    assert [n.notice_id for n in delivered] == ["n1"]
    assert store.get_agent("a1").report_delivered_at is not None  # type: ignore[union-attr]
    assert store.release_claimed_notices(tid, "turn-1") == 0      # delivered stays delivered
    assert store.release_claimed_notices(tid, "turn-2") == 1
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n2"]


def test_startup_release(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_notice(_notice("n1", tid))
    store.claim_notices(["n1"], "turn-1", 0)
    assert store.release_all_unpersisted_notices() == 1
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n1"]


def test_message_metadata_and_move(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    first = store.append_message(tid, ChatMessage(role="user", content="queued"))
    store.append_message(tid, ChatMessage(role="agent", content="reply"))
    assert first is not None
    assert store.set_message_metadata(tid, first, {"checkpoint_anchor": "m0"})
    assert store.move_message_to_end(tid, first)
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert [m.content for m in messages] == ["reply", "queued"]
    assert messages[-1].id == first and messages[-1].metadata == {"checkpoint_anchor": "m0"}
    assert not store.move_message_to_end(tid, "missing")


def test_notice_message_type_round_trips(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.append_message(tid, ChatMessage(role="agent", content="🔔", type="notice",
                                          metadata={"target": {"agent_id": "a1"}}))
    assert store.get_thread(tid).messages[-1].type == "notice"  # type: ignore[union-attr]
```

- [ ] **Step 2: Run to verify failure** — `cd services/agentd-py && pytest tests/test_agent_notices_store.py --timeout=60 > /tmp/2b1.txt 2>&1; echo exit=$?`; expected exit=1.

- [ ] **Step 3: Models** — add `NoticeRecord` (above) to `chat/models.py`; extend `ChatMessage.type`'s
`Literal` with `"notice", "agent_message", "team_created"`; add to `ThreadLiveState`:

```python
    # Which kind of main turn is running (spec §5.3): the composer stays usable during a
    # notice turn. None when no turn is running.
    turn_kind: Literal["user", "notice"] | None = None
```

- [ ] **Step 4: Inbox** — in `subagents/inbox.py`:

```python
@dataclass(frozen=True)
class InboxItem:
    kind: Literal["report", "note", "user"]   # "user": a message the user sent mid-turn (§5.3)
    text: str
    wakes: bool         # an idle owner is re-activated for it (§3.6 leftovers)
    source_id: str = ""  # the agent the item came from, or the queued message's id
    author: str = ""     # who wrote it, shown in the frame header (spec §3.10)
    notice_id: str = ""  # the agent_notices row it carries, for the main agent (§5.2)
```

- [ ] **Step 5: Storage** — in `_migrate`, after `chat_agents`:

```python
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_notices (
                notice_id               TEXT PRIMARY KEY,
                thread_id               TEXT NOT NULL,
                source_kind             TEXT NOT NULL,
                source_id               TEXT NOT NULL,
                kind                    TEXT NOT NULL,
                payload_json            TEXT NOT NULL,
                delivery                TEXT NOT NULL,
                created_at              TEXT NOT NULL,
                claimed_turn_id         TEXT,
                claimed_checkpoint_seq  INTEGER,
                delivered_at            TEXT
            );
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS agent_notices_by_thread ON agent_notices(thread_id)")
```

and the methods:

```python
    @staticmethod
    def _notice_from_row(row: sqlite3.Row) -> NoticeRecord:
        return NoticeRecord(
            notice_id=row["notice_id"], thread_id=row["thread_id"],
            source_kind=row["source_kind"], source_id=row["source_id"], kind=row["kind"],
            payload=json.loads(row["payload_json"]), delivery=row["delivery"],
            created_at=datetime.fromisoformat(row["created_at"]),
            claimed_turn_id=row["claimed_turn_id"],
            claimed_checkpoint_seq=row["claimed_checkpoint_seq"],
            delivered_at=(datetime.fromisoformat(row["delivered_at"])
                          if row["delivered_at"] else None))

    def insert_notice(self, record: NoticeRecord) -> None:
        self._conn.execute(
            "INSERT INTO agent_notices (notice_id, thread_id, source_kind, source_id, kind, "
            "payload_json, delivery, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record.notice_id, record.thread_id, record.source_kind, record.source_id,
             record.kind, json.dumps(record.payload), record.delivery,
             record.created_at.isoformat()))
        self._conn.commit()

    def unclaimed_notices(self, thread_id: str) -> list[NoticeRecord]:
        rows = self._conn.execute(
            "SELECT * FROM agent_notices WHERE thread_id = ? AND claimed_turn_id IS NULL "
            "ORDER BY created_at, rowid", (thread_id,)).fetchall()
        return [self._notice_from_row(r) for r in rows]

    def claim_notices(self, notice_ids: list[str], turn_id: str, checkpoint_seq: int) -> None:
        self._conn.executemany(
            "UPDATE agent_notices SET claimed_turn_id = ?, claimed_checkpoint_seq = ? "
            "WHERE notice_id = ? AND claimed_turn_id IS NULL",
            [(turn_id, checkpoint_seq, nid) for nid in notice_ids])
        self._conn.commit()

    def deliver_claimed_notices(self, thread_id: str, turn_id: str) -> list[NoticeRecord]:
        rows = self._conn.execute(
            "SELECT * FROM agent_notices WHERE thread_id = ? AND claimed_turn_id = ? "
            "AND delivered_at IS NULL", (thread_id, turn_id)).fetchall()
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE agent_notices SET delivered_at = ? WHERE thread_id = ? "
            "AND claimed_turn_id = ? AND delivered_at IS NULL", (now, thread_id, turn_id))
        self._conn.executemany(
            "UPDATE chat_agents SET report_delivered_at = ? WHERE agent_id = ?",
            [(now, r["source_id"]) for r in rows if r["source_kind"] == "agent"])
        self._conn.commit()
        return [self._notice_from_row(r) for r in rows]

    def release_claimed_notices(self, thread_id: str, turn_id: str) -> int:
        cur = self._conn.execute(
            "UPDATE agent_notices SET claimed_turn_id = NULL, claimed_checkpoint_seq = NULL "
            "WHERE thread_id = ? AND claimed_turn_id = ? AND delivered_at IS NULL",
            (thread_id, turn_id))
        self._conn.commit()
        return cur.rowcount

    def release_all_unpersisted_notices(self) -> int:
        """Startup (spec §5.2): a claim whose turn never persisted is retried."""
        cur = self._conn.execute(
            "UPDATE agent_notices SET claimed_turn_id = NULL, claimed_checkpoint_seq = NULL "
            "WHERE claimed_turn_id IS NOT NULL AND delivered_at IS NULL")
        self._conn.commit()
        return cur.rowcount

    def claimed_agent_sources(self, thread_id: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT source_id FROM agent_notices WHERE thread_id = ? "
            "AND source_kind = 'agent' AND claimed_turn_id IS NOT NULL",
            (thread_id,)).fetchall()
        return {r["source_id"] for r in rows}

    def _rewrite_messages(
        self, thread_id: str, edit: Callable[[list[dict[str, Any]]], bool],
    ) -> bool:
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return False
        messages: list[dict[str, Any]] = json.loads(row["messages_json"])
        if not edit(messages):
            return False
        self._conn.execute("UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
                           (json.dumps(messages), thread_id))
        self._conn.commit()
        return True

    def set_message_metadata(
        self, thread_id: str, message_id: str, updates: dict[str, Any],
    ) -> bool:
        def edit(messages: list[dict[str, Any]]) -> bool:
            for message in messages:
                if message.get("id") == message_id:
                    message.setdefault("metadata", {}).update(updates)
                    return True
            return False
        return self._rewrite_messages(thread_id, edit)

    def move_message_to_end(self, thread_id: str, message_id: str) -> bool:
        """A queued message is answered after the notice turn it arrived during (spec §5.3):
        same id, now after everything that turn wrote."""
        def edit(messages: list[dict[str, Any]]) -> bool:
            index = next((i for i, m in enumerate(messages) if m.get("id") == message_id), None)
            if index is None:
                return False
            messages.append(messages.pop(index))
            return True
        return self._rewrite_messages(thread_id, edit)
```

(Imports: `NoticeRecord` from `agentd.chat.models`; `Callable` from `collections.abc` if not already
imported.)

- [ ] **Step 6: TypeScript enums in lockstep** — a missing Zod value makes `getChatThread` throw for the whole
thread (spec §6). In `task-contracts.ts`, add `"notice", "agent_message", "team_created"` to the
`ChatMessageSchema` `type` enum; in `webview-ui/src/types.ts` add `| "notice" | "agent_message" |
"team_created"` to `ChatMsg.type`. (They render as nothing until Part 2D.) Then
`npm run -w @crucible/editor-client build && npm run typecheck`.

- [ ] **Step 7: Run** — `pytest tests/test_agent_notices_store.py tests/test_agent_store_v2.py --timeout=60`; expected exit=0.

- [ ] **Step 8: Commit** — `feat(chat): agent notices and the notice message types` (+ trailers)

---

### Task 2B.2: The notice router and exactly-once delivery

**Files:**
- Create: `services/agentd-py/agentd/subagents/notices.py`
- Modify: `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/main.py`
- Test: `services/agentd-py/tests/test_notice_delivery.py` (new)

**Interfaces:**
- Produces (`notices.py`):
  - `notice_body(notice: NoticeRecord) -> str` — status, changed files and the full report of an agent notice
  - `notice_author(notice: NoticeRecord) -> str` — `"<label> (<name>)"`
  - `estimate_tokens(text: str) -> int` — `len(text) // 4 + 1`
- Produces (`ChatController`):
  - `_main_inbox: dict[str, list[InboxItem]]` (per thread)
  - `_on_main_report(handle, result) -> None` — writes the notice and routes it (spec §5.2 paths 1–4)
  - `_drain_main(thread_id: str, turn_id: str) -> list[InboxItem]` — all notes and user messages plus at
    most one report; claims the reports' notices with the live turn and its checkpoint seq
  - `_rearm_notices(thread_id: str) -> None` — a stub until Task 2B.4 (`pass`, docstring naming 2B.4)
- Changes: `_route_report`'s main branch calls `_on_main_report`; `_wait_agents` for the main agent claims
  instead of stamping `report_delivered_at`; `_run_loop` delivers claims after each history write and releases
  leftovers in its `finally`; `reap_subagents` also calls `release_all_unpersisted_notices`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_notice_delivery.py`:

```python
"""Notices reach the main agent exactly once (spec §5.2)."""
import asyncio
from pathlib import Path

import pytest

from agentd.subagents.runtime import ChildResult
from tests.test_background_dispatch import DONE, _setup, _tool


def _report_rows(store, tid):  # type: ignore[no-untyped-def]
    return store._conn.execute(
        "SELECT * FROM agent_notices WHERE thread_id = ?", (tid,)).fetchall()


@pytest.mark.asyncio
async def test_wait_delivers_on_persist_and_writes_one_notice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = _report_rows(store, tid)
    assert row["delivered_at"] is not None and row["claimed_turn_id"] is not None
    assert store.list_agents(tid)[0].report_delivered_at is not None


@pytest.mark.asyncio
async def test_a_report_during_a_running_turn_arrives_at_an_iteration_top(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("list_directory", path="."), _tool("search_code", query="a"),
        _tool("search_code", query="b"), _tool("search_code", query="c"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    step = ctrl._reasoning.create_controller_step

    async def slow_step(*args, **kwargs):  # type: ignore[no-untyped-def]
        # A scripted step never suspends; a real provider call does, which is when a
        # background agent runs.
        await asyncio.sleep(0.02)
        return await step(*args, **kwargs)

    monkeypatch.setattr(ctrl._reasoning, "create_controller_step", slow_step)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    arrivals = [m for m in history if m.get("role") == "user"
                and "found it" in str(m.get("content", ""))]
    assert len(arrivals) == 1 and "New message:" in str(arrivals[0]["content"])
    [row] = _report_rows(store, tid)
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_a_report_with_no_turn_running_stays_undelivered(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [DONE], {})
    from agentd.chat.models import AgentRecord
    store.insert_agent(AgentRecord(agent_id="a1", thread_id=tid, turn_id="u", depth=1,
                                   name="explore", label="scout", prompt="p",
                                   status="completed", dispatcher_id="main",
                                   on_finish="notify"))
    handle = ctrl._handle_from_record(tid, "a1")
    ctrl._route_report(handle, ChildResult(status="completed", report="late", files_changed=[]))
    [row] = _report_rows(store, tid)
    assert row["claimed_turn_id"] is None and row["delivery"] == "notify"


@pytest.mark.asyncio
async def test_a_turn_that_never_persists_releases_its_claims(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [DONE], {})
    from datetime import UTC, datetime

    from agentd.chat.models import NoticeRecord
    store.insert_notice(NoticeRecord(notice_id="n1", thread_id=tid, source_kind="agent",
                                     source_id="a1", kind="agent_finished",
                                     payload={"report": "r"}, delivery="notify",
                                     created_at=datetime.now(UTC)))
    store.claim_notices(["n1"], "dead-turn", 0)
    ctrl.reap_subagents()
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n1"]
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: `notices.py`**

```python
"""Text for agent notices (spec §5)."""
from __future__ import annotations

from agentd.chat.models import NoticeRecord


def notice_author(notice: NoticeRecord) -> str:
    label = str(notice.payload.get("label") or notice.source_id)
    name = str(notice.payload.get("name") or "")
    return f"{label} ({name})" if name else label


def notice_body(notice: NoticeRecord) -> str:
    """The whole report, never truncated (v1 D8), with what the agent changed."""
    files = notice.payload.get("files_changed") or []
    lines = [f"status: {notice.payload.get('status', '')}"]
    if files:
        lines.append("files_changed: " + ", ".join(str(f) for f in files))
    lines.append("")
    lines.append(str(notice.payload.get("report", "")))
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    # The same rough rule the memory compactor's budget uses: about four characters a token.
    return len(text) // 4 + 1
```

- [ ] **Step 4: Controller — writing and routing** (replace the early `return` for the main dispatcher in
`_route_report` with a call to `_on_main_report(handle, result)`):

```python
    def _on_main_report(self, handle: AgentHandle, result: ChildResult) -> None:
        """Spec §5.2: write the notice, then the first path that applies."""
        thread_id = handle.thread_id
        record = self._store.get_agent(handle.agent_id)
        delivery = "wake" if record is not None and record.on_finish == "wake" else "notify"
        notice = NoticeRecord(
            notice_id=uuid4().hex, thread_id=thread_id, source_kind="agent",
            source_id=handle.agent_id, kind="agent_finished",
            payload={"label": handle.context.label, "name": handle.context.name,
                     "status": result.status, "report": result.report,
                     "files_changed": self._subtree_files(handle.agent_id)},
            delivery=delivery, created_at=datetime.now(UTC))
        self._store.insert_notice(notice)
        if handle.agent_id in self._waiting.get((thread_id, MAIN_AGENT_ID), set()):
            return                       # path 1: the waiting wait_agents claims it
        if thread_id in self._active_loops:
            self._main_inbox.setdefault(thread_id, []).append(InboxItem(
                kind="report", text=notice_body(notice), wakes=False,
                source_id=handle.agent_id, author=notice_author(notice),
                notice_id=notice.notice_id))
            return                       # path 2
        self._rearm_notices(thread_id)   # paths 3 and 4

    def _drain_main(self, thread_id: str, turn_id: str) -> list[InboxItem]:
        """All notes and user messages plus at most one report (spec §3.6)."""
        pending = self._main_inbox.get(thread_id, [])
        taken: list[InboxItem] = []
        kept: list[InboxItem] = []
        report_taken = False
        for item in pending:
            if item.kind == "report":
                if report_taken:
                    kept.append(item)
                    continue
                report_taken = True
            taken.append(item)
        self._main_inbox[thread_id] = kept
        claims = [i.notice_id for i in taken if i.notice_id]
        if claims:
            self._store.claim_notices(
                claims, turn_id, self._store.current_checkpoint_seq(thread_id))
        return taken

    def _rearm_notices(self, thread_id: str) -> None:
        """Arms a notice turn for undelivered `wake` notices — Task 2B.4."""
```

- [ ] **Step 5: Controller — the main agent's `wait_agents`** — in `_wait_agents`, compute `already` and the
delivery mark per caller:

```python
        claimed = (self._store.claimed_agent_sources(thread_id)
                   if caller_id == MAIN_AGENT_ID else set())
        already = {i for i in ids
                   if (mine[i].report_delivered_at is not None or i in claimed)
                   and mine[i].status not in LIVE_STATUSES}
```

and, where the entry with a report is built, replace `self._store.update_agent(agent_id,
report_delivered_at=now)` with:

```python
                if caller_id == MAIN_AGENT_ID:
                    self._claim_agent_notices(thread_id, agent_id)
                else:
                    self._store.update_agent(agent_id, report_delivered_at=now)
```

```python
    def _claim_agent_notices(self, thread_id: str, agent_id: str) -> None:
        """The main agent's wait result carries the report: its notice is claimed by the
        running turn and delivered when that turn's history is written (spec §5.2 path 1).
        An agent stopped before reporting has no notice; mark it directly."""
        turn_id = self._live_turns.get(thread_id)
        ids = [n.notice_id for n in self._store.unclaimed_notices(thread_id)
               if n.source_kind == "agent" and n.source_id == agent_id]
        if ids and turn_id is not None:
            self._store.claim_notices(ids, turn_id, self._store.current_checkpoint_seq(thread_id))
        else:
            self._store.update_agent(agent_id, report_delivered_at=datetime.now(UTC))
```

and where 2A.4 discards a child's inbox copies, add the main agent's twin:

```python
        if caller is not None:
            self._subagents.discard_reports(caller_id, delivered)
        else:
            # The same rule for the main agent's inbox (spec §5.2 path 1 before path 2).
            self._main_inbox[thread_id] = [
                i for i in self._main_inbox.get(thread_id, [])
                if not (i.kind == "report" and i.source_id in delivered)]
```

`already` also excludes active agents (Task 2A.4). (The test `test_a_second_wait_says_already_delivered`
from Task 2A.4 still holds: the first turn delivered.)

- [ ] **Step 6: Controller — persistence decides delivery.** In `_run_loop`:
  - pass `inbox_drain=partial(self._drain_main, thread_id, turn_id) if turn_id else None` to `loop.run`;
  - right after **each** of the three `self._store.set_controller_history(...)` calls (cancel branch, generic
    exception branch, normal path), add `self._store.deliver_claimed_notices(thread_id, turn_id)` when
    `turn_id` is set;
  - in the **cancel branch only**, after its optional persist, add
    `if turn_id: self._store.release_claimed_notices(thread_id, turn_id)` — a retry for anything the stopped
    turn claimed but never wrote. **Not in the `finally`:** the normal path persists and delivers *after*
    the try/finally, so a release there would undo every claim before it could be delivered (the dry run
    delivered nothing until this moved). The generic-exception branch falls through to the normal path;
  - the main inbox outlives `_run_loop` only until the turn ends: Task 2B.4 clears it at turn end.
- `reap_subagents`: add `released = self._store.release_all_unpersisted_notices()` and log it.

- [ ] **Step 7: Run** — `pytest tests/test_notice_delivery.py tests/test_background_dispatch.py --timeout=60`; expected exit=0.

- [ ] **Step 8: Commit** — `feat(chat): route agent reports to the main agent exactly once` (+ trailers)

---

### Task 2B.3: The first-turn seed, the fold and its size guard

**Files:**
- Modify: `services/agentd-py/agentd/subagents/notices.py`, `services/agentd-py/agentd/chat/controller.py`
- Test: `services/agentd-py/tests/test_notice_fold.py` (new); update tests that assert
  `artifact_seed_len == 0` or `seed_history is None` on a thread's first turn

**Interfaces:**
- Produces (`notices.py`):

```python
@dataclass(frozen=True)
class Fold:
    text: str                 # "While you were away:" block, "" when nothing was folded
    folded: list[str]         # notice ids folded into the text
    overflow: list[NoticeRecord]  # reports past the budget: delivered one per iteration


def build_fold(notices: list[NoticeRecord], max_tokens: int) -> Fold
def notice_fold_max_tokens() -> int   # CRUCIBLE_NOTICE_FOLD_MAX_TOKENS, default 8000
```

- Produces (`ChatController`): `_fold_notices(thread_id: str, turn_id: str, own_input: str) -> str` — claims
  what it folds, queues overflow into the main inbox, records them as **announced**, returns the user-message
  content; `_announced: dict[str, int]` (per-turn redirect count) and the combined terminal guard
  `_main_terminal_guard(thread_id) -> str | None`.

**Behavior (spec §5.2 path 3):**
- `handle_message` builds `seed_history = seed + [user message]` **always** (the stored seed may be empty) —
  unconditional, with sub-agents off too.
- The user message's content is `_fold_notices(thread_id, turn_id, turn_message)` in `handle_message`, and
  the answer in `resolve_clarify`; `resolve_mode`'s implement re-entry appends a user message carrying only
  the block when the fold is non-empty.
- Fold layout:
  ```
  While you were away:
  <<<agent-content author=scout (explore) kind=report>>>
  | …
  <<<end>>>
  Also finished: big-one (general-purpose) — their reports follow.

  <own input>
  ```
- While the main inbox holds reports, `answer`/`submit_changes` is redirected with `More agent reports are
  arriving — read them before answering.` — at most 5 times per turn; the unwaited-dispatch redirect (2A.5)
  is checked first.

- [ ] **Step 1: Write the failing tests**

```python
"""Folding undelivered notices into the next main turn (spec §5.2 path 3)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import NoticeRecord
from agentd.subagents.notices import build_fold
from tests.test_background_dispatch import _setup

ANSWER = {"type": "answer", "thought": "t", "answer": "ok"}


def _n(nid: str, report: str, label: str = "scout") -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id="t", source_kind="agent", source_id=f"a-{nid}",
                        kind="agent_finished",
                        payload={"label": label, "name": "explore", "status": "completed",
                                 "report": report, "files_changed": []},
                        delivery="notify", created_at=datetime.now(UTC))


def test_fold_respects_the_budget_without_truncating() -> None:
    fold = build_fold([_n("1", "short"), _n("2", "x" * 40_000, "big")], max_tokens=8000)
    assert fold.folded == ["1"] and [n.notice_id for n in fold.overflow] == ["2"]
    assert "While you were away:" in fold.text and "Also finished: big (explore)" in fold.text
    assert "x" * 100 not in fold.text


def test_empty_fold() -> None:
    assert build_fold([], max_tokens=8000).text == ""


@pytest.mark.asyncio
async def test_first_turn_seed_holds_the_user_message(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    await ctrl.handle_message(tid, "hello", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert history[0] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_next_turn_folds_and_delivers(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    n = _n("1", "the cache is cold")
    store.insert_notice(n.model_copy(update={"thread_id": tid}))
    await ctrl.handle_message(tid, "what happened?", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    first = str(history[0]["content"])
    assert first.startswith("While you were away:") and first.endswith("what happened?")
    assert store.unclaimed_notices(tid) == []
    row = store._conn.execute("SELECT delivered_at FROM agent_notices").fetchone()
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_overflow_reports_hold_the_turn_open_at_most_five_times(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_NOTICE_FOLD_MAX_TOKENS", "10")
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER] * 8, {})
    for i in range(7):
        store.insert_notice(_n(str(i), f"report {i} " + "y" * 200).model_copy(
            update={"thread_id": tid}))
    await ctrl.handle_message(tid, "status?", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    redirects = [m for m in history if "More agent reports are arriving" in str(m.get("content"))]
    assert len(redirects) == 5
    # Every report the turn did not read is offered again.
    delivered = store._conn.execute(
        "SELECT COUNT(*) AS c FROM agent_notices WHERE delivered_at IS NOT NULL").fetchone()["c"]
    assert delivered + len(store.unclaimed_notices(tid)) == 7
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: `build_fold`** (append to `notices.py`; imports `os`, `dataclass`, `frame`):

```python
def notice_fold_max_tokens() -> int:
    try:
        return max(0, int(os.environ.get("CRUCIBLE_NOTICE_FOLD_MAX_TOKENS", "8000")))
    except ValueError:
        return 8000


def build_fold(notices: list[NoticeRecord], max_tokens: int) -> Fold:
    """Reports up to the budget go into the message; the rest are named and follow one
    per iteration, never truncated (spec §5.2)."""
    if not notices:
        return Fold(text="", folded=[], overflow=[])
    blocks: list[str] = []
    folded: list[str] = []
    overflow: list[NoticeRecord] = []
    used = 0
    for notice in notices:
        block = frame(notice_author(notice), "report", notice_body(notice))
        cost = estimate_tokens(block)
        if notice.source_kind == "agent" and used + cost > max_tokens:
            overflow.append(notice)
            continue
        used += cost
        blocks.append(block)
        folded.append(notice.notice_id)
    lines = ["While you were away:", *blocks]
    if overflow:
        lines.append("Also finished: " + ", ".join(notice_author(n) for n in overflow)
                     + " — their reports follow.")
    return Fold(text="\n".join(lines), folded=folded, overflow=overflow)
```

- [ ] **Step 4: Controller**

```python
    def _fold_notices(self, thread_id: str, turn_id: str, own_input: str) -> str:
        """The user message that opens a main turn: undelivered notices, then the turn's own
        input (spec §5.2 path 3). Folded notices are claimed by this turn; overflow is
        queued into the main inbox and counted as announced."""
        if not is_subagents_enabled():
            return own_input
        fold = build_fold(self._store.unclaimed_notices(thread_id), notice_fold_max_tokens())
        if not fold.text:
            return own_input
        seq = self._store.current_checkpoint_seq(thread_id)
        self._store.claim_notices(fold.folded, turn_id, seq)
        inbox = self._main_inbox.setdefault(thread_id, [])
        for notice in fold.overflow:
            inbox.append(InboxItem(kind="report", text=notice_body(notice), wakes=False,
                                   source_id=notice.source_id, author=notice_author(notice),
                                   notice_id=notice.notice_id))
        return f"{fold.text}\n\n{own_input}" if own_input else fold.text

    def _main_terminal_guard(self, thread_id: str) -> str | None:
        redirect = self._unwaited_guard(thread_id)
        if redirect is not None:
            return redirect
        if not any(i.kind == "report" for i in self._main_inbox.get(thread_id, [])):
            return None
        count = self._announced.get(thread_id, 0)
        if count >= _MAX_ARRIVAL_REDIRECTS:
            return None   # the rest return to undelivered at turn end
        self._announced[thread_id] = count + 1
        return "More agent reports are arriving — read them before answering."
```

with `_MAX_ARRIVAL_REDIRECTS = 5` at module level; `self._announced: dict[str, int] = {}` in `__init__`,
reset to 0 in `_run_loop` next to `_turn_dispatches`, popped in the same `finally`; and
`terminal_guard=partial(self._main_terminal_guard, thread_id)` replaces 2A.5's
`partial(self._unwaited_guard, thread_id)`.

  The fold claims with the turn's `turn_id`, so `_run_loop`'s delivery and release (Task 2B.2) cover it. The
  call sites:
  - **`handle_message`**: replace
    `seed_history = (seed + [{"role": "user", "content": turn_message}]) if seed else None` with
    ```python
        # Always a list, even on a thread's first turn (spec §5.2): the fold needs a user
        # message in persisted history, and a first message was never in it before.
        seed_history = [*seed, {"role": "user",
                                "content": self._fold_notices(thread_id, turn_id, turn_message)}]
    ```
    (`_seed_for` returns `[]` for a thread with no stored history — check; if it returns `None`, use
    `[*(seed or []), …]`.)
  - **`resolve_clarify`**: the appended user message's content becomes
    `self._fold_notices(thread_id, turn_id, answer)`; move `turn_id = uuid4().hex` above it.
  - **`resolve_mode`** implement: move `turn_id = uuid4().hex` above the seed and add
    ```python
            block = self._fold_notices(thread_id, turn_id, "")
            if block:
                seed_history = [*(seed_history or []), {"role": "user", "content": block}]
    ```

- [ ] **Step 5: Check first-turn tests.** Run `pytest -k "controller or chat or rewind or artifact or memory or seed" --timeout=120`. The dry run found no test depending on the old first-turn seed (630
passed); fix only what fails, giving a first turn's seed length 1 and `history[0]` the user message.

- [ ] **Step 6: Run** — `pytest tests/test_notice_fold.py tests/test_notice_delivery.py tests/test_unwaited_dispatch.py --timeout=60`; then the Step 5 subset; expected exit=0 for both.

- [ ] **Step 7: Commit** — `feat(chat): fold waiting notices into the next main turn` (+ trailers)

---

### Task 2B.4: Notice turns

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/api/routes.py`
- Modify: `services/agentd-py/agentd/subagents/config.py` (`subagent_max_wake_turns`)
- Test: `services/agentd-py/tests/test_notice_turns.py` (new)

**Interfaces:**
- Produces (`ChatController`):
  - `async handle_notices(thread_id: str) -> None` — the notice turn (spec §5.3)
  - `turn_kind(thread_id: str) -> str | None`
  - `_rearm_notices` (real body), `_fire_notice_turn(thread_id)`, `_notice_blocked(thread_id) -> bool`
  - `_end_turn(thread_id)` — called from `_run_turn`'s `finally`, no `await`
  - state: `_turn_kinds: dict[str, str]`, `_wake_timers: dict[str, asyncio.TimerHandle]`,
    `_wake_turns: dict[str, int]`, `_wakes_suppressed: set[str]`, `_wake_cap_noted: set[str]`,
    `_plan_mode: bool` (set by `handle_message` when `plan_mode` is given; Part 2C makes it process-level)
- Produces (`config.py`): `subagent_max_wake_turns() -> int` (`CRUCIBLE_SUBAGENT_MAX_WAKE_TURNS`, default 10);
  `NOTICE_BATCH_SEC = 2.0`.
- Produces (route): `/live` sets `live.turn_kind = _chat_agent.turn_kind(thread_id)` when the controller has it.

**Behavior:**
- `_rearm_notices(thread_id)`: when not blocked and an unclaimed `wake` notice exists, arm one 2-second timer
  per thread (`loop.call_later(NOTICE_BATCH_SEC, self._fire_notice_turn, thread_id)`) unless one is armed.
  Blocked = a turn is running, a main-agent gate is pending (`gate.agent is None`), wakes are suppressed, or
  the cap is reached. At the cap, write one breadcrumb per streak: `🔔 Agents keep finishing — I'll tell you
  about them at your next message.`
- `_fire_notice_turn`: pop the timer, re-check blockers, `launch_turn(thread_id, self.handle_notices(thread_id),
  channel_id=f"chat:{thread_id}")`.
- `handle_message` (and Task 2B.5's `handle_queued_message`) cancels the thread's timer, resets
  `_wake_turns`, clears `_wakes_suppressed` and `_wake_cap_noted`, and sets `_turn_kinds[thread] = "user"`.
- `handle_notices`: `_turn_kinds[thread] = "notice"`; increments `_wake_turns`; writes the marker message
  (`type="notice"`, `metadata.target`), broadcasts `{"type": "notice", "payload": {"message": …}}` on the chat
  channel; opens a checkpoint anchored to the marker; `seed_history = [*seed, {"role": "user", "content":
  fold}]` where fold = `_fold_notices(thread_id, turn_id, _NOTICE_TURN_INPUT)`; runs `_run_loop` in `PLAN` when
  `self._plan_mode`, else `ACTIVE`, with the thread's last review value; `_finish`.
- `stop_turn` on a notice turn adds the thread to `_wakes_suppressed` **before** cancelling.
- `_end_turn`: pops `_turn_kinds`, clears the main inbox's reports and notes (their rows are unclaimed, so
  they return to undelivered), leaves queued user messages for Task 2B.5, then `_rearm_notices`.
- `resolve_mode`'s non-implement branches (no turn started) call `_rearm_notices` before `chat_done` — that
  covers "a main-agent gate resolved without starting a turn".
- After a restart nothing arms at startup (undelivered `wake` notices wait like `notify`): only
  `_on_main_report` and `_end_turn` arm.

- [ ] **Step 1: Write the failing tests**

```python
"""Notice turns (spec §5.3)."""
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import NoticeRecord, PendingGate
from agentd.chat.rewind import RewindStore
from tests.test_background_dispatch import _setup

ANSWER = {"type": "answer", "thought": "t", "answer": "Your agent finished."}


def _wake(tid: str, nid: str = "n1") -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id=tid, source_kind="agent", source_id="a1",
                        kind="agent_finished",
                        payload={"label": "migrate", "name": "general-purpose",
                                 "status": "completed", "report": "done", "files_changed": []},
                        delivery="wake", created_at=datetime.now(UTC))


async def _settle(ctrl, tid: str) -> None:  # type: ignore[no-untyped-def]
    for _ in range(400):
        await asyncio.sleep(0.01)
        if tid not in ctrl._wake_timers and tid not in ctrl._active_turns:
            return


@pytest.mark.asyncio
async def test_a_wake_notice_starts_a_notice_turn(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    # The shared test controller has no rewind store; a notice turn opens a checkpoint when one exists.
    ctrl._rewind = RewindStore(store, Path(ctrl._workspace_path))
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert messages[0].type == "notice" and messages[0].metadata["target"] == {"agent_id": "a1"}
    assert store.get_checkpoint_by_anchor(tid, messages[0].id) is not None
    row = store._conn.execute("SELECT delivered_at FROM agent_notices").fetchone()
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_a_pending_main_gate_blocks_and_suppression_blocks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    store.insert_notice(_wake(tid))
    store.add_controller_gate(tid, PendingGate(gate_id="g", kind="clarify", payload={}))
    ctrl._rearm_notices(tid)
    assert tid not in ctrl._wake_timers
    store.clear_main_gates(tid)
    ctrl._wakes_suppressed.add(tid)
    ctrl._rearm_notices(tid)
    assert tid not in ctrl._wake_timers


@pytest.mark.asyncio
async def test_the_wake_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_WAKE_TURNS", "1")
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER, ANSWER], {})
    store.insert_notice(_wake(tid, "n1"))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    store.insert_notice(_wake(tid, "n2"))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n2"]
    crumbs = [m for m in store.get_thread(tid).messages  # type: ignore[union-attr]
              if "next message" in m.content]
    assert len(crumbs) == 1


@pytest.mark.asyncio
async def test_a_user_message_takes_the_batch(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)          # the real 2-second window
    assert tid in ctrl._wake_timers
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}")
    assert tid not in ctrl._wake_timers
    assert not any(m.type == "notice" for m in store.get_thread(tid).messages)  # type: ignore[union-attr]
    assert store.unclaimed_notices(tid) == []
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Implement.** Module constants in `controller.py`:

```python
_NOTICE_TURN_INPUT = ("No new message from the user. These agents finished while you were "
                      "away: tell the user what they found or changed, and act on it if "
                      "the user's earlier request needs that.")
_WAKE_CAP_TEXT = "🔔 Agents keep finishing — I'll tell you about them at your next message."
```

The methods:

```python
    def turn_kind(self, thread_id: str) -> str | None:
        return self._turn_kinds.get(thread_id) if thread_id in self._active_turns else None

    def _notice_blocked(self, thread_id: str) -> bool:
        if thread_id in self._active_turns or thread_id in self._wakes_suppressed:
            return True
        thread = self._store.get_thread(thread_id)
        if thread is None:
            return True
        return any(g.agent is None for g in thread.pending_controller_gates)

    def _rearm_notices(self, thread_id: str) -> None:
        """Arm a notice turn for undelivered wake notices (spec §5.2 re-arming, §5.3)."""
        if thread_id in self._wake_timers or self._notice_blocked(thread_id):
            return
        if not any(n.delivery == "wake" for n in self._store.unclaimed_notices(thread_id)):
            return
        if self._wake_turns.get(thread_id, 0) >= subagent_max_wake_turns():
            if thread_id not in self._wake_cap_noted:
                self._wake_cap_noted.add(thread_id)
                self._write_breadcrumb(thread_id, f"chat:{thread_id}", _WAKE_CAP_TEXT)
            return
        self._wake_timers[thread_id] = asyncio.get_running_loop().call_later(
            NOTICE_BATCH_SEC, self._fire_notice_turn, thread_id)

    def _fire_notice_turn(self, thread_id: str) -> None:
        self._wake_timers.pop(thread_id, None)
        if self._notice_blocked(thread_id):
            return
        channel_id = f"chat:{thread_id}"
        self._broadcaster.clear_replay(channel_id)
        self.launch_turn(thread_id, self.handle_notices(thread_id), channel_id=channel_id)

    def _cancel_wake_timer(self, thread_id: str) -> None:
        timer = self._wake_timers.pop(thread_id, None)
        if timer is not None:
            timer.cancel()

    async def handle_notices(self, thread_id: str) -> None:
        """A main turn whose input is the undelivered notices (spec §5.3)."""
        thread = self._store.get_thread(thread_id)
        pending = self._store.unclaimed_notices(thread_id)
        if thread is None or not pending:
            return
        self._turn_kinds[thread_id] = "notice"
        self._accepting_queued.add(thread_id)            # Task 2B.5
        self._wake_turns[thread_id] = self._wake_turns.get(thread_id, 0) + 1
        channel_id = f"chat:{thread_id}"
        sources = [n for n in pending if n.source_kind == "agent"]
        target = {"agent_id": sources[0].source_id} if len(sources) == 1 else {}
        text = (f"🔔 Agent \"{sources[0].payload.get('label', '')}\" finished — main agent woke"
                if len(sources) == 1 else
                f"🔔 {len(pending)} agents finished — main agent woke")
        marker = ChatMessage(role="agent", content=text, type="notice",
                             metadata={"target": target})
        marker_id = self._store.append_message(thread_id, marker)
        self._broadcaster.broadcast(channel_id, {"type": "notice", "payload": {
            "message": marker.model_copy(update={"id": marker_id}).model_dump(mode="json")}})
        turn_id = uuid4().hex
        if self._rewind is not None and marker_id is not None:
            self._rewind.open_checkpoint(
                thread_id, marker_id, turn_id, thread=thread,
                memory_anchor_md=self._memory_harness.anchor_markdown(thread_id))
        self._notice_markers[thread_id] = marker_id or ""   # Task 2B.5's checkpoint_anchor
        review = self._step_review_by_thread.get(thread_id)
        seed_history = [*(self._seed_for(thread_id) or []), {
            "role": "user",
            "content": self._fold_notices(thread_id, turn_id, _NOTICE_TURN_INPUT)}]
        outcome = await self._run_loop(
            thread_id, channel_id, _NOTICE_TURN_INPUT, seed_history=seed_history,
            step_review=review, phase="PLAN" if self._plan_mode else "ACTIVE",
            turn_id=turn_id)
        await self._finish(thread_id, channel_id, outcome, step_review=review, turn_id=turn_id)

    def _end_turn(self, thread_id: str) -> None:
        """Turn end, with no await (spec §5.3): undrained reports return to undelivered
        (their rows were never claimed), queued user messages are answered (Task 2B.5),
        and wake notices may arm the next notice turn."""
        self._turn_kinds.pop(thread_id, None)
        self._accepting_queued.discard(thread_id)
        leftover = self._main_inbox.pop(thread_id, [])
        self._answer_queued(thread_id, [i for i in leftover if i.kind == "user"])
        self._rearm_notices(thread_id)
```

`_answer_queued` and `_accepting_queued`/`_notice_markers` are defined in Task 2B.5; until then add
`_accepting_queued: set[str] = set()`, `_notice_markers: dict[str, str] = {}` in `__init__` and a stub
`def _answer_queued(self, thread_id: str, items: list[InboxItem]) -> None: """Task 2B.5."""`.

Wire-ups:
- `_run_turn`'s `finally`: after `self._active_turns.pop(thread_id, None)` add `self._end_turn(thread_id)`.
- `handle_message`, at the top (after the thread check):
  ```python
        self._cancel_wake_timer(thread_id)
        self._wake_turns.pop(thread_id, None)
        self._wakes_suppressed.discard(thread_id)
        self._wake_cap_noted.discard(thread_id)
        self._turn_kinds[thread_id] = "user"
        if plan_mode is not None:
            self._plan_mode = plan_mode
  ```
- `stop_turn`: before `task.cancel()`, `if self._turn_kinds.get(thread_id) == "notice":
  self._wakes_suppressed.add(thread_id)`.
- `resolve_mode`: before its final `chat_done` broadcast, `self._rearm_notices(thread_id)`.
- Route `/live`: after `live.turn_active = …`:
  ```python
            _turn_kind = getattr(_chat_agent, "turn_kind", None)
            if _turn_kind is not None:
                live.turn_kind = _turn_kind(thread_id)
  ```

- [ ] **Step 4: Run** — `pytest tests/test_notice_turns.py tests/test_notice_fold.py tests/test_notice_delivery.py --timeout=60`; expected exit=0.

- [ ] **Step 5: Commit** — `feat(chat): notice turns for agents that finish while you are away` (+ trailers)

---

### Task 2B.5: User messages during a notice turn are queued

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/chat/controller_loop.py`,
  `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_queued_messages.py` (new)

**Interfaces:**
- Produces (`ChatController`):
  - `accepts_queued(thread_id: str) -> bool`
  - `queue_message(thread_id, message, *, message_id, step_review, plan_mode, mentioned_files,
    forced_skills) -> str` — persists the message, returns its id
  - `async handle_queued_message(thread_id: str, message_id: str) -> None`
  - `_answer_queued(thread_id, items)` (real body); `_queued: dict[str, dict[str, object]]` (per message id:
    the turn options)
- Produces (loop): a drained `user` item is appended as `{"role": "user", "content": item.text}` — the user's
  own words, never framed as agent content.
- Produces (route): `POST /message` during a notice turn that accepts → `202 {"queued": true, "message_id"}`.

**Behavior (spec §5.3):**
1. The route checks `accepts_queued` (a notice turn is running and has not reached its end) in the same
   synchronous step as the 409 check.
2. `queue_message` persists the message (no checkpoint), resets the wake counter and suppression as a user
   message does, and appends `InboxItem(kind="user", text=turn_message, wakes=False, source_id=message_id)`
   to the main inbox.
3. `_drain_main` switches the turn to a user turn when it drains a `user` item (`_turn_kinds[thread] = "user"`,
   `accepting_queued` cleared — a user turn queues nothing) and stamps the message's
   `metadata.checkpoint_anchor` with the notice marker id, so rewinding to it rewinds the whole notice turn.
4. `_end_turn` hands any undrained user item to `_answer_queued`, which launches
   `handle_queued_message` (one per message, in order: the first launches; the rest are re-queued into its
   inbox — at most one turn per thread).
5. `handle_queued_message` moves the message to the end of the transcript, opens its checkpoint anchored to
   the message id, and runs the turn exactly as `handle_message` does after persisting.

- [ ] **Step 1: Write the failing tests**

```python
"""A message sent during a notice turn is queued and always answered (spec §5.3)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.rewind import RewindStore
from tests.test_background_dispatch import _setup, _tool
from tests.test_notice_turns import _settle, _wake


@pytest.mark.asyncio
async def test_queued_message_is_drained_into_the_notice_turn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    gate = asyncio.Event()
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("list_directory", path="."),
        {"type": "answer", "thought": "t", "answer": "done"}], {})
    original = ctrl._drain_main

    def drain(thread_id: str, turn_id: str):  # type: ignore[no-untyped-def]
        if not gate.is_set():
            mid = ctrl.queue_message(thread_id, "also do X", message_id="m-q", step_review=None,
                                     plan_mode=None, mentioned_files=None, forced_skills=None)
            assert mid == "m-q"
            gate.set()
        return original(thread_id, turn_id)

    monkeypatch.setattr(ctrl, "_drain_main", drain)
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert {"role": "user", "content": "also do X"} in history
    queued = next(m for m in store.get_thread(tid).messages if m.id == "m-q")  # type: ignore[union-attr]
    marker = next(m for m in store.get_thread(tid).messages if m.type == "notice")  # type: ignore[union-attr]
    assert queued.metadata["checkpoint_anchor"] == marker.id


@pytest.mark.asyncio
async def test_an_undrained_queued_message_gets_its_own_turn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        {"type": "answer", "thought": "t", "answer": "notice reply"},
        {"type": "answer", "thought": "t", "answer": "queued reply"}], {})

    queued: list[str] = []

    async def finish_then_queue(*args, **kwargs):  # type: ignore[no-untyped-def]
        # Once, at the notice turn's end: after its last drain, before _end_turn. (Queueing
        # on every turn chains queued turns forever.)
        if not queued:
            queued.append(ctrl.queue_message(
                tid, "later", message_id="m-late", step_review=None, plan_mode=None,
                mentioned_files=None, forced_skills=None))
        return await real_finish(*args, **kwargs)

    real_finish = ctrl._finish
    monkeypatch.setattr(ctrl, "_finish", finish_then_queue)
    ctrl._rewind = RewindStore(store, Path(ctrl._workspace_path))
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    await _settle(ctrl, tid)
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    ids = [m.id for m in messages]
    assert ids.index("m-late") > ids.index(next(m.id for m in messages if m.type == "notice"))
    assert store.get_checkpoint_by_anchor(tid, "m-late") is not None
    assert messages[-1].content == "queued reply"


def test_a_user_turn_does_not_accept_queued(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _store, tid = _setup(tmp_path, monkeypatch, [], {})
    ctrl._turn_kinds[tid] = "user"
    assert not ctrl.accepts_queued(tid)
```

Route test, appended to `tests/test_subagent_routes.py` (add `import asyncio` at its top):

```python
@pytest.mark.asyncio
async def test_message_during_a_notice_turn_is_202(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    never = asyncio.Event()
    ctrl._active_turns[tid] = asyncio.create_task(never.wait())
    ctrl._turn_kinds[tid] = "notice"
    ctrl._accepting_queued.add(tid)
    async with _client(tmp_path, ctrl) as client:
        response = await client.post(f"/v1/chat/threads/{tid}/message",
                                     json={"content": "hi", "message_id": "m1"})
    assert response.status_code == 202
    assert response.json() == {"queued": True, "message_id": "m1"}
    ctrl._active_turns[tid].cancel()
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Loop** — in `_iterate`'s inbox block:

```python
            if inbox_drain is not None:
                for item in inbox_drain():
                    if item.kind == "user":
                        # The user's own words (spec §5.3), never framed as agent content.
                        history.append({"role": "user", "content": item.text})
                        continue
                    author = item.author or item.source_id or "agent"
                    history.append({"role": "user", "content": "New message:\n"
                                    + frame(author, item.kind, item.text)})
```

- [ ] **Step 4: Controller.** Import `ChatThread` from `agentd.chat.models`. Split `handle_message` so a
queued message reuses everything after the append: `_user_spoke` (what any user message does to the wake
loop, the turn kind, Plan Mode and the main agent's cards — the queued turn must also supersede main gates)
and `_run_user_turn` (checkpoint → seed + fold → loop → finish). Add, at module level, the @-mention
helper both callers share:

```python
def _with_mentioned_files(message: str, mentioned_files: list[dict[str, str]] | None) -> str:
    """@-mentions are turn-scoped: the model sees the referenced file content only for the
    turn the message starts (it feeds that turn's goal). The persisted message keeps the
    short original text, tagged with the paths only."""
    blocks = "\n\n".join(
        f"### {f['path']}\n```\n{f['content']}\n```"
        for f in mentioned_files or [] if f.get("path"))
    return f"{message}\n\n---\nReferenced files:\n{blocks}" if blocks else message
```

and replace `handle_message` (from its `def` to `_run_loop`) with:

```python
    async def handle_message(
        self, thread_id: str, message: str, channel_id: str, step_review: bool | None = None,
        forced_skills: list[str] | None = None,
        mentioned_files: list[dict[str, str]] | None = None,
        plan_mode: bool | None = None,
        message_id: str | None = None,
    ) -> None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id!r} not found")
        self._user_spoke(thread_id, plan_mode)
        # Auto-name the thread from its first user message (mirrors ChatAgent).
        if not any(m.role == "user" for m in thread.messages):
            title = message.strip().replace("\n", " ")[:50]
            self._store.update_title(thread_id, title)
            self._broadcaster.broadcast(channel_id, {
                "type": "thread_title_updated",
                "payload": {"thread_id": thread_id, "title": title},
            })
        mentioned_paths = [f["path"] for f in mentioned_files or [] if f.get("path")]
        # The client may supply the id: the webview echoes the user's message
        # optimistically, before this call persists it, so letting it choose the id keeps
        # the echoed bubble and the stored message in agreement — otherwise the echo has
        # no rewind anchor and the affordance is missing on the message you just sent
        # until the thread is reloaded. None (any other client) still gets a fresh uuid.
        anchor_message_id = self._store.append_message(thread_id, ChatMessage(
            role="user", content=message, id=message_id,
            metadata={"mentioned_files": mentioned_paths} if mentioned_paths else {}))
        await self._run_user_turn(
            thread, thread_id, anchor_message_id, _with_mentioned_files(message, mentioned_files),
            channel_id, step_review=step_review, plan_mode=plan_mode,
            forced_skills=forced_skills)

    def _user_spoke(self, thread_id: str, plan_mode: bool | None) -> None:
        """A user message takes any batch of wake notices and resets the wake loop
        (spec §5.3), and supersedes only the MAIN agent's cards (spec §3.8): a late decision
        on a superseded card hits `gate is None` and no-ops. A sub-agent's or a team's gate
        belongs to work that keeps running, so it stays."""
        self._cancel_wake_timer(thread_id)
        self._wake_turns.pop(thread_id, None)
        self._wakes_suppressed.discard(thread_id)
        self._wake_cap_noted.discard(thread_id)
        self._turn_kinds[thread_id] = "user"
        if plan_mode is not None:
            self._plan_mode = plan_mode
        self._store.clear_main_gates(thread_id)

    async def _run_user_turn(
        self, thread: ChatThread, thread_id: str, anchor_message_id: str | None,
        turn_message: str, channel_id: str, *, step_review: bool | None,
        plan_mode: bool | None, forced_skills: list[str] | None,
    ) -> None:
        """The turn a persisted user message starts (handle_message, handle_queued_message).
        `thread` is the pre-turn state a rewind to this message restores."""
        # One id for this turn's in-flight pills message AND its rewind checkpoint.
        turn_id = uuid4().hex
        if self._rewind is not None and anchor_message_id is not None:
            self._rewind.open_checkpoint(
                thread_id, anchor_message_id, turn_id, thread=thread,
                memory_anchor_md=self._memory_harness.anchor_markdown(thread_id))
        # A new turn invalidates any prior in-flight pills marker (a stopped/orphaned
        # earlier turn). Drop it so this turn's switch-back dedup is scoped to its own
        # message (finding 5); the orphan's pills stay as a normal message.
        self._store.clear_inflight_markers(thread_id)
        # Remember this turn's review toggle so a propose_mode → "implement" re-entry
        # (resolved via /mode-decision, which carries no step_review) honors it.
        self._step_review_by_thread[thread_id] = step_review
        seed = self._seed_for(thread_id)
        # Always a list, even on a thread's first turn (spec §5.2): the fold needs a user
        # message in persisted history, and a first message was never in it before.
        seed_history = [*seed, {"role": "user",
                                "content": self._fold_notices(thread_id, turn_id, turn_message)}]
        # A plain message always starts fresh, in the phase the sticky Plan Mode toggle
        # selects (NEW-I6 — this MUST be the plan_mode computation, never a stray None).
        resume_phase = "PLAN" if plan_mode else "ACTIVE"
        outcome = await self._run_loop(
            thread_id, channel_id, turn_message, seed_history=seed_history,
            step_review=step_review, phase=resume_phase, turn_id=turn_id,
            # A plain message is ALWAYS a fresh entry, never a resume (see resolve_clarify):
            # passing (resume_phase == "ACTIVE") would suppress the C1b entry hint on every
            # follow-up turn in a thread.
            edit_is_resume=False, forced_skills=forced_skills)
        await self._finish(thread_id, channel_id, outcome, step_review, turn_id=turn_id)

    def accepts_queued(self, thread_id: str) -> bool:
        return (thread_id in self._active_turns and thread_id in self._accepting_queued
                and self._turn_kinds.get(thread_id) == "notice")

    def queue_message(
        self, thread_id: str, message: str, *, message_id: str | None,
        step_review: bool | None, plan_mode: bool | None,
        mentioned_files: list[dict[str, str]] | None, forced_skills: list[str] | None,
    ) -> str:
        """Spec §5.3: persisted now, no checkpoint yet; delivered at the notice turn's next
        iteration top, or answered by its own turn when the notice turn ends first."""
        turn_message = _with_mentioned_files(message, mentioned_files)
        paths = [f["path"] for f in mentioned_files or [] if f.get("path")]
        stored_id = self._store.append_message(thread_id, ChatMessage(
            role="user", content=message, id=message_id,
            metadata={"mentioned_files": paths} if paths else {}))
        assert stored_id is not None
        self._wake_turns.pop(thread_id, None)
        self._wakes_suppressed.discard(thread_id)
        self._wake_cap_noted.discard(thread_id)
        if plan_mode is not None:
            self._plan_mode = plan_mode
        self._queued[stored_id] = {"turn_message": turn_message, "step_review": step_review,
                                   "forced_skills": forced_skills}
        self._main_inbox.setdefault(thread_id, []).append(InboxItem(
            kind="user", text=turn_message, wakes=False, source_id=stored_id))
        return stored_id

    async def handle_queued_message(self, thread_id: str, message_id: str) -> None:
        options = self._queued.pop(message_id, {})
        if self._store.get_thread(thread_id) is None:
            return
        self._user_spoke(thread_id, None)
        # After everything the notice turn wrote: the checkpoint snapshot, the files and the
        # transcript position then agree (spec §5.3).
        self._store.move_message_to_end(thread_id, message_id)
        thread = self._store.get_thread(thread_id)
        assert thread is not None
        step_review = options.get("step_review")
        forced = options.get("forced_skills")
        await self._run_user_turn(
            thread, thread_id, message_id, str(options.get("turn_message", "")),
            f"chat:{thread_id}",
            step_review=step_review if isinstance(step_review, bool) else None,
            plan_mode=self._plan_mode,
            forced_skills=list(forced) if isinstance(forced, list) else None)
```

(`_queued: dict[str, dict[str, object]] = {}` joins the notice-turn state in `__init__`; when Part 2C lands,
`handle_message` and `queue_message` also set the review control from `step_review` — see Task 2C.1.)
`_answer_queued` replaces the 2B.4 stub:

```python
    def _answer_queued(self, thread_id: str, items: list[InboxItem]) -> None:
        """Called from _end_turn (no await): the first undrained queued message gets its own
        turn; any others wait in that turn's inbox, drained at its first iteration top."""
        if not items:
            return
        first, rest = items[0], items[1:]
        self._main_inbox[thread_id] = list(rest)
        self._turn_kinds[thread_id] = "user"
        self.launch_turn(
            thread_id, self.handle_queued_message(thread_id, first.source_id),
            channel_id=f"chat:{thread_id}")
```

In `_drain_main`, after computing `taken`:

```python
        for item in taken:
            if item.kind == "user":
                # The user spoke: the rest of this turn is theirs (spec §5.3).
                self._turn_kinds[thread_id] = "user"
                self._accepting_queued.discard(thread_id)
                self._queued.pop(item.source_id, None)
                marker = self._notice_markers.get(thread_id)
                if marker:
                    self._store.set_message_metadata(
                        thread_id, item.source_id, {"checkpoint_anchor": marker})
```

`_end_turn` also pops `_notice_markers[thread_id]`.

**Rewind anchor.** `RewindStore.restore(thread_id, anchor_message_id)` resolves the checkpoint by anchor; a
queued message delivered into a notice turn has no checkpoint of its own. In the rewind route (and its
preview), resolve the anchor first: if the message's `metadata.checkpoint_anchor` is set, use that id. Add the
test:

```python
def test_rewind_anchor_follows_checkpoint_anchor(tmp_path: Path) -> None:
    from agentd.chat.models import ChatMessage
    from agentd.chat.storage import ChatThreadStore
    from agentd.chat.rewind import resolve_rewind_anchor

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.append_message(tid, ChatMessage(role="user", content="q", id="m1",
                                          metadata={"checkpoint_anchor": "marker"}))
    assert resolve_rewind_anchor(store, tid, "m1") == "marker"
    assert resolve_rewind_anchor(store, tid, "other") == "other"
```

with, in `chat/rewind.py`:

```python
def resolve_rewind_anchor(store: ChatThreadStore, thread_id: str, message_id: str) -> str:
    """A message queued into a notice turn shares that turn's checkpoint (spec §5.3)."""
    thread = store.get_thread(thread_id)
    for message in thread.messages if thread is not None else []:
        if message.id == message_id:
            return str(message.metadata.get("checkpoint_anchor") or message_id)
    return message_id
```

and the rewind and rewind-preview routes call it before using the anchor.

- [ ] **Step 5: Route** — in `post_chat_message`, inside `if _active is not None:` replace the 409 block with:

```python
                if thread_id in _active:
                    _accepts = getattr(_chat_agent, "accepts_queued", None)
                    if _accepts is not None and _accepts(thread_id):
                        # Spec §5.3: a notice turn queues the message instead of refusing it.
                        # Check and enqueue have no await between them.
                        queued_id = _chat_agent.queue_message(  # type: ignore[attr-defined]
                            thread_id, message, message_id=_message_id,
                            step_review=step_review, plan_mode=plan_mode,
                            mentioned_files=mentioned_files, forced_skills=forced_skills)
                        return JSONResponse(status_code=202,
                                            content={"queued": True, "message_id": queued_id})
                    raise HTTPException(
                        status_code=409,
                        detail=f"Thread {thread_id} already has a turn in progress")
```

(The handler's return annotation becomes `StreamingResponse | JSONResponse`; import `JSONResponse` from
`fastapi.responses` at the top of `routes.py` if it is not imported.)

- [ ] **Step 6: Run** — `pytest tests/test_queued_messages.py tests/test_notice_turns.py tests/test_subagent_routes.py tests/test_rewind_routes.py --timeout=60`; expected exit=0.

- [ ] **Step 7: Commit** — `feat(chat): queue messages sent during a notice turn` (+ trailers)

---

### Task 2B.6: Part 2B checkpoint

- [ ] **Step 1:** Full Python suite with `--timeout=120` (redirected, read `$?` and the tail) — all pass except the
known timing flakes.
- [ ] **Step 2:** `npm run -w @crucible/editor-client build && npm run typecheck && npm run test` — green.
- [ ] **Step 3:** `ruff check` the changed Python files.

---

# Part 2C — Preferences and rewind

**Goal:** "Review each edit" and Plan Mode are global in the extension, so the backend keeps one
process-level value of each that reaches every thread, turn and background activation (spec §5.4). Rewind
refuses while any agent runs, deletes the notices of agents it deletes, re-offers notices it un-delivers, and
tells surviving agents which of their files it restored (spec §8.10).

**Architecture:** `ChatController._review_control` (one `ChatTurnControl`) replaces the per-turn
`_turn_controls` map; `_plan_mode` (Part 2B) becomes settable on its own. Two `PUT` routes set them; the
extension pushes both on every toggle and on every (re)connect. Rewind changes stay in the rewind route and
`forget_rewound_agents`.

## 2C file map

| File | Change |
|---|---|
| `services/agentd-py/agentd/chat/controller.py` | `_review_control`; `set_review_pref(auto_accept)`; `set_plan_mode`; `_pending_edit_gates`; rewind cleanup |
| `services/agentd-py/agentd/api/routes.py` | `PUT /v1/chat/review-pref`, `PUT /v1/chat/plan-mode`; remove `POST …/threads/{id}/review-pref`; rewind 409 + preview |
| `services/agentd-py/agentd/chat/storage.py` | `undeliver_notices_from`, `delete_notices_for_sources`, `live_agent_labels` |
| `services/agentd-py/agentd/chat/rewind.py` | `RewindPreview.blocked_by_agents` |
| `apps/editor-client/src/contracts/task-contracts.ts`, `src/client/http-backend-client.ts` | `setGlobalReviewPref`, `setPlanMode`; remove `setChatReviewPref`; `RewindPreview.blockedByAgents` |
| `apps/vscode-extension/src/controller.ts`, `src/extension.ts` | unconditional push; push on connect; background-accept notification |

---

### Task 2C.1: One process-level review control and Plan Mode

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_global_review_pref.py` (new); rewrite
  `tests/test_live_edit_review_pref.py`'s controller- and route-level tests (the `ControllerLoop`-level ones
  are unchanged); update `tests/test_controller_durable_edit.py` and `tests/test_multi_gate_decisions.py` where
  they call `set_review_pref(thread_id, …)`

**Interfaces:**
- Produces (`ChatController`):
  - `_review_control: ChatTurnControl` — starts at `auto_accept_edits=True` (today's default when the
    message carries no `step_review`)
  - `set_review_pref(*, auto_accept: bool) -> ReviewPrefResult` with
    `@dataclass(frozen=True) class ReviewPrefResult: auto_resolved: int; background: int`
  - `set_plan_mode(plan_mode: bool) -> None`
  - `_pending_edit_gates: dict[str, tuple[str, PendingGate]]` — gate id → (thread id, gate), filled and
    cleared next to `_pending_edit`
- Removes: `_turn_controls`, the per-thread `set_review_pref(thread_id, …)` and its route.
- Produces (routes): `PUT /v1/chat/review-pref {auto_accept}` → `{"auto_resolved": n, "background": m}`;
  `PUT /v1/chat/plan-mode {plan_mode}` → `{"plan_mode": bool}`.

**Behavior:**
- Every main turn and every effective-`default` activation that is not capped uses `_review_control`
  (`ctx.edit_review` `required`/`auto` keep their fixed controls).
- `handle_message` / `queue_message`: when `step_review` is present, set
  `_review_control.auto_accept_edits = not step_review` — never resolving gates.
- `set_review_pref(auto_accept=True)` resolves, as accept, every pending edit gate in every thread except
  `protected` and `review_required` ones; `background` counts those whose gate has an `agent`.
- `_step_review_by_thread` stays only for `create_task`'s `step_review_auto_accept`.

- [ ] **Step 1: Write the failing tests**

```python
"""One review control for the whole process (spec §5.4)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.models import GateAgent, PendingGate
from tests.test_background_dispatch import _setup


@pytest.mark.asyncio
async def test_flip_to_auto_accept_resolves_gates_in_every_thread(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    other = store.create_thread(str(tmp_path), title="o").thread_id
    loop = asyncio.get_running_loop()
    futures = {}
    for thread_id, gate in (
            (tid, PendingGate.new("edit", {"diff_entries": []})),
            (other, PendingGate.new("edit", {"diff_entries": []},
                                    agent=GateAgent(id="a1", label="bg", name="explore"))),
            (other, PendingGate.new("edit", {"diff_entries": [], "protected": True}))):
        store.add_controller_gate(thread_id, gate)
        futures[gate.gate_id] = loop.create_future()
        ctrl._pending_edit[gate.gate_id] = futures[gate.gate_id]
        ctrl._pending_edit_gates[gate.gate_id] = (thread_id, gate)
    ctrl._review_control.auto_accept_edits = False
    result = ctrl.set_review_pref(auto_accept=True)
    assert (result.auto_resolved, result.background) == (2, 1)
    assert ctrl._review_control.auto_accept_edits is True
    assert sum(f.done() for f in futures.values()) == 2


@pytest.mark.asyncio
async def test_a_message_sets_the_value_without_resolving(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch,
                              [{"type": "answer", "thought": "t", "answer": "ok"}], {})
    fut = asyncio.get_running_loop().create_future()
    gate = PendingGate.new("edit", {"diff_entries": []})
    ctrl._pending_edit[gate.gate_id] = fut
    ctrl._pending_edit_gates[gate.gate_id] = ("elsewhere", gate)
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}", step_review=False)
    assert ctrl._review_control.auto_accept_edits is True and not fut.done()
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}", step_review=True)
    assert ctrl._review_control.auto_accept_edits is False


def test_plan_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _store, _tid = _setup(tmp_path, monkeypatch, [], {})
    ctrl.set_plan_mode(True)
    assert ctrl._plan_mode is True
```

Route test (append to `tests/test_subagent_routes.py`):

```python
@pytest.mark.asyncio
async def test_global_preference_routes(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    async with _client(tmp_path, ctrl) as client:
        pref = await client.put("/v1/chat/review-pref", json={"auto_accept": False})
        mode = await client.put("/v1/chat/plan-mode", json={"plan_mode": True})
        gone = await client.post("/v1/chat/threads/x/review-pref", json={"auto_accept": True})
    assert pref.json() == {"auto_resolved": 0, "background": 0}
    assert mode.json() == {"plan_mode": True} and ctrl._plan_mode is True
    assert gone.status_code in (404, 405)
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Controller**
  - `__init__`: `self._review_control = ChatTurnControl(auto_accept_edits=True)`;
    `self._pending_edit_gates: dict[str, tuple[str, PendingGate]] = {}`; delete `self._turn_controls`.
  - `_run_loop`: delete `is_review`/`control = ChatTurnControl(...)`,
    `self._turn_controls[thread_id] = control` and its `pop`; pass `turn_control=self._review_control`.
  - `_activate`: the `else` branch becomes `control = self._review_control`.
  - `_edit_decision_cb`: after `self._pending_edit[gate.gate_id] = fut` add
    `self._pending_edit_gates[gate.gate_id] = (thread_id, gate)`; pop it in the `finally` next to
    `_pending_edit`.
  - `handle_message` (and `queue_message`): `if step_review is not None:
    self._review_control.auto_accept_edits = not step_review`.
  - Replace `set_review_pref`:

```python
    def set_review_pref(self, *, auto_accept: bool) -> ReviewPrefResult:
        """The one "Review each edit" value for every thread, turn and background agent
        (spec §5.4). Turning auto-accept ON accepts the edit gates waiting under it — the
        diff on screen would otherwise contradict the switch — except protected-path and
        untrusted-agent gates, which take an explicit decision (§3.9, §3.12). Turning it
        off only governs future edits. No await between the flip and the resolutions."""
        self._review_control.auto_accept_edits = auto_accept
        if not auto_accept:
            return ReviewPrefResult(auto_resolved=0, background=0)
        resolved = background = 0
        for gate_id, (_thread_id, gate) in list(self._pending_edit_gates.items()):
            if gate.payload.get("protected") or gate.payload.get("review_required"):
                continue
            future = self._pending_edit.get(gate_id)
            if future is None or future.done():
                continue
            future.set_result({"decision": "accept", "reason": "auto-accept turned on"})
            resolved += 1
            if gate.agent is not None:
                background += 1
        return ReviewPrefResult(auto_resolved=resolved, background=background)

    def set_plan_mode(self, plan_mode: bool) -> None:
        self._plan_mode = plan_mode
```

- [ ] **Step 4: Routes** — delete `post_chat_review_pref`; add:

```python
        @router.put("/chat/review-pref")
        async def put_chat_review_pref(request: ReviewPrefRequest) -> dict:
            set_pref = getattr(_chat_agent, "set_review_pref", None)
            if set_pref is None:
                return {"auto_resolved": 0, "background": 0}
            result = set_pref(auto_accept=bool(request.auto_accept))
            return {"auto_resolved": result.auto_resolved, "background": result.background}

        @router.put("/chat/plan-mode")
        async def put_chat_plan_mode(request: dict) -> dict:
            plan_mode = request.get("plan_mode")
            if not isinstance(plan_mode, bool):
                raise HTTPException(status_code=422, detail="plan_mode must be a boolean")
            set_mode = getattr(_chat_agent, "set_plan_mode", None)
            if set_mode is not None:
                set_mode(plan_mode)
            return {"plan_mode": plan_mode}
```

- [ ] **Step 5: Update existing tests.**
  - `tests/test_live_edit_review_pref.py` (the `ControllerLoop`-level tests at the top are unchanged):
    - delete `test_set_review_pref_reports_no_turn_in_flight` and the route's 409/404 tests — the value is
      always settable now;
    - `test_set_review_pref_mutates_the_live_turn_control` becomes "sets the process control"
      (`controller.set_review_pref(auto_accept=True)`; assert `controller._review_control`);
    - the two pending-gate tests register each fake gate in **both** maps — a test that only sets
      `_pending_edit[gate_id]` is invisible to the flip, which walks `_pending_edit_gates`:
      ```python
      def _pending_edit(controller, store, thread_id, payload):  # type: ignore[no-untyped-def]
          gate = store.add_controller_gate(thread_id, PendingGate(kind="edit", payload=payload))
          future = asyncio.get_event_loop().create_future()
          controller._pending_edit[gate.gate_id] = future
          controller._pending_edit_gates[gate.gate_id] = (thread_id, gate)
          return future
      ```
      and the auto-accept test also checks that a `protected` gate stays pending;
    - the route test moves to `PUT /v1/chat/review-pref` (returns `{"auto_resolved": 0, "background": 0}`),
      plus a test that `POST /v1/chat/threads/{id}/review-pref` is gone (404/405);
    - `test_pref_set_at_a_gate_is_honored_by_the_resumed_turn` asserts the process control after a flip
      with no turn running.
  - `tests/test_multi_gate_decisions.py::test_review_pref_on_accepts_every_pending_edit_gate`:
    `ctrl._review_control.auto_accept_edits = False`, register `_pending_edit_gates`, and assert
    `ctrl.set_review_pref(auto_accept=True).auto_resolved == 2`.
  - `tests/test_controller_durable_edit.py` needs no change.

- [ ] **Step 6: Run** — `pytest tests/test_global_review_pref.py tests/test_live_edit_review_pref.py tests/test_controller_durable_edit.py tests/test_multi_gate_decisions.py tests/test_subagent_routes.py --timeout=60`; expected exit=0.

- [ ] **Step 7: Commit** — `feat(chat): one review preference and Plan Mode for the whole backend` (+ trailers)

---

### Task 2C.2: The extension pushes both preferences

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Modify: `apps/vscode-extension/src/controller.ts`, `apps/vscode-extension/src/extension.ts`
- Test: `apps/editor-client/test/http-backend-client.test.ts` (extend), `apps/vscode-extension/test/controller*.test.ts` (extend the file that covers `setReviewPref`)

**Interfaces:**
- Produces (editor-client `BackendTaskClient`): `setGlobalReviewPref(options: {autoAccept: boolean}):
  Promise<{autoResolved: number; background: number}>`; `setPlanMode(planMode: boolean): Promise<void>`.
  Removes `setChatReviewPref`.
- Produces (`CrucibleController`): `setReviewPref(autoAccept)` always calls `setGlobalReviewPref` (and the
  task route when a task is live), and shows `ui.showInfo("Accepted N pending background edit(s).")` when
  `background > 0`; `pushPreferences(prefs: {autoAccept: boolean; planMode: boolean}): Promise<void>`;
  `onBackendReachable(listener: () => void)` — fired when the `/live` poll succeeds after a failure.

- [ ] **Step 1: Write the failing tests.**

Extension controller (`test/controller.test.ts`; its helpers are `createStubBackend(state)`,
`createUi(overrides)` and `new CrucibleController(() => backend, new MemorySessionStore(), createSettings(),
createUi(), {openDiff}, now)`). Add `vi` to the file's vitest import. In `StubBackendState`, replace
`chatReviewPrefCalls` with `globalReviewPrefCalls?: Array<{ autoAccept: boolean }>` and
`planModeCalls?: boolean[]`; in `createStubBackend`, replace the `setChatReviewPref` stub with:

```ts
    setGlobalReviewPref: async (options) => {
      state.globalReviewPrefCalls?.push({ autoAccept: options.autoAccept });
      return { autoResolved: 0, background: 0 };
    },
    setPlanMode: async (planMode) => {
      state.planModeCalls?.push(planMode);
    },
```

Replace the test "setReviewPref posts to the chat thread so a controller turn picks it up mid-flight" with:

```ts
  test("setReviewPref reaches the backend with no thread or task open", async () => {
    // The backend keeps one value for every thread and background agent (spec §5.4).
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      reviewPrefCalls: [], globalReviewPrefCalls: [],
    };
    const backend = createStubBackend(state);
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.setReviewPref(true);
    controller.dispose();
    expect(state.globalReviewPrefCalls).toEqual([{ autoAccept: true }]);
    expect(state.reviewPrefCalls).toEqual([]);  // no task is running
  });

  test("setReviewPref reports edits it accepted for background agents", async () => {
    const infos: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      setGlobalReviewPref: async () => ({ autoResolved: 1, background: 1 }),
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ showInfo: (m) => { infos.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    await controller.setReviewPref(true);
    controller.dispose();
    expect(infos).toEqual(["Accepted 1 pending background edit."]);
  });

  test("onBackendReachable fires when the live poll recovers", async () => {
    let calls = 0;
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
        getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      }),
      getThreadLiveState: async () => {
        calls += 1;
        if (calls === 1) throw new Error("down");
        return { activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false };
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), createUi(),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-06-11T00:00:00.000Z"
    );
    const reachable = vi.fn();
    controller.onBackendReachable(reachable);
    await controller.switchChatThread("chat-reach");
    await controller.pollThreadLiveState();
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(reachable).toHaveBeenCalledTimes(1);
  });
```

In `apps/editor-client/test/decision-clients.test.ts`, replace the `setChatReviewPref` describe block with
tests of the two `PUT` methods (URL, method `PUT`, body `{auto_accept}` / `{plan_mode}`, mapped result).

- [ ] **Step 2: Run to verify failure** — `npm run -w @crucible/editor-client test` and `npm run -w crucible-vscode-extension test`; expected failures.

- [ ] **Step 3: editor-client** — in the interface replace `setChatReviewPref` with the two methods; in
`HttpBackendClient` (add `import { z } from "zod";` at the top — the client does not import zod yet):

```ts
  // One "Review each edit" value for the whole backend (spec §5.4): every thread, turn
  // and background agent. Turning auto-accept on accepts the edits waiting under it.
  async setGlobalReviewPref(options: { autoAccept: boolean }): Promise<{ autoResolved: number; background: number }> {
    const body = await this.fetchJson("/v1/chat/review-pref", {
      method: "PUT", body: JSON.stringify({ auto_accept: options.autoAccept }),
    });
    const parsed = z.object({ auto_resolved: z.number(), background: z.number() }).parse(body);
    return { autoResolved: parsed.auto_resolved, background: parsed.background };
  }

  async setPlanMode(planMode: boolean): Promise<void> {
    await this.fetchJson("/v1/chat/plan-mode", {
      method: "PUT", body: JSON.stringify({ plan_mode: planMode }),
    });
  }
```

- [ ] **Step 4: Extension controller** — replace `setReviewPref`:

```ts
  /** The backend keeps one value for every thread and background agent (spec §5.4), so
   * this is sent whether or not a thread or task is open. */
  async setReviewPref(autoAccept: boolean): Promise<void> {
    const taskId = this.latestLiveState?.activeTaskId;
    try {
      const result = await this.clientForChat().setGlobalReviewPref({ autoAccept });
      if (result.background > 0) {
        const noun = result.background === 1 ? "edit" : "edits";
        this.ui.showInfo(`Accepted ${result.background} pending background ${noun}.`);
      }
      if (taskId) {
        await this.clientForChat().setReviewPref(taskId, { autoAccept });
      }
      this.lastLiveSignature = null;
      void this.pollThreadLiveState();
    } catch (error) {
      if (this.isBenignConflict(error)) return;
      this.ui.showError(`Failed to update review preference: ${formatError(error)}`);
    }
  }

  async pushPreferences(prefs: { autoAccept: boolean; planMode: boolean }): Promise<void> {
    try {
      const client = this.clientForChat();
      await client.setGlobalReviewPref({ autoAccept: prefs.autoAccept });
      await client.setPlanMode(prefs.planMode);
    } catch {
      // Unreachable now; the next reconnect pushes again.
    }
  }

  onBackendReachable(listener: () => void): void {
    this.backendReachableListeners.push(listener);
  }
```

with `private backendReachableListeners: Array<() => void> = []` and `private livePollFailing = false`. In
`pollThreadLiveState`, the `catch` sets `this.livePollFailing = true`; right after a successful
`getThreadLiveState`:

```ts
    if (this.livePollFailing) {
      this.livePollFailing = false;
      for (const listener of this.backendReachableListeners) listener();
    }
```

- [ ] **Step 5: `extension.ts`**
  - the review toggle handler keeps `await runtimeManager.setStepReview(!autoAccept); await
    controller.setReviewPref(autoAccept);`
  - the Plan Mode handler becomes
    `async (enabled: boolean) => { await runtimeManager.setPlanMode(enabled); await controller.configClient().setPlanMode(enabled).catch(() => undefined); }`
  - add, next to `refreshCapabilityFlags`:

```ts
  // Spec §5.4: the backend's values must match the toggles after every (re)connect —
  // a restarted backend starts from its defaults.
  const pushPreferences = (): Promise<void> => controller.pushPreferences({
    autoAccept: !runtimeManager.getStepReview(),
    planMode: runtimeManager.getPlanMode(),
  });
```

    call `void pushPreferences()` inside `refreshCapabilityFlags` after a successful `getConfig` (covers
    activation and every `onBackendReady`), and register `controller.onBackendReachable(() => void
    pushPreferences())` (covers an explicit `crucible.backendBaseUrl`, where `onBackendReady` never fires).

- [ ] **Step 6: Run** — `npm run -w @crucible/editor-client build && npm run typecheck && npm run test`; expected green.

- [ ] **Step 7: Commit** — `feat(extension): push review and Plan Mode preferences to the backend` (+ trailers)

---

### Task 2C.3: Rewind with background agents and notices

**Files:**
- Modify: `services/agentd-py/agentd/chat/storage.py`, `services/agentd-py/agentd/chat/rewind.py`,
  `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/api/routes.py`
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`RewindPreviewSchema.blockedByAgents`),
  `apps/editor-client/src/client/http-backend-client.ts` (mapping)
- Test: `services/agentd-py/tests/test_rewind_agents.py` (new)

**Interfaces:**
- Produces (store): `live_agent_labels(thread_id) -> list[str]`; `undeliver_notices_from(thread_id, seq) ->
  int` (clears claim and `delivered_at` on rows with `claimed_checkpoint_seq >= seq`, and the source agents'
  `report_delivered_at`); `delete_notices_for_sources(thread_id, source_ids: list[str]) -> int`.
- Produces: `RewindPreview.blocked_by_agents: list[str] = []`;
  `forget_rewound_agents(thread_id, turn_ids, from_seq=None, restored_files: list[str] | None = None)`.

**Behavior (spec §8.10):**
- `POST /rewind` answers 409 while any agent in the thread is queued, running or waiting: `Agents <labels> are
  still running — stop them before rewinding.` (Teams join this check in Phase 4.) The preview lists them in
  `blocked_by_agents`.
- `forget_rewound_agents` also deletes the deleted agents' notices, un-delivers notices claimed inside the
  span, and puts a note in each surviving agent's inbox whose `files_changed` intersects `restored_files`:
  `The user rewound the conversation; these files were restored to an earlier state: <paths>. Re-read before
  relying on them.` The route passes `outcome.restored_files + outcome.deleted_files`.

- [ ] **Step 1: Write the failing tests**

```python
"""Rewind with agents and notices (spec §8.10)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import AgentRecord, NoticeRecord
from tests.test_background_dispatch import _setup


def _agent(tid: str, agent_id: str, seq: int, status: str = "completed",
           files: list[str] | None = None) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id=tid, turn_id="u", depth=1, name="explore",
                       label=agent_id, prompt="p", status=status, dispatcher_id="main",
                       checkpoint_seq=seq, files_changed=files or [])


def _notice(tid: str, nid: str, source: str) -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id=tid, source_kind="agent", source_id=source,
                        kind="agent_finished", payload={"report": "r"}, delivery="notify",
                        created_at=datetime.now(UTC))


def test_live_agent_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    store.insert_agent(_agent(tid, "busy", 0, status="running"))
    store.insert_agent(_agent(tid, "idle", 0))
    assert store.live_agent_labels(tid) == ["busy"]


def test_forget_cleans_notices_and_notes_survivors(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    store.insert_agent(_agent(tid, "old", 0, files=["a.py"]))
    store.insert_agent(_agent(tid, "new", 2))
    store.insert_notice(_notice(tid, "n-old", "old"))
    store.insert_notice(_notice(tid, "n-new", "new"))
    store.claim_notices(["n-old"], "turn-2", 2)
    store.deliver_claimed_notices(tid, "turn-2")
    delivered: list[tuple[str, str]] = []
    ctrl._subagents.deliver = lambda agent_id, item: delivered.append((agent_id, item.text))  # type: ignore[union-attr, method-assign]
    ctrl.forget_rewound_agents(tid, [], 2, restored_files=["a.py"])
    assert store.get_agent("new") is None
    rows = {r["notice_id"]: r for r in store._conn.execute("SELECT * FROM agent_notices")}
    assert "n-new" not in rows
    assert rows["n-old"]["delivered_at"] is None and rows["n-old"]["claimed_turn_id"] is None
    assert store.get_agent("old").report_delivered_at is None  # type: ignore[union-attr]
    assert delivered and delivered[0][0] == "old" and "a.py" in delivered[0][1]
```

Route tests, appended to `tests/test_rewind_routes.py` (its `_build` helper has a rewind store and a
checkpoint anchored to `msg_id`):

```python
@pytest.mark.asyncio
async def test_rewind_refuses_while_an_agent_runs(tmp_path: Path):
    """Spec §8.10: a background agent could edit files the rewind restores."""
    from agentd.chat.models import AgentRecord

    app, controller, tid, msg_id = _build(tmp_path)
    controller._store.insert_agent(AgentRecord(
        agent_id="busy", thread_id=tid, turn_id="u", depth=1, name="explore",
        label="scout", prompt="p", status="running", dispatcher_id="main"))
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
        preview = await client.get(f"/v1/chat/threads/{tid}/rewind-preview",
                                   params={"message_id": msg_id})
    assert r.status_code == 409 and "scout" in r.json()["detail"]
    assert preview.json()["blocked_by_agents"] == ["scout"]


@pytest.mark.asyncio
async def test_rewind_to_a_queued_message_uses_its_notice_turn_checkpoint(tmp_path: Path):
    """Spec §5.3: a message delivered into a notice turn shares that turn's checkpoint."""
    app, controller, tid, msg_id = _build(tmp_path)
    queued = controller._store.append_message(tid, ChatMessage(
        role="user", content="also", metadata={"checkpoint_anchor": msg_id}))
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": queued})
    assert r.status_code == 200
    assert controller._store.get_thread(tid).messages == []
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Store**

```python
    def live_agent_labels(self, thread_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT label FROM chat_agents WHERE thread_id = ? "
            "AND status IN ('queued', 'running', 'waiting') ORDER BY depth, rowid",
            (thread_id,)).fetchall()
        return [r["label"] for r in rows]

    def undeliver_notices_from(self, thread_id: str, seq: int) -> int:
        """Rewind restores the main history from before the span, so notices folded into it
        are offered again (spec §8.10)."""
        rows = self._conn.execute(
            "SELECT source_kind, source_id FROM agent_notices WHERE thread_id = ? "
            "AND claimed_checkpoint_seq >= ?", (thread_id, seq)).fetchall()
        self._conn.execute(
            "UPDATE agent_notices SET claimed_turn_id = NULL, claimed_checkpoint_seq = NULL, "
            "delivered_at = NULL WHERE thread_id = ? AND claimed_checkpoint_seq >= ?",
            (thread_id, seq))
        self._conn.executemany(
            "UPDATE chat_agents SET report_delivered_at = NULL WHERE agent_id = ?",
            [(r["source_id"],) for r in rows if r["source_kind"] == "agent"])
        self._conn.commit()
        return len(rows)

    def delete_notices_for_sources(self, thread_id: str, source_ids: list[str]) -> int:
        cur = self._conn.executemany(
            "DELETE FROM agent_notices WHERE thread_id = ? AND source_id = ?",
            [(thread_id, s) for s in source_ids])
        self._conn.commit()
        return cur.rowcount
```

- [ ] **Step 4: Controller** — in `forget_rewound_agents`, add the parameter and, after `agent_ids` is
computed and before the memory loop:

```python
        self._store.delete_notices_for_sources(thread_id, agent_ids)
        if from_seq is not None:
            self._store.undeliver_notices_from(thread_id, from_seq)
        if restored_files and self._subagents is not None:
            restored = set(restored_files)
            for record in self._store.list_agents(thread_id):
                touched = sorted(restored & set(record.files_changed))
                if touched:
                    self._subagents.deliver(record.agent_id, InboxItem(
                        kind="note", wakes=False, author="system",
                        text=("The user rewound the conversation; these files were restored "
                              f"to an earlier state: {', '.join(touched)}. Re-read before "
                              "relying on them.")))
```

- [ ] **Step 5: Routes and preview** — add `blocked_by_agents: list[str] = Field(default_factory=list)` to
`RewindPreview`. In `get_rewind_preview`, `preview.blocked_by_agents =
_chat_agent._store.live_agent_labels(thread_id)`. In `post_rewind`, after the in-flight-turn check:

```python
            running = _chat_agent._store.live_agent_labels(thread_id)
            if running:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Agents {', '.join(running)} are still running — stop them "
                            "before rewinding."))
```

and pass `restored_files=outcome.restored_files + outcome.deleted_files` to `forget`. Both routes resolve the
anchor through `resolve_rewind_anchor` (Task 2B.5) first.

- [ ] **Step 6: editor-client** — `RewindPreviewSchema` gains `blockedByAgents: z.array(z.string()).default([])`
and `previewRewind`'s explicit mapping adds `blockedByAgents: raw["blocked_by_agents"] ?? []`. Extend the
existing "maps rewind preview" test in `test/http-backend-client.test.ts` with
`blocked_by_agents: ["scout"]` → `expect(preview.blockedByAgents).toEqual(["scout"])`. (The dialog shows it
in Part 2D.)

- [ ] **Step 7: Run** — `pytest tests/test_rewind_agents.py tests/test_rewind_routes.py tests/test_subagent_rewind.py tests/test_rewind_integration.py tests/test_rewind_scenarios.py --timeout=60`; `npm run -w @crucible/editor-client build && npm run -w @crucible/editor-client test`; expected green.

- [ ] **Step 8: Commit** — `feat(chat): rewind waits for agents and re-offers rewound notices` (+ trailers)

---

### Task 2C.4: Part 2C checkpoint

- [ ] Full Python suite with `--timeout=120` (redirected); `npm run build && npm run typecheck && npm run test`; `ruff check` the changed files.

---

# Part 2D — UI for background agents and notices

**Goal:** The user can see and control agents that outlive a turn (spec §6): roster rows that re-light on
resume, notice markers, a "Stop all agents" control, a composer that stays usable while background agents or
a notice turn run, queued sends, a cross-thread attention notification, a transcript that picks up messages
written while nothing was streaming, and a rewind dialog that explains a refusal.

**Architecture:** Backend first (the fields and routes the UI reads), then the editor-client contract, then
the extension host, then the webview. Every new `/live` field consumed after the dedup gate goes into
`lastLiveSignature` (CLAUDE.md invariant).

## 2D file map

| File | Change |
|---|---|
| `services/agentd-py/agentd/chat/models.py` | `AgentRecord.summary()` fields; `ThreadLiveState.agents_running`, `message_count` |
| `services/agentd-py/agentd/chat/controller.py` | `live_agents` from the database; `agent_message` on resume |
| `services/agentd-py/agentd/api/routes.py` | `/live` fields; thread summaries `agents_running`; `GET /v1/chat/attention` |
| `apps/editor-client/src/contracts/task-contracts.ts`, `src/client/http-backend-client.ts` | schemas, events, `sendChatMessage` result, `getAttention`, `stopAllAgents` |
| `apps/vscode-extension/src/controller.ts`, `src/agent-views.ts`, `src/extension.ts`, `src/chat-panel.ts` | message events, queued send, signature, main-only gates, reconcile, attention poll, re-follow, stop all |
| `apps/vscode-extension/webview-ui/src/…` | types, reducer, `inputAvailability`, notice and agent-message lines, Stop all, history chip, rewind dialog |

---

### Task 2D.1: Backend fields and routes the UI reads

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py`, `services/agentd-py/agentd/chat/controller.py`,
  `services/agentd-py/agentd/chat/storage.py`, `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_agents_ui_backend.py` (new)

**Interfaces:**
- `AgentRecord.summary()` adds `activation_count`, `team_id`, `dispatcher_id`, `on_finish`,
  `activation_started_at`, `activation_ended_at`.
- `ThreadLiveState` adds `agents_running: int = 0`, `message_count: int = 0`.
- `ChatController.live_agents(thread_id)` lists the thread's agents that are live or ended since the last user
  message, from the database, overlaying `now`/`tool_count` from a live handle.
- `ChatController.resume_agent` writes an `agent_message` chat message (`metadata: {agent_id, activation,
  from}`) — on the thread for the main agent (broadcast `{"type": "agent_message", "payload": {"message"}}` on
  `chat:{thread}`), in the dispatcher's transcript and channel for a child.
- Store: `last_user_message_at(thread_id) -> datetime | None`.
- Routes: `/live` sets `agents_running = store.count_live_agents(thread)` and `message_count =
  len(thread.messages)`; the thread list adds `agents_running`; `GET /v1/chat/attention?workspace=` →
  `[{thread_id, pending_gates, agents_running}]` for threads with either count > 0 (ids and counts only).

- [ ] **Step 1: Write the failing tests**

```python
"""Backend fields for the background-agent UI (spec §6)."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentd.chat.models import AgentRecord, ChatMessage, PendingGate
from tests.test_background_dispatch import _setup


def _row(tid: str, agent_id: str, status: str, ended: datetime | None) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id=tid, turn_id="u", depth=1, name="explore",
                       label=agent_id, prompt="p", status=status, dispatcher_id="main",
                       ended_at=ended, activation_count=2, on_finish="wake")


def test_summary_fields() -> None:
    summary = _row("t", "a", "completed", None).summary()
    assert summary["activation_count"] == 2 and summary["on_finish"] == "wake"
    assert {"team_id", "dispatcher_id", "activation_started_at",
            "activation_ended_at"} <= summary.keys()


def test_live_agents_are_live_or_recent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    store.append_message(tid, ChatMessage(role="user", content="go"))
    now = datetime.now(UTC)
    store.insert_agent(_row(tid, "old", "completed", now - timedelta(days=1)))
    store.insert_agent(_row(tid, "recent", "completed", now + timedelta(seconds=1)))
    store.insert_agent(_row(tid, "busy", "running", None))
    assert sorted(a["agent_id"] for a in ctrl.live_agents(tid)) == ["busy", "recent"]


@pytest.mark.asyncio
async def test_resume_writes_an_agent_message(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [],
                              {"a1": [{"type": "report", "thought": "t", "summary": "x"}]})
    store.insert_agent(_row(tid, "a1", "completed", None))
    ctrl.resume_agent(tid, "a1", "look again")
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert messages[-1].type == "agent_message"
    assert messages[-1].metadata["agent_id"] == "a1"
    assert messages[-1].metadata["activation"] == 3
    await ctrl.stop_all_agents(tid)


@pytest.mark.asyncio
async def test_attention_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_subagent_routes import _client

    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    quiet = store.create_thread(store.get_thread(tid).workspace_path, title="q").thread_id  # type: ignore[union-attr]
    store.add_controller_gate(tid, PendingGate.new("edit", {}))
    store.insert_agent(_row(tid, "busy", "running", None))
    async with _client(tmp_path, ctrl) as client:
        response = await client.get(
            "/v1/chat/attention",
            params={"workspace": store.get_thread(tid).workspace_path})  # type: ignore[union-attr]
    assert response.json() == [{"thread_id": tid, "pending_gates": 1, "agents_running": 1}]
    assert quiet not in str(response.json())
```

- [ ] **Step 2: Run to verify failure** (redirected, `--timeout=60`); expected exit=1.

- [ ] **Step 3: Implement.**
  - `summary()` adds:
    ```python
            "activation_count": self.activation_count, "team_id": self.team_id,
            "dispatcher_id": self.dispatcher_id, "on_finish": self.on_finish,
            "activation_started_at": (self.activation_started_at.isoformat()
                                      if self.activation_started_at else None),
            "activation_ended_at": (self.activation_ended_at.isoformat()
                                    if self.activation_ended_at else None),
    ```
  - `ThreadLiveState`: `agents_running: int = 0` and `message_count: int = 0`, each with a one-line comment
    (both are in `lastLiveSignature`, Task 2D.3).
  - Store:
    ```python
    def last_user_message_at(self, thread_id: str) -> datetime | None:
        thread = self.get_thread(thread_id)
        stamps = [m.timestamp for m in (thread.messages if thread else []) if m.role == "user"]
        return stamps[-1] if stamps else None
    ```
    (If `ChatMessage.timestamp` is a string, parse with `datetime.fromisoformat`; check the model.)
  - `live_agents`:
    ```python
    def live_agents(self, thread_id: str) -> list[dict[str, object]]:
        """/live's roster (spec §6): agents that are live, or that ended since the user's
        last message — not only the current turn's, since agents outlive turns now."""
        if self._subagents is None:
            return []
        since = self._store.last_user_message_at(thread_id)
        rows: list[dict[str, object]] = []
        for record in self._store.list_agents(thread_id):
            live = record.status in LIVE_STATUSES
            recent = since is not None and record.ended_at is not None and record.ended_at >= since
            if not (live or recent):
                continue
            row = record.summary()
            handle = self._subagents.registry.get(record.agent_id)
            calls = (handle.loop.tool_calls
                     if handle is not None and isinstance(handle.loop, ControllerLoop) else [])
            row["now"] = ""
            if calls and live:
                last = calls[-1]
                target = last.arguments.get("path") or last.arguments.get("command") or ""
                row["now"] = f"{last.tool_name} {target}".strip()
            if live and handle is not None:
                row["status"] = handle.status
            rows.append(row)
        return rows
    ```
    (The handle's status is fresher than the row while running; `tool_count` comes from the persisted
    transcript via `summary()`, which updates at iteration boundaries — stable enough for the signature.)
  - `resume_agent`, after enqueueing:
    ```python
        self._write_agent_message(thread_id, record, caller_id, message)
    ```
    ```python
    def _write_agent_message(
        self, thread_id: str, record: AgentRecord, caller_id: str, message: str,
    ) -> None:
        """The durable record of a resume (spec §6): which activation, from whom."""
        msg = ChatMessage(role="agent", content=message, type="agent_message", metadata={
            "agent_id": record.agent_id, "label": record.label,
            "activation": record.activation_count + 1, "from": caller_id})
        event = {"type": "agent_message", "payload": {"message": msg.model_dump(mode="json")}}
        if caller_id == MAIN_AGENT_ID:
            self._mark_pills_boundary(thread_id)
            self._store.append_message(thread_id, msg)
            self._broadcaster.broadcast(f"chat:{thread_id}", event)
            return
        dispatcher = self._subagents.registry.get(caller_id) if self._subagents else None
        if dispatcher is not None and dispatcher.broadcaster is not None:
            dispatcher.broadcaster.broadcast(agent_channel(thread_id, caller_id), event)
        if dispatcher is not None and dispatcher.transcript is not None:
            dispatcher.transcript.append(msg)
    ```
  - Routes: in `/live`, after `live.agents = …`:
    ```python
            _count_live = getattr(_chat_agent._store, "count_live_agents", None)
            if _count_live is not None:
                live.agents_running = _count_live(thread_id)
            live.message_count = len(thread.messages)
    ```
    In the thread-list enrichment loop add `summary["agents_running"] = store_.count_live_agents(t.thread_id)`
    (use the store variable that loop already reads). New route:
    ```python
        @router.get("/chat/attention")
        async def get_chat_attention(workspace: str) -> list[dict]:
            """Threads needing the user, for the cross-thread notification (spec §6): ids
            and counts only, never gate payloads."""
            chat_store = _chat_agent._store
            out: list[dict] = []
            for t in chat_store.list_threads(workspace):
                full = chat_store.get_thread(t.thread_id)
                gates = len(full.pending_controller_gates) if full is not None else 0
                running = chat_store.count_live_agents(t.thread_id)
                if gates or running:
                    out.append({"thread_id": t.thread_id, "pending_gates": gates,
                                "agents_running": running})
            return out
    ```
    Register it **before** any `/chat/threads/{thread_id}` route that could shadow it (it has a distinct prefix,
    so order only matters if a catch-all exists — check).

- [ ] **Step 4: Update the two v1 tests that pin the roster shape.**
  - `tests/test_subagent_routes.py::test_list_get_stop_live_and_config`: the exact summary dict gains
    `"activation_count": 0, "team_id": None, "dispatcher_id": None, "on_finish": None,
    "activation_started_at": None, "activation_ended_at": None`.
  - `tests/test_subagent_lifecycle.py::test_live_roster_then_stopping_one_agent`: the key set gains `turn_id`
    and the six fields; its last check becomes
    ```python
    # Agents that reported since the user's last message stay listed (spec §6).
    assert {a["label"]: a["status"] for a in ctrl.live_agents(tid)} == {
        "waiter": "stopped", "quick": "completed"}
    ```

- [ ] **Step 5: Run** — `pytest -k "agent or dispatch or subagent or live or route" --timeout=120`; expected exit=0.

- [ ] **Step 6: Commit** — `feat(chat): background-agent fields for the live roster and attention` (+ trailers)

---

### Task 2D.2: The editor-client contract

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`, `apps/editor-client/src/index.ts` (exports)
- Test: `apps/editor-client/test/http-backend-client.test.ts`

**Interfaces:**
- `AgentSummarySchema` adds `activationCount: number`, `teamId: string | null`, `dispatcherId: string | null`,
  `onFinish: string | null`, `activationStartedAt: string | null`, `activationEndedAt: string | null` (all with
  defaults, mapped in `toAgentSummary`).
- `ThreadLiveStateSchema` adds `agentsRunning` (default 0), `turnKind: "user" | "notice" | null`,
  `messageCount` (default 0) — and the explicit `getThreadLiveState` mapping carries all three.
- `ChatThreadSummarySchema` adds `agentsRunning` (default 0); `listChatThreads` maps it.
- `StreamEvent` adds `{ type: "agent_message" | "notice" | "team_created"; payload: { message: Record<string, unknown> } }`.
- `sendChatMessage(...)` returns `Promise<SendChatResult>`:
  ```ts
  export type SendChatResult =
    | { kind: "stream"; events: AsyncIterable<StreamEvent> }
    | { kind: "queued"; messageId: string };
  ```
- `getAttention(workspace: string): Promise<ThreadAttention[]>` with
  `ThreadAttention = { threadId: string; pendingGates: number; agentsRunning: number }`.
- `stopAllAgents(threadId: string): Promise<number>`.

- [ ] **Step 1: Write the failing tests.** Append to `test/http-backend-client.test.ts` (its JSON helper is
`jsonClient(payload, captured)`; add `import type { SendChatResult, StreamEvent } from
"../src/contracts/task-contracts.js";`):

```ts
/** The stream of a sendChatMessage result; a test that expects a stream fails on a queue. */
function streamOf(result: SendChatResult): AsyncIterable<StreamEvent> {
  if (result.kind !== "stream") throw new Error(`expected a stream, got ${result.kind}`);
  return result.events;
}

describe("HttpBackendClient background agents (spec §5.3, §6)", () => {
  test("a 202 is a queued result", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://x",
      fetchFn: async () => new Response(
        JSON.stringify({ queued: true, message_id: "m1" }), { status: 202 }),
    });
    await expect(client.sendChatMessage("t1", "hi"))
      .resolves.toEqual({ kind: "queued", messageId: "m1" });
  });

  test("a 409 rejects with the status attached", async () => {
    const client = new HttpBackendClient({
      baseUrl: "http://x", fetchFn: async () => new Response("busy", { status: 409 }),
    });
    await expect(client.sendChatMessage("t1", "hi")).rejects.toMatchObject({ status: 409 });
  });

  test("maps the new live fields", async () => {
    const client = jsonClient({
      turn_active: true, turn_kind: "notice", agents_running: 2, message_count: 7,
      pending_gates: [], agents: [{ agent_id: "a", depth: 1, name: "explore", label: "a",
        status: "completed", files_changed_count: 0, activation_count: 2, on_finish: "wake" }],
    }, {});
    const live = await client.getThreadLiveState("t1");
    expect([live.turnKind, live.agentsRunning, live.messageCount]).toEqual(["notice", 2, 7]);
    expect([live.agents?.[0].activationCount, live.agents?.[0].onFinish]).toEqual([2, "wake"]);
  });

  test("reads attention", async () => {
    const client = jsonClient([{ thread_id: "t1", pending_gates: 1, agents_running: 0 }], {});
    await expect(client.getAttention("/ws")).resolves.toEqual(
      [{ threadId: "t1", pendingGates: 1, agentsRunning: 0 }]);
  });

  test("stops all agents", async () => {
    const captured: { body?: string } = {};
    const client = jsonClient({ stopped: 3 }, captured);
    await expect(client.stopAllAgents("t1")).resolves.toBe(3);
  });
});
```

Seven existing tests consume `sendChatMessage` as a generator. Convert them:
  - the five body tests (`forced_skills`, `mentioned_files` ×2, `plan_mode` ×2): `const iter =
    client.sendChatMessage(…); await iter[Symbol.asyncIterator]().next();` → `await
    client.sendChatMessage(…);` (the request is sent before the result returns);
  - "sendChatMessage streams SSE events": `for await (const event of streamOf(await
    client.sendChatMessage("chat-abc123", "hello")))`;
  - the idle-timeout test: `const iterator = streamOf(await client.sendChatMessage("t1",
    "hi"))[Symbol.asyncIterator]();`.

- [ ] **Step 2: Run to verify failure** — `npm run -w @crucible/editor-client test`; expected failures.

- [ ] **Step 3: Implement.** `sendChatMessage`:

```ts
  async sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: SendChatOptions): Promise<SendChatResult> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/message`,
      { /* unchanged request */ });
    if (response.status === 202) {
      // Spec §5.3: a notice turn is running; the backend queued the message.
      const body = z.object({ queued: z.literal(true), message_id: z.string() }).parse(await response.json());
      return { kind: "queued", messageId: body.message_id };
    }
    if (!response.ok) {
      throw new Error(`Chat message failed (${response.status}) for thread ${threadId}`);
    }
    return { kind: "stream", events: this.consumeChatEventStream(response) };
  }
```

Move the inline options type to an exported `SendChatOptions` interface (next to `SendChatResult` and
`ThreadAttention` in `task-contracts.ts`) and import the three into the client as `type` imports. The
non-ok branch attaches the status to the error — `const error = new Error(…) as Error & { status?: number };
error.status = response.status; throw error;` — so the host's `isBenignConflict` still recognises a 409.
`getAttention` and `stopAllAgents` use `fetchJson` and parse with `z`; `stopAllAgents` POSTs
`/v1/chat/threads/{id}/agents/stop-all` and returns `stopped`. Add the three event types to the
`StreamEvent` union (`ChatEventSchema` accepts any type string).

- [ ] **Step 4: Run** — `npm run -w @crucible/editor-client build && npm run -w @crucible/editor-client test`; expected green. The extension's typecheck then has exactly one error (`controller.ts` passes the
result to `streamTurn`) until Task 2D.3.

- [ ] **Step 5: Commit** — `feat(editor-client): background-agent fields, queued sends, attention` (+ trailers)

---

### Task 2D.3: The extension host

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts`, `src/agent-views.ts`, `src/extension.ts`, `src/chat-panel.ts`
- Test: `apps/vscode-extension/test/controller.test.ts`, `apps/vscode-extension/test/agent-views.test.ts`

**Interfaces (host → webview messages, added to the webview `HostMessage` union in Task 2D.4):**
`replaceMessages {messages}`, `removeChatMessage {id}`; `restoreDraft` reuses the existing
`composerPrefill {text}` (the webview already puts that text back in the composer after a rewind).
(webview → host) `stopAllAgents`. No `openAgent`: the webview opens agent windows itself
(`AgentsContext.openWindow`).
- `ControllerUI` gains `replaceChatMessages(messages)`, `removeChatMessage(id)`, `restoreDraft(text)`, and
  `sendLiveStatus` gains an optional third argument `extra?: { turnKind; agentsRunning }` (the panel posts
  both on its `liveStatus` message).
- `ChatPanel` takes its callbacks positionally: `onStopAllAgents` goes after `onStopAgent`, and
  `extension.ts` passes `() => controller.stopAllAgents()`.
- `CrucibleController` gains `stopAllAgents()`, `pollAttention()`, and the `attention` listener hook
  `onAttention(listener: (threadId: string, pendingGates: number) => void)`.
- `AgentViewManager.noteActivation(agentId: string, activationCount: number)`.

**Behavior:**
1. **Message events.** `streamTurn` appends `agent_message`, `notice` and `team_created` events exactly like
   `agent_dispatch` (`this.ui.appendChatMessage(parseWireChatMessage(event.payload.message))`).
2. **Queued send.** `sendChatMessage` awaits `client.sendChatMessage(...)`: on `queued`, keep the optimistic
   bubble, re-enable input, and return **without** entering `streamTurn`; on `stream`, `streamTurn(result.events)`
   as today; on a thrown error (other than a benign 409/abort), `ui.removeChatMessage(messageId)`,
   `ui.restoreDraft(text)`, and show the error.
3. **Signature.** `lastLiveSignature` adds `agentsRunning`, `turnKind`, `messageCount`.
4. **Main-only gates.** `channelActive` counts only gates with no `agent` (and no `team`): a background agent's
   gate never opens a thread-channel relay.
5. **Reconcile by replacement.** Keep `lastReconciledCount`, set only from a `getChatThread` result
   (`switchChatThread` and the reconcile itself). `messageCount` is in the signature, so check it **after**
   the gate: when `live.messageCount !== this.lastReconciledCount`, reconcile if `this.turnAbort === null`,
   else set `reconcilePending = true`. On a poll whose signature is unchanged, apply a pending reconcile once
   `turnAbort` is null. `reconcileTranscript` re-checks `activeThreadId` and `turnAbort` after its fetch
   (a stream may have started meanwhile). Never touches agents, views, live cards, subscriptions or
   `_liveResumeThreadId`.
6. **Re-follow.** In the `/live` agents loop also call `this.agentViews.noteActivation(agent.agentId,
   agent.activationCount)`. `AgentViewManager` keeps `finished` and `activation` per view: `run` sets
   `finished = true` after a terminal backfill; `noteActivation` restarts a finished view whose count grew;
   `setOpen` restarts a finished view instead of skipping it.
7. **Stop all.** `stopAllAgents()` → `client.stopAllAgents(activeThreadId)`, then reset the signature and poll.
8. **Attention.** `extension.ts` runs `controller.pollAttention()` every 10 s (a `setInterval` disposed with the
   extension). `pollAttention` calls `getAttention(workspace)`; for each thread that is not the active one with
   `pendingGates > 0` whose `(threadId, pendingGates)` differs from the last notified value, it fires the
   listener; `extension.ts` shows `vscode.window.showInformationMessage("A chat thread needs your answer.",
   "Open thread")` and on the action calls `controller.switchChatThread(threadId)` and opens the panel
   (the repo's `vscode-shim.d.ts` leaves `.then`'s argument untyped — annotate `choice: string | undefined`).
   A thread whose count drops to 0 is forgotten so a later gate notifies again.

- [ ] **Step 1: Write the failing tests.** In `test/controller.test.ts` (helpers: `createStubBackend(state)`,
`createUi(overrides)`, `new CrucibleController(...)`):
  - import `SendChatResult` and `StreamEvent` types, and add a helper above `StubBackendState`:
    ```ts
    /** Wraps a generator stub as sendChatMessage's {kind: "stream"} result (spec §5.3). */
    function asStream<A extends unknown[]>(
      gen: (...args: A) => AsyncIterable<StreamEvent>,
    ): (...args: A) => Promise<SendChatResult> {
      return async (...args: A) => ({ kind: "stream", events: gen(...args) });
    }
    ```
  - wrap all 12 existing `sendChatMessage: async function* (…) {…}` stubs as
    `sendChatMessage: asStream(async function* (…) {…})`;
  - add no-ops to `createUi()` — `replaceChatMessages: () => {}`, `removeChatMessage: () => {}`,
    `restoreDraft: () => {}` — or every poll's reconcile throws an unhandled rejection (the tests still pass
    but vitest exits 1);
  - append:

```ts
describe("CrucibleController — background agents (spec §5.3, §6)", () => {
  const baseState = (): StubBackendState => ({
    submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
    getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
  });
  const make = (backend: BackendTaskClient, ui: ControllerUI) => new CrucibleController(
    () => backend, new MemorySessionStore(), createSettings(), ui,
    { openDiff: async (_entry: ReviewFileEntry) => {} },
    () => "2026-06-11T00:00:00.000Z"
  );

  test("a queued send keeps the bubble, re-enables input and opens no stream", async () => {
    const appended: ChatMessage[] = [];
    const enabled: boolean[] = [];
    const removed: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      sendChatMessage: async () => ({ kind: "queued", messageId: "m1" }),
    };
    const controller = make(backend, createUi({
      appendChatMessage: (m) => { appended.push(m); },
      setChatInputEnabled: (e) => { enabled.push(e); },
      removeChatMessage: (id) => { removed.push(id); },
    }));
    await controller.switchChatThread("t1");
    await controller.sendChatMessage("hi");
    controller.dispose();
    expect(appended.filter((m) => m.role === "user")).toHaveLength(1);
    expect(removed).toEqual([]);
    expect(enabled.at(-1)).toBe(true);
  });

  test("a failed send removes the bubble and restores the draft", async () => {
    const removed: string[] = [];
    const drafts: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      sendChatMessage: async () => { throw new Error("Chat message failed (500)"); },
    };
    const controller = make(backend, createUi({
      removeChatMessage: (id) => { removed.push(id); },
      restoreDraft: (t) => { drafts.push(t); },
    }));
    await controller.switchChatThread("t1");
    await controller.sendChatMessage("hi");
    controller.dispose();
    expect(removed).toHaveLength(1);
    expect(drafts).toEqual(["hi"]);
  });

  test("reconciles the transcript when the backend has more messages, once", async () => {
    let fetches = 0;
    const replaced: ChatMessage[][] = [];
    const message = (content: string): ChatMessage =>
      ({ role: "agent", content, type: "text", timestamp: "2026-06-11T00:00:00Z", metadata: {} } as ChatMessage);
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      getChatThread: async (threadId: string) => {
        fetches += 1;
        return { threadId, workspacePath: "/w", title: "t", createdAt: "x",
                 messages: fetches === 1 ? [] : [message("a"), message("b")] } as never;
      },
      getThreadLiveState: async () => ({
        activeTaskId: null, status: null, pendingGates: [], plan: null, turnActive: false,
        agentsRunning: 0, turnKind: null, messageCount: 2 }),
    };
    const controller = make(backend, createUi({
      replaceChatMessages: (m) => { replaced.push(m); },
    }));
    await controller.switchChatThread("t1");        // fetch 1: an empty transcript
    await controller.pollThreadLiveState();
    await new Promise((r) => setTimeout(r, 0));
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(replaced.map((m) => m.length)).toEqual([2]);
    expect(fetches).toBe(2);
  });

  test("a background agent's gate opens no thread relay", async () => {
    let streams = 0;
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      streamChannel: async function* () { streams += 1; },
      getThreadLiveState: async () => ({
        activeTaskId: null, status: null, plan: null, turnActive: false,
        agentsRunning: 1, turnKind: null, messageCount: 0,
        pendingGates: [{ gateId: "g", kind: "edit", payload: {},
                         agent: { id: "a", label: "a", name: "explore" } }] }),
    };
    const controller = make(backend, createUi());
    await controller.switchChatThread("t1");
    await controller.pollThreadLiveState();
    controller.dispose();
    expect(streams).toBe(0);
  });

  test("notifies once per attention change, never for the open thread", async () => {
    const seen: Array<[string, number]> = [];
    let rows = [{ threadId: "t2", pendingGates: 1, agentsRunning: 0 },
                { threadId: "t1", pendingGates: 3, agentsRunning: 0 }];
    const backend: BackendTaskClient = {
      ...createStubBackend(baseState()),
      getAttention: async () => rows,
    };
    const controller = make(backend, createUi());
    await controller.switchChatThread("t1");
    controller.onAttention((id, n) => seen.push([id, n]));
    await controller.pollAttention();
    await controller.pollAttention();
    rows = [{ threadId: "t2", pendingGates: 2, agentsRunning: 0 }];
    await controller.pollAttention();
    controller.dispose();
    expect(seen).toEqual([["t2", 1], ["t2", 2]]);
  });
});
```

In `test/agent-views.test.ts` (its `detail(status, lastSeq)` helper has no `activationCount`; spread it on top):

```ts
describe("AgentViewManager re-follow (spec §6)", () => {
  test("restarts a finished view when the agent is resumed, not on the same activation", async () => {
    let activation = 1;
    let backfills = 0;
    const client = {
      getAgent: async () => {
        backfills += 1;
        return { ...detail("completed", 0), activationCount: activation } as AgentDetail;
      },
      streamChannel: channel([], false),
    };
    const m = new AgentViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush();
    expect(backfills).toBe(1);
    m.noteActivation("agent-a", 1);
    await flush();
    expect(backfills).toBe(1);   // same activation: nothing new to show
    activation = 2;
    m.noteActivation("agent-a", 2);
    await flush();
    expect(backfills).toBe(2);
    m.closeAll();
  });

  test("setOpen restarts a finished view instead of skipping it", async () => {
    let backfills = 0;
    const client = {
      getAgent: async () => {
        backfills += 1;
        return { ...detail("completed", 0), activationCount: 1 } as AgentDetail;
      },
      streamChannel: channel([], false),
    };
    const m = new AgentViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush();
    m.setOpen("t", ["agent-a"]);
    await flush();
    expect(backfills).toBe(2);
    m.closeAll();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm run -w crucible-vscode-extension test`; expected failures.

- [ ] **Step 3: Implement** the behaviors above. The queued-send code replaces the tail of
`sendChatMessage` (from `setChatInputEnabled(false)`):

```ts
    this.ui.setChatInputEnabled(false);
    let result: SendChatResult;
    try {
      result = await client.sendChatMessage(threadId, text, undefined, {
        ...(stepReview !== undefined ? { stepReview } : {}),
        ...(forcedSkills?.length ? { forcedSkills } : {}),
        ...(mentionedFiles?.length ? { mentionedFiles } : {}),
        ...(planMode !== undefined ? { planMode } : {}),
        messageId,
      });
    } catch (error) {
      // InputArea already cleared the draft: give the text back (spec §5.3).
      this.ui.removeChatMessage(messageId);
      this.ui.restoreDraft(text);
      this.ui.setChatInputEnabled(true);
      if (!this.isBenignConflict(error)) {
        this.ui.showError(`Failed to send: ${formatError(error)}`);
      }
      return;
    }
    if (result.kind === "queued") {
      // A notice turn took the message; its relay is already live (spec §5.3).
      this.ui.setChatInputEnabled(true);
      return;
    }
    this.turnAbort = new AbortController();
    await this.streamTurn(result.events);
```

`turnAbort` is created only on the stream path, so the queued and error paths leave it null. In
`agent-views.ts`, extend `OpenView` with `finished: boolean; activation: number` and add:

```ts
  /** A resumed agent (spec §6): a short activation can start and end between two polls,
   * so the view keys on activation_count, not a status edge. Views still following are
   * never restarted. */
  noteActivation(agentId: string, activationCount: number): void {
    const view = this.views.get(agentId);
    if (!view || activationCount <= view.activation) return;
    view.activation = activationCount;
    if (view.finished) this.restart(agentId, view.threadId);
  }

  private restart(agentId: string, threadId: string): void {
    const previous = this.views.get(agentId);
    const activation = previous?.activation ?? 0;
    this.close(agentId);
    const view: OpenView = { threadId, closed: false, abort: null, finished: false, activation };
    this.views.set(agentId, view);
    void this.run(agentId, view);
  }
```

`run` sets `view.activation = Math.max(view.activation, detail.activationCount)` after each backfill and
`view.finished = true` before returning on a terminal status; `setOpen` calls `restart` for an id whose view
is `finished` (and skips one still following). `chat-panel.ts` routes the `stopAllAgents` webview message to
`onStopAllAgents` and implements the new UI methods: `replaceMessages` posts `{type: "replaceMessages"}`,
`removeMessage` posts `{type: "removeChatMessage"}`, `restoreDraft` posts `{type: "composerPrefill", text}`.

- [ ] **Step 4: Run** — `npm run build && npm run typecheck && npm run -w crucible-vscode-extension test`; expected green.

- [ ] **Step 5: Commit** — `feat(extension): background agents, queued sends and transcript reconcile` (+ trailers)

---

### Task 2D.4: The webview

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`, `hooks/useAppState.ts`, `agents.ts`,
  `inputAvailability.ts`, `components/MessageRow.tsx`, `components/ThreadView.tsx`,
  `components/HistoryView.tsx`, `components/RewindDialog.tsx`
- Create: `components/messages/NoticeLine.tsx`, `components/messages/AgentMessageLine.tsx`
- Test: `inputAvailability.test.ts`, `test/useAppState.test.ts`, `components/RewindDialog.test.tsx`,
  `components/ThreadView.test.tsx`; fixtures in `test/agentWindow.test.tsx`, `test/assembly.test.tsx`,
  `test/views.test.tsx`

**Behavior:**
- **Types:** `AgentSummaryView` gains optional `activationCount`, `dispatcherId`, `onFinish`,
  `activationStartedAt`, `activationEndedAt`; `ThreadSummary.agentsRunning?: number`;
  `RewindPreviewView.blockedByAgents?: string[]`; `AppState` gains `turnKind: "user" | "notice" | null` and
  `agentsRunning: number`; the `liveStatus` host message gains optional `turnKind` and `agentsRunning`;
  `HostMessage` gains `replaceMessages {messages}` and `removeChatMessage {id}`; `WebviewMessage` gains
  `{type: "stopAllAgents"}`. **No draft-restore state:** the host's `restoreDraft` posts the existing
  `composerPrefill {text}`, which ThreadView already turns into the draft.
- **Reducer:** `liveStatus` copies `turnKind`/`agentsRunning` on both of its return paths; `replaceMessages`
  seals any streaming bubble and swaps `messages` only; `removeChatMessage` filters by id; `appendMessage`
  routes `agent_message`, `notice` and `team_created` through `appendDurable`, whose key function becomes:

```ts
/** The key a durable live message is deduplicated by (spec §6): a live broadcast and a
 * reload replay can both deliver one. */
function rosterKey(m: ChatMsg): string | null {
  const meta = m.metadata ?? {};
  switch (m.type) {
    case "agent_dispatch": {
      const ids = meta.agent_ids;
      return Array.isArray(ids) ? `dispatch:${ids.join(",")}` : null;
    }
    case "agent_message":
      return `message:${String(meta.agent_id)}:${String(meta.activation)}`;
    case "team_created":
      return `team:${String(meta.team_id)}`;
    case "notice":
      return m.id ? `notice:${m.id}` : null;
    default:
      return null;
  }
}
```

- **`inputAvailability`:** its parameter becomes `Pick<AppState, …> & Partial<Pick<AppState, "turnKind">>`
  (older call sites still compile). Rows 1–2 (edit/mode/clarify) count only main-agent gates
  (`!g.agent`). **Row 2b keeps counting every agent's command/MCP gate** — v1 Part E's rule that, during a
  turn, a sub-agent's card points the composer at it with Stop kept (pinned by
  `views.test.tsx` "a pending command or MCP gate during a turn…"). Row 3 ("Agent is working…") skips a
  notice turn, which gets its own row (enabled, Stop shown); a `null` kind with `turnActive` (a v1 backend)
  still disables. Before the default, background-only gates keep the composer enabled with
  `N card(s) need(s) your answer above`. The resulting function:

```ts
export function inputAvailability(
  state: Pick<AppState, "inputEnabled" | "liveStatus" | "workbar" | "liveGates" | "turnActive">
    & Partial<Pick<AppState, "turnKind">>,
): InputAvailability {
  const { inputEnabled, liveStatus, workbar, liveGates, turnActive } = state;
  const turnKind = state.turnKind ?? null;
  // Composer rules key on the MAIN agent's gates only (spec §6): a background agent's
  // card waits above without taking the composer away.
  const mainGates = liveGates.filter((g) => !g.agent);
  const backgroundGates = liveGates.length - mainGates.length;
  const hasGate = (kind: LiveGateView["kind"]) => mainGates.some((g) => g.kind === kind);
  const taskStop = liveStatus !== null && ABORTABLE_STATUSES.has(liveStatus);

  // ── Controller precedence (spec §5), first match wins, ahead of task rows ──
  // Row 1: per-edit gate — only the EditGate card is interactive.
  if (hasGate("edit")) {
    return {
      disabled: true,
      placeholder: "Waiting for your decision on the card above",
      showStop: false,
      taskStop,
    };
  }
  // Row 2: mode/clarify gate — the card (incl. its in-card field) is the input path.
  if (hasGate("mode")) {
    return {
      disabled: true,
      placeholder: "Choose how to proceed — or chat about it on the card",
      showStop: false,
      taskStop,
    };
  }
  if (hasGate("clarify")) {
    return {
      disabled: true,
      placeholder: "Answer on the card above",
      showStop: false,
      taskStop,
    };
  }
  // Row 2b: a command or MCP approval is pending — a sub-agent's or the main agent's.
  // The card is the input path; Stop stays available because the turn is still running.
  // Every agent's card counts here (v1 Part E): the turn already holds the composer, and
  // this row only points the user at the card while keeping Stop.
  const anyGate = (kind: LiveGateView["kind"]) => liveGates.some((g) => g.kind === kind);
  if (turnActive && (anyGate("command") || anyGate("mcp_tool"))) {
    return {
      disabled: true,
      placeholder: "Answer the card above…",
      showStop: true,
      taskStop,
    };
  }
  // Row 3: a controller turn is running (no gate). The durable reload-window guard:
  // a fresh webview mounts inputEnabled=true while the detached turn still runs.
  // Stop is shown — a controller turn can be stopped (no task is active here).
  // A notice turn (spec §5.3): the user may keep typing; a message sent now is queued.
  if (turnActive && turnKind === "notice") {
    return {
      disabled: false,
      placeholder: "Agents reported — type to add to this turn",
      showStop: true,
      taskStop,
    };
  }
  if (turnActive && (liveStatus === null || !TASK_ACTIVE_STATUSES.has(liveStatus))) {
    return {
      disabled: true,
      placeholder: "Agent is working…",
      showStop: true,
      taskStop,
    };
  }

  // Precedence 1: a local chat turn is streaming.
  if (!inputEnabled) {
    // Stop only shown when the disable comes from a streaming chat turn, not
    // from task execution — the task being active overrides the chat-turn case.
    const showStop =
      liveStatus === null || !TASK_ACTIVE_STATUSES.has(liveStatus);
    return {
      disabled: true,
      placeholder: "Agent is working…",
      showStop,
      taskStop,
    };
  }

  // Precedence 2: awaiting plan approval.
  if (liveStatus === "AWAITING_PLAN_APPROVAL") {
    return {
      disabled: true,
      placeholder: "Review the plan — Implement or Give feedback",
      showStop: false,
      taskStop,
    };
  }

  // Precedence 3: gate (waiting for a card decision).
  if (liveStatus !== null && GATE_STATUSES.has(liveStatus)) {
    return {
      disabled: true,
      placeholder: "Waiting for your decision on the card above",
      showStop: false,
      taskStop,
    };
  }

  // Precedence 4: task is actively running.
  if (liveStatus !== null && RUNNING_STATUSES.has(liveStatus)) {
    const { stepIndex, totalSteps } = workbar ?? {};
    const placeholder =
      stepIndex !== undefined &&
      stepIndex !== null &&
      totalSteps !== undefined &&
      totalSteps !== null
        ? `Task is running — step ${stepIndex} of ${totalSteps}…`
        : "Task is running…";
    return {
      disabled: true,
      placeholder,
      showStop: false,
      taskStop,
    };
  }

  // Background agents' cards wait above; the composer stays usable (spec §6).
  if (backgroundGates > 0) {
    const noun = backgroundGates === 1 ? "card needs" : "cards need";
    return {
      disabled: false,
      placeholder: `${backgroundGates} ${noun} your answer above`,
      showStop: false,
      taskStop,
    };
  }

  // Precedence 5 (default): enabled.
  return {
    disabled: false,
    placeholder: "Ask anything or describe a change…",
    showStop: false,
    taskStop,
  };
}
```

- **Rendering:** `notice` → `NoticeLine` (opens the agent through `useAgentsUi().openWindow`, no host round
  trip); `agent_message` → `AgentMessageLine`; `team_created` → nothing until Phase 4. Roster elapsed time
  uses the activation span; a running resumed agent still carries the previous activation's end stamp, so
  an end earlier than the start means "still running":

```ts
export function elapsedMs(
  agent: { startedAt: string | null; endedAt: string | null;
           activationStartedAt?: string | null; activationEndedAt?: string | null },
  now: number,
): number | null {
  // The current activation's span (spec §6): a resumed agent's clock restarts.
  const started = agent.activationStartedAt ?? agent.startedAt;
  if (!started) return null;
  const ended = agent.activationStartedAt ? agent.activationEndedAt : agent.endedAt;
  const start = Date.parse(started);
  // A running resumed agent still carries the previous activation's end stamp.
  const end = ended && Date.parse(ended) >= start ? Date.parse(ended) : now;
  return end - start;
}
```

```tsx
// components/messages/NoticeLine.tsx
import type { ChatMsg } from "../../types";
import { useAgentsUi } from "../agents/AgentsContext";

/** The marker a notice turn writes first (spec §5.3, §6): a compact bell line; clicking it
 * opens the agent that woke the main agent. */
export function NoticeLine({ msg }: { msg: ChatMsg }) {
  const { openWindow } = useAgentsUi();
  const target = msg.metadata?.target as { agent_id?: string } | undefined;
  const agentId = typeof target?.agent_id === "string" ? target.agent_id : null;
  const text = msg.content.replace(/^🔔\s*/, "");
  return (
    <div className="flex items-center gap-2 px-2 py-1 text-xs text-text-2">
      <span aria-hidden="true">🔔</span>
      {agentId ? (
        <button
          type="button"
          className="text-left underline-offset-2 hover:underline"
          style={{ color: "var(--color-accent)" }}
          onClick={() => openWindow(agentId, [agentId])}
        >
          {text}
        </button>
      ) : (
        <span>{text}</span>
      )}
    </div>
  );
}
```

```tsx
// components/messages/AgentMessageLine.tsx
import type { ChatMsg } from "../../types";

/** A resume (spec §6): which agent got a follow-up, and its first line. */
export function AgentMessageLine({ msg }: { msg: ChatMsg }) {
  const label = String(msg.metadata?.label ?? msg.metadata?.agent_id ?? "agent");
  const firstLine = msg.content.split("\n")[0] ?? "";
  return (
    <div className="px-2 py-1 text-xs text-text-2 truncate" title={msg.content}>
      ↳ to <span className="text-text">{label}</span>: {firstLine}
    </div>
  );
}
```

- **Header:** a "Stop all agents" button (`Icon name="stop"`, `title="Stop all agents"`, posts
  `stopAllAgents`) shown while `state.agentsRunning > 0`.
- **History:** next to `StatusChip`, `N agent(s)` when `thread.agentsRunning > 0`.
- **Rewind dialog:** blocked when `blockedByTask != null || (blockedByAgents ?? []).length > 0`; for agents it
  reads `Agents <labels> are still running and could edit these files.` with a **Stop all agents** button
  (new optional prop `onStopAllAgents`). ThreadView passes a handler that posts `stopAllAgents` and then
  `rewindPreview {messageId}` to refresh the refusal.

- [ ] **Step 1: Write the failing tests.**

```ts
// inputAvailability.test.ts (uses the file's existing `base`)
describe("inputAvailability — background agents (spec §5.3, §6)", () => {
  it("a background agent's edit gate leaves the composer usable", () => {
    const r = inputAvailability({ ...base, turnKind: null,
      liveGates: [{ gateId: "g", kind: "edit", taskId: "", payload: {},
                    agent: { id: "a", label: "a", name: "explore" } }] });
    expect(r.disabled).toBe(false);
    expect(r.placeholder).toBe("1 card needs your answer above");
  });

  it("a notice turn keeps the composer enabled with Stop", () => {
    const r = inputAvailability({ ...base, turnActive: true, turnKind: "notice" });
    expect(r).toMatchObject({ disabled: false, showStop: true });
  });

  it("a user turn still disables", () => {
    expect(inputAvailability({ ...base, turnActive: true, turnKind: "user" }).disabled).toBe(true);
  });

  it("an old backend that sends no turn kind still disables during a turn", () => {
    expect(inputAvailability({ ...base, turnActive: true }).disabled).toBe(true);
  });
});
```

```ts
// test/useAppState.test.ts (uses the file's fireMessage helper)
describe("useAppState — background agents (spec §6)", () => {
  const msg = (id: string) => ({ role: "agent" as const, content: id, type: "text" as const,
                                 id, timestamp: "2026-10-04T00:00:00Z", metadata: {} });

  it("replaceMessages swaps the transcript and keeps the roster", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "appendMessage", message: msg("a") }); });
    act(() => { fireMessage({ type: "renderAgents", agents: [{
      agentId: "x", parentAgentId: null, depth: 1, name: "explore", label: "x",
      status: "running", now: "", toolCount: 0, filesChangedCount: 0, startedAt: null,
      endedAt: null, reportPreview: "" }] }); });
    const agents = result.current.state.agents;
    act(() => { fireMessage({ type: "replaceMessages", messages: [msg("a"), msg("b")] }); });
    expect(result.current.state.messages.map((m) => m.id)).toEqual(["a", "b"]);
    expect(result.current.state.agents).toBe(agents);
  });

  it("removeChatMessage drops one message by id", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "appendMessage", message: msg("a") }); });
    act(() => { fireMessage({ type: "appendMessage", message: msg("b") }); });
    act(() => { fireMessage({ type: "removeChatMessage", id: "a" }); });
    expect(result.current.state.messages.map((m) => m.id)).toEqual(["b"]);
  });

  it("a notice marker delivered twice renders once", () => {
    const { result } = renderHook(() => useAppState());
    const notice = { ...msg("n1"), type: "notice" as const, content: "🔔 done" };
    act(() => { fireMessage({ type: "appendMessage", message: notice }); });
    act(() => { fireMessage({ type: "appendMessage", message: notice }); });
    expect(result.current.state.messages.filter((m) => m.type === "notice")).toHaveLength(1);
  });

  it("liveStatus carries the turn kind and the running-agent count", () => {
    const { result } = renderHook(() => useAppState());
    act(() => { fireMessage({ type: "liveStatus", status: null, turnActive: true,
                              turnKind: "notice", agentsRunning: 2 }); });
    expect([result.current.state.turnKind, result.current.state.agentsRunning])
      .toEqual(["notice", 2]);
  });
});
```

```tsx
// components/RewindDialog.test.tsx (uses the file's `preview` fixture)
describe("RewindDialog — running agents (spec §8.10)", () => {
  it("explains running agents and offers Stop all", () => {
    const onStopAllAgents = vi.fn();
    render(<RewindDialog preview={{ ...preview, blockedByAgents: ["scout"] }}
                         onCancel={() => {}} onConfirm={() => {}}
                         onStopAllAgents={onStopAllAgents} />);
    expect(screen.getByText(/scout are still running/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop all agents" }));
    expect(onStopAllAgents).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "Rewind" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
```

```tsx
// components/ThreadView.test.tsx (uses the file's `base` AppState)
describe("ThreadView — Stop all agents (spec §6)", () => {
  it("shows Stop all agents only while agents run", () => {
    const { rerender } = render(
      <ThreadView state={base} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />,
    );
    expect(screen.queryByTitle("Stop all agents")).toBeNull();
    rerender(
      <ThreadView state={{ ...base, agentsRunning: 2 }} onBack={() => {}} dismissedErrorTaskId={null}
                  onDismissError={() => {}} />,
    );
    fireEvent.click(screen.getByTitle("Stop all agents"));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "stopAllAgents" });
  });
});
```

- [ ] **Step 2: Update fixtures.** Full-`AppState` fixtures gain `turnKind: null, agentsRunning: 0`:
`components/ThreadView.test.tsx` (`base`), `test/agentWindow.test.tsx`, `test/assembly.test.tsx`
(`makeState`). `test/views.test.tsx` builds an `inputAvailability` argument — give it `turnKind: null` only.

- [ ] **Step 3: Run to verify failure** — in `apps/vscode-extension/webview-ui`: `npx vitest run`.

- [ ] **Step 4: Implement** per the behavior list.

- [ ] **Step 5: Run** — `npx tsc --noEmit && npx vitest run` in `webview-ui`, then from the repo root
`npm run build` (rebuilds `webview-ui/dist`), `npm run typecheck`, `npm run test`; expected green.

- [ ] **Step 6: Commit** — `feat(webview): background agents, notices and Stop all` (+ trailers)

---

### Task 2D.5: Phase 2 checkpoint

- [ ] **Step 1:** Full Python suite with `--timeout=120` (redirected; read `$?` and the tail) — all pass except the
known timing flakes (`test_command_only_step`; `test_memory_harness_warmup_no_race` under load). Reproduce any
other failure in isolation before attributing it.
- [ ] **Step 2:** `npm run build && npm run typecheck && npm run test` from the repo root — green.
- [ ] **Step 3:** `ruff check` and `mypy agentd` on the changed Python files — no new findings.
- [ ] **Step 4:** Live smoke (dev host, NIM nemotron-3-ultra, reasoning effort unset): dispatch with `wake` →
answer → the notice turn starts and its marker renders; dispatch → `wait_agents` → answer; a message typed
during a notice turn is queued and answered; Stop all; a rewind refused while an agent runs.
