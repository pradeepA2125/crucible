# Chat UI v2 Tier A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the five "design already settled" items deferred from the 2026-06-09 chat UI redesign: thread-list status chips + message counts + updated-at, `unified_diff` on the wire with tabbed in-card diff panes, a `✓ Step completed` breadcrumb on auto-accept, a persisted transcript record of step-review diffs, and a per-task "Review each step" composer toggle.

**Architecture:** Backend-first per item: enrich existing payloads (thread summaries, `DiffEntry`) rather than adding new endpoints; the webview renders from persisted/polled state per the Class-A model. The hi-fi mockup `docs/superpowers/design/chat-ui-hifi.html` is the visual source of truth (frame 1 `.chip-status`/`.hmeta`, frame 3 `.tabs`/`.diffpane`/`.dline`).

**Tech Stack:** FastAPI + Pydantic + sqlite (agentd-py), Zod + fetch (editor-client), React + Tailwind webview (webview-ui), vitest + pytest.

**Verified source facts this plan relies on (re-verify if stale):**
- `ChatThreadStore.list_threads` (`services/agentd-py/agentd/chat/storage.py:52`) already deserializes every thread's full `messages` list; `ChatThread` has `active_task_id` (`chat/models.py:46`); `ChatMessage` has a `timestamp` field (`chat/models.py:32`).
- `GET /chat/threads` route (`api/routes.py:953-956`) returns `t.model_dump(exclude={"messages"})`; the chat router closes over `store` (the TaskStore) and `orchestrator`.
- `_compute_diff_entries` (`orchestrator/engine.py:1067-1091`) computes `difflib.unified_diff` and discards the text after counting; `DiffEntry` is a dataclass `{path, additions, deletions, temp_path}` (`domain/models.py:849`).
- Step review: `_pause_for_step_review` (`engine.py:1835`) serializes entries via `dataclasses.asdict`, stores `StepReviewPayload{step_id, step_title, diff_entries}` (`domain/models.py:192`), broadcasts `step_review_requested`, writes a `✓/↩` breadcrumb in `finally`. Auto-accept path: `_execute_plan` skips the gate when `task.step_review_auto_accept` (`engine.py:1475`).
- `TaskCreateRequest.step_review_auto_accept: bool | None` exists (`domain/models.py:766`); `create_task_from_chat` (`engine.py:1137`) builds `TaskCreateRequest` and applies the env default itself (`engine.py:1158-1162`).
- `POST /chat/threads/{id}/message` body is a plain dict; route reads `request.get("content") or request.get("message", "")` (`api/routes.py:1010`).
- Client: `sendChatMessage` posts `{content}` (`http-backend-client.ts:340-348`); `listChatThreads` parses `ChatThreadSummarySchema {threadId, workspacePath, title, createdAt}` (`task-contracts.ts:183`).
- Webview: `ThreadSummary {threadId, title, createdAt}` (`types.ts:30`); `HistoryView.tsx` renders `relativeTime(thread.createdAt)` at line ~273; `StepGate.tsx` parses `payload.diff_entries`; `DiffCard.tsx` expanded body renders `FileRow`s; `InputArea.tsx` posts `{type:"sendMessage", text}`; `chat-panel.ts:96-97` forwards to `onMessage(text)`.
- Test harness pattern for chat routes: `tests/test_chat_live_route.py::_build` (real `ChatThreadStore` + `InMemoryTaskStore` + `AgentOrchestrator` + httpx `ASGITransport`).

**Task order:** 1 → 2 → 3 → 4 → 5. Task 3 depends on Task 2. Tasks 4 and 5 are independent of 2/3 but ordered after to keep diffs reviewable.

---

### Task 1: Thread-list enrichment — status chip, message count, updated_at

The mockup's history rows show `2 min ago · 3 messages` plus a Running/Review/Done chip. None of that is on the wire today.

**Decision (locked):** derive everything at query time in the route — `message_count = len(messages)`, `updated_at` = last message timestamp (fall back to `created_at`), `status` chip from the thread's `active_task_id` task via a pure mapping. No sqlite schema change, no per-thread `/live` polling. Failed/aborted tasks get a `failed` chip (mockup omits it; red styling mirrors `done`).

**Files:**
- Modify: `services/agentd-py/agentd/chat/live_state.py` (add `thread_status_chip`)
- Modify: `services/agentd-py/agentd/api/routes.py` (`list_chat_threads`)
- Test: `services/agentd-py/tests/test_thread_summaries.py` (new)
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`ChatThreadSummarySchema`)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`listChatThreads`)
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (`ThreadSummary`)
- Modify: `apps/vscode-extension/webview-ui/src/components/HistoryView.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/views.test.tsx` (extend)

- [ ] **Step 1: Write the failing backend test**

Create `services/agentd-py/tests/test_thread_summaries.py`:

