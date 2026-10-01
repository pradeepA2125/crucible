# Sub-agents Phase 4 — UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sub-agents visible and controllable in the chat webview, built to the approved wireframe (`.superpowers/brainstorm/11903-1790698215/content/subagent-ui-v3.html`):
- a roster card for each dispatch;
- a live-following inline scroll box per agent (▸);
- a floating window per agent (⤢), with sibling tabs and a ■ Stop button;
- agent chips on gate cards.

**Architecture:**
- **Webview.** The roster card renders from the `agent_dispatch` message. Its row data comes from one `agents` map, fed by `/live` during a turn and by `GET …/agents` on thread load and at turn end. An open agent's content comes from `agentViews` state: a backfilled transcript, plus live events folded in by a pure reducer function.
- **Extension host.** A vscode-free `AgentViewManager` owns one subscription per open agent:
  - backfill with `getAgent`, then follow `chat:{thread}:agent:{agent}`, skipping events with `seq <= lastSeq`;
  - re-backfill when the channel goes idle;
  - one final backfill when the agent ends.
- **Webview → host.** The webview reports the *set* of open agents (`setOpenAgents`), so subscription lifetime is an idempotent diff, not refcounting.

**Tech stack:** React 18 + Tailwind v4 (webview, Vite, vitest + Testing Library), TypeScript (extension host, vitest), Zod (editor-client), Python/FastAPI (two small backend additions, pytest).

**Spec:** `docs/superpowers/specs/2026-09-29-subagents-design.md` rev 11 — §10 (UI), §11.1 (`/live` agents), §11.2 (stream events), §5.5 (seq cursor), §16 phase 4.

**Branch:** `feat/subagents`, on top of `69f65a6` plus the stream-deadline fix.

## Ground truth this plan is written against (verified)

**Backend**
- `agentd/chat/controller.py::_on_dispatch_start`:
  - persists the `agent_dispatch` roster message (`append_message` for the main agent, `dispatcher.transcript.append` for a nested dispatch);
  - broadcasts only `agent_started`.
  - **The roster message itself is never broadcast**, so in a live turn the webview has no anchor for the card until a reload.
- `agentd/chat/models.py:70` — `AgentRecord.summary()` returns no `tool_count` (only `/live`'s `live_agents` does), so finished rows can't show "9 tools".
- `agentd/subagents/events.py:28` — `SequencedBroadcaster` stamps `seq` at the **top level** of each child-channel event. `AgentTranscript._stamped` records the seq current at write time on each persisted message, and `GET …/agents/{id}` returns `last_seq` = the max persisted seq.

**editor-client**
- `src/contracts/task-contracts.ts:328` — `ChatEventSchema = z.object({type, payload})`. Zod strips unknown keys, so **the top-level `seq` never reaches a consumer**; spec §10's skip rule can't work today.
- `src/client/http-backend-client.ts:440` — `streamChannel(channelId)` takes no `AbortSignal`, so a closed view can only stop by waiting out the 120 s idle timeout.
- `toChatMessage` (`:589`) is `private static`.

**Extension**
- `src/controller.ts`:
  - `streamTurn` (`:772`) has no branch for `agent_started`/`agent_status`/`agent_finished` (spec §11.2 wants a `/live` poke).
  - `pollThreadLiveState` (`:1851`) signature lacks `agents`. The spec says it **must** include them, under the dedup invariant.
  - Thread loads happen in `openChat` (`:591-600`), `newChatThread`, and `switchChatThread` (`:636`).
  - `dispose` is at `:1434`; `clientForChat` at `:1787`.
- `src/chat-panel.ts` — webview → host routing is an `if/else` chain in `registerHandlers`. Handlers are positional constructor params with defaults, and `extension.ts:142-178` passes them in order.

**Webview**
- `webview-ui/src`:
  - `MessageRow.tsx` returns `null` for `agent_dispatch` ("Phase 4").
  - `hooks/useAppState.ts` has a non-exported reducer, tested through `renderHook` + `window` message events.
  - `components/shared/scroll-pinning.ts::isPinnedToBottom` and `ThinkingBlock.tsx`'s pin pattern are the follow-bottom precedent.
  - `ChatSettingsOverlay.tsx` is the floating-overlay precedent (`.scrim` + `.surface-card anim-pop`, Esc).
  - `LiveSlot.tsx:31` renders the Phase 1B plain agent chip.
- Tailwind v4 `@theme` tokens (`text-text-3`, `bg-surface`, `border-border`, `text-amber`, …) plus `:root` vars (`--accent-bg`, `--accent-brd`, `--green-brd`, `--red-brd`, `--amber-bg`).
- `Icon.tsx` has no fork or expand glyph.
- The webview test setup stubs `acquireVsCodeApi`; component tests `vi.mock("../vscodeApi")`.

## Decisions (and why)

1. **Broadcast the roster message** as `agent_dispatch {message}`:
   - main dispatch → the thread channel;
   - nested dispatch → the dispatcher's own channel, **before** persisting it, so the persisted copy records the seq of the event that produced it (the `_child_edit_record_cb` convention) and backfill-then-subscribe never shows it twice.
   - On the main thread a reload-plus-replay can still deliver it twice, so the webview dedups roster messages by their `agent_ids`.
2. **`seq` goes on the schema, not into payloads.** `ChatEventSchema` gains `seq?: number`. A `SequencedStreamEvent` type (`StreamEvent & {seq?: number}`) is what `streamChannel` yields.
3. **The webview reports the full open set** (`setOpenAgents {agentIds}`) on every change. The host diffs it. A row expanded inline *and* shown in the window is one subscription.
4. **The roster card reads from one `agents` map** (`agentId → summary`), merged from `/live` (live) and `listAgents` (thread load + turn end). Entries are never removed during a thread, so a finished turn keeps its last values until the route refresh replaces them.
5. **Live events don't rebuild what the backend persists.** The webview folds only these into the open view: `tool_call`/`tool_result` (pills), `chat_progress`, `chat_breadcrumb`, `diff_ready`, `agent_dispatch`. Everything else (thinking chunks, token progress) is ignored, matching the wireframe. The final backfill at termination brings the authoritative transcript, including the full report.
6. **Chip tint by definition name.** `explore` gets the read-only (code-blue) tint; every other name gets violet. Summaries don't carry the permission, and the wireframe distinguishes only these two.

## File structure

| File | Change |
|---|---|
| `services/agentd-py/agentd/chat/models.py` | `AgentRecord.summary()` adds `tool_count` |
| `services/agentd-py/agentd/chat/controller.py` | `_on_dispatch_start` broadcasts `agent_dispatch` |
| `apps/editor-client/src/contracts/task-contracts.ts` | `ChatEventSchema.seq`, `agent_dispatch` StreamEvent, `SequencedStreamEvent`, `streamChannel(channelId, signal?)` |
| `apps/editor-client/src/client/http-backend-client.ts` | `streamChannel` signal; public `toChatMessage`; `parseWireChatMessage` |
| `apps/vscode-extension/src/agent-views.ts` | **new** — `AgentViewManager`, `agentChannel` |
| `apps/vscode-extension/src/controller.ts` | UI methods, roster refresh, `/live` agents, pokes, `setOpenAgents`, `stopAgent` |
| `apps/vscode-extension/src/chat-panel.ts`, `src/extension.ts` | post the three new messages; route `setOpenAgents`/`stopAgent` |
| `apps/vscode-extension/webview-ui/src/types.ts` | views + messages |
| `apps/vscode-extension/webview-ui/src/agents.ts` | **new** — pure view logic |
| `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts` | reducer cases |
| `apps/vscode-extension/webview-ui/src/components/agents/*` | **new** — `AgentsContext`, `useNow`, `useFollowBottom`, `AgentChip`, `AgentRosterCard`, `InlineAgentBox`, `AgentTranscript`, `AgentWindow` |
| `apps/vscode-extension/webview-ui/src/components/{Icon,MessageRow,ThreadView,LiveSlot}.tsx` | icons, roster case, wiring, chip |

Commands:
- Python, from `services/agentd-py`: `./.venv/bin/pytest <paths>`. Never `-q` and never piped.
- TypeScript, from the repo root:
  - `npm run -w @crucible/editor-client test`
  - `npm run -w @crucible/editor-client build` (**required before the extension typecheck**, which types off `dist/`)
  - `npm run -w crucible-vscode-extension test`
  - `npm run -w crucible-vscode-extension typecheck`
  - `npm --prefix apps/vscode-extension/webview-ui test`
  - `npm --prefix apps/vscode-extension/webview-ui run typecheck`
- **Stop any `--reload` backend before editing `agentd/`.** A hot reload mid-turn waits on the open turn stream and stops answering HTTP (seen live 2026-10-01).

---

## Part A — Backend and contract

### Task A1: `tool_count` in the agent summary

**Files:** Modify `services/agentd-py/agentd/chat/models.py`. Test: `services/agentd-py/tests/test_agent_summary_tool_count.py`.

- [ ] **Step 1: Write the failing test**

```python
"""The routes' agent summary carries a tool count, like /live's (spec §11.1)."""
from agentd.chat.models import AgentRecord, ChatMessage


def _pills(n: int) -> ChatMessage:
    return ChatMessage(role="agent", content="", metadata={
        "tool_events": [{"id": i, "tool": "read_file"} for i in range(n)]})


def test_summary_counts_every_persisted_tool_call() -> None:
    record = AgentRecord(
        agent_id="agent-a", thread_id="t", turn_id="u", depth=1, name="explore",
        label="survey", prompt="p", status="completed", transcript=[
            _pills(2),
            ChatMessage(role="agent", content="note", metadata={"progress": True}),
            _pills(1),
            ChatMessage(role="agent", content="report", metadata={"report": True})])
    assert record.summary()["tool_count"] == 3


def test_summary_of_an_agent_with_no_transcript() -> None:
    record = AgentRecord(agent_id="agent-b", thread_id="t", turn_id="u", depth=1,
                         name="explore", label="x", prompt="p", status="queued")
    assert record.summary()["tool_count"] == 0
```

- [ ] **Step 2:** `./.venv/bin/pytest tests/test_agent_summary_tool_count.py` → FAIL with `KeyError: 'tool_count'`.

- [ ] **Step 3: Implement.** In `AgentRecord.summary()`, add the count before the return, and the key after `"files_changed_count"`:

```python
        # Pills persist per segment, each message holding its segment's full list
        # (AgentTranscript.upsert_pills), so the sum over messages is the call count.
        tool_count = sum(len(m.metadata.get("tool_events") or []) for m in self.transcript)
```

```python
            "files_changed_count": len(self.files_changed),
            "tool_count": tool_count,
```

`tests/test_subagent_routes.py::test_list_get_stop_live_and_config` asserts the exact summary dict, so add the new key to its expectation (the stored agent has no transcript):

```python
        "files_changed_count": 1, "tool_count": 0, "started_at": None, "ended_at": None,
```

(replacing `"files_changed_count": 1, "started_at": None, "ended_at": None,` in its `listed == [{…}]` assertion).

- [ ] **Step 4:** `./.venv/bin/pytest tests/test_agent_summary_tool_count.py tests/test_subagent_routes.py tests/test_chat_agents_store.py` → all pass.

- [ ] **Step 5: Commit** — `feat(subagents): agent summaries carry a tool count`

### Task A2: Broadcast the roster message

**Files:** Modify `services/agentd-py/agentd/chat/controller.py`. Test: `services/agentd-py/tests/test_dispatch_roster_event.py`.

- [ ] **Step 1: Write the failing test**

```python
"""The dispatch roster message is broadcast live, not only persisted (spec §6.1, §10)."""
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_dispatch_integration import DONE, _controller, _creates, _dispatch


def _record(ctrl) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    inner = ctrl._broadcaster.broadcast

    def broadcast(channel: str, event: dict) -> None:
        calls.append((channel, event))
        inner(channel, event)

    ctrl._broadcaster.broadcast = broadcast  # SequencedBroadcaster forwards here, stamped
    return calls


@pytest.mark.asyncio
async def test_main_dispatch_broadcasts_its_roster_on_the_thread_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("general-purpose", "impl-a", "Create a.py")), DONE],
        agent_scripts={"impl-a": _creates("a.py", "A = 1\n")})
    ctrl = _controller(ws, tmp_path, store, engine)
    calls = _record(ctrl)

    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    on_thread = [e for ch, e in calls if ch == f"chat:{tid}"]
    types = [e["type"] for e in on_thread]
    assert "agent_dispatch" in types
    assert types.index("agent_dispatch") < types.index("agent_started")
    message = next(e for e in on_thread if e["type"] == "agent_dispatch")["payload"]["message"]
    [row] = store.list_agents(tid)
    assert message["type"] == "agent_dispatch"
    assert message["metadata"]["agent_ids"] == [row.agent_id]


@pytest.mark.asyncio
async def test_nested_roster_is_broadcast_before_it_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("general-purpose", "lead", "Split the work")), DONE],
        agent_scripts={
            "lead": [_dispatch(("general-purpose", "leaf", "Create leaf.py")),
                     {"type": "report", "thought": "t", "summary": "Lead done."}],
            "leaf": _creates("leaf.py", "LEAF = 1\n")})
    ctrl = _controller(ws, tmp_path, store, engine)
    calls = _record(ctrl)

    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    rows = {r.label: r for r in store.list_agents(tid)}
    lead_channel = f"chat:{tid}:agent:{rows['lead'].agent_id}"
    [event] = [e for ch, e in calls if ch == lead_channel and e["type"] == "agent_dispatch"]
    [persisted] = [m for m in rows["lead"].transcript if m.type == "agent_dispatch"]
    # The persisted copy records the seq of the event that produced it, so a viewer that
    # backfills and then subscribes skips the replayed event (seq <= last_seq).
    assert persisted.metadata["seq"] == event["seq"]
    assert event["payload"]["message"]["metadata"]["agent_ids"] == [rows["leaf"].agent_id]
```

- [ ] **Step 2:** `./.venv/bin/pytest tests/test_dispatch_roster_event.py` → both FAIL with `"agent_dispatch" in types` / `ValueError: not enough values to unpack`.

- [ ] **Step 3: Implement.** In `_on_dispatch_start`, replace the `if dispatcher is None: … else: …` block that persists the roster with:

```python
        event = {"type": "agent_dispatch",
                 "payload": {"message": roster.model_dump(mode="json")}}
        if dispatcher is None:
            self._mark_pills_boundary(thread_id)
            self._store.append_message(thread_id, roster)
            self._broadcaster.broadcast(f"chat:{thread_id}", event)
        else:
            if isinstance(dispatcher.loop, ControllerLoop):
                dispatcher.loop.mark_pills_boundary()
            # Broadcast first: the persisted copy then records the seq of the event that
            # produced it, so backfill-then-subscribe never shows the roster twice.
            if dispatcher.broadcaster is not None:
                dispatcher.broadcaster.broadcast(
                    agent_channel(thread_id, dispatcher.agent_id), event)
            if dispatcher.transcript is not None:
                dispatcher.transcript.append(roster)
```

- [ ] **Step 4:** `./.venv/bin/pytest tests/test_dispatch_roster_event.py tests/test_dispatch_integration.py tests/test_subagent_lifecycle.py` → all pass. Then `./.venv/bin/ruff check agentd/chat/controller.py agentd/chat/models.py tests/test_dispatch_roster_event.py tests/test_agent_summary_tool_count.py`. Expected: only findings that already exist at the base — `controller.py:8:1 I001` and `models.py`'s two `UP017` (`datetime.UTC` alias, lines 48 and 169 after this change).

- [ ] **Step 5: Commit** — `feat(subagents): broadcast the dispatch roster message live`

### Task A3: editor-client — `seq`, `agent_dispatch`, cancellable `streamChannel`, wire-message parser

**Files:** Modify `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`. Test: `apps/editor-client/test/subagent-contracts.test.ts`.

- [ ] **Step 1: Write the failing tests.** Append to `test/subagent-contracts.test.ts`, and add `parseWireChatMessage` to its import from `../src/client/http-backend-client`:

```ts
describe("sub-agent channel streaming", () => {
  it("keeps each event's seq and passes the abort signal to fetch", async () => {
    const body = 'data: {"type":"tool_call","payload":{"tool":"read_file"},"seq":4}\n\n'
      + 'data: {"type":"agent_dispatch","payload":{"message":{"role":"agent","content":"","type":"agent_dispatch","timestamp":"2026-10-01T00:00:00Z","metadata":{"agent_ids":["agent-z"]}}},"seq":5}\n\n';
    const fetchFn = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const abort = new AbortController();
    const events = [];
    for await (const e of c.streamChannel("chat:t:agent:a", abort.signal)) events.push(e);
    expect(events.map((e) => [e.type, e.seq])).toEqual([["tool_call", 4], ["agent_dispatch", 5]]);
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/channels/chat%3At%3Aagent%3Aa/stream");
    expect(fetchFn.mock.calls[0][1].signal).toBe(abort.signal);
  });

  it("parses a wire chat message into the camelCase contract shape", () => {
    const m = parseWireChatMessage({
      role: "agent", content: "", type: "agent_dispatch", task_id: null, id: "m1",
      timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-a"] } });
    expect(m).toMatchObject({ type: "agent_dispatch", id: "m1", taskId: null,
                              metadata: { agent_ids: ["agent-a"] } });
  });
});
```

- [ ] **Step 2:** `npm run -w @crucible/editor-client test` → FAIL (`parseWireChatMessage` is not exported; seq undefined).

- [ ] **Step 3: Implement.** In `task-contracts.ts`:

- `ChatEventSchema` becomes:

  ```ts
  export const ChatEventSchema = z.object({
    type: z.string(),
    payload: z.record(z.unknown()).default({}),
    // A sub-agent channel stamps a monotonic seq (spec §5.5); other channels omit it.
    seq: z.number().optional(),
  });
  ```

- Add a member to the `StreamEvent` union, directly after the `agent_finished` member:

  ```ts
    | { type: "agent_dispatch"; payload: { message: Record<string, unknown> } }
  ```

- Directly after the union's last line, `  | { type: "edit_failed"; payload: { reason: string; ops: number } };`, add:

  ```ts
  /** What a channel subscription yields: a sub-agent channel's events carry a seq. */
  export type SequencedStreamEvent = StreamEvent & { seq?: number };
  ```

- In `BackendTaskClient`, change `streamChannel(channelId: string): AsyncIterable<StreamEvent>;` to:

  ```ts
    streamChannel(channelId: string, signal?: AbortSignal): AsyncIterable<SequencedStreamEvent>;
  ```

In `http-backend-client.ts`:

- In the import from `../contracts/task-contracts.js`, add `ChatMessageSchema,` directly after `ChatEventSchema,`, and `type ChatMessage,` and `type SequencedStreamEvent,` directly after `type StreamEvent,`.
- Replace `streamChannel`:

  ```ts
    async *streamChannel(channelId: string, signal?: AbortSignal): AsyncIterable<SequencedStreamEvent> {
      const response = await this.fetchFn(
        `${this.options.baseUrl}/v1/channels/${encodeURIComponent(channelId)}/stream`,
        // exactOptionalPropertyTypes: RequestInit.signal may be absent, never undefined.
        { headers: { accept: "text/event-stream" }, ...(signal ? { signal } : {}) }
      );
      if (!response.ok) {
        throw new Error(`Channel stream failed (${response.status}) for ${channelId}`);
      }
      yield* this.consumeChatEventStream(response, new Set(["chat_done", "done"]));
    }
  ```

- Change `private static toChatMessage(` to `static toChatMessage(`.
- After the class's closing `}`, add:

  ```ts
  /** A chat message as the backend serializes it (snake_case) → the contract shape. */
  export function parseWireChatMessage(raw: Record<string, unknown>): ChatMessage {
    return ChatMessageSchema.parse(HttpBackendClient.toChatMessage(raw));
  }
  ```

`consumeChatEventStream` already parses each line with `ChatEventSchema`, so `seq` now survives into the yielded object.

- [ ] **Step 4:** `npm run -w @crucible/editor-client test`, then `npm run -w @crucible/editor-client build`. Both exit 0.

- [ ] **Step 5: Commit** — `feat(editor-client): channel events keep their seq; cancellable streamChannel`

---

## Part B — Extension host

### Task B1: `AgentViewManager`

**Files:** Create `apps/vscode-extension/src/agent-views.ts`. Test: `apps/vscode-extension/test/agent-views.test.ts`.

- [ ] **Step 1: Write the failing test**

```ts
import type { AgentDetail, SequencedStreamEvent } from "@crucible/editor-client";
import { describe, expect, test } from "vitest";

import { AgentViewManager, agentChannel } from "../src/agent-views.js";

function detail(status: string, lastSeq: number, report = ""): AgentDetail {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status, now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null,
    reportPreview: "", prompt: "p", report, filesChanged: [], staleRefusals: 0,
    transcript: [], lastSeq,
  };
}

/** A channel that yields `events`, then either ends or waits until aborted. */
function channel(events: SequencedStreamEvent[], hold: boolean) {
  return async function* (_id: string, signal?: AbortSignal) {
    for (const e of events) yield e;
    if (hold) {
      await new Promise<void>((_, reject) => signal?.addEventListener(
        "abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    }
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("AgentViewManager", () => {
  test("backfills, then forwards only events newer than the backfill", async () => {
    const seen: Array<[string, unknown]> = [];
    const client = {
      getAgent: async () => detail("running", 4),
      streamChannel: channel([
        { type: "tool_call", payload: { tool: "read_file" } as never, seq: 4 },
        { type: "tool_call", payload: { tool: "search_code" } as never, seq: 5 },
        { type: "agent_dispatch", payload: { message: {
          role: "agent", content: "", type: "agent_dispatch", task_id: null,
          timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-z"] } } }, seq: 6 },
      ], true),
    };
    const m = new AgentViewManager(() => client, {
      detail: (id, d) => seen.push(["detail", d.status]),
      event: (id, e) => seen.push([e.type, e.seq]),
    }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    expect(seen).toEqual([["detail", "running"], ["tool_call", 5], ["agent_dispatch", 6]]);
    m.closeAll();
  });

  test("a nested roster arrives as a contract ChatMessage", async () => {
    const events: SequencedStreamEvent[] = [];
    const client = {
      getAgent: async () => detail("running", 0),
      streamChannel: channel([{ type: "agent_dispatch", payload: { message: {
        role: "agent", content: "", type: "agent_dispatch", task_id: null,
        timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-z"] } } }, seq: 1 }], true),
    };
    const m = new AgentViewManager(() => client, { detail: () => {}, event: (_, e) => events.push(e) }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    expect(events[0].payload).toEqual({ message: expect.objectContaining({ taskId: null, type: "agent_dispatch" }) });
    m.closeAll();
  });

  test("an idle channel re-backfills; a terminal backfill ends the view", async () => {
    const statuses = ["running", "completed"];
    let subscriptions = 0;
    const client = {
      getAgent: async () => detail(statuses.shift() ?? "completed", 0, "full report"),
      streamChannel: (id: string, signal?: AbortSignal) => {
        subscriptions += 1;
        return channel([], false)(id, signal);  // ends at once, like an idle timeout
      },
    };
    const details: string[] = [];
    const m = new AgentViewManager(() => client, { detail: (_, d) => details.push(d.status), event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    for (let i = 0; i < 6; i++) await flush();
    expect(details).toEqual(["running", "completed"]);
    expect(subscriptions).toBe(1);
  });

  test("closing a view aborts its subscription", async () => {
    let signal: AbortSignal | undefined;
    const client = {
      getAgent: async () => detail("running", 0),
      streamChannel: (id: string, s?: AbortSignal) => { signal = s; return channel([], true)(id, s); },
    };
    const m = new AgentViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    expect(signal?.aborted).toBe(false);
    m.setOpen("t", []);
    expect(signal?.aborted).toBe(true);
  });

  test("a terminal status cuts the stream so the final backfill runs", async () => {
    const statuses = ["running", "completed"];
    const client = {
      getAgent: async () => detail(statuses.shift() ?? "completed", 0),
      streamChannel: channel([], true),
    };
    const details: string[] = [];
    const m = new AgentViewManager(() => client, { detail: (_, d) => details.push(d.status), event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    m.noteStatus("agent-a", "completed");
    for (let i = 0; i < 4; i++) await flush();
    expect(details).toEqual(["running", "completed"]);
  });

  test("agentChannel names the child's channel", () => {
    expect(agentChannel("t", "agent-a")).toBe("chat:t:agent:agent-a");
  });
});
```

- [ ] **Step 2:** `npm run -w crucible-vscode-extension test -- test/agent-views.test.ts` → FAIL (`Cannot find module '../src/agent-views.js'`).

- [ ] **Step 3: Implement** `src/agent-views.ts`:

```ts
import {
  parseWireChatMessage,
  type AgentDetail,
  type BackendTaskClient,
  type SequencedStreamEvent,
} from "@crucible/editor-client";

export const TERMINAL_AGENT_STATUSES: ReadonlySet<string> = new Set([
  "completed", "partial", "failed", "stopped",
]);

export function agentChannel(threadId: string, agentId: string): string {
  return `chat:${threadId}:agent:${agentId}`;
}

export interface AgentViewSink {
  detail(agentId: string, detail: AgentDetail): void;
  event(agentId: string, event: SequencedStreamEvent): void;
}

type AgentClient = Pick<BackendTaskClient, "getAgent" | "streamChannel">;

interface OpenView {
  threadId: string;
  closed: boolean;
  abort: AbortController | null;
}

/**
 * Live data for the sub-agents the user is looking at (spec §10): one subscription per
 * open agent. Backfill with getAgent, then follow the agent's channel, skipping events the
 * backfill already contains (seq <= lastSeq, spec §5.5). The channel ends on its idle
 * timeout while an agent waits at a gate, so a running agent is re-backfilled and
 * re-subscribed; a terminal one gets one final backfill (it carries the report) and stops.
 */
export class AgentViewManager {
  private readonly views = new Map<string, OpenView>();

  constructor(
    private readonly client: () => AgentClient,
    private readonly sink: AgentViewSink,
    private readonly retryDelayMs = 1000,
  ) {}

  /** The full set of agents open in the webview; anything not in it is closed. */
  setOpen(threadId: string, agentIds: readonly string[]): void {
    const wanted = new Set(agentIds);
    for (const [id, view] of [...this.views]) {
      if (!wanted.has(id) || view.threadId !== threadId) this.close(id);
    }
    for (const id of wanted) {
      if (this.views.has(id)) continue;
      const view: OpenView = { threadId, closed: false, abort: null };
      this.views.set(id, view);
      void this.run(id, view);
    }
  }

  /** A roster status: a terminal agent's stream is cut so its final backfill runs. */
  noteStatus(agentId: string, status: string): void {
    if (TERMINAL_AGENT_STATUSES.has(status)) this.views.get(agentId)?.abort?.abort();
  }

  closeAll(): void {
    for (const id of [...this.views.keys()]) this.close(id);
  }

  private close(agentId: string): void {
    const view = this.views.get(agentId);
    if (!view) return;
    view.closed = true;
    view.abort?.abort();
    this.views.delete(agentId);
  }

  private async run(agentId: string, view: OpenView): Promise<void> {
    while (!view.closed) {
      let detail: AgentDetail;
      try {
        detail = await this.client().getAgent(view.threadId, agentId);
      } catch {
        await delay(this.retryDelayMs);
        continue;
      }
      if (view.closed) return;
      this.sink.detail(agentId, detail);
      if (TERMINAL_AGENT_STATUSES.has(detail.status)) return;
      let lastSeq = detail.lastSeq;
      view.abort = new AbortController();
      try {
        const stream = this.client().streamChannel(
          agentChannel(view.threadId, agentId), view.abort.signal);
        for await (const event of stream) {
          if (view.closed) return;
          if (event.seq !== undefined) {
            if (event.seq <= lastSeq) continue;
            lastSeq = event.seq;
          }
          this.sink.event(agentId, normalize(event));
        }
      } catch {
        // Aborted (closed, or the agent ended) or the connection dropped: re-backfill.
      } finally {
        view.abort = null;
      }
    }
  }
}

/** A nested roster arrives as a wire dict; the webview renders contract ChatMessages. */
function normalize(event: SequencedStreamEvent): SequencedStreamEvent {
  if (event.type !== "agent_dispatch") return event;
  return { ...event, payload: { message: parseWireChatMessage(event.payload.message) } };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
```

- [ ] **Step 4:** `npm run -w crucible-vscode-extension test -- test/agent-views.test.ts` → all pass.

- [ ] **Step 5: Commit** — `feat(extension): live data for open sub-agent views`

### Task B2: Controller wiring

**Files:** Modify `apps/vscode-extension/src/controller.ts`. Test: `apps/vscode-extension/test/controller.test.ts`.

- [ ] **Step 1: Write the failing tests.**

In `controller.test.ts`:
- Add `renderAgents: () => {}, agentDetail: () => {}, agentEvent: () => {},` to `createUi`'s defaults, before `...overrides`.
- Add `AgentSummary` to the `@crucible/editor-client` type import.
- Append:

```ts
describe("CrucibleController — sub-agents", () => {
  const AGENT: AgentSummary = {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status: "running", now: "read_file a.py", toolCount: 2, filesChangedCount: 0,
    startedAt: "2026-10-01T00:00:00Z", endedAt: null, reportPreview: "",
  };

  function setup(extra: Record<string, unknown> = {}) {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [], liveResponse: NULL_LIVE_STATE,
    };
    const listCalls: string[] = [];
    const backend = {
      ...createStubBackend(state),
      listAgents: async (threadId: string) => { listCalls.push(threadId); return [{ ...AGENT, status: "completed", toolCount: 9 }]; },
      ...extra,
    } as BackendTaskClient;
    const rosters: AgentSummary[][] = [];
    const appended: ChatMessage[] = [];
    const ui = createUi({
      renderAgents: (agents) => { rosters.push(agents); },
      appendChatMessage: (m) => { appended.push(m); },
    });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-10-01T00:00:00.000Z",
    );
    return { state, controller, rosters, appended, listCalls };
  }

  test("switching to a thread loads its roster from the routes", async () => {
    const { controller, rosters, listCalls } = setup();
    await controller.switchChatThread("chat-1");
    await Promise.resolve(); await Promise.resolve();
    controller.dispose();
    expect(listCalls).toEqual(["chat-1"]);
    expect(rosters.at(-1)?.[0]).toMatchObject({ agentId: "agent-a", toolCount: 9 });
  });

  test("/live agents render, are in the dedup signature, and turn end refreshes from the routes", async () => {
    const { state, controller, rosters, listCalls } = setup();
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    rosters.length = 0; listCalls.length = 0;

    state.liveResponse = { ...NULL_LIVE_STATE, turnActive: true, agents: [AGENT] };
    await controller.pollThreadLiveState();
    expect(rosters).toHaveLength(1);
    await controller.pollThreadLiveState();
    expect(rosters).toHaveLength(1);  // unchanged → deduped
    state.liveResponse = { ...NULL_LIVE_STATE, turnActive: true, agents: [{ ...AGENT, toolCount: 3 }] };
    await controller.pollThreadLiveState();
    expect(rosters).toHaveLength(2);  // a row change re-renders: agents ARE in the signature

    state.liveResponse = NULL_LIVE_STATE;  // the turn ended
    await controller.pollThreadLiveState();
    await Promise.resolve(); await Promise.resolve();
    expect(listCalls).toEqual(["chat-1"]);
  });

  test("a live roster message is appended as a contract ChatMessage; agent events poke /live", async () => {
    const { state, controller, appended } = setup({
      sendChatMessage: async function* () {
        yield { type: "agent_dispatch" as const, payload: { message: {
          role: "agent", content: "", type: "agent_dispatch", task_id: null,
          timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-a"] } } } };
        yield { type: "agent_started" as const, payload: {
          agent_id: "agent-a", parent_agent_id: null, depth: 1, name: "explore", label: "survey" } };
        yield { type: "chat_done" as const, payload: {} as Record<string, never> };
      },
    });
    await controller.switchChatThread("chat-1");
    const before = state.liveCalls!.length;
    await controller.sendChatMessage("go");
    controller.dispose();
    const roster = appended.find((m) => m.type === "agent_dispatch");
    expect(roster).toMatchObject({ type: "agent_dispatch", taskId: null, metadata: { agent_ids: ["agent-a"] } });
    expect(state.liveCalls!.length).toBeGreaterThan(before);
  });

  test("stopAgent posts to the agent's stop route", async () => {
    const stops: Array<[string, string]> = [];
    const { controller } = setup({
      stopAgent: async (threadId: string, agentId: string) => { stops.push([threadId, agentId]); return { ok: true }; },
    });
    await controller.switchChatThread("chat-1");
    await controller.stopAgent("agent-a");
    controller.dispose();
    expect(stops).toEqual([["chat-1", "agent-a"]]);
  });
});
```

- [ ] **Step 2:** `npm run -w crucible-vscode-extension test -- test/controller.test.ts` → the four new tests FAIL (`listCalls` empty, `rosters` empty, no `agent_dispatch` appended, `controller.stopAgent is not a function`).

- [ ] **Step 3: Implement** in `src/controller.ts`.

Imports — the existing `@crucible/editor-client` import is `import type { … }`, so it can take only types:
- add `AgentDetail,` and `AgentSummary,` as its first two names, and `SequencedStreamEvent,` directly after `SessionTranscript,`;
- directly after that import's closing `} from "@crucible/editor-client";`, add the value import and the manager:

```ts
import { parseWireChatMessage } from "@crucible/editor-client";
import { AgentViewManager } from "./agent-views.js";
```

`ControllerUI` — add after `sendLiveStatus(...)`:

```ts
  // Sub-agents (spec §10): roster rows, and the open agents' transcripts and live events.
  renderAgents(agents: AgentSummary[]): void;
  agentDetail(agentId: string, detail: AgentDetail): void;
  agentEvent(agentId: string, event: SequencedStreamEvent): void;
```

Fields — next to `private lastLiveSignature`:

```ts
  private readonly agentViews = new AgentViewManager(() => this.clientForChat(), {
    detail: (agentId, detail) => this.ui.agentDetail(agentId, detail),
    event: (agentId, event) => this.ui.agentEvent(agentId, event),
  });
  // The previous poll's turnActive: a true→false edge is when /live stops reporting the
  // turn's agents, so the final roster comes from the routes then.
  private lastTurnActive = false;
```

`dispose()` — add `this.agentViews.closeAll();` as its last line.

Thread loads:
- In `openChat`, inside the `try` that appends `thread.messages`, add after the `for` loop: `void this.refreshAgentRoster(this.activeThreadId);`.
- In `newChatThread`, after `this._liveResumeThreadId = null;`, add:

  ```ts
      this.agentViews.closeAll();
      this.lastTurnActive = false;
  ```

- In `switchChatThread`, after `this._liveResumeThreadId = null;`, add the same two lines. After its `for (const message of thread.messages)` loop, add `void this.refreshAgentRoster(threadId);`.

New methods — place directly before `async previewRewind(`:

```ts
  /** The webview's full set of open agent views (inline boxes + the window). */
  setOpenAgents(agentIds: string[]): void {
    if (!this.activeThreadId) return;
    this.agentViews.setOpen(this.activeThreadId, agentIds);
  }

  async stopAgent(agentId: string): Promise<void> {
    const threadId = this.activeThreadId;
    if (!threadId) return;
    try {
      await this.clientForChat().stopAgent(threadId, agentId);
    } catch (error) {
      this.ui.showError(`Failed to stop agent: ${formatError(error)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
  }

  /** The thread's whole roster from the routes — the source once a turn is over (§11.1). */
  private async refreshAgentRoster(threadId: string): Promise<void> {
    let agents: AgentSummary[];
    try {
      agents = await this.clientForChat().listAgents(threadId);
    } catch {
      return; // transient, or a backend without agents: roster rows show placeholders
    }
    if (threadId !== this.activeThreadId) return;
    if (agents.length > 0) this.ui.renderAgents(agents);
    for (const agent of agents) this.agentViews.noteStatus(agent.agentId, agent.status);
  }
```

`streamTurn` — add before the final `} else if (event.type === "chat_done") {`:

```ts
        } else if (
          event.type === "agent_started" ||
          event.type === "agent_status" ||
          event.type === "agent_finished"
        ) {
          // Low-volume roster pokes (spec §11.2): /live is the source of truth.
          void this.pollThreadLiveState();
        } else if (event.type === "agent_dispatch") {
          this.ui.appendChatMessage(parseWireChatMessage(event.payload.message));
```

`pollThreadLiveState`:
- Add to the signature object, after `sessions: live.sessions,`:

  ```ts
        // INVARIANT (CLAUDE.md /live dedup): roster rows are consumed after this gate.
        agents: live.agents,
  ```

- Directly before `this.ui.sendLiveStatus(live.status ?? null, live.turnActive ?? false);`, add:

  ```ts
      if (live.agents && live.agents.length > 0) {
        this.ui.renderAgents(live.agents);
        for (const agent of live.agents) this.agentViews.noteStatus(agent.agentId, agent.status);
      }
      if (this.lastTurnActive && !live.turnActive) void this.refreshAgentRoster(threadId);
      this.lastTurnActive = live.turnActive ?? false;
  ```

- [ ] **Step 4:** `npm run -w crucible-vscode-extension test` → all pass (the whole extension suite, since `createUi` changed). Don't typecheck yet: `extension.ts`'s `ControllerUI` object lacks the three new methods until Task B3 (`TS2739 … missing renderAgents, agentDetail, agentEvent`).

- [ ] **Step 5: Commit** — `feat(extension): sub-agent roster, live pokes and stop in the controller`

### Task B3: Panel routing and extension wiring

**Files:** Modify `apps/vscode-extension/src/chat-panel.ts`, `apps/vscode-extension/src/extension.ts`.

- [ ] **Step 1: Implement `chat-panel.ts`.**
- Its `@crucible/editor-client` import is `import type { … }`: add `AgentDetail,` and `AgentSummary,` before `ChatMessage,`, and `SequencedStreamEvent,` after `ReasoningEffort,`.
- Append two constructor params after `onRewindConfirm`'s (add a `,` after that param):

  ```ts
      private readonly onSetOpenAgents: (agentIds: string[]) => void = () => {},
      private readonly onStopAgent: (agentId: string) => Promise<void> = async () => {}
  ```

- In `registerHandlers`, add before the `} else if (m["type"] === "stopTurn") {` branch:

  ```ts
        } else if (m["type"] === "setOpenAgents") {
          const ids = Array.isArray(m["agentIds"])
            ? (m["agentIds"] as unknown[]).filter((x): x is string => typeof x === "string")
            : [];
          this.onSetOpenAgents(ids);
          return;
        } else if (m["type"] === "stopAgent") {
          p = this.onStopAgent(String(m["agentId"] ?? ""));
  ```

- Add the three poster methods next to `sendLiveStatus`:

  ```ts
    renderAgents(agents: AgentSummary[]): void {
      this.panel?.webview.postMessage({ type: "renderAgents", agents });
    }

    agentDetail(agentId: string, detail: AgentDetail): void {
      this.panel?.webview.postMessage({ type: "agentDetail", agentId, detail });
    }

    agentEvent(agentId: string, event: SequencedStreamEvent): void {
      this.panel?.webview.postMessage({ type: "agentEvent", agentId, event });
    }
  ```

- [ ] **Step 2: Implement `extension.ts`.**
- In `new ChatPanel(...)`, after `(messageId) => controller.rewindTo(messageId)`, add:

  ```ts
      (agentIds) => controller.setOpenAgents(agentIds),
      (agentId) => controller.stopAgent(agentId)
  ```

  (put a `,` after the `rewindTo` arrow).
- In the `ui: ControllerUI` object, after `sendLiveStatus`, add:

  ```ts
      renderAgents: (agents) => {
        chatPanel.renderAgents(agents);
      },
      agentDetail: (agentId, detail) => {
        chatPanel.agentDetail(agentId, detail);
      },
      agentEvent: (agentId, event) => {
        chatPanel.agentEvent(agentId, event);
      },
  ```

- [ ] **Step 3:** `npm run -w crucible-vscode-extension typecheck` and `npm run -w crucible-vscode-extension test` → both exit 0.

- [ ] **Step 4: Commit** — `feat(extension): route sub-agent view messages between webview and host`

---

## Part C — Webview

### Task C1: Types, pure view logic, reducer

**Files:** Modify `webview-ui/src/types.ts`, `webview-ui/src/hooks/useAppState.ts`. Create `webview-ui/src/agents.ts`. Test: `webview-ui/src/test/agents.test.ts`; extend `webview-ui/src/test/useAppState.test.ts`.

- [ ] **Step 1: Types.** In `types.ts`, add after `RetryStatusView`:

```ts
// ── Sub-agents (mirrors editor-client AgentSummary/AgentDetail — camelCase) ─────
export interface AgentSummaryView {
  agentId: string;
  turnId?: string;
  parentAgentId: string | null;
  depth: number;
  name: string;
  label: string;
  status: string;
  now: string;
  toolCount: number;
  filesChangedCount: number;
  startedAt: string | null;
  endedAt: string | null;
  reportPreview: string;
}

export interface AgentDetailView extends AgentSummaryView {
  prompt: string;
  report: string;
  filesChanged: string[];
  staleRefusals: number;
  transcript: ChatMsg[];
  lastSeq: number;
}

/** A child-channel event as forwarded by the host (payload keys stay snake_case). */
export interface AgentEventView {
  type: string;
  payload: Record<string, unknown>;
  seq?: number;
}

/** An open agent: its backfilled transcript plus live pills not yet sealed into it. */
export interface AgentViewState {
  detail: AgentDetailView;
  messages: ChatMsg[];
  live: ToolEventView[];
  callIds: Record<number, number>;  // call_index → live pill id
  nextId: number;
}
```

Add to `ExtensionMessage`, before `| { type: "composerPrefill"; text: string };`:

```ts
  | { type: "renderAgents"; agents: AgentSummaryView[] }
  | { type: "agentDetail"; agentId: string; detail: AgentDetailView }
  | { type: "agentEvent"; agentId: string; event: AgentEventView }
```

Add to `WebviewMessage`, before `| { type: "listWorkspaceFiles" }`:

```ts
  | { type: "setOpenAgents"; agentIds: string[] }
  | { type: "stopAgent"; agentId: string }
```

Add to `AppState`, after `stepReview: boolean;`:

```ts
  // Sub-agent roster rows (agentId → summary), merged from /live and the routes.
  agents: Record<string, AgentSummaryView>;
  // Open agents' transcripts (backfill + live events).
  agentViews: Record<string, AgentViewState>;
```

- [ ] **Step 2: Write the failing tests.** Create `src/test/agents.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { appendDurable, applyAgentEvent, formatElapsed, viewFromDetail } from "../agents";
import type { AgentDetailView, ChatMsg } from "../types";

const AT = "2026-10-01T00:00:00.000Z";

function detail(transcript: ChatMsg[] = []): AgentDetailView {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status: "running", now: "", toolCount: 0, filesChangedCount: 0, startedAt: null,
    endedAt: null, reportPreview: "", prompt: "p", report: "", filesChanged: [],
    staleRefusals: 0, transcript, lastSeq: 0,
  };
}