```python
"""GET /chat/threads carries message_count, updated_at, and a status chip."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.agent import ChatAgent
from agentd.chat.live_state import thread_status_chip
from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import TaskRecord, TaskStatus, ValidationResult
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _NullTransport:
    async def generate_text(self, **_) -> str:
        return "x"

    async def generate_json(self, *, schema_name, **_) -> dict:
        return {"intent": "qa", "rationale": "", "likely_targets": []}


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _build(tmp_path: Path):
    store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    orch = AgentOrchestrator(
        store=store,
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ws_manager,
        chat_store=chat_store,
    )
    agent = ChatAgent(
        store=chat_store,
        workspace_path=str(tmp_path),
        transport=_NullTransport(),
        model="m",
        orchestrator=orch,
    )
    app = FastAPI()
    app.include_router(build_router(store, orch, ws_manager, chat_agent=agent))
    return app, store, chat_store


# ── chip mapping (pure) ──────────────────────────────────────────────────────


def test_chip_mapping() -> None:
    assert thread_status_chip("EXECUTING") == "running"
    assert thread_status_chip("QUEUED") == "running"
    assert thread_status_chip("VALIDATING") == "running"
    assert thread_status_chip("AWAITING_PLAN_APPROVAL") == "review"
    assert thread_status_chip("AWAITING_STEP_REVIEW") == "review"
    assert thread_status_chip("AWAITING_COMMAND_DECISION") == "review"
    assert thread_status_chip("READY_FOR_REVIEW") == "review"
    assert thread_status_chip("SUCCEEDED") == "done"
    assert thread_status_chip("FAILED") == "failed"
    assert thread_status_chip("ABORTED") == "failed"
    assert thread_status_chip(None) is None
    assert thread_status_chip("NOT_A_STATUS") is None


# ── route ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_threads_carries_count_updated_at_and_status(tmp_path: Path) -> None:
    app, store, chat_store = _build(tmp_path)
    thread = chat_store.create_thread(str(tmp_path))
    chat_store.append_message(thread.thread_id, ChatMessage(role="user", content="hi"))
    chat_store.append_message(thread.thread_id, ChatMessage(role="agent", content="yo"))

    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "gate")
    await store.create(task)
    chat_store.set_active_task(thread.thread_id, "t1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(f"/v1/chat/threads?workspace={tmp_path}")

    assert resp.status_code == 200
    [summary] = resp.json()["threads"]
    assert summary["message_count"] == 2
    assert summary["status"] == "review"
    # updated_at must be the LAST message's timestamp, not created_at.
    assert summary["updated_at"] >= summary["created_at"]


@pytest.mark.asyncio
async def test_list_threads_without_task_has_null_status(tmp_path: Path) -> None:
    app, _store, chat_store = _build(tmp_path)
    chat_store.create_thread(str(tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(f"/v1/chat/threads?workspace={tmp_path}")

    [summary] = resp.json()["threads"]
    assert summary["status"] is None
    assert summary["message_count"] == 0
    assert summary["updated_at"] == summary["created_at"]
```

NOTE: before running, verify `ChatThreadStore.set_active_task` exists (grep `set_active_task` in `chat/storage.py`); if it is named differently (e.g. only settable via engine), set the column directly in the test:
`chat_store._conn.execute("UPDATE chat_threads SET active_task_id=? WHERE thread_id=?", ("t1", thread.thread_id)); chat_store._conn.commit()`.
Also verify the `ChatAgent` constructor kwarg names against `chat/agent.py::__init__` and `build_router`'s `chat_agent` kwarg name against `api/routes.py::build_router` — copy whatever `tests/test_chat_live_route.py::_build` does.

- [ ] **Step 2: Run the test — expect failure**

Run: `cd services/agentd-py && source .venv/bin/activate && python -m pytest tests/test_thread_summaries.py -q`
Expected: `ImportError: cannot import name 'thread_status_chip'`

- [ ] **Step 3: Implement `thread_status_chip` in `chat/live_state.py`**

Append to `services/agentd-py/agentd/chat/live_state.py`:

```python
# Task-status → history-list chip. Mirrors the mockup's Running/Review/Done
# chips (frame 1); failed/aborted get a "failed" chip the mockup omits.
_CHIP_RUNNING = frozenset({
    "QUEUED", "CONTEXT_READY", "PLANNED", "EXECUTING",
    "VALIDATING", "REPAIRING", "VALIDATED", "PROMOTING",
})
_CHIP_REVIEW = frozenset({
    "AWAITING_PLAN_APPROVAL", "AWAITING_STEP_REVIEW",
    "AWAITING_COMMAND_DECISION", "AWAITING_SCOPE_DECISION",
    "AWAITING_VALIDATION_DECISION", "READY_FOR_REVIEW",
})


def thread_status_chip(status: str | None) -> str | None:
    """Map a task status to the history-list chip, or None for no chip."""
    if status in _CHIP_RUNNING:
        return "running"
    if status in _CHIP_REVIEW:
        return "review"
    if status == "SUCCEEDED":
        return "done"
    if status in ("FAILED", "ABORTED"):
        return "failed"
    return None
```

- [ ] **Step 4: Enrich the route**

In `services/agentd-py/agentd/api/routes.py`, replace the `list_chat_threads` body (`routes.py:953-956`):

```python
        @router.get("/chat/threads")
        async def list_chat_threads(workspace: str) -> dict:
            from agentd.chat.live_state import thread_status_chip

            threads = _chat_agent._store.list_threads(workspace)
            summaries: list[dict] = []
            for t in threads:
                status: str | None = None
                if t.active_task_id:
                    try:
                        task = await store.get(t.active_task_id)
                        status = str(task.status)
                    except KeyError:
                        status = None  # task pruned — no chip
                last_ts = t.messages[-1].timestamp if t.messages else t.created_at
                summary = t.model_dump(exclude={"messages"})
                summary["message_count"] = len(t.messages)
                summary["updated_at"] = last_ts.isoformat()
                summary["status"] = thread_status_chip(status)
                summaries.append(summary)
            return {"threads": summaries}
```

- [ ] **Step 5: Run the backend tests**

Run: `python -m pytest tests/test_thread_summaries.py tests/test_chat_routes.py tests/test_chat_live_route.py -q`
Expected: all PASS

- [ ] **Step 6: Extend the client contract + mapping**

`apps/editor-client/src/contracts/task-contracts.ts` — extend `ChatThreadSummarySchema` (line 183):

```typescript
export const ChatThreadSummarySchema = z.object({
  threadId: z.string(),
  workspacePath: z.string(),
  title: z.string(),
  createdAt: z.string(),
  updatedAt: z.string().optional(),
  messageCount: z.number().optional(),
  status: z.enum(["running", "review", "done", "failed"]).nullable().optional(),
});
```

`apps/editor-client/src/client/http-backend-client.ts` — extend the `listChatThreads` mapping (line 277-284):

```typescript
    return (threads as Record<string, unknown>[]).map((t) =>
      ChatThreadSummarySchema.parse({
        threadId: t["thread_id"],
        workspacePath: t["workspace_path"],
        title: t["title"],
        createdAt: t["created_at"],
        updatedAt: t["updated_at"] ?? undefined,
        messageCount: t["message_count"] ?? undefined,
        status: t["status"] ?? null,
      })
    );
```

Also update `createChatThread`'s parse call in the same file if it reuses `ChatThreadSummarySchema` — the new fields are optional so it parses unchanged; verify by reading it.

- [ ] **Step 7: Rebuild editor-client (extension types off dist)**

Run: `cd "$(git rev-parse --show-toplevel)" && npm run -w @crucible/editor-client build && npm run -w @crucible/editor-client test`
Expected: build OK, 23+ tests PASS

- [ ] **Step 8: Webview type + HistoryView render**

`apps/vscode-extension/webview-ui/src/types.ts` — extend `ThreadSummary` (line 30):

```typescript
export interface ThreadSummary {
  threadId: string;
  title: string;
  createdAt: string;
  updatedAt?: string;
  messageCount?: number;
  status?: "running" | "review" | "done" | "failed" | null;
}
```

NOTE: the extension forwards summaries to the webview via `renderChatThreadList` (`extension.ts:120-122` → `chat-panel.ts::renderThreadList`). Verify the forwarded objects carry the new fields (they're plain spreads of `ChatThreadSummary`; if the panel re-maps fields explicitly, add the three new ones there).

`apps/vscode-extension/webview-ui/src/components/HistoryView.tsx`:
1. Switch grouping/relative time to `thread.updatedAt ?? thread.createdAt` (lines ~83 and ~226 — `getDayGroup(...)` / `relativeTime(...)` arguments).
2. Extend the meta line (around line 273) to:

```tsx
        <div className="text-[10.5px] text-text-4 tabular-nums flex items-center gap-1.5 mt-0.5">
          {relTime}
          {typeof thread.messageCount === "number" && (
            <>
              <span>·</span>
              <span>
                {thread.messageCount} {thread.messageCount === 1 ? "message" : "messages"}
              </span>
            </>
          )}
        </div>
```

(Adapt the wrapper classes to whatever the current meta line uses — read it first; only ADD the `· N messages` span.)

3. Add the chip between the title block and the chevron, mirroring mockup `.chip-status`:

```tsx
function StatusChip({ status }: { status?: ThreadSummary["status"] }) {
  if (!status) return null;
  const cfg = {
    running: { label: "Running", color: "var(--color-accent-ink)", bg: "var(--accent-bg)", brd: "var(--accent-brd)" },
    review:  { label: "Review",  color: "var(--color-amber, #fbbf24)", bg: "rgba(251,191,36,.09)", brd: "rgba(251,191,36,.25)" },
    done:    { label: "Done",    color: "var(--color-green)", bg: "var(--green-bg)", brd: "var(--green-brd)" },
    failed:  { label: "Failed",  color: "var(--color-red)", bg: "var(--red-bg)", brd: "var(--red-brd)" },
  }[status];
  return (
    <span
      className="inline-flex items-center gap-1 text-[9.5px] font-semibold px-[7px] py-px rounded-full flex-shrink-0"
      style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.brd}` }}
    >
      {status === "running" && (
        <span className="w-[5px] h-[5px] rounded-full flex-shrink-0"
          style={{ background: "var(--color-accent)", animation: "pulse 1.5s ease-in-out infinite" }} />
      )}
      {cfg.label}
    </span>
  );
}
```

Place `<StatusChip status={thread.status} />` in the row's flex container right before the chevron icon. Verify the CSS variable names against `webview-ui/src/index.css` (or wherever the token vars are declared) — use the exact names that exist; hard-code the hex from the mockup tokens if a var is missing.

- [ ] **Step 9: Webview test**

Append to `apps/vscode-extension/webview-ui/src/test/views.test.tsx` (mirror its existing HistoryView test setup — read the file's helpers first and reuse them):

```tsx
describe("HistoryView — enriched summaries", () => {
  it("renders message count and a Review chip", () => {
    renderHistory([
      { threadId: "t1", title: "Fix planner", createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(), messageCount: 7, status: "review" },
    ]);
    expect(screen.getByText(/7 messages/)).toBeTruthy();
    expect(screen.getByText("Review")).toBeTruthy();
  });

  it("renders no chip and no count for a bare summary", () => {
    renderHistory([
      { threadId: "t2", title: "Old thread", createdAt: new Date().toISOString() },
    ]);
    expect(screen.queryByText(/messages/)).toBeNull();
    expect(screen.queryByText("Review")).toBeNull();
  });
});
```

(`renderHistory` = whatever helper the existing HistoryView tests use; if none exists, render `<HistoryView state={...}/>` with the minimal AppState the component requires — copy an existing test's state literal.)

- [ ] **Step 10: Run webview tests + builds**

Run: `cd apps/vscode-extension/webview-ui && npm test && npm run build && cd .. && npm run typecheck`
Expected: all PASS, build OK

- [ ] **Step 11: Commit**

```bash
git add services/agentd-py/agentd/chat/live_state.py services/agentd-py/agentd/api/routes.py services/agentd-py/tests/test_thread_summaries.py apps/editor-client/src apps/vscode-extension/webview-ui/src
git commit -m "feat(chat): thread list status chips, message counts, updated_at"
```

---

### Task 2: `unified_diff` on the wire

`_compute_diff_entries` already builds the unified diff and throws it away. Keep it, capped.

**Decision (locked):** cap at 400 lines AND 24,000 chars per file (whichever hits first), append a truncation marker line. The full diff stays available via "Open diff in editor" (`temp_path` against the real file).

**Files:**
- Modify: `services/agentd-py/agentd/domain/models.py` (`DiffEntry`)
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (`_compute_diff_entries`)
- Test: `services/agentd-py/tests/test_unified_diff_wire.py` (new)

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_unified_diff_wire.py`:

```python
"""DiffEntry carries the unified diff text (capped) for in-card rendering."""
from __future__ import annotations

from pathlib import Path

from agentd.orchestrator.engine import AgentOrchestrator, _cap_unified_diff
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


def _orch(tmp_path: Path) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )


def test_diff_entries_carry_unified_diff(tmp_path: Path) -> None:
    real = tmp_path / "real"; real.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    (real / "a.py").write_text("x = 1\ny = 2\n")
    (shadow / "a.py").write_text("x = 1\ny = 3\n")

    [entry] = _orch(tmp_path)._compute_diff_entries(real, shadow, ["a.py"], "t1")

    assert entry.additions == 1 and entry.deletions == 1
    assert "-y = 2" in entry.unified_diff
    assert "+y = 3" in entry.unified_diff
    assert "@@" in entry.unified_diff


def test_unified_diff_is_capped() -> None:
    lines = [f"+line {i}" for i in range(1000)]
    capped = _cap_unified_diff("\n".join(lines))
    assert len(capped.splitlines()) <= 401  # 400 + truncation marker
    assert capped.endswith("… diff truncated — open in editor for the full diff")


def test_new_file_diff_renders(tmp_path: Path) -> None:
    real = tmp_path / "real"; real.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    (shadow / "new.py").write_text("a = 1\n")

    [entry] = _orch(tmp_path)._compute_diff_entries(real, shadow, ["new.py"], "t1")
    assert "+a = 1" in entry.unified_diff
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_unified_diff_wire.py -q`
Expected: `ImportError: cannot import name '_cap_unified_diff'`

- [ ] **Step 3: Implement**

`services/agentd-py/agentd/domain/models.py` — extend the dataclass (line 849):

```python
@dataclass
class DiffEntry:
    path: str
    additions: int
    deletions: int
    temp_path: str
    # Capped unified diff text for in-card rendering (Tier A item; the full
    # diff stays available via the native editor diff against temp_path).
    unified_diff: str = ""
```

`services/agentd-py/agentd/orchestrator/engine.py` — add the module-level cap helper (near the other module helpers) and keep the diff text:

```python
_DIFF_MAX_LINES = 400
_DIFF_MAX_CHARS = 24_000
_DIFF_TRUNCATION_MARKER = "… diff truncated — open in editor for the full diff"


def _cap_unified_diff(diff_text: str) -> str:
    """Bound per-file diff text for chat payload/persistence."""
    lines = diff_text.splitlines()
    truncated = False
    if len(lines) > _DIFF_MAX_LINES:
        lines = lines[:_DIFF_MAX_LINES]
        truncated = True
    text = "\n".join(lines)
    if len(text) > _DIFF_MAX_CHARS:
        text = text[:_DIFF_MAX_CHARS]
        truncated = True
    if truncated:
        text += "\n" + _DIFF_TRUNCATION_MARKER
    return text
```

In `_compute_diff_entries` (`engine.py:1082-1090`), the diff is already in `diff`; pass it through:

```python
            diff = list(difflib.unified_diff(real_lines, shadow_lines, lineterm=""))
            additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
            entries.append(DiffEntry(
                path=rel,
                additions=additions,
                deletions=deletions,
                temp_path=str(shadow_file),
                unified_diff=_cap_unified_diff("\n".join(diff)),
            ))
```

No consumer changes needed: `diff_ready` / diff-card persistence build payloads via dict comprehension over fields or `dataclasses.asdict` — **verify both serializers**: `engine.py` diff-card persistence (`{"path": e.path, ...}` dict literal at ~line 1020) must add `"unified_diff": e.unified_diff`; `_pause_for_step_review` uses `dataclasses.asdict` (picks it up automatically); the `diff_ready` broadcast uses a dict literal (~line 1044) — add the field there too.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_unified_diff_wire.py tests/test_orchestrator_inline_change.py tests/test_chat_tool_events.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/domain/models.py services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/test_unified_diff_wire.py
git commit -m "feat(diff): carry capped unified_diff on DiffEntry payloads"
```

---

### Task 3: Tabbed diff panes in DiffCard + StepGate (webview)

Mockup frame 3: `.tabs` (one tab per file, colored dot) + `.diffpane` with `.dline add/del/ctx/hunk` rows and line numbers. Render when `unified_diff` is present; fall back to today's `FileRow` list when absent (old persisted messages).

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/shared/DiffPanes.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (`DiffEntry`)
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/gates/StepGate.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/DiffPanes.test.tsx` (new)

- [ ] **Step 1: Extend the webview `DiffEntry` type**

In `apps/vscode-extension/webview-ui/src/types.ts`, find `interface DiffEntry` (grep it) and add:

```typescript
  unified_diff?: string;
```

(Snake_case — SSE/live payloads are NOT case-mapped; the persisted metadata uses the same key.)

- [ ] **Step 2: Write the failing component test**

Create `apps/vscode-extension/webview-ui/src/test/DiffPanes.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DiffPanes } from "../components/shared/DiffPanes";
import type { DiffEntry } from "../types";

const ENTRIES: DiffEntry[] = [
  {
    path: "src/a.py", additions: 1, deletions: 1, temp_path: "/tmp/a.py",
    unified_diff: "--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3",
  },
  {
    path: "src/b.ts", additions: 1, deletions: 0, temp_path: "/tmp/b.ts",
    unified_diff: "--- a/src/b.ts\n+++ b/src/b.ts\n@@ -5,1 +5,2 @@\n ctx\n+added",
  },
];

describe("DiffPanes", () => {
  it("renders one tab per file and the first pane's lines", () => {
    render(<DiffPanes entries={ENTRIES} />);
    expect(screen.getByRole("tab", { name: /a\.py/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /b\.ts/ })).toBeTruthy();
    expect(screen.getByText("-")).toBeTruthy();   // del marker cell
    expect(screen.getByText("y = 3")).toBeTruthy();
  });

  it("switches panes on tab click", () => {
    render(<DiffPanes entries={ENTRIES} />);
    fireEvent.click(screen.getByRole("tab", { name: /b\.ts/ }));
    expect(screen.getByText("added")).toBeTruthy();
    expect(screen.queryByText("y = 3")).toBeNull();
  });

  it("numbers lines from the hunk header", () => {
    render(<DiffPanes entries={[ENTRIES[1]]} />);
    // @@ -5,1 +5,2 @@ → ctx line is 5, added line is 6 (new-file numbering)
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();
  });

  it("returns null when no entry has diff text", () => {
    const bare = ENTRIES.map((e) => ({ ...e, unified_diff: undefined }));
    const { container } = render(<DiffPanes entries={bare} />);
    expect(container.innerHTML).toBe("");
  });
});
```