const roster = (ids: string[]): ChatMsg => ({
  role: "agent", content: "", type: "agent_dispatch", timestamp: AT, metadata: { agent_ids: ids } });

describe("applyAgentEvent", () => {
  it("pairs a tool result with its call by call_index", () => {
    let v = viewFromDetail(detail());
    v = applyAgentEvent(v, { type: "tool_call", payload: { tool: "read_file", args: { path: "a.py" }, call_index: 0 } }, AT);
    v = applyAgentEvent(v, { type: "tool_call", payload: { tool: "search_code", args: {}, call_index: 1 } }, AT);
    v = applyAgentEvent(v, { type: "tool_result", payload: { output: "ok", is_error: false, call_index: 0 } }, AT);
    expect(v.live.map((t) => [t.tool, t.done])).toEqual([["read_file", true], ["search_code", false]]);
    expect(v.live[0].output).toBe("ok");
  });

  it("seals live pills before a durable message", () => {
    let v = viewFromDetail(detail());
    v = applyAgentEvent(v, { type: "tool_call", payload: { tool: "read_file", args: {}, call_index: 0 } }, AT);
    v = applyAgentEvent(v, { type: "chat_progress", payload: { note: "halfway" } }, AT);
    v = applyAgentEvent(v, { type: "chat_breadcrumb", payload: { text: "✓ Edit accepted: a.py" } }, AT);
    v = applyAgentEvent(v, { type: "diff_ready", payload: { diff_entries: [{ path: "a.py", additions: 1, deletions: 0 }], resolved: "applied" } }, AT);
    expect(v.live).toEqual([]);
    expect(v.messages.map((m) => [m.type, m.content])).toEqual([
      ["text", ""], ["text", "halfway"], ["text", "✓ Edit accepted: a.py"], ["diff_card", ""]]);
    expect(v.messages[0].metadata.tool_events).toHaveLength(1);
    expect(v.messages[1].metadata.progress).toBe(true);
    expect(v.messages[2].metadata.breadcrumb).toBe(true);
    expect(v.messages[3].metadata).toMatchObject({ resolved: "applied" });
  });

  it("adds a nested roster once, even if the backfill already had it", () => {
    let v = viewFromDetail(detail([roster(["agent-z"])]));
    v = applyAgentEvent(v, { type: "agent_dispatch", payload: { message: roster(["agent-z"]) } }, AT);
    expect(v.messages.filter((m) => m.type === "agent_dispatch")).toHaveLength(1);
  });

  it("ignores events the view does not render", () => {
    const v = viewFromDetail(detail());
    expect(applyAgentEvent(v, { type: "token_progress", payload: { thinking: 9 } }, AT)).toBe(v);
  });
});