- [ ] **Step 3: Run — expect failure**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/DiffPanes.test.tsx`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement `DiffPanes`**

Create `apps/vscode-extension/webview-ui/src/components/shared/DiffPanes.tsx`:

```tsx
import { useState } from "react";
import type { DiffEntry } from "../../types";

interface Props {
  entries: DiffEntry[];
}

type LineKind = "add" | "del" | "ctx" | "hunk";
interface DiffLine { kind: LineKind; num: string; marker: string; text: string }

/** Parse capped unified-diff text into renderable lines with new-file numbering. */
function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  let newLine = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("+++") || raw.startsWith("---")) continue;
    const hunk = raw.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      newLine = parseInt(hunk[1], 10);
      out.push({ kind: "hunk", num: "", marker: "", text: raw });
      continue;
    }
    if (raw.startsWith("+")) {
      out.push({ kind: "add", num: String(newLine++), marker: "+", text: raw.slice(1) });
    } else if (raw.startsWith("-")) {
      out.push({ kind: "del", num: "", marker: "-", text: raw.slice(1) });
    } else {
      out.push({ kind: "ctx", num: String(newLine++), marker: "", text: raw.startsWith(" ") ? raw.slice(1) : raw });
    }
  }
  return out;
}

const LINE_STYLE: Record<LineKind, { row: string; marker: string }> = {
  add:  { row: "bg-[rgba(74,222,128,.07)] text-[#b6f0c8]", marker: "text-green" },
  del:  { row: "bg-[rgba(248,113,113,.06)] text-[#f3b8b8]", marker: "text-red" },
  ctx:  { row: "text-text-3", marker: "" },
  hunk: { row: "text-text-4 italic", marker: "" },
};

/**
 * DiffPanes — mockup frame 3 `.tabs` + `.diffpane`: one tab per changed file,
 * unified-diff lines with new-file line numbers. Renders nothing when no entry
 * carries diff text (pre-unified_diff messages fall back to FileRow lists).
 */