describe("helpers", () => {
  it("appendDurable dedups roster messages by agent ids", () => {
    const once = appendDurable([], roster(["a", "b"]));
    expect(appendDurable(once, roster(["a", "b"]))).toBe(once);
    expect(appendDurable(once, roster(["c"]))).toHaveLength(2);
  });

  it("formatElapsed", () => {
    expect(formatElapsed(38_000)).toBe("38s");
    expect(formatElapsed(72_000)).toBe("1m12s");
    expect(formatElapsed(3_905_000)).toBe("1h05m");
    expect(formatElapsed(-5)).toBe("0s");
  });
});
```

Append to `src/test/useAppState.test.ts`, inside the top-level `describe("useAppState", …)`:

```ts
  it("merges roster rows, opens an agent view, folds its events, and resets on clearThread", () => {
    const { result } = renderHook(() => useAppState());
    const row = {
      agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
      status: "running", now: "read_file a.py", toolCount: 1, filesChangedCount: 0,
      startedAt: null, endedAt: null, reportPreview: "",
    };
    act(() => { fireMessage({ type: "renderAgents", agents: [row] }); });
    act(() => { fireMessage({ type: "renderAgents", agents: [{ ...row, toolCount: 2 }] }); });
    expect(result.current.state.agents["agent-a"].toolCount).toBe(2);

    act(() => { fireMessage({ type: "agentDetail", agentId: "agent-a", detail: {
      ...row, prompt: "Survey the code", report: "", filesChanged: [], staleRefusals: 0,
      transcript: [], lastSeq: 3 } }); });
    act(() => { fireMessage({ type: "agentEvent", agentId: "agent-a", event: {
      type: "tool_call", payload: { tool: "read_file", args: {}, call_index: 0 }, seq: 4 } }); });
    expect(result.current.state.agentViews["agent-a"].live).toHaveLength(1);

    act(() => { fireMessage({ type: "clearThread" }); });
    expect(result.current.state.agents).toEqual({});
    expect(result.current.state.agentViews).toEqual({});
  });

  it("appends a roster message once even when it is delivered twice", () => {
    const { result } = renderHook(() => useAppState());
    const message = { role: "agent" as const, content: "", type: "agent_dispatch" as const,
      timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-a"] } };
    act(() => { fireMessage({ type: "appendMessage", message }); });
    act(() => { fireMessage({ type: "appendMessage", message }); });
    expect(result.current.state.messages.filter((m) => m.type === "agent_dispatch")).toHaveLength(1);
  });
```

- [ ] **Step 3:** `npm --prefix apps/vscode-extension/webview-ui test -- src/test/agents.test.ts src/test/useAppState.test.ts` → FAIL (`Failed to resolve import "../agents"`).

- [ ] **Step 4: Implement** `src/agents.ts`:

```ts
import type { AgentDetailView, AgentEventView, AgentViewState, ChatMsg, ToolEventView } from "./types";

export const TERMINAL_AGENT_STATUSES: ReadonlySet<string> = new Set([
  "completed", "partial", "failed", "stopped",
]);

export function isTerminalAgent(status: string): boolean {
  return TERMINAL_AGENT_STATUSES.has(status);
}

/** A fresh view from a backfill: the persisted transcript is authoritative. */
export function viewFromDetail(detail: AgentDetailView): AgentViewState {
  return { detail, messages: detail.transcript, live: [], callIds: {}, nextId: 1 };
}

function rosterKey(m: ChatMsg): string | null {
  if (m.type !== "agent_dispatch") return null;
  const ids = m.metadata?.agent_ids;
  return Array.isArray(ids) ? ids.join(",") : null;
}

/** Appends a durable message, skipping a roster card that is already there: a live
 * broadcast and a backfill (or a reload replay) can both deliver the same one. */
export function appendDurable(messages: ChatMsg[], message: ChatMsg): ChatMsg[] {
  const key = rosterKey(message);
  if (key !== null && messages.some((m) => rosterKey(m) === key)) return messages;
  return [...messages, message];
}

function seal(view: AgentViewState, at: string): AgentViewState {
  if (view.live.length === 0) return view;
  const pills: ChatMsg = {
    role: "agent", content: "", type: "text", timestamp: at, metadata: { tool_events: view.live },
  };
  return { ...view, messages: [...view.messages, pills], live: [], callIds: {} };
}

function text(at: string, content: string, flag: "progress" | "breadcrumb"): ChatMsg {
  return { role: "agent", content, type: "text", timestamp: at, metadata: { [flag]: true } };
}

/** Folds one child-channel event into an open view (spec §10). Events the wireframe
 * does not show (thinking, token counts) are ignored; the final backfill is
 * authoritative anyway. */
export function applyAgentEvent(view: AgentViewState, event: AgentEventView, at: string): AgentViewState {
  const p = event.payload;
  switch (event.type) {
    case "tool_call": {
      const id = view.nextId;
      const pill: ToolEventView = {
        id,
        tool: String(p.tool ?? ""),
        args: (p.args as Record<string, unknown> | undefined) ?? {},
        thought: typeof p.thought === "string" ? p.thought : undefined,
        source: "execution",
        done: false,
      };
      const callIds = typeof p.call_index === "number"
        ? { ...view.callIds, [p.call_index]: id }
        : view.callIds;
      return { ...view, live: [...view.live, pill], callIds, nextId: id + 1 };
    }
    case "tool_result": {
      const byIndex = typeof p.call_index === "number" ? view.callIds[p.call_index] : undefined;
      // No call_index: the newest unfinished pill is the one that returned.
      const id = byIndex ?? [...view.live].reverse().find((t) => !t.done)?.id;
      if (id === undefined) return view;
      return {
        ...view,
        live: view.live.map((t) => (t.id === id
          ? { ...t, output: String(p.output ?? ""), isError: p.is_error === true, done: true }
          : t)),
      };
    }
    case "chat_progress": {
      const v = seal(view, at);
      return { ...v, messages: [...v.messages, text(at, String(p.note ?? ""), "progress")] };
    }
    case "chat_breadcrumb": {
      const v = seal(view, at);
      return { ...v, messages: [...v.messages, text(at, String(p.text ?? ""), "breadcrumb")] };
    }
    case "diff_ready": {
      const v = seal(view, at);
      const card: ChatMsg = {
        role: "agent", content: "", type: "diff_card", timestamp: at,
        metadata: { diff_entries: p.diff_entries ?? [], resolved: p.resolved },
      };
      return { ...v, messages: [...v.messages, card] };
    }
    case "agent_dispatch": {
      const message = p.message as ChatMsg | undefined;
      if (!message) return view;
      const v = seal(view, at);
      return { ...v, messages: appendDurable(v.messages, message) };
    }
    default:
      return view;
  }
}

export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}