export function DiffPanes({ entries }: Props) {
  const withDiff = entries.filter((e) => !!e.unified_diff);
  const [active, setActive] = useState(0);
  if (withDiff.length === 0) return null;
  const current = withDiff[Math.min(active, withDiff.length - 1)];

  return (
    <div className="border-t border-border">
      <div role="tablist" className="flex gap-0.5 px-2 border-b border-border overflow-x-auto">
        {withDiff.map((entry, i) => (
          <button
            key={entry.path}
            role="tab"
            aria-selected={i === active}
            onClick={(e) => { e.stopPropagation(); setActive(i); }}
            className={[
              "mono text-[10.5px] px-2.5 py-[7px] whitespace-nowrap cursor-pointer bg-transparent border-0",
              "border-b-[1.5px] -mb-px",
              i === active
                ? "text-accent-ink border-b-accent"
                : "text-text-3 hover:text-text-2 border-b-transparent",
            ].join(" ")}
            style={{ borderBottomStyle: "solid" }}
          >
            {entry.path.split("/").pop()}
          </button>
        ))}
      </div>
      <div className="max-h-48 overflow-auto py-1.5">
        {parseUnifiedDiff(current.unified_diff ?? "").map((line, i) => (
          <div key={i} className={`flex mono text-[10.5px] leading-[1.8] whitespace-pre pr-3 ${LINE_STYLE[line.kind].row}`}>
            <span className="w-[34px] flex-shrink-0 text-right pr-2.5 text-text-4 select-none tabular-nums">
              {line.num}
            </span>
            <span className={`w-3.5 flex-shrink-0 ${LINE_STYLE[line.kind].marker}`}>{line.marker}</span>
            <span>{line.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the DiffPanes test**

Run: `npx vitest run src/test/DiffPanes.test.tsx`
Expected: PASS. If the line-numbering assertion fails, fix the parser, not the test — `@@ -5,1 +5,2 @@` means the new file's hunk starts at line 5.

- [ ] **Step 6: Wire into DiffCard and StepGate**

`DiffCard.tsx` — inside the expanded body (`{expanded && (...)}` block that maps `FileRow`s): render `DiffPanes` above the file rows when any entry has diff text, and keep the `FileRow` list as the no-diff fallback:

```tsx
      {expanded && (
        <div className="anim-rise">
          <DiffPanes entries={diffEntries} />
          <div className="border-t border-border py-1">
            {diffEntries.map((entry, idx) => (
              <FileRow key={`${entry.path}-${idx}`} entry={entry} />
            ))}
          </div>
        </div>
      )}
```

`StepGate.tsx` — same: render `<DiffPanes entries={entries} />` between the file rows and the action buttons (read the component first; place it where the layout matches the mockup — panes below the file-row summary).

Import `DiffPanes` in both files.

- [ ] **Step 7: Run all webview tests + build, then live-check**

Run: `npm test && npm run build`
Expected: all PASS (DiffCard tests unchanged — fallback preserved).

Live check (backend + dev host already running per CLAUDE.md recipe): send an inline change in the chat, expand the diff card, confirm tabs + colored lines render and the tab click switches files.

- [ ] **Step 8: Commit**

```bash
git add apps/vscode-extension/webview-ui/src
git commit -m "feat(webview): tabbed unified-diff panes in DiffCard and StepGate"
```

---

### Task 4: Step-review transcript record + auto-accept breadcrumb

Two persistence gaps in one engine area: (a) when a step review resolves, only the `✓/↩` breadcrumb survives — persist the reviewed diff as a read-only `diff_card` message (it reuses the existing resolved-state rendering, and with Task 2 the panes work after the shadow is pruned); (b) when `step_review_auto_accept` is on, no transcript record at all — write a `✓ Step completed` breadcrumb.

**Decision (locked):** the step diff record is persisted BEFORE the decision breadcrumb (reads: diff → ✓ accepted), with `metadata.resolved` already set — it is never interactive. NOT broadcast live (the live gate card already showed the diff; same rationale as tool pills — no shared id to dedup a re-delivery).

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (`_pause_for_step_review`, `_execute_plan`)
- Test: `services/agentd-py/tests/test_step_review_record.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_step_review_record.py`:

```python
"""Step reviews leave a durable diff_card record; auto-accept leaves a breadcrumb."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import PlanStep, StepRunResult, TaskRecord, TaskStatus
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
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


def _make(tmp_path: Path) -> tuple[AgentOrchestrator, ChatThreadStore, str]:
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    thread = chat_store.create_thread(str(tmp_path))
    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        chat_store=chat_store,
    )
    return orch, chat_store, thread.thread_id


async def _seed_executing(orch, thread_id: str, ws: Path) -> TaskRecord:
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(ws),
                      chat_channel_id=f"chat:{thread_id}")
    for status, reason in [
        (TaskStatus.CONTEXT_READY, "ctx"),
        (TaskStatus.AWAITING_PLAN_APPROVAL, "approval"),
        (TaskStatus.PLANNED, "planned"),
        (TaskStatus.EXECUTING, "executing"),
    ]:
        task = transition(task, status, reason)
    await orch._store.create(task)
    return task


async def _wait_pending(d: dict, key: str) -> None:
    for _ in range(200):
        await asyncio.sleep(0)
        if key in d:
            return
    raise AssertionError("gate future never registered")


@pytest.mark.asyncio
async def test_step_review_accept_persists_diff_record(tmp_path: Path) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    (shadow / "a.py").write_text("x = 2\n")
    (ws / "a.py").write_text("x = 1\n")

    orch, chat_store, thread_id = _make(tmp_path)
    task = await _seed_executing(orch, thread_id, ws)
    step = PlanStep(id="s1", goal="bump x", targets=[], risk="low")
    step_result = StepRunResult(
        step_id="s1", outcome="step_completed", validation_result="validation_passed",
        attempts_used=1, touched_files=["a.py"],
    )

    gate = asyncio.create_task(
        orch._pause_for_step_review(task, step, step_result, shadow, ws)
    )
    await _wait_pending(orch._pending_step_decisions, task.task_id)
    orch._pending_step_decisions[task.task_id].set_result("accept")
    await gate

    thread = chat_store.get_thread(thread_id)
    cards = [m for m in thread.messages if m.type == "diff_card"]
    assert len(cards) == 1
    card = cards[0]
    assert card.metadata["resolved"] == "applied"
    assert card.metadata["step_id"] == "s1"
    [entry] = card.metadata["diff_entries"]
    assert entry["path"] == "a.py"
    assert "+x = 2" in entry["unified_diff"]
    # Record precedes the breadcrumb in the transcript.
    crumb_idx = next(i for i, m in enumerate(thread.messages)
                     if m.metadata.get("breadcrumb") and "accepted" in m.content)
    card_idx = thread.messages.index(card)
    assert card_idx < crumb_idx


@pytest.mark.asyncio
async def test_step_review_discard_persists_discarded_record(tmp_path: Path) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    (shadow / "a.py").write_text("x = 2\n")

    orch, chat_store, thread_id = _make(tmp_path)
    task = await _seed_executing(orch, thread_id, ws)
    step = PlanStep(id="s1", goal="bump x", targets=[], risk="low")
    step_result = StepRunResult(
        step_id="s1", outcome="step_completed", validation_result="validation_passed",
        attempts_used=1, touched_files=["a.py"],
    )

    gate = asyncio.create_task(
        orch._pause_for_step_review(task, step, step_result, shadow, ws)
    )
    await _wait_pending(orch._pending_step_decisions, task.task_id)
    orch._pending_step_decisions[task.task_id].set_result("discard")
    await gate

    thread = chat_store.get_thread(thread_id)
    [card] = [m for m in thread.messages if m.type == "diff_card"]
    assert card.metadata["resolved"] == "discarded"


@pytest.mark.asyncio
async def test_auto_accept_writes_step_completed_breadcrumb(tmp_path: Path) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    orch, chat_store, thread_id = _make(tmp_path)
    task = await _seed_executing(orch, thread_id, ws)
    step = PlanStep(id="s1", goal="bump x in calculator", targets=[], risk="low")

    orch._write_step_completed_breadcrumb(task, step)

    thread = chat_store.get_thread(thread_id)
    crumbs = [m.content for m in thread.messages if m.metadata.get("breadcrumb")]
    assert any("Step completed" in c and "bump x" in c for c in crumbs)
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_step_review_record.py -q`
Expected: first two FAIL (no diff_card persisted), third FAILS with `AttributeError: _write_step_completed_breadcrumb`

- [ ] **Step 3: Implement the record writer + breadcrumb helper**

In `services/agentd-py/agentd/orchestrator/engine.py`:

(a) Add next to `write_chat_breadcrumb` (~line 1214):

```python
    def _write_step_completed_breadcrumb(self, task: TaskRecord, step: PlanStep) -> None:
        """Auto-accept leaves no review gate — record completion in the transcript."""
        self.write_chat_breadcrumb(task, f"✓ Step completed: {step.goal[:120]}")

    def _write_chat_step_diff_record(
        self,
        task: TaskRecord,
        step_id: str,
        step_title: str,
        diff_entries: list[dict],
        resolution: str,
    ) -> None:
        """Persist a reviewed step's diff as a read-only diff_card transcript message.

        The live StepGate card is /live-slot only and vanishes once the decision
        lands; this is the durable record (same Class-A model as breadcrumbs).
        Not broadcast live — the gate card already showed the diff. resolved is
        pre-set so the card renders inert (Applied/Discarded), never interactive.
        """
        if not task.chat_channel_id or self._chat_store is None or not diff_entries:
            return
        from agentd.chat.models import ChatMessage
        thread_id = task.chat_channel_id[len("chat:"):]
        msg = ChatMessage(
            role="agent", content=task.task_id, type="diff_card", task_id=task.task_id,
            metadata={
                "task_id": task.task_id,
                "step_id": step_id,
                "step_title": step_title,
                "diff_entries": diff_entries,
                "resolved": "applied" if resolution == "accept" else "discarded",
            },
        )
        self._chat_store.append_message(thread_id, msg)  # type: ignore[union-attr]
```

(b) In `_pause_for_step_review`'s `finally` block (`engine.py:1877-1894`), persist the record BEFORE the breadcrumb:

```python
            task.execution_state.pending_step_review = None
            if task.status == TaskStatus.AWAITING_STEP_REVIEW:
                task = transition(task, TaskStatus.EXECUTING, "step decision received")
            await self._store.save(task)
            self._write_chat_step_diff_record(
                task, step.id, payload.step_title, serialized, decision,
            )
            if decision == "accept":
                self.write_chat_breadcrumb(task, f"✓ Step changes accepted: {payload.step_title}")
            else:
                self.write_chat_breadcrumb(task, f"↩ Step changes discarded: {payload.step_title}")
```

(c) In `_execute_plan` (`engine.py:1474-1492`), add the auto-accept breadcrumb after `_mark_step_completed`:

```python
                await self._partial_promote(shadow_path, real_path, step_result.touched_files)
                self._mark_step_completed(task, step.id)
                await self._store.save(task)
                if task.step_review_auto_accept:
                    self._write_step_completed_breadcrumb(task, step)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_step_review_record.py tests/test_gate_breadcrumbs.py -q`
Expected: all PASS

- [ ] **Step 5: Webview sanity — read-only step diff cards**

No code expected: `MessageRow` routes `type === "diff_card"` to `DiffCard` with `resolved` from metadata, which suppresses Accept/Reject and shows Applied/Discarded; Task 3's `DiffPanes` renders the persisted `unified_diff`. **Verify** `DiffCard` doesn't break when `taskId` is a `task-*` id (it only matters for postMessage actions, which resolved cards never fire). Add one vitest case to `DiffCard.test.tsx`:

```tsx
  it("renders a resolved step-review record inert with panes", () => {
    render(
      <DiffCard taskId="task-123" resolved="applied"
        diffEntries={[{ path: "a.py", additions: 1, deletions: 0, temp_path: "/tmp/a.py",
          unified_diff: "@@ -1,1 +1,2 @@\n x = 1\n+y = 2" }]} />
    );
    expect(screen.getByText("Applied")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /accept all/i })).toBeNull();
  });
```

Run: `npx vitest run src/test/DiffCard.test.tsx` — expect PASS.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/test_step_review_record.py apps/vscode-extension/webview-ui/src/test/DiffCard.test.tsx
git commit -m "feat(chat): persist step-review diff records; step-completed breadcrumb on auto-accept"
```

---

### Task 5: Per-task "Review each step" composer toggle

Plumb an explicit per-message flag end-to-end so the user controls step review per task instead of per-backend-env.

**Decision (locked):** a checkbox in the composer footer labeled "Review each step", **default checked**, state held per webview session. The flag is ALWAYS sent with the message; the backend applies it only when the turn creates a task (large_change). Explicit-always beats tri-state "unset = env" — predictable, and the env default stays relevant only for API-created tasks.

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py` (`post_chat_message`)
- Modify: `services/agentd-py/agentd/chat/agent.py` (`handle_message`)
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (`create_task_from_chat`)
- Test: `services/agentd-py/tests/test_step_review_toggle.py` (new)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`sendChatMessage`)
- Modify: `apps/vscode-extension/src/chat-panel.ts`, `apps/vscode-extension/src/controller.ts`, `apps/vscode-extension/src/extension.ts` (handler signature)
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`

- [ ] **Step 1: Write the failing backend test**

Create `services/agentd-py/tests/test_step_review_toggle.py`:

```python
"""The per-message step_review flag reaches the created task."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.orchestrator.engine import AgentOrchestrator
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


class _NullStore:
    def append_message(self, thread_id: str, message: object) -> None: ...
    def set_active_task(self, thread_id: str, task_id: str) -> None: ...


def _orch(tmp_path: Path) -> tuple[AgentOrchestrator, InMemoryTaskStore]:
    store = InMemoryTaskStore()
    orch = AgentOrchestrator(
        store=store,
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    return orch, store


@pytest.mark.asyncio
async def test_step_review_flag_forces_review(tmp_path: Path) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    orch, store = _orch(tmp_path)
    task_id = await orch.create_task_from_chat(
        thread_id="t", goal="g", workspace_path=str(ws),
        explore_context=[], store=_NullStore(),
        step_review_auto_accept=False,
    )
    task = await store.get(task_id)
    assert task.step_review_auto_accept is False


@pytest.mark.asyncio
async def test_step_review_flag_none_keeps_env_default(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    monkeypatch.setenv("CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT", "true")
    orch, store = _orch(tmp_path)
    task_id = await orch.create_task_from_chat(
        thread_id="t", goal="g", workspace_path=str(ws),
        explore_context=[], store=_NullStore(),
    )
    task = await store.get(task_id)
    assert task.step_review_auto_accept is True
```

NOTE: `create_task_from_chat` schedules `run_task` via `asyncio.create_task` (verify when reading it) — the NoopReasoning task will fail in the background; that's fine for this assertion, but if the test is flaky, cancel `orch`'s pending tasks or assert before yielding. Also verify `_NullStore` satisfies whatever `create_task_from_chat` calls on `store` (read the body; it calls `set_active_task` — confirm the exact method name).

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_step_review_toggle.py -q`
Expected: FAIL — `create_task_from_chat() got an unexpected keyword argument 'step_review_auto_accept'`

- [ ] **Step 3: Backend plumbing (three layers)**

(a) `engine.py::create_task_from_chat` (~line 1137) — add the kwarg and prefer it over env:

```python
    async def create_task_from_chat(
        self,
        *,
        thread_id: str,
        goal: str,
        workspace_path: str,
        explore_context: list[dict[str, object]],
        store: object,
        step_review_auto_accept: bool | None = None,
    ) -> str:
```

and where the env default is applied (~line 1158-1166), prefer the explicit flag:

```python
        if step_review_auto_accept is not None:
            request.step_review_auto_accept = step_review_auto_accept
        else:
            # Honor CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT the same way POST /v1/tasks does.
            ... (existing env code unchanged)
```

(b) `chat/agent.py::handle_message` (line 147) — accept and forward:

```python
    async def handle_message(
        self, thread_id: str, message: str, channel_id: str,
        step_review: bool | None = None,
    ) -> None:
```

and in the large_change branch, pass it through:

```python
                task_id = await self._orchestrator.create_task_from_chat(
                    thread_id=thread_id,
                    goal=message,
                    workspace_path=self._workspace_path,
                    explore_context=context,
                    store=self._store,
                    step_review_auto_accept=(not step_review) if step_review is not None else None,
                )
```

(c) `api/routes.py::post_chat_message` (~line 1010) — read the flag and pass it:

```python
            message = request.get("content") or request.get("message", "")
            raw_flag = request.get("step_review")
            step_review = raw_flag if isinstance(raw_flag, bool) else None
            ...
                    await _chat_agent.handle_message(
                        thread_id, message, channel_id=channel_id, step_review=step_review,
                    )
```

- [ ] **Step 4: Run backend tests**

Run: `python -m pytest tests/test_step_review_toggle.py tests/test_chat_agent_broadcaster.py -q`
Expected: PASS. If `test_chat_agent_broadcaster`'s stub orchestrator now fails on the new kwarg, add `step_review_auto_accept=None` to its `create_task_from_chat` signature (same pattern as the `explore_events` stub fix).

- [ ] **Step 5: Client + extension plumbing**

(a) `http-backend-client.ts::sendChatMessage` (line 340) — add an options param:

```typescript
  async *sendChatMessage(
    threadId: string,
    message: string,
    signal?: AbortSignal,
    options?: { stepReview?: boolean },
  ): AsyncIterable<StreamEvent> {
    ...
        body: JSON.stringify({
          content: message,
          ...(options?.stepReview !== undefined ? { step_review: options.stepReview } : {}),
        }),
```

Update the `BackendTaskClient` interface signature in `task-contracts.ts` to match (find `sendChatMessage` there), then rebuild editor-client.

(b) `chat-panel.ts` — handler type + forward (line 96-97):

```typescript
      } else if (m["type"] === "sendMessage") {
        p = this.onMessage(m["text"] as string, m["stepReview"] === true);
```

and widen `ChatMessageHandler` (declared near the constructor, line ~30) to `(text: string, stepReview?: boolean) => Promise<void>`.

(c) `extension.ts` — the chat panel wiring passes `(text, stepReview) => controller.sendChatMessage(text, stepReview)` (find the `new ChatPanel(...)`/`onMessage:` site and add the second arg).

(d) `controller.ts::sendChatMessage` (line 527) — accept and forward:

```typescript
  async sendChatMessage(text: string, stepReview?: boolean): Promise<void> {
    ...
    // at the client.sendChatMessage call site inside this method:
    for await (const event of client.sendChatMessage(threadId, text, abort.signal, {
      ...(stepReview !== undefined ? { stepReview } : {}),
    })) {
```

(Read the method to find the exact call — it may go through a helper; the flag rides to whichever call posts the message.)

- [ ] **Step 6: Composer checkbox**

`InputArea.tsx` — add state + checkbox in the footer row (left of the `⌘↵` hint), and send it:

```tsx
  const [stepReview, setStepReview] = useState(true);
  ...
  function doSend() {
    ...
    vscode.postMessage({ type: "sendMessage", text: trimmed, stepReview });
    ...
  }
  ...
  {/* footer row, before the hint: */}
  <label className="flex items-center gap-1.5 text-[10px] text-text-3 cursor-pointer select-none">
    <input
      type="checkbox"
      checked={stepReview}
      onChange={(e) => setStepReview(e.target.checked)}
      className="accent-[var(--color-accent)] w-3 h-3"
    />
    Review each step
  </label>
```

- [ ] **Step 7: Webview test**

Append to the existing InputArea/views test file (find which file covers InputArea — grep `sendMessage` in `src/test/`):

```tsx
  it("sends stepReview flag with the message; toggle flips it", () => {
    renderInput();  // existing helper / pattern
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "do it" } });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "sendMessage", text: "do it", stepReview: true }),
    );
    fireEvent.click(screen.getByLabelText(/review each step/i));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ text: "again", stepReview: false }),
    );
  });
```

(Adapt selector details to the actual test harness; `getByLabelText` works because the checkbox is inside a `<label>`.)

- [ ] **Step 8: Full verification**

```bash
cd "$(git rev-parse --show-toplevel)"
npm run -w @crucible/editor-client build && npm run build && npm run typecheck && npm run test
cd services/agentd-py && python -m pytest -q
```
Expected: TS all green; pytest shows only the known pre-existing failures (gemini/groq transports + `@requires_live_snapshot` graph-walker).

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py apps/editor-client/src apps/vscode-extension/src apps/vscode-extension/webview-ui/src
git commit -m "feat(chat): per-task review-each-step toggle plumbed composer→task"
```

---

### Final: live smoke + docs

- [ ] **Step 1: Live smoke** (backend on :8001 + worktree dev-host window per the CLAUDE.md recipe; Playwright caveat: `browser_wait_for` does NOT pierce the webview iframes — use `browser_snapshot` + grep):
  1. History list shows chips + `· N messages`, sorted/grouped by updated time.
  2. Inline change → diff card shows tabbed panes with colored diff lines.
  3. Large-change task with "Review each step" checked → StepGate shows panes; accept → reload webview → resolved `diff_card` record with panes + `✓ Step changes accepted` breadcrumb in the transcript.
  4. Same task with the toggle unchecked → no step gates; `✓ Step completed: …` breadcrumbs appear per step.
- [ ] **Step 2: Update `CLAUDE.md`** — chat section: thread summary fields (`message_count`, `updated_at`, `status` chip), `DiffEntry.unified_diff` (cap 400 lines/24k chars), step diff_card records (`metadata.step_id`, not broadcast live), `step_review` flag on `POST /message`.
- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md
git commit -m "docs: chat v2 tier A — enriched summaries, unified_diff, step records, review toggle"
```