/** Milliseconds an agent has run (or ran), or null before it started. */
export function elapsedMs(agent: { startedAt: string | null; endedAt: string | null }, now: number): number | null {
  if (!agent.startedAt) return null;
  const end = agent.endedAt ? Date.parse(agent.endedAt) : now;
  return end - Date.parse(agent.startedAt);
}
```

Reducer (`hooks/useAppState.ts`):
- Import `{ appendDurable, applyAgentEvent, viewFromDetail }` from `"../agents"`.
- Add `agents: {}, agentViews: {},` to `INITIAL` after `stepReview: true,`.
- In `clearThread`, add `agents: {}, agentViews: {},`.
- In `appendMessage`, after the `diff_card` branch, add:

  ```ts
        if (m.type === "agent_dispatch") {
          return { ...next, messages: appendDurable(next.messages, m) };
        }
  ```

- Add cases before `default:`:

  ```ts
      case "renderAgents": {
        const agents = { ...state.agents };
        for (const agent of msg.agents) agents[agent.agentId] = agent;
        return { ...state, agents };
      }

      case "agentDetail":
        return {
          ...state,
          agentViews: { ...state.agentViews, [msg.agentId]: viewFromDetail(msg.detail) },
        };

      case "agentEvent": {
        const view = state.agentViews[msg.agentId];
        if (!view) return state;  // a late event for a view the backfill has not opened
        return {
          ...state,
          agentViews: { ...state.agentViews, [msg.agentId]: applyAgentEvent(view, msg.event, at) },
        };
      }
  ```

The webview typecheck covers test files, and two of them build a whole `AppState` literal. Add `agents: {}, agentViews: {},` to each, directly after its `stepReview: true,`:
- `src/test/assembly.test.tsx` (`makeState`);
- `src/components/ThreadView.test.tsx` (`base`).

Otherwise: `TS2322 … Property 'agents' is missing`.

- [ ] **Step 5:** `npm --prefix apps/vscode-extension/webview-ui test` → all pass. `npm --prefix apps/vscode-extension/webview-ui run typecheck` → exit 0.

- [ ] **Step 6: Commit** — `feat(webview): sub-agent roster and view state`

### Task C2: Icons, context, chip, roster card

**Files:** Modify `webview-ui/src/components/Icon.tsx`, `webview-ui/src/components/MessageRow.tsx`. Create `webview-ui/src/components/agents/{AgentsContext.tsx,useNow.ts,AgentChip.tsx,AgentRosterCard.tsx}`. Test: `webview-ui/src/test/agentRoster.test.tsx`.

- [ ] **Step 1: Write the failing test** — `src/test/agentRoster.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentRosterCard, statusLine } from "../components/agents/AgentRosterCard";
import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MessageRow } from "../components/MessageRow";
import type { AgentSummaryView } from "../types";

function agent(over: Partial<AgentSummaryView>): AgentSummaryView {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status: "running", now: "read_file api/a.py", toolCount: 7, filesChangedCount: 1,
    startedAt: "2026-10-01T00:00:00Z", endedAt: "2026-10-01T00:01:12Z", reportPreview: "",
    ...over,
  };
}

function ui(agents: AgentSummaryView[], over: Partial<AgentsUi> = {}): AgentsUi {
  return {
    agents: Object.fromEntries(agents.map((a) => [a.agentId, a])), views: {},
    expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(), ...over,
  };
}

describe("AgentRosterCard", () => {
  const rows = [
    agent({ agentId: "agent-a", label: "middleware survey", status: "completed", reportPreview: "4 hooks found\nmore" }),
    agent({ agentId: "agent-b", label: "limiter impl", name: "general-purpose", status: "running" }),
    agent({ agentId: "agent-c", label: "docs", name: "general-purpose", status: "waiting" }),
  ];

  it("shows the count, the status tags and one row per agent", () => {
    render(<AgentsContext.Provider value={ui(rows)}><AgentRosterCard agentIds={["agent-a", "agent-b", "agent-c"]} /></AgentsContext.Provider>);
    expect(screen.getByText("3 agents")).toBeInTheDocument();
    expect(screen.getByText("1 done")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(screen.getByText("1 waiting")).toBeInTheDocument();
    expect(screen.getByText("✓ reported: 4 hooks found")).toBeInTheDocument();
    expect(screen.getByText("read_file api/a.py")).toBeInTheDocument();
    expect(screen.getByText("⏸ needs your approval ↓")).toBeInTheDocument();
    expect(screen.getAllByText("7 tools · 1 file")).toHaveLength(3);
  });

  it("▸ toggles the inline box and ⤢ opens the window with the siblings", () => {
    const value = ui(rows);
    render(<AgentsContext.Provider value={value}><AgentRosterCard agentIds={["agent-a", "agent-b"]} /></AgentsContext.Provider>);
    fireEvent.click(screen.getByRole("button", { name: "Expand limiter impl" }));
    expect(value.toggleExpanded).toHaveBeenCalledWith("agent-b");
    fireEvent.click(screen.getByRole("button", { name: "Open limiter impl in a window" }));
    expect(value.openWindow).toHaveBeenCalledWith("agent-b", ["agent-a", "agent-b"]);
  });

  it("renders a placeholder row for an agent whose summary has not arrived", () => {
    render(<AgentsContext.Provider value={ui([])}><AgentRosterCard agentIds={["agent-x"]} /></AgentsContext.Provider>);
    expect(screen.getByText("1 agent")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("MessageRow renders the roster for an agent_dispatch message", () => {
    render(<AgentsContext.Provider value={ui(rows)}><MessageRow msg={{
      role: "agent", content: "", type: "agent_dispatch", timestamp: "t",
      metadata: { agent_ids: ["agent-a"] } }} /></AgentsContext.Provider>);
    expect(screen.getByTestId("agent-roster")).toBeInTheDocument();
  });
});

describe("statusLine", () => {
  it("covers each status", () => {
    expect(statusLine(agent({ status: "queued" }))).toBe("queued");
    expect(statusLine(agent({ status: "failed", reportPreview: "boom" }))).toBe("✗ boom");
    expect(statusLine(agent({ status: "stopped" }))).toBe("■ stopped");
    expect(statusLine(agent({ status: "running", now: "" }))).toBe("working…");
  });
});
```

- [ ] **Step 2:** `npm --prefix apps/vscode-extension/webview-ui test -- src/test/agentRoster.test.tsx` → FAIL (module not found).

- [ ] **Step 3: Implement.**

`Icon.tsx`:
- Extend `IconName` with `| "fork" | "expand"` (append to the last line of the union).
- Add to `ICONS`:

```tsx
  fork: (
    <>
      <circle cx="4.5" cy="3.5" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="11.5" cy="3.5" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="12.5" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4.5 5.1v1.4c0 1.1.9 2 2 2h3c1.1 0 2-.9 2-2V5.1M8 8.5v2.4" fill="none"
        stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),

  expand: (
    <path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9" fill="none"
      stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
  ),
```

`components/agents/AgentsContext.tsx`:

```tsx
import { createContext, useContext } from "react";
import type { AgentSummaryView, AgentViewState } from "../../types";

/** What sub-agent UI needs from the thread: roster rows, open views and the two
 * affordances (inline ▸, floating ⤢). Provided by ThreadView. */
export interface AgentsUi {
  agents: Record<string, AgentSummaryView>;
  views: Record<string, AgentViewState>;
  expanded: ReadonlySet<string>;
  toggleExpanded(agentId: string): void;
  openWindow(agentId: string, siblings: string[]): void;
}

export const AgentsContext = createContext<AgentsUi>({
  agents: {}, views: {}, expanded: new Set(), toggleExpanded: () => {}, openWindow: () => {},
});

export function useAgentsUi(): AgentsUi {
  return useContext(AgentsContext);
}
```

`components/agents/useNow.ts`:

```ts
import { useEffect, useState } from "react";

/** The wall clock, re-rendered every `ms` while `active` (a running agent's elapsed). */
export function useNow(active: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [active, ms]);
  return now;
}
```

`components/agents/AgentChip.tsx`:

```tsx
/** An agent's definition name as a chip; read-only `explore` gets the code tint. */
export function AgentChip({ name, label }: { name: string; label?: string }) {
  const readOnly = name === "explore";
  return (
    <span
      className="whitespace-nowrap rounded-full border px-1.5 text-[9.5px] leading-[15px]"
      style={readOnly
        ? { color: "var(--color-code)", borderColor: "rgba(125,211,252,.3)" }
        : { color: "var(--color-accent-ink)", borderColor: "var(--accent-brd)" }}
    >
      {label ? `${label} · ${name}` : name}
    </span>
  );
}
```

`components/agents/AgentRosterCard.tsx`:

```tsx
import { Icon } from "../Icon";
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import type { AgentSummaryView } from "../../types";
import { AgentChip } from "./AgentChip";
import { useAgentsUi } from "./AgentsContext";
import { InlineAgentBox } from "./InlineAgentBox";
import { useNow } from "./useNow";

export type AgentTone = "done" | "running" | "waiting" | "failed";

export function toneOf(status: string): AgentTone {
  if (status === "completed" || status === "partial") return "done";
  if (status === "waiting") return "waiting";
  if (status === "failed" || status === "stopped") return "failed";
  return "running";  // queued | running
}

export const TONE_COLOR: Record<AgentTone, string> = {
  done: "var(--color-green)",
  running: "var(--color-accent)",
  waiting: "var(--color-amber)",
  failed: "var(--color-red)",
};

function firstLine(text: string): string {
  return text.split("\n").find((line) => line.trim())?.trim() ?? "";
}

export function statusLine(agent: AgentSummaryView): string {
  switch (agent.status) {
    case "queued": return "queued";
    case "waiting": return "⏸ needs your approval ↓";
    case "completed":
    case "partial": return `✓ reported: ${firstLine(agent.reportPreview)}`;
    case "failed": return `✗ ${firstLine(agent.reportPreview) || "failed"}`;
    case "stopped": return "■ stopped";
    default: return agent.now || "working…";
  }
}

function placeholder(agentId: string): AgentSummaryView {
  return {
    agentId, parentAgentId: null, depth: 1, name: "agent", label: "agent", status: "queued",
    now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null, reportPreview: "",
  };
}

export function rosterRow(agents: Record<string, AgentSummaryView>, agentId: string): AgentSummaryView {
  return agents[agentId] ?? placeholder(agentId);
}

const TAGS: Array<{ tone: AgentTone; word: string; bg: string }> = [
  { tone: "done", word: "done", bg: "var(--green-bg)" },
  { tone: "running", word: "running", bg: "var(--accent-bg)" },
  { tone: "waiting", word: "waiting", bg: "var(--amber-bg)" },
  { tone: "failed", word: "failed", bg: "var(--red-bg)" },
];

/** The dispatch roster (spec §10): one row per agent with ▸ (inline) and ⤢ (window). */
export function AgentRosterCard({ agentIds }: { agentIds: string[] }) {
  const ui = useAgentsUi();
  const rows = agentIds.map((id) => rosterRow(ui.agents, id));
  const now = useNow(rows.some((a) => !isTerminalAgent(a.status)));
  const counts = new Map<AgentTone, number>();
  for (const a of rows) counts.set(toneOf(a.status), (counts.get(toneOf(a.status)) ?? 0) + 1);
  return (
    <div className="surface-card overflow-hidden" data-testid="agent-roster">
      <div className="accent-wash flex items-center gap-2 px-3 py-2"
        style={{ borderBottom: "1px solid var(--color-border)" }}>
        <span className="flex h-5 w-5 items-center justify-center rounded-md"
          style={{ background: "var(--accent-bg)", border: "1px solid var(--accent-brd)",
                   color: "var(--color-accent-ink)" }}>
          <Icon name="fork" size={11} />
        </span>
        <span className="text-xs font-semibold text-text">
          {rows.length} agent{rows.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto flex gap-1.5">
          {TAGS.filter((t) => counts.has(t.tone)).map((t) => (
            <span key={t.tone} className="rounded-full px-1.5 text-[10px]"
              style={{ background: t.bg, color: TONE_COLOR[t.tone] }}>
              {counts.get(t.tone)} {t.word}
            </span>
          ))}
        </span>
      </div>
      {rows.map((agent) => (
        <AgentRosterRow key={agent.agentId} agent={agent} now={now} siblings={agentIds} />
      ))}
    </div>
  );
}

function AgentRosterRow({ agent, now, siblings }: {
  agent: AgentSummaryView; now: number; siblings: string[];
}) {
  const ui = useAgentsUi();
  const open = ui.expanded.has(agent.agentId);
  const tone = toneOf(agent.status);
  const ms = elapsedMs(agent, now);
  const files = agent.filesChangedCount;
  const counts = `${agent.toolCount} tool${agent.toolCount === 1 ? "" : "s"}`
    + (files > 0 ? ` · ${files} file${files === 1 ? "" : "s"}` : "");
  return (
    <div className="[&:not(:last-child)]:border-b border-border">
      <div className="flex items-center gap-2 px-3 py-2"
        style={open ? { background: "var(--accent-bg)" } : undefined}>
        <span aria-label={agent.status} className="h-2 w-2 flex-shrink-0 rounded-full"
          style={{ background: TONE_COLOR[tone],
                   animation: tone === "running" ? "pulse 1.3s ease-in-out infinite" : undefined }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-xs font-semibold text-text">{agent.label}</span>
            <AgentChip name={agent.name} />
          </div>
          <div className={tone === "waiting"
            ? "truncate text-[11px] text-amber"
            : "truncate font-mono text-[11px] text-text-3"}>
            {statusLine(agent)}
          </div>
        </div>
        <div className="whitespace-nowrap text-right text-[10.5px] leading-tight text-text-3">
          <div>{counts}</div>
          {ms !== null && <div>{formatElapsed(ms)}</div>}
        </div>
        <RowButton label={`${open ? "Collapse" : "Expand"} ${agent.label}`} active={open}
          onClick={() => ui.toggleExpanded(agent.agentId)}>
          <Icon name={open ? "chev-d" : "chev-r"} size={12} />
        </RowButton>
        <RowButton label={`Open ${agent.label} in a window`}
          onClick={() => ui.openWindow(agent.agentId, siblings)}>
          <Icon name="expand" size={11} />
        </RowButton>
      </div>
      {open && <InlineAgentBox agentId={agent.agentId} />}
    </div>
  );
}

function RowButton({ label, active, onClick, children }: {
  label: string; active?: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick}
      className="flex h-[22px] w-[22px] flex-shrink-0 cursor-pointer items-center justify-center rounded-md border transition-colors duration-150 hover:text-accent-ink"
      style={active
        ? { color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }
        : { color: "var(--color-text-3)", borderColor: "transparent" }}>
      {children}
    </button>
  );
}
```

`AgentRosterCard` imports `InlineAgentBox` (Task C3). To keep this task's test green, create a stub `components/agents/InlineAgentBox.tsx` now and let C3 replace it:

```tsx
export function InlineAgentBox({ agentId }: { agentId: string }) {
  return <div data-testid={`agent-box-${agentId}`} />;
}
```

`MessageRow.tsx`:
- Add `import { AgentRosterCard } from "./agents/AgentRosterCard";`.
- Replace the `agent_dispatch` case (its comment and `return null;`) with:

```tsx
    // The dispatch roster anchor (spec §6.1, §10).
    case "agent_dispatch": {
      const ids = msg.metadata?.agent_ids;
      return Array.isArray(ids) && ids.length > 0
        ? <AgentRosterCard agentIds={ids as string[]} />
        : null;
    }
```

`src/test/components.test.tsx` pins the Phase 2 placeholder (`describe("MessageRow — agent_dispatch", …)` asserts the message renders nothing). Replace that whole `describe` with the one invariant that still holds:

```tsx
describe("MessageRow — agent_dispatch", () => {
  it("renders nothing for a roster message without agent ids", () => {
    const { container } = render(
      <MessageRow msg={{ role: "agent", content: "", type: "agent_dispatch",
                         timestamp: "2026-10-01T00:00:00Z", metadata: {} }} />,
    );
    expect(container.textContent).toBe("");
  });
});
```

- [ ] **Step 4:** `npm --prefix apps/vscode-extension/webview-ui test` → all pass (including `icons.test.tsx`, which checks only its listed names). Then `npm --prefix apps/vscode-extension/webview-ui run typecheck` → exit 0. React is in scope for `React.ReactNode` through the automatic runtime's types. If the typecheck rejects `React.ReactNode`, add `import type { ReactNode } from "react";` and use `ReactNode`.

- [ ] **Step 5: Commit** — `feat(webview): sub-agent roster card`

### Task C3: Transcript and inline box

**Files:** Create `webview-ui/src/components/agents/{useFollowBottom.ts,AgentTranscript.tsx}`. Replace `webview-ui/src/components/agents/InlineAgentBox.tsx`. Test: `webview-ui/src/test/agentTranscript.test.tsx`.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { viewFromDetail } from "../agents";
import { AgentTranscript } from "../components/agents/AgentTranscript";
import { AgentsContext } from "../components/agents/AgentsContext";
import { InlineAgentBox } from "../components/agents/InlineAgentBox";
import type { AgentDetailView, ChatMsg } from "../types";

const AT = "2026-10-01T00:00:00Z";

function detail(status: string, transcript: ChatMsg[], report = ""): AgentDetailView {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "general-purpose", label: "impl",
    status, now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null,
    reportPreview: "", prompt: "Implement the limiter in api/limiter.py", report,
    filesChanged: [], staleRefusals: 0, transcript, lastSeq: 0,
  };
}

function withView(d: AgentDetailView, live = false) {
  const view = viewFromDetail(d);
  return {
    agents: {}, expanded: new Set<string>(), toggleExpanded: () => {}, openWindow: () => {},
    views: { "agent-a": live ? { ...view, live: [{ id: 1, tool: "read_file", args: {}, source: "execution" as const, done: false }] } : view },
  };
}

describe("AgentTranscript", () => {
  it("shows the task, the transcript and the full report", () => {
    const transcript: ChatMsg[] = [
      { role: "agent", content: "Added TokenBucket; wiring next.", type: "text", timestamp: AT, metadata: { progress: true } },
      { role: "agent", content: "All done:\n- limiter added", type: "text", timestamp: AT, metadata: { report: true, status: "completed" } },
    ];
    render(<AgentsContext.Provider value={withView(detail("completed", transcript))}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("TASK FROM PARENT")).toBeInTheDocument();
    expect(screen.getByText("Implement the limiter in api/limiter.py")).toBeInTheDocument();
    expect(screen.getByText("Added TokenBucket; wiring next.")).toBeInTheDocument();
    expect(screen.getByText("REPORT")).toBeInTheDocument();
    expect(screen.getByText("limiter added")).toBeInTheDocument();
  });

  it("falls back to the detail's report when the transcript has none", () => {
    render(<AgentsContext.Provider value={withView(detail("failed", [], "Status: failed — boom"))}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("REPORT · failed")).toBeInTheDocument();
    expect(screen.getByText("Status: failed — boom")).toBeInTheDocument();
  });

  it("renders live pills and a loading state", () => {
    const { rerender } = render(<AgentsContext.Provider value={withView(detail("running", []), true)}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("read_file")).toBeInTheDocument();
    rerender(<AgentsContext.Provider value={{ ...withView(detail("running", [])), views: {} }}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("the inline box is a bounded, contained scroller", () => {
    render(<AgentsContext.Provider value={withView(detail("running", []))}><InlineAgentBox agentId="agent-a" /></AgentsContext.Provider>);
    const box = screen.getByTestId("agent-box-agent-a");
    expect(box.style.height).toBe("min(240px, 40vh)");
    expect(box.style.overscrollBehavior).toBe("contain");
  });
});
```

- [ ] **Step 2:** `npm --prefix apps/vscode-extension/webview-ui test -- src/test/agentTranscript.test.tsx` → FAIL (module `AgentTranscript` not found).

- [ ] **Step 3: Implement.**

`components/agents/useFollowBottom.ts`:

```ts
import { useLayoutEffect, useRef } from "react";
import { isPinnedToBottom } from "../shared/scroll-pinning";

/** Keeps a scroller at its newest entry while the reader is at the bottom; scrolling
 * up stops following until they come back (the ThinkingBlock rule). */
export function useFollowBottom(changeToken: string) {
  const ref = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [changeToken]);
  function onScroll() {
    const el = ref.current;
    if (!el) return;
    pinned.current = isPinnedToBottom({
      scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
    });
  }
  return { ref, onScroll };
}
```

`components/agents/AgentTranscript.tsx`:

```tsx
import { isTerminalAgent } from "../../agents";
import type { AgentViewState } from "../../types";
import { MessageRow } from "../MessageRow";
import { AgentRow } from "../messages/AgentRow";
import { MarkdownContent } from "../shared/MarkdownContent";
import { useAgentsUi } from "./AgentsContext";

/** Changes whenever the view grows or a pill finishes — the follow-bottom trigger. */
export function viewToken(view: AgentViewState | undefined): string {
  if (!view) return "";
  return `${view.messages.length}:${view.live.length}:${view.live.filter((t) => t.done).length}`;
}

function SectionLabel({ children, color }: { children: string; color?: string }) {
  return (
    <div className="text-[9.5px] font-semibold tracking-[.08em]"
      style={{ color: color ?? "var(--color-text-4)" }}>
      {children}
    </div>
  );
}

function ReportBlock({ text, status }: { text: string; status: string }) {
  const ok = status === "completed";
  return (
    <div className="flex flex-col gap-1">
      <SectionLabel color={ok ? "var(--color-green)" : undefined}>
        {ok ? "REPORT" : `REPORT · ${status}`}
      </SectionLabel>
      <div className="rounded-lg px-2.5 py-2 text-[11.5px] text-text-2"
        style={ok
          ? { border: "1px solid var(--green-brd)", background: "linear-gradient(180deg, var(--green-bg), transparent)" }
          : { border: "1px solid var(--color-border-strong)", background: "var(--color-surface)" }}>
        <MarkdownContent content={text} />
      </div>
    </div>
  );
}

/** One agent's history (spec §10): the task it was given, its transcript rendered with
 * the thread's own message components, live pills, and its full report. Shared by the
 * inline box and the floating window. */
export function AgentTranscript({ agentId }: { agentId: string }) {
  const { views } = useAgentsUi();
  const view = views[agentId];
  if (!view) return <div className="text-[11px] text-text-3">Loading…</div>;
  const { detail } = view;
  const hasReport = view.messages.some((m) => m.metadata?.report === true);
  return (
    <div className="flex flex-col gap-2 [&>*]:flex-shrink-0">
      <SectionLabel>TASK FROM PARENT</SectionLabel>
      <div className="whitespace-pre-wrap rounded-lg border border-border bg-surface px-2 py-1.5 text-[11.5px] text-text-2">
        {detail.prompt}
      </div>
      {view.messages.map((m, i) => (m.metadata?.report === true
        ? <ReportBlock key={i} text={m.content} status={String(m.metadata?.status ?? detail.status)} />
        : <MessageRow key={i} msg={m} />))}
      {view.live.length > 0 && <AgentRow content="" toolEvents={view.live} />}
      {!hasReport && isTerminalAgent(detail.status) && detail.report !== "" && (
        <ReportBlock text={detail.report} status={detail.status} />
      )}
    </div>
  );
}
```

`components/agents/InlineAgentBox.tsx` (replaces the C2 stub):

```tsx
import { AgentTranscript, viewToken } from "./AgentTranscript";
import { useAgentsUi } from "./AgentsContext";
import { useFollowBottom } from "./useFollowBottom";

/** ▸ — the agent's transcript in a bounded box inside the thread. Scrolling it never
 * drags the thread (overscroll contained), and it follows the newest entry. */
export function InlineAgentBox({ agentId }: { agentId: string }) {
  const { views } = useAgentsUi();
  const { ref, onScroll } = useFollowBottom(viewToken(views[agentId]));
  return (
    <div ref={ref} onScroll={onScroll} data-testid={`agent-box-${agentId}`}
      className="mb-2.5 ml-4 mr-2.5 overflow-y-auto rounded-lg px-2.5 py-2"
      style={{
        height: "min(240px, 40vh)", overscrollBehavior: "contain",
        background: "var(--color-surface)", border: "1px solid var(--color-border)",
        borderLeft: "2px solid var(--color-accent)",
      }}>
      <AgentTranscript agentId={agentId} />
    </div>
  );
}
```

- [ ] **Step 4:** `npm --prefix apps/vscode-extension/webview-ui test` → all pass. Then `npm --prefix apps/vscode-extension/webview-ui run typecheck` → exit 0.

- [ ] **Step 5: Commit** — `feat(webview): sub-agent transcript and inline box`

### Task C4: Floating window and thread wiring

**Files:** Create `webview-ui/src/components/agents/AgentWindow.tsx`. Modify `webview-ui/src/components/ThreadView.tsx`. Test: `webview-ui/src/test/agentWindow.test.tsx`.

- [ ] **Step 1: Write the failing test**

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentWindow } from "../components/agents/AgentWindow";
import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import type { AgentSummaryView, AppState } from "../types";

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const row = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "general-purpose", label, status,
  now: "", toolCount: 7, filesChangedCount: 1, startedAt: "2026-10-01T00:00:00Z",
  endedAt: "2026-10-01T00:01:12Z", reportPreview: "",
});

const ui: AgentsUi = {
  agents: { a: row("a", "limiter", "running"), b: row("b", "docs", "completed") },
  views: {}, expanded: new Set(), toggleExpanded: () => {}, openWindow: () => {},
};

describe("AgentWindow", () => {
  it("shows the agent's meta, stops it, switches tabs and closes on Esc", () => {
    const onSwitch = vi.fn();
    const onClose = vi.fn();
    render(<AgentsContext.Provider value={ui}>
      <AgentWindow agentId="a" siblings={["a", "b"]} onSwitch={onSwitch} onClose={onClose} />
    </AgentsContext.Provider>);
    expect(screen.getByRole("dialog", { name: "Sub-agent limiter" })).toBeInTheDocument();
    expect(screen.getByText("depth 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "■ Stop" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "stopAgent", agentId: "a" });
    fireEvent.click(screen.getByRole("tab", { name: /docs/ }));
    expect(onSwitch).toHaveBeenCalledWith("b");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("a finished agent has no Stop button and a lone agent has no tabs", () => {
    render(<AgentsContext.Provider value={ui}>
      <AgentWindow agentId="b" siblings={["b"]} onSwitch={() => {}} onClose={() => {}} />
    </AgentsContext.Provider>);
    expect(screen.queryByRole("button", { name: "■ Stop" })).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
  });
});

describe("ThreadView sub-agent wiring", () => {
  it("reports the open set to the host when a row is expanded", () => {
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null,
      inputEnabled: true, liveGates: [], livePlan: null, liveReview: null, liveError: null,
      liveTodos: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null,
      turnActive: false, planMode: false, stepReview: true,
      agents: { a: row("a", "limiter", "running") }, agentViews: {},
      messages: [{ role: "agent", content: "", type: "agent_dispatch", timestamp: "t",
                   metadata: { agent_ids: ["a"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: [] });
    act(() => { screen.getByRole("button", { name: "Expand limiter" }).click(); });
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["a"] });
    act(() => { screen.getByRole("button", { name: "Open limiter in a window" }).click(); });
    expect(screen.getByRole("dialog", { name: "Sub-agent limiter" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2:** `npm --prefix apps/vscode-extension/webview-ui test -- src/test/agentWindow.test.tsx` → FAIL (module not found).

- [ ] **Step 3: Implement** `components/agents/AgentWindow.tsx`:

```tsx
import { useEffect } from "react";
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import { vscode } from "../../vscodeApi";
import { Icon } from "../Icon";
import { AgentChip } from "./AgentChip";
import { AgentTranscript, viewToken } from "./AgentTranscript";
import { TONE_COLOR, rosterRow, toneOf } from "./AgentRosterCard";
import { useAgentsUi } from "./AgentsContext";
import { useFollowBottom } from "./useFollowBottom";
import { useNow } from "./useNow";

interface Props {
  agentId: string;
  siblings: string[];
  onSwitch(agentId: string): void;
  onClose(): void;
}

/** ⤢ — one agent full height over the thread (spec §10): meta, ■ Stop, sibling tabs.
 * One window at a time; Esc, ✕ or a backdrop click closes it. */
export function AgentWindow({ agentId, siblings, onSwitch, onClose }: Props) {
  const ui = useAgentsUi();
  const agent = rosterRow(ui.agents, agentId);
  const running = !isTerminalAgent(agent.status);
  const now = useNow(running);
  const ms = elapsedMs(agent, now);
  const { ref, onScroll } = useFollowBottom(`${agentId}|${viewToken(ui.views[agentId])}`);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div role="presentation" className="scrim absolute inset-0 z-40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}>
      <div role="dialog" aria-modal="true" aria-label={`Sub-agent ${agent.label}`}
        className="surface-card anim-pop absolute inset-x-3 bottom-3 top-10 flex flex-col overflow-hidden">
        <div className="accent-wash px-3 pb-2 pt-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 flex-shrink-0 rounded-full" style={{ background: TONE_COLOR[toneOf(agent.status)] }} />
            <span className="truncate text-[13px] font-semibold text-text">{agent.label}</span>
            <AgentChip name={agent.name} />
            <span className="ml-auto flex items-center gap-1">
              {running && (
                <button type="button"
                  onClick={() => vscode.postMessage({ type: "stopAgent", agentId })}
                  className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px]"
                  style={{ border: "1px solid var(--red-brd)", color: "var(--color-red)", background: "var(--red-bg)" }}>
                  ■ Stop
                </button>
              )}
              <button type="button" onClick={onClose} aria-label="Close agent window" title="Close"
                className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text">
                <Icon name="x" size={12} />
              </button>
            </span>
          </div>
          <div className="mt-1 flex gap-2.5 text-[10.5px] text-text-3">
            <span><b className="font-semibold text-text-2">{agent.status}</b>{ms !== null ? ` · ${formatElapsed(ms)}` : ""}</span>
            <span><b className="font-semibold text-text-2">{agent.toolCount}</b> tools</span>
            <span><b className="font-semibold text-text-2">{agent.filesChangedCount}</b> file{agent.filesChangedCount === 1 ? "" : "s"} changed</span>
            <span>depth {agent.depth}</span>
          </div>
        </div>
        {siblings.length > 1 && (
          <div role="tablist" className="flex gap-0.5 px-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
            {siblings.map((id) => {
              const sib = rosterRow(ui.agents, id);
              const on = id === agentId;
              return (
                <button key={id} type="button" role="tab" aria-selected={on} onClick={() => onSwitch(id)}
                  className="flex cursor-pointer items-center gap-1.5 border-b-2 px-2 py-1.5 text-[11px]"
                  style={{ color: on ? "var(--color-accent-ink)" : "var(--color-text-3)",
                           borderColor: on ? "var(--color-accent)" : "transparent" }}>
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: TONE_COLOR[toneOf(sib.status)] }} />
                  {sib.label}
                </button>
              );
            })}
          </div>
        )}
        <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
          <AgentTranscript agentId={agentId} />
        </div>
      </div>
    </div>
  );
}
```

`ThreadView.tsx`:
- Imports: change `import { useState, useRef, useEffect } from "react";` to `import { useState, useRef, useEffect, useMemo } from "react";`. Add:

  ```tsx
  import { AgentsContext, type AgentsUi } from "./agents/AgentsContext";
  import { AgentWindow } from "./agents/AgentWindow";
  ```

- State, after `const bottomRef = useRef<HTMLDivElement>(null);`:

  ```tsx
    // Sub-agent views (spec §10): rows expanded inline, and the one floating window.
    const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
    const [agentWindow, setAgentWindow] = useState<{ agentId: string; siblings: string[] } | null>(null);

    // Another thread's agents are not ours.
    useEffect(() => {
      setExpanded(new Set());
      setAgentWindow(null);
    }, [state.activeThreadId]);

    // The host keeps one live subscription per open agent: tell it the full set.
    const openAgentsKey = [...new Set([...expanded, ...(agentWindow ? [agentWindow.agentId] : [])])]
      .sort().join(",");
    useEffect(() => {
      vscode.postMessage({ type: "setOpenAgents", agentIds: openAgentsKey ? openAgentsKey.split(",") : [] });
    }, [openAgentsKey]);

    const agentsUi = useMemo<AgentsUi>(() => ({
      agents: state.agents,
      views: state.agentViews,
      expanded,
      toggleExpanded: (agentId) => setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(agentId)) next.delete(agentId);
        else next.add(agentId);
        return next;
      }),
      openWindow: (agentId, siblings) => setAgentWindow({ agentId, siblings }),
    }), [state.agents, state.agentViews, expanded]);
  ```

- Wrap the component's returned root `<div className="relative flex h-full overflow-hidden">…</div>` in `<AgentsContext.Provider value={agentsUi}> … </AgentsContext.Provider>`.
- Directly after the `{rewindPreview !== null && ( … )}` block, inside the root div, add:

  ```tsx
        {agentWindow !== null && (
          <AgentWindow
            agentId={agentWindow.agentId}
            siblings={agentWindow.siblings}
            onSwitch={(agentId) => setAgentWindow({ ...agentWindow, agentId })}
            onClose={() => setAgentWindow(null)}
          />
        )}
  ```


- [ ] **Step 4:** `npm --prefix apps/vscode-extension/webview-ui test` and `npm --prefix apps/vscode-extension/webview-ui run typecheck` → both exit 0.

- [ ] **Step 5: Commit** — `feat(webview): floating sub-agent window and thread wiring`

### Task C5: Gate chip polish and the composer hint

**Files:** Modify `webview-ui/src/components/LiveSlot.tsx`, `webview-ui/src/inputAvailability.ts`. Test: `webview-ui/src/test/views.test.tsx` (append).

- [ ] **Step 1: Write the failing tests.** Append inside `describe("inputAvailability", …)` in `views.test.tsx`:

```tsx
  it("a pending command or MCP gate during a turn points the composer at the card", () => {
    for (const kind of ["command", "mcp_tool"] as const) {
      const r = inputAvailability({
        inputEnabled: false, liveStatus: null, workbar: null, turnActive: true,
        liveGates: [{ gateId: "g", kind, taskId: "t", payload: {}, agent: { id: "a", label: "docs", name: "general-purpose" } }],
      });
      expect(r).toMatchObject({ disabled: true, placeholder: "Answer the card above…", showStop: true });
    }
  });
```

- [ ] **Step 2:** `npm --prefix apps/vscode-extension/webview-ui test -- src/test/views.test.tsx` → the new test FAILS (`"Agent is working…"`).

- [ ] **Step 3: Implement.**
- In `inputAvailability.ts`, insert directly before the `// Row 3: a controller turn is running (no gate).` comment:

  ```ts
    // Row 2b: a command or MCP approval is pending — a sub-agent's or the main agent's.
    // The card is the input path; Stop stays available because the turn is still running.
    if (turnActive && (hasGate("command") || hasGate("mcp_tool"))) {
      return {
        disabled: true,
        placeholder: "Answer the card above…",
        showStop: true,
        taskStop,
      };
    }
  ```

- In `LiveSlot.tsx`, import `{ AgentChip } from "./agents/AgentChip"` and replace the chip `<span …>{agent.label} · {agent.name}</span>` in `GateDispatch` with:

  ```tsx
        <span className="self-start" title={`Raised by sub-agent ${agent.label} (${agent.name})`}>
          <AgentChip name={agent.name} label={agent.label} />
        </span>
  ```

- [ ] **Step 4:** `npm --prefix apps/vscode-extension/webview-ui test` → all pass. (No existing test asserts the old placeholder with a pending command gate, so nothing else changes.) `npm --prefix apps/vscode-extension/webview-ui run typecheck` → exit 0.

- [ ] **Step 5: Commit** — `feat(webview): agent chips on gate cards; composer points at a pending approval`

---

## Part D — Verification and docs

- [ ] **Step 1: Everything green.**
  - backend: `./.venv/bin/pytest` (redirect to a file, check `$?`; only the known `test_command_only_step` may fail);
  - TypeScript, from the root: `npm run build`, `npm run typecheck`, `npm run test`, `npm --prefix apps/vscode-extension/webview-ui test`, `npm --prefix apps/vscode-extension/webview-ui run typecheck`, `npm run -w crucible-vscode-extension webview:build`.
- [ ] **Step 2: Live check** (a dev host on a sub-agents-enabled backend; drive the webview over CDP per the smoke recipe in memory). Expect:
  1. A two-agent dispatch shows the roster live, with no reload needed.
  2. ▸ follows the running agent; scrolling up stops following.
  3. ⤢ opens the window; tabs switch; Esc closes.
  4. ■ Stop on a running agent marks it `stopped` while its sibling continues.
  5. A sub-agent's command gate shows its chip and the composer reads "Answer the card above…".
  6. After the turn, a reload shows the same roster with final statuses, tool counts and reports.
- [ ] **Step 3: CLAUDE.md** — in "Sub-agents (P5)", replace the API + frontend bullet's "Not built yet (Phase 4)…" sentence with a "**UI (Phase 4)**" bullet. It should state:
  - the roster renders from `agent_dispatch`, which is now broadcast, and dedups by `agent_ids`;
  - roster rows come from the `agents` map (`/live` + `listAgents` at load and at turn end);
  - `agents` is in `lastLiveSignature`;
  - `AgentViewManager` keeps one subscription per open agent: backfill → follow → skip `seq <= lastSeq` → re-backfill on idle → final backfill when terminal;
  - `setOpenAgents` reports the full open set;
  - `ChatEventSchema.seq` exists because Zod otherwise strips it.

  Commit with `docs(claude): sub-agents UI`.
