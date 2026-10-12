# Chat Activity Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse a chat turn's tool calls and edits into expandable one-line summaries, show a transient line for live work, end edit turns with a changes card, and make `progress` narration frequent and prominent.

**Architecture:** A pure `groupTranscript()` pass turns `ChatMsg[]` into render items (messages, activity groups, turn changes) that `ThreadView` renders; new presentational components (`ActivityGroup`, `TurnChanges`, `TransientActivity`) reuse `ToolTrack` and `DiffPanes`. The backend adds an `edit_outcome` flag to edit breadcrumbs, and the controller prompt asks for `progress` notes every several steps.

**Tech Stack:** React 18 + Tailwind (webview-ui, vitest + Testing Library), TypeScript host (apps/vscode-extension), Python 3.12 backend (agentd-py, pytest).

**Spec:** `docs/superpowers/specs/2026-10-13-chat-activity-groups-design.md`

## Global Constraints

- No change to the controller response schema or to the `progress` action's semantics or cost.
- Persisted messages are not rewritten: grouping is derived at render time.
- Live and reloaded threads must render the same items.
- Expanded activity boxes are capped at 320 px with their own vertical scroll.
- Copy: "read N files", "searched code N times", "listed N folders", "ran N commands", "searched the web N times", "fetched N pages", "queried the code graph N times", "started N agents", "updated the todo list", "used <tool> N times"; singular forms for 1; " · N failed"; "and N more".
- Colors and surfaces use existing tokens (`var(--color-*)`, `text-text`, `text-text-2`, `text-text-3`), per the webview design system.
- Webview tests: `cd apps/vscode-extension/webview-ui && npx vitest run <path>`. Backend tests: `cd services/agentd-py && .venv/bin/python -m pytest <path>` (never add `-q`).

## Review Focus

1. A live turn where an edit breadcrumb or `diff_ready` arrives while the step's pills are still streaming → the group keeps chronological order (pills of the step, then the edit) and nothing is duplicated after the bubble seals.
2. Reloading a thread mid-turn (an `inflight_turn_id` pills message plus a resumed stream) → the persisted pills and the streaming tail render once, as one group plus the transient line.
3. A turn whose only edits were rejected or not applied → the changes card reads "No changes applied" and lists the files as not applied, with no +/− totals.
4. A transient line or summary built from a very long path/command/query → one line, ellipsized, no horizontal scroll in the transcript.
5. An agent message that has both content and `tool_events` (older QA answers) → stays a full message (boundary), never swallowed into a group.

---

### Task 1: Backend — flag edit-outcome breadcrumbs

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py` (`_write_breadcrumb` ~L3848, `_edit_record_cb` ~L1436–1482, `_child_breadcrumb` ~L1981, child edit outcomes ~L3830–3840)
- Modify: `apps/editor-client/src/contracts/task-contracts.ts:205` (`chat_breadcrumb` payload type)
- Modify: `apps/vscode-extension/src/controller.ts` (both `chat_breadcrumb` branches, ~L1222 and ~L1519)
- Test: `services/agentd-py/tests/test_edit_outcome_breadcrumbs.py` (new)

**Interfaces:**
- Produces: persisted edit-outcome breadcrumbs carry `metadata.edit_outcome = true`; live `chat_breadcrumb` payload carries `edit_outcome: true`; the host forwards it as `metadata.edit_outcome` on the appended message.

- [ ] **Step 1: Write the failing test**

```python
"""Edit-outcome breadcrumbs carry metadata.edit_outcome so the transcript can group them
with the edit they belong to, instead of matching their text."""
from __future__ import annotations

from agentd.chat.controller import ChatController


class _Store:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def append_message(self, thread_id: str, msg: object) -> str:
        self.messages.append(msg)
        return "id"


class _Broadcaster:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def broadcast(self, channel: str, event: dict) -> None:
        self.events.append(event)


def _controller() -> ChatController:
    c = ChatController.__new__(ChatController)
    c._store = _Store()
    c._broadcaster = _Broadcaster()
    c._mark_pills_boundary = lambda thread_id: None  # type: ignore[method-assign]
    return c


def test_outcome_breadcrumb_is_flagged_persisted_and_live() -> None:
    c = _controller()
    c._write_breadcrumb("t", "chat:t", "✓ Edit accepted: a.py", edit_outcome=True)
    assert c._store.messages[-1].metadata == {"breadcrumb": True, "edit_outcome": True}
    assert c._broadcaster.events[-1]["payload"]["edit_outcome"] is True


def test_other_breadcrumbs_are_unchanged() -> None:
    c = _controller()
    c._write_breadcrumb("t", "chat:t", "✓ Implement chosen")
    assert c._store.messages[-1].metadata == {"breadcrumb": True}
    assert "edit_outcome" not in c._broadcaster.events[-1]["payload"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/agentd-py && .venv/bin/python -m pytest tests/test_edit_outcome_breadcrumbs.py`
Expected: FAIL — `_write_breadcrumb() got an unexpected keyword argument 'edit_outcome'`

- [ ] **Step 3: Implement**

In `_write_breadcrumb`:

```python
    def _write_breadcrumb(self, thread_id: str, channel_id: str, text: str,
                          *, edit_outcome: bool = False) -> None:
        """Persist a durable transcript breadcrumb AND broadcast it live (mirror
        engine.write_chat_breadcrumb). The live mode/edit gate is ephemeral; this is
        the permanent record of the user's decision so history reads as a narrative.
        `edit_outcome` marks an edit's accept/reject/not-applied record, which the
        transcript groups with that edit."""
        self._mark_pills_boundary(thread_id)
        metadata: dict[str, object] = {"breadcrumb": True}
        payload: dict[str, object] = {"text": text, "task_id": ""}
        if edit_outcome:
            metadata["edit_outcome"] = True
            payload["edit_outcome"] = True
        self._store.append_message(thread_id, ChatMessage(
            role="agent", content=text, type="text", metadata=metadata))
        self._broadcaster.broadcast(channel_id, {"type": "chat_breadcrumb", "payload": payload})
```

In `_edit_record_cb`, pass `edit_outcome=True` to both `_write_breadcrumb(...)` calls (the `✗ Not applied` one and the gated accept/reject one). In `_child_breadcrumb`, add a keyword `edit_outcome: bool = False` and, when true, add `"edit_outcome": True` to both the broadcast payload and the `ChatMessage` metadata; pass `edit_outcome=True` at the three child outcome calls (`✗ Not applied`, `✓ Edit accepted`, `✗ Edit rejected`).

In `task-contracts.ts`:

```ts
  | { type: "chat_breadcrumb"; payload: { text: string; task_id: string; edit_outcome?: boolean } }
```

In both `chat_breadcrumb` branches of `apps/vscode-extension/src/controller.ts`, set:

```ts
            metadata: event.payload["edit_outcome"] === true
              ? { breadcrumb: true, edit_outcome: true }
              : { breadcrumb: true },
```

- [ ] **Step 4: Run tests**

Run: `cd services/agentd-py && .venv/bin/python -m pytest tests/test_edit_outcome_breadcrumbs.py tests/test_controller_edit*.py`
Expected: PASS. Then `cd ../.. && npm run -w @crucible/editor-client build && npm run -w crucible-vscode-extension typecheck` — exit 0.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_edit_outcome_breadcrumbs.py apps/editor-client/src/contracts/task-contracts.ts apps/vscode-extension/src/controller.ts
git commit -m "feat(chat): flag edit-outcome breadcrumbs so the transcript can group them"
```

---

### Task 2: Prompt — ask for `progress` notes every several steps

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (the "Variant — progress" block, ~L520–539)
- Modify (re-capture): `services/agentd-py/tests/goldens/controller_prompt_main.json`, `controller_prompt_subagents.json`, `controller_prompt_teams.json`
- Test: `services/agentd-py/tests/test_controller_progress_guidance.py` (new)

**Interfaces:**
- Produces: the main and child system prompts contain the narration guidance below.

- [ ] **Step 1: Write the failing test**

```python
"""The controller asks for progress notes every several steps, written for the user."""
from __future__ import annotations

from agentd.chat.controller_prompts import format_controller_system_prompt


def test_main_prompt_expects_regular_progress_notes() -> None:
    prompt = format_controller_system_prompt()
    assert "Keep the user oriented" in prompt
    assert "every several steps" in prompt
    assert "Do not repeat your previous note" in prompt
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/agentd-py && .venv/bin/python -m pytest tests/test_controller_progress_guidance.py`
Expected: FAIL — `"Keep the user oriented" in prompt` is false. (If `format_controller_system_prompt()` needs arguments, mirror the call in `tests/prompt_goldens.py::collect`.)

- [ ] **Step 3: Implement**

Replace the `<<main>>` paragraph under `Variant — progress` with:

```
<<main>>
  Keep the user oriented. Every several steps — after a finding, when you change direction,
  or before a long stretch of work — post a progress note: one or two sentences for the user,
  saying what you found and what you will do next. Do not repeat your previous note, and do
  not post one instead of real work. The 'note' is shown to the user immediately; the turn does
  NOT end and you run again right after.
<</main>>
```

Replace the `<<child>>` paragraph with:

```
<<child>>
  Keep whoever is watching oriented: every several steps — after a finding or a change of
  direction — post a short progress note. It is shown to a human watching, but it does NOT
  reach your dispatcher — only 'report' does, so put every finding in the report. Do not
  repeat your previous note. Posting a note does not end your task; you run again right after.
<</child>>
```

- [ ] **Step 4: Re-capture goldens and run**

```bash
cd services/agentd-py
.venv/bin/python -m tests.prompt_goldens
.venv/bin/python -m tests.test_prompt_goldens_subagents
.venv/bin/python -m tests.test_prompt_goldens_teams
git diff --stat tests/goldens/
.venv/bin/python -m pytest tests/test_controller_progress_guidance.py tests/test_prompt_goldens.py tests/test_prompt_goldens_subagents.py tests/test_prompt_goldens_teams.py tests/test_prompt_leak_lint.py
```

Expected: the golden diffs touch only the progress paragraphs; all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/tests/goldens services/agentd-py/tests/test_controller_progress_guidance.py
git commit -m "feat(controller): ask for a progress note every several steps"
```

---

### Task 3: Pure grouping — `groupTranscript`

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/transcript/groupTranscript.ts`
- Test: `apps/vscode-extension/webview-ui/src/transcript/groupTranscript.test.ts`

**Interfaces:**
- Consumes: `ChatMsg`, `DiffEntry`, `ToolEventView` from `../types`.
- Produces:

```ts
export type ActivityStep =
  | { type: "tools"; events: ToolEventView[] }
  | { type: "edit"; entries: DiffEntry[]; applied: boolean }
  | { type: "outcome"; text: string };
export interface FileChange { path: string; additions: number; deletions: number; applied: boolean; diff?: string }
export type TranscriptItem =
  | { kind: "message"; msg: ChatMsg; index: number }
  | { kind: "activity"; key: string; steps: ActivityStep[] }
  | { kind: "changes"; key: string; files: FileChange[] };
export function isActivityMember(msg: ChatMsg): boolean;
export function groupTranscript(messages: ChatMsg[]): TranscriptItem[];
```

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../types";
import { groupTranscript } from "./groupTranscript";

const t = "2026-10-13T00:00:00Z";
const user = (content: string): ChatMsg => ({ role: "user", content, type: "text", timestamp: t, metadata: {} });
const pills = (...tools: string[]): ChatMsg => ({
  role: "agent", content: "", type: "text", timestamp: t,
  metadata: { tool_events: tools.map((tool, id) => ({ id, tool, args: { path: `${tool}${id}.py` }, source: "explore", done: true })) },
});
const diff = (path: string, add: number, del: number, resolved: "applied" | "discarded" = "applied"): ChatMsg => ({
  role: "agent", content: "", type: "diff_card", timestamp: t,
  metadata: { resolved, diff_entries: [{ path, additions: add, deletions: del, unified_diff: `@@ ${path}` }] },
});
const crumb = (content: string, extra: Record<string, unknown> = {}): ChatMsg => ({
  role: "agent", content, type: "text", timestamp: t, metadata: { breadcrumb: true, ...extra },
});
const progress = (content: string): ChatMsg => ({ role: "agent", content, type: "text", timestamp: t, metadata: { progress: true } });
const answer = (content: string): ChatMsg => ({ role: "agent", content, type: "text", timestamp: t, metadata: {} });

describe("groupTranscript", () => {
  it("merges consecutive pills, edits and outcome breadcrumbs into one group", () => {
    const items = groupTranscript([user("go"), pills("read_file"), diff("a.ts", 3, 1), crumb("✓ Edit accepted: a.ts", { edit_outcome: true }), pills("run_command")]);
    expect(items.map((i) => i.kind)).toEqual(["message", "activity", "changes"]);
    const group = items[1];
    expect(group.kind === "activity" && group.steps.map((s) => s.type)).toEqual(["tools", "edit", "outcome", "tools"]);
  });

  it("splits groups at progress notes and answers, and keeps them as messages", () => {
    const items = groupTranscript([user("go"), pills("read_file"), progress("found it"), pills("run_command"), answer("done")]);
    expect(items.map((i) => i.kind)).toEqual(["message", "activity", "message", "activity", "message"]);
  });

  it("recognises legacy outcome breadcrumbs by their text", () => {
    const items = groupTranscript([user("go"), diff("a.ts", 1, 0, "discarded"), crumb("✗ Not applied: stale")]);
    const group = items[1];
    expect(group.kind === "activity" && group.steps.map((s) => s.type)).toEqual(["edit", "outcome"]);
  });

  it("keeps other breadcrumbs and messages with content plus pills as boundaries", () => {
    const qa: ChatMsg = { ...answer("here is the answer"), metadata: pills("read_file").metadata };
    const items = groupTranscript([user("go"), crumb("✓ Implement chosen"), qa]);
    expect(items.map((i) => i.kind)).toEqual(["message", "message", "message"]);
  });

  it("sums a turn's applied edits per file and lists rejected files as not applied", () => {
    const items = groupTranscript([user("go"), diff("a.ts", 3, 1), diff("a.ts", 2, 0), diff("b.ts", 5, 5, "discarded"), answer("done")]);
    const changes = items[items.length - 1];
    expect(changes.kind).toBe("changes");
    expect(changes.kind === "changes" && changes.files).toEqual([
      { path: "a.ts", additions: 5, deletions: 1, applied: true, diff: "@@ a.ts" },
      { path: "b.ts", additions: 0, deletions: 0, applied: false, diff: undefined },
    ]);
  });

  it("emits one changes item per edited turn and none for a turn without edits", () => {
    const items = groupTranscript([user("1"), diff("a.ts", 1, 0), answer("ok"), user("2"), pills("read_file"), answer("ok")]);
    expect(items.filter((i) => i.kind === "changes")).toHaveLength(1);
    expect(items.findIndex((i) => i.kind === "changes")).toBe(3);
  });

  it("ignores a pills-only message without tool events", () => {
    const empty: ChatMsg = { ...answer(""), metadata: { tool_events: [] } };
    expect(groupTranscript([user("go"), empty]).map((i) => i.kind)).toEqual(["message"]);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/transcript/groupTranscript.test.ts`
Expected: FAIL — cannot resolve `./groupTranscript`.

- [ ] **Step 3: Implement**

```ts
import type { ChatMsg, DiffEntry, ToolEventView } from "../types";

export type ActivityStep =
  | { type: "tools"; events: ToolEventView[] }
  | { type: "edit"; entries: DiffEntry[]; applied: boolean }
  | { type: "outcome"; text: string };

export interface FileChange { path: string; additions: number; deletions: number; applied: boolean; diff?: string }

export type TranscriptItem =
  | { kind: "message"; msg: ChatMsg; index: number }
  | { kind: "activity"; key: string; steps: ActivityStep[] }
  | { kind: "changes"; key: string; files: FileChange[] };

// Edit outcomes written before metadata.edit_outcome existed are only recognisable by text.
const LEGACY_OUTCOME = ["✓ Edit accepted:", "✗ Edit rejected:", "✗ Not applied:"];

function toolEvents(msg: ChatMsg): ToolEventView[] {
  return (msg.metadata?.tool_events as ToolEventView[] | undefined) ?? [];
}

function isOutcome(msg: ChatMsg): boolean {
  if (msg.metadata?.breadcrumb !== true) return false;
  return msg.metadata?.edit_outcome === true || LEGACY_OUTCOME.some((p) => msg.content.startsWith(p));
}

function isPillsOnly(msg: ChatMsg): boolean {
  return msg.role === "agent" && msg.type === "text" && !msg.content.trim()
    && msg.metadata?.progress !== true && msg.metadata?.breadcrumb !== true;
}

export function isActivityMember(msg: ChatMsg): boolean {
  return msg.type === "diff_card" || isOutcome(msg) || isPillsOnly(msg);
}

function toStep(msg: ChatMsg): ActivityStep | null {
  if (msg.type === "diff_card") {
    return { type: "edit", entries: (msg.metadata?.diff_entries as DiffEntry[] | undefined) ?? [],
      applied: msg.metadata?.resolved !== "discarded" };
  }
  if (isOutcome(msg)) return { type: "outcome", text: msg.content };
  const events = toolEvents(msg);
  return events.length ? { type: "tools", events } : null;
}

function turnChanges(steps: ActivityStep[]): FileChange[] {
  const files = new Map<string, FileChange>();
  for (const step of steps) {
    if (step.type !== "edit") continue;
    for (const entry of step.entries) {
      const current = files.get(entry.path) ?? { path: entry.path, additions: 0, deletions: 0, applied: false, diff: undefined };
      if (step.applied) {
        current.additions += entry.additions;
        current.deletions += entry.deletions;
        current.applied = true;
        current.diff = entry.unified_diff ?? current.diff;
      }
      files.set(entry.path, current);
    }
  }
  return [...files.values()];
}

export function groupTranscript(messages: ChatMsg[]): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  let group: { key: string; steps: ActivityStep[] } | null = null;
  let turnSteps: ActivityStep[] = [];
  let turnKey = "start";

  const closeGroup = () => {
    if (group && group.steps.length) items.push({ kind: "activity", key: group.key, steps: group.steps });
    group = null;
  };
  const closeTurn = () => {
    closeGroup();
    if (turnSteps.some((s) => s.type === "edit")) {
      items.push({ kind: "changes", key: `changes-${turnKey}`, files: turnChanges(turnSteps) });
    }
    turnSteps = [];
  };

  messages.forEach((msg, index) => {
    if (msg.role === "user") {
      closeTurn();
      turnKey = msg.id ?? String(index);
      items.push({ kind: "message", msg, index });
      return;
    }
    if (isActivityMember(msg)) {
      const step = toStep(msg);
      if (!step) return;
      group ??= { key: msg.id ?? `activity-${index}`, steps: [] };
      group.steps.push(step);
      turnSteps.push(step);
      return;
    }
    closeGroup();
    items.push({ kind: "message", msg, index });
  });
  closeTurn();
  return items;
}
```

- [ ] **Step 4: Run tests**

Run: `npx vitest run src/transcript/groupTranscript.test.ts` — PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/transcript
git commit -m "feat(webview): group a turn's tool calls and edits into activity items"
```

---

### Task 4: Pure summary — `summarizeActivity` and `describeTool`

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/transcript/summarizeActivity.ts`
- Test: `apps/vscode-extension/webview-ui/src/transcript/summarizeActivity.test.ts`

**Interfaces:**
- Consumes: `ActivityStep` (Task 3), `ToolEventView`.
- Produces:

```ts
export function summarizeActivity(steps: ActivityStep[]): string;  // "Read 2 files, edited `a.ts` +3 −1 · 1 failed"
export function describeTool(event: ToolEventView): string;        // "Reading `a.py`", "Running `pytest -q`"
```

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, expect, it } from "vitest";
import type { ToolEventView } from "../types";
import type { ActivityStep } from "./groupTranscript";
import { describeTool, summarizeActivity } from "./summarizeActivity";

const ev = (tool: string, args: Record<string, unknown> = {}, isError = false): ToolEventView =>
  ({ id: 0, tool, args, source: "explore", done: true, isError });
const tools = (...events: ToolEventView[]): ActivityStep => ({ type: "tools", events });

describe("summarizeActivity", () => {
  it("counts per tool in first-seen order with distinct file reads", () => {
    const steps = [tools(ev("read_file", { path: "a.py" }), ev("read_file", { path: "a.py" }), ev("read_file", { path: "b.py" })),
      tools(ev("search_code"), ev("search_semantic"), ev("run_command"))];
    expect(summarizeActivity(steps)).toBe("Read 2 files, searched code 2 times, ran a command");
  });

  it("names a single edit and totals several", () => {
    const one: ActivityStep = { type: "edit", applied: true, entries: [{ path: "src/a.ts", additions: 3, deletions: 1 }] };
    const two: ActivityStep = { type: "edit", applied: true, entries: [{ path: "b.ts", additions: 2, deletions: 0 }] };
    expect(summarizeActivity([one])).toBe("Edited `a.ts` +3 −1");
    expect(summarizeActivity([one, two])).toBe("Edited 2 files +5 −1");
  });

  it("marks edits that were not applied and counts failures", () => {
    const rejected: ActivityStep = { type: "edit", applied: false, entries: [{ path: "a.ts", additions: 1, deletions: 0 }] };
    expect(summarizeActivity([tools(ev("run_command", {}, true)), rejected])).toBe("Ran a command, edited `a.ts` (not applied) · 1 failed");
  });

  it("caps at three phrases plus 'and N more'", () => {
    const steps = [tools(ev("read_file", { path: "a" }), ev("search_code"), ev("run_command"), ev("web_search"), ev("web_fetch"))];
    expect(summarizeActivity(steps)).toBe("Read a file, searched code once, ran a command, and 2 more");
  });

  it("falls back to the tool name", () => {
    expect(summarizeActivity([tools(ev("mcp__x__y"), ev("mcp__x__y"))])).toBe("Used mcp__x__y 2 times");
  });
});

describe("describeTool", () => {
  it("describes the running tool", () => {
    expect(describeTool(ev("read_file", { path: "src/controller.py" }))).toBe("Reading `controller.py`");
    expect(describeTool(ev("run_command", { command: "pytest", args: ["-q"] }))).toBe("Running `pytest -q`");
    expect(describeTool(ev("web_search", { query: "ollama vision" }))).toBe("Searching the web for “ollama vision”");
    expect(describeTool(ev("dispatch_agents"))).toBe("Starting agents");
    expect(describeTool(ev("other_tool"))).toBe("Using other_tool");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/transcript/summarizeActivity.test.ts` — FAIL (module missing).

- [ ] **Step 3: Implement**

```ts
import type { ToolEventView } from "../types";
import type { ActivityStep } from "./groupTranscript";

type Phrase = { one: string; many: (n: number) => string };

// Tools that share a phrase share a key, so they are counted together.
const PHRASES: Record<string, { key: string } & Phrase> = {
  read_file: { key: "read", one: "read a file", many: (n) => `read ${n} files` },
  search_code: { key: "search", one: "searched code once", many: (n) => `searched code ${n} times` },
  search_semantic: { key: "search", one: "searched code once", many: (n) => `searched code ${n} times` },
  list_directory: { key: "list", one: "listed a folder", many: (n) => `listed ${n} folders` },
  run_command: { key: "run", one: "ran a command", many: (n) => `ran ${n} commands` },
  start_session: { key: "run", one: "ran a command", many: (n) => `ran ${n} commands` },
  write_stdin: { key: "run", one: "ran a command", many: (n) => `ran ${n} commands` },
  web_search: { key: "web", one: "searched the web once", many: (n) => `searched the web ${n} times` },
  web_fetch: { key: "fetch", one: "fetched a page", many: (n) => `fetched ${n} pages` },
  query_graph: { key: "graph", one: "queried the code graph once", many: (n) => `queried the code graph ${n} times` },
  dispatch_agents: { key: "agents", one: "started an agent", many: (n) => `started ${n} agents` },
  write_todos: { key: "todos", one: "updated the todo list", many: () => "updated the todo list" },
};

const basename = (path: string) => path.split("/").pop() || path;
const MAX_PHRASES = 3;

export function summarizeActivity(steps: ActivityStep[]): string {
  const counts = new Map<string, { phrase: Phrase; n: number; paths?: Set<string> }>();
  const edits: { path: string; additions: number; deletions: number; applied: boolean }[] = [];
  let failed = 0;
  const order: string[] = [];

  for (const step of steps) {
    if (step.type === "tools") {
      for (const event of step.events) {
        if (event.isError) failed += 1;
        const known = PHRASES[event.tool];
        const key = known?.key ?? `tool:${event.tool}`;
        if (!counts.has(key)) {
          const phrase = known ?? { one: `used ${event.tool} once`, many: (n: number) => `used ${event.tool} ${n} times` };
          counts.set(key, { phrase, n: 0, paths: event.tool === "read_file" ? new Set() : undefined });
          order.push(key);
        }
        const entry = counts.get(key)!;
        const path = typeof event.args?.path === "string" ? event.args.path : null;
        if (entry.paths && path) entry.paths.add(path); else entry.n += 1;
      }
    } else if (step.type === "edit") {
      if (!edits.length) order.push("edits");
      for (const e of step.entries) edits.push({ ...e, applied: step.applied });
    }
  }

  const phrases = order.map((key) => {
    if (key === "edits") {
      if (edits.length === 1) {
        const e = edits[0];
        return e.applied ? `edited \`${basename(e.path)}\` +${e.additions} −${e.deletions}`
          : `edited \`${basename(e.path)}\` (not applied)`;
      }
      const applied = edits.filter((e) => e.applied);
      const add = applied.reduce((s, e) => s + e.additions, 0);
      const del = applied.reduce((s, e) => s + e.deletions, 0);
      return `edited ${edits.length} files +${add} −${del}`;
    }
    const { phrase, n, paths } = counts.get(key)!;
    const total = paths ? paths.size + n : n;
    return total === 1 ? phrase.one : phrase.many(total);
  });

  const shown = phrases.length > MAX_PHRASES + 1
    ? [...phrases.slice(0, MAX_PHRASES), `and ${phrases.length - MAX_PHRASES} more`]
    : phrases;
  const text = shown.join(", ");
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return failed ? `${sentence} · ${failed} failed` : sentence;
}

export function describeTool(event: ToolEventView): string {
  const a = event.args ?? {};
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  switch (event.tool) {
    case "read_file": return `Reading \`${basename(str(a.path))}\``;
    case "list_directory": return `Listing \`${str(a.path) || "."}\``;
    case "search_code":
    case "search_semantic": return `Searching code for “${str(a.pattern) || str(a.query)}”`;
    case "run_command":
    case "start_session": {
      const args = Array.isArray(a.args) ? a.args.join(" ") : "";
      return `Running \`${[str(a.command), args].filter(Boolean).join(" ")}\``;
    }
    case "web_search": return `Searching the web for “${str(a.query)}”`;
    case "web_fetch": return `Fetching ${str(a.url)}`;
    case "dispatch_agents": return "Starting agents";
    case "write_todos": return "Updating the todo list";
    default: return `Using ${event.tool}`;
  }
}
```

Note the expected test strings for the "caps" case: four distinct phrases + one more = five, so output is the first three plus "and 2 more". Adjust nothing else.

- [ ] **Step 4: Run tests** — `npx vitest run src/transcript/summarizeActivity.test.ts` — PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/transcript
git commit -m "feat(webview): summarize an activity group and describe the running tool"
```

---

### Task 5: `ActivityGroup` component

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/activity/ActivityGroup.tsx`
- Test: `apps/vscode-extension/webview-ui/src/components/activity/ActivityGroup.test.tsx`

**Interfaces:**
- Consumes: `ActivityStep` (Task 3), `summarizeActivity` (Task 4), `ToolTrack` (`components/shared/ToolTrack`, props `{ events }`), `DiffPanes` (`components/shared/DiffPanes`, props `{ entries }`).
- Produces: `export function ActivityGroup({ steps }: { steps: ActivityStep[] }): JSX.Element` with `data-testid="activity-group"`, a toggle button whose accessible name is the summary, and when open a `data-testid="activity-detail"` box.

- [ ] **Step 1: Write the failing tests**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActivityGroup } from "./ActivityGroup";
import type { ActivityStep } from "../../transcript/groupTranscript";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const steps: ActivityStep[] = [
  { type: "tools", events: [{ id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true }] },
  { type: "edit", applied: true, entries: [{ path: "a.ts", additions: 3, deletions: 1, unified_diff: "@@ -1 +1 @@\n-old_line\n+new_line" }] },
  { type: "outcome", text: "✓ Edit accepted: a.ts" },
];

describe("ActivityGroup", () => {
  it("is one collapsed summary line by default", () => {
    render(<ActivityGroup steps={steps} />);
    expect(screen.getByRole("button", { name: /Read a file, edited `a.ts` \+3 −1/ })).toBeInTheDocument();
    expect(screen.queryByTestId("activity-detail")).toBeNull();
  });

  it("expands to the steps in order inside a capped scroll box", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    const detail = screen.getByTestId("activity-detail");
    expect(detail.style.maxHeight).toBe("320px");
    expect(detail.className).toContain("overflow-y-auto");
    expect(detail.textContent).toContain("Edited a.ts");
    expect(detail.textContent).toContain("Edit accepted: a.ts");
  });

  it("opens an edit row to its diff", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    fireEvent.click(screen.getByRole("button", { name: /Edited a.ts/ }));
    expect(screen.getByTestId("activity-detail").textContent).toContain("new_line");
  });

  it("keeps a very long summary on one ellipsized line", () => {
    const long: ActivityStep[] = [{ type: "tools", events: [{ id: 1, tool: "mcp__" + "x".repeat(300), args: {}, source: "explore", done: true }] }];
    render(<ActivityGroup steps={long} />);
    expect(screen.getByTestId("activity-summary").className).toContain("truncate");
  });
});
```

- [ ] **Step 2: Run to verify they fail** — `npx vitest run src/components/activity/ActivityGroup.test.tsx` — FAIL (module missing).

- [ ] **Step 3: Implement**

```tsx
import { useState } from "react";
import type { ActivityStep } from "../../transcript/groupTranscript";
import { summarizeActivity } from "../../transcript/summarizeActivity";
import { DiffPanes } from "../shared/DiffPanes";
import { ToolTrack } from "../shared/ToolTrack";

const basename = (path: string) => path.split("/").pop() || path;

function EditRow({ step }: { step: Extract<ActivityStep, { type: "edit" }> }) {
  const [open, setOpen] = useState(false);
  const label = step.entries.length === 1
    ? `Edited ${basename(step.entries[0].path)}`
    : `Edited ${step.entries.length} files`;
  const add = step.entries.reduce((s, e) => s + e.additions, 0);
  const del = step.entries.reduce((s, e) => s + e.deletions, 0);
  return (
    <div className="grid min-w-0 gap-1">
      <button type="button" onClick={() => setOpen(!open)}
        className="flex min-w-0 items-center gap-2 text-left text-[11.5px] text-text-2 hover:text-text">
        <span className="truncate">{label}</span>
        {step.applied ? (
          <span className="shrink-0">
            <span style={{ color: "var(--color-green)" }}>+{add}</span>{" "}
            <span style={{ color: "var(--color-red)" }}>−{del}</span>
          </span>
        ) : <span className="shrink-0 text-text-3">Not applied</span>}
        <span className="shrink-0 text-text-3">{open ? "▾" : "›"}</span>
      </button>
      {open && <DiffPanes entries={step.entries} />}
    </div>
  );
}

export function ActivityGroup({ steps }: { steps: ActivityStep[] }) {
  const [open, setOpen] = useState(false);
  const summary = summarizeActivity(steps);
  return (
    <div data-testid="activity-group" className="grid min-w-0 gap-1.5">
      <button type="button" aria-expanded={open} aria-label={summary} onClick={() => setOpen(!open)}
        className="flex min-w-0 items-center gap-1.5 text-left text-[12px] text-text-3 hover:text-text-2">
        <span data-testid="activity-summary" className="min-w-0 truncate">{summary}</span>
        <span className="shrink-0">{open ? "▾" : "›"}</span>
      </button>
      {open && (
        <div data-testid="activity-detail" className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2 overflow-y-auto rounded-[8px] p-2"
          style={{ maxHeight: "320px", border: "1px solid var(--color-border)", background: "var(--color-surface)" }}>
          {steps.map((step, i) => step.type === "tools" ? <ToolTrack key={i} events={step.events} />
            : step.type === "edit" ? <EditRow key={i} step={step} />
            : <div key={i} className="text-[11px] text-text-3">{step.text.replace(/^[✓✗]\s*/, "")}</div>)}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/activity
git commit -m "feat(webview): collapsed activity line that expands into a capped scroll box"
```

---

### Task 6: `TurnChanges` card

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/activity/TurnChanges.tsx`
- Test: `apps/vscode-extension/webview-ui/src/components/activity/TurnChanges.test.tsx`

**Interfaces:**
- Consumes: `FileChange` (Task 3), `DiffPanes`, `vscode.postMessage({ type: "openFile", path })` (existing host message).
- Produces: `export function TurnChanges({ files }: { files: FileChange[] }): JSX.Element` with `data-testid="turn-changes"`.

- [ ] **Step 1: Write the failing tests**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TurnChanges } from "./TurnChanges";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("TurnChanges", () => {
  it("totals applied files and lists not-applied ones without counting them", () => {
    render(<TurnChanges files={[
      { path: "src/a.ts", additions: 5, deletions: 1, applied: true, diff: "@@ -1 +1 @@\n-x\n+y" },
      { path: "b.ts", additions: 0, deletions: 0, applied: false },
    ]} />);
    expect(screen.getByTestId("turn-changes").textContent).toContain("Edited 1 file");
    expect(screen.getAllByText("+5")).toHaveLength(2); // header total and the a.ts row
    expect(screen.getByText("not applied")).toBeInTheDocument();
  });

  it("says no changes were applied when every edit was rejected", () => {
    render(<TurnChanges files={[{ path: "b.ts", additions: 0, deletions: 0, applied: false }]} />);
    expect(screen.getByTestId("turn-changes").textContent).toContain("No changes applied");
  });

  it("opens a file and expands its diff", async () => {
    const { vscode } = await import("../../vscodeApi");
    render(<TurnChanges files={[{ path: "src/a.ts", additions: 1, deletions: 0, applied: true, diff: "@@ -1 +1 @@\n+hello" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "src/a.ts" }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "src/a.ts" });
    fireEvent.click(screen.getByRole("button", { name: "Show diff for src/a.ts" }));
    expect(screen.getByTestId("turn-changes").textContent).toContain("hello");
  });
});
```

- [ ] **Step 2: Run to verify they fail** — FAIL (module missing).

- [ ] **Step 3: Implement**

```tsx
import { useState } from "react";
import { vscode } from "../../vscodeApi";
import type { FileChange } from "../../transcript/groupTranscript";
import { DiffPanes } from "../shared/DiffPanes";

function Row({ file }: { file: FileChange }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="grid min-w-0 gap-1">
      <div className="flex min-w-0 items-center gap-2 text-[11.5px]">
        <button type="button" onClick={() => vscode.postMessage({ type: "openFile", path: file.path })}
          className="min-w-0 truncate text-left font-mono text-[11px] text-[var(--color-code)] hover:underline">{file.path}</button>
        <span className="flex-1" />
        {file.applied ? (
          <>
            <span style={{ color: "var(--color-green)" }}>+{file.additions}</span>
            <span style={{ color: "var(--color-red)" }}>−{file.deletions}</span>
          </>
        ) : <span className="text-text-3">not applied</span>}
        {file.diff && (
          <button type="button" aria-label={`Show diff for ${file.path}`} onClick={() => setOpen(!open)}
            className="text-text-3 hover:text-text">{open ? "▾" : "›"}</button>
        )}
      </div>
      {open && file.diff && <DiffPanes entries={[{ path: file.path, additions: file.additions, deletions: file.deletions, unified_diff: file.diff }]} />}
    </div>
  );
}

export function TurnChanges({ files }: { files: FileChange[] }) {
  const applied = files.filter((f) => f.applied);
  const add = applied.reduce((s, f) => s + f.additions, 0);
  const del = applied.reduce((s, f) => s + f.deletions, 0);
  const title = applied.length ? `Edited ${applied.length} file${applied.length === 1 ? "" : "s"}` : "No changes applied";
  return (
    <div data-testid="turn-changes" className="grid min-w-0 gap-1.5 rounded-[10px] px-3 py-2"
      style={{ border: "1px solid var(--color-border-strong)", background: "var(--color-surface)" }}>
      <div className="flex items-center gap-2 text-[12px] text-text">
        <span>{title}</span>
        {applied.length > 0 && (
          <span>
            <span style={{ color: "var(--color-green)" }}>+{add}</span>{" "}
            <span style={{ color: "var(--color-red)" }}>−{del}</span>
          </span>
        )}
      </div>
      {files.map((f) => <Row key={f.path} file={f} />)}
    </div>
  );
}
```

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/activity
git commit -m "feat(webview): end-of-turn changes card with per-file totals and diffs"
```

---

### Task 7: Progress notes as narration; one avatar per agent run

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx` (`ProgressLine` ~L130; add `showAvatar` prop)
- Modify: `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx` (pass `showAvatar` through for agent rows)
- Test: `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.progress.test.tsx` (new)

**Interfaces:**
- Produces: `AgentRow` accepts `showAvatar?: boolean` (default `true`); when `false` it renders a same-width spacer instead of `<Avatar />`. `MessageRow` accepts `showAvatar?: boolean` and forwards it to every `AgentRow` it renders.

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentRow } from "./AgentRow";

describe("progress notes", () => {
  it("render as body-size markdown prose, not muted italic", () => {
    render(<AgentRow content="Found it: the **payload** still has images." progress />);
    const strong = screen.getByText("payload");
    expect(strong.tagName).toBe("STRONG");
    expect(strong.closest(".italic")).toBeNull();
    expect(strong.closest(".text-text-3")).toBeNull();
  });

  it("can omit the avatar to continue the previous agent row", () => {
    const { container } = render(<AgentRow content="next" progress showAvatar={false} />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify they fail** — FAIL (italic present / no spacer).

- [ ] **Step 3: Implement**

In `AgentRow.tsx`, add `showAvatar?: boolean` to `Props` (default `true`) and replace `<Avatar />` with:

```tsx
      {showAvatar ? <Avatar /> : <div data-testid="avatar-spacer" className="w-5 flex-shrink-0" aria-hidden="true" />}
```

Replace `ProgressLine` with:

```tsx
function ProgressLine({ text }: { text: string }) {
  // Narration: the main thing to read between collapsed activity lines, so it renders as
  // answer prose (body size, normal color, markdown), not muted status text.
  return <MarkdownContent content={text} />;
}
```

In `MessageRow.tsx`, add `showAvatar?: boolean` to `Props`, destructure it (default `true`), and pass `showAvatar={showAvatar}` to each `<AgentRow …>` it renders (progress, breadcrumb and normal agent text branches).

- [ ] **Step 4: Run tests** — `npx vitest run src/components/messages src/components/MessageRow*` — PASS (update any existing assertion that checked the old italic progress styling to the new prose expectation).

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages apps/vscode-extension/webview-ui/src/components/MessageRow.tsx
git commit -m "feat(webview): render progress notes as narration prose"
```

---

### Task 8: `ThreadView` — render items, transient line, shared avatars

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/activity/TransientActivity.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx` (message loop ~L379–388; streaming bubble ~L425–433)
- Test: `apps/vscode-extension/webview-ui/src/components/activity/TransientActivity.test.tsx`, `apps/vscode-extension/webview-ui/src/test/thread-activity.test.tsx`

**Interfaces:**
- Consumes: `groupTranscript`, `TranscriptItem` (Task 3); `describeTool` (Task 4); `ActivityGroup` (Task 5); `TurnChanges` (Task 6); `MessageRow` with `showAvatar` (Task 7); `StreamingBubble` (`types.ts`: `text`, `thinkingEntries`, `activeThinkingChunk`, `toolEvents`).
- Produces: `export function TransientActivity({ bubble, startedAt }: { bubble: StreamingBubble; startedAt: number }): JSX.Element` with `data-testid="transient-activity"`.

- [ ] **Step 1: Write the failing tests**

`TransientActivity.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TransientActivity } from "./TransientActivity";

const bubble = (over: Partial<Parameters<typeof TransientActivity>[0]["bubble"]> = {}) => ({
  text: "", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [], ...over,
});

describe("TransientActivity", () => {
  it("shows the newest unfinished tool", () => {
    render(<TransientActivity startedAt={Date.now()} bubble={bubble({ toolEvents: [
      { id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true },
      { id: 2, tool: "run_command", args: { command: "pytest" }, source: "explore", done: false },
    ] })} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Running `pytest`");
  });

  it("falls back to the thinking text, then to Working…", () => {
    const { rerender } = render(<TransientActivity startedAt={Date.now()} bubble={bubble({ activeThinkingChunk: "Checking the cache path" })} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Checking the cache path");
    rerender(<TransientActivity startedAt={Date.now()} bubble={bubble()} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Working…");
  });

  it("is one ellipsized line and expands to the in-flight pills", () => {
    render(<TransientActivity startedAt={Date.now()} bubble={bubble({ toolEvents: [
      { id: 1, tool: "read_file", args: { path: "x".repeat(400) }, source: "explore", done: false },
    ] })} />);
    expect(screen.getByTestId("transient-text").className).toContain("truncate");
    fireEvent.click(screen.getByRole("button", { name: /Show current step/ }));
    expect(screen.getByTestId("transient-detail")).toBeInTheDocument();
  });
});
```

`thread-activity.test.tsx` (pure item rendering through ThreadView's item renderer — export it as `TranscriptItems` from `ThreadView.tsx` so it can be tested without the full app state):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptItems } from "../components/ThreadView";
import type { ChatMsg } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const t = "2026-10-13T00:00:00Z";
const msgs: ChatMsg[] = [
  { role: "user", content: "fix it", type: "text", timestamp: t, metadata: {} },
  { role: "agent", content: "", type: "text", timestamp: t, metadata: { tool_events: [{ id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true }] } },
  { role: "agent", content: "Found the bug.", type: "text", timestamp: t, metadata: { progress: true } },
  { role: "agent", content: "", type: "diff_card", timestamp: t, metadata: { resolved: "applied", diff_entries: [{ path: "a.py", additions: 2, deletions: 1 }] } },
  { role: "agent", content: "Fixed.", type: "text", timestamp: t, metadata: {} },
];

describe("TranscriptItems", () => {
  it("renders activity lines, narration and the changes card in order", () => {
    render(<TranscriptItems messages={msgs} turnActive={false} queuedIds={[]} planVersions={new Map()} onRewindRequest={() => {}} />);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(2);
    expect(screen.getByText("Found the bug.")).toBeInTheDocument();
    expect(screen.getByTestId("turn-changes")).toBeInTheDocument();
    expect(screen.getAllByTestId("avatar-spacer").length).toBeGreaterThan(0);
  });

  it("renders the same items for a reloaded copy of the thread", () => {
    const { container: a } = render(<TranscriptItems messages={msgs} turnActive={false} queuedIds={[]} planVersions={new Map()} onRewindRequest={() => {}} />);
    const { container: b } = render(<TranscriptItems messages={JSON.parse(JSON.stringify(msgs))} turnActive={false} queuedIds={[]} planVersions={new Map()} onRewindRequest={() => {}} />);
    expect(b.innerHTML).toBe(a.innerHTML);
  });
});
```

- [ ] **Step 2: Run to verify they fail** — FAIL (modules/exports missing).

- [ ] **Step 3: Implement**

`TransientActivity.tsx`:

```tsx
import { useEffect, useState } from "react";
import type { StreamingBubble } from "../../types";
import { describeTool } from "../../transcript/summarizeActivity";
import { ToolTrack } from "../shared/ToolTrack";

function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function TransientActivity({ bubble, startedAt }: { bubble: StreamingBubble; startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  const [open, setOpen] = useState(false);
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  const running = [...bubble.toolEvents].reverse().find((e) => !e.done);
  const thinking = bubble.activeThinkingChunk.trim().split("\n").pop()
    || bubble.thinkingEntries[bubble.thinkingEntries.length - 1] || "";
  const text = running ? describeTool(running) : thinking || "Working…";
  return (
    <div data-testid="transient-activity" className="grid min-w-0 gap-1.5">
      <div className="flex min-w-0 items-center gap-2 text-[12px] text-text-2">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full" aria-hidden="true"
          style={{ background: "var(--color-accent)", animation: "pulse 1.5s ease-in-out infinite" }} />
        <span data-testid="transient-text" className="min-w-0 truncate">{text}</span>
        <span className="shrink-0 text-text-3">{elapsed(now - startedAt)}</span>
        {bubble.toolEvents.length > 0 && (
          <button type="button" aria-label="Show current step" onClick={() => setOpen(!open)}
            className="shrink-0 text-text-3 hover:text-text">{open ? "▾" : "›"}</button>
        )}
      </div>
      {open && (
        <div data-testid="transient-detail" className="grid min-w-0 grid-cols-[minmax(0,1fr)] overflow-y-auto rounded-[8px] p-2"
          style={{ maxHeight: "320px", border: "1px solid var(--color-border)", background: "var(--color-surface)" }}>
          <ToolTrack events={bubble.toolEvents} />
        </div>
      )}
    </div>
  );
}
```

In `ThreadView.tsx`, add (above the component) and export:

```tsx
export function TranscriptItems({ messages, turnActive, queuedIds, planVersions, onRewindRequest }: {
  messages: ChatMsg[]; turnActive: boolean; queuedIds: string[];
  planVersions: Map<number, number | undefined>; onRewindRequest: (id: string) => void;
}) {
  const items = useMemo(() => groupTranscript(messages), [messages]);
  return (
    <>
      {items.map((item, i) => {
        // An agent item right after another agent item continues the same row (one avatar).
        const prev = items[i - 1];
        const prevIsAgent = !!prev && (prev.kind !== "message" || prev.msg.role === "agent");
        const isAgent = item.kind !== "message" || item.msg.role === "agent";
        const showAvatar = isAgent && !prevIsAgent;
        if (item.kind === "activity" || item.kind === "changes") {
          return (
            <div key={item.key} className="flex min-w-0 items-start gap-2.5">
              {showAvatar ? <Avatar /> : <div data-testid="avatar-spacer" className="w-5 flex-shrink-0" aria-hidden="true" />}
              <div className="min-w-0 flex-1">
                {item.kind === "activity" ? <ActivityGroup steps={item.steps} /> : <TurnChanges files={item.files} />}
              </div>
            </div>
          );
        }
        return (
          <MessageRow key={item.msg.id ?? `m-${item.index}`} msg={item.msg} planVersion={planVersions.get(item.index)}
            turnActive={turnActive} queued={item.msg.id != null && queuedIds.includes(item.msg.id)}
            onRewindRequest={onRewindRequest} showAvatar={showAvatar} />
        );
      })}
    </>
  );
}
```

Imports to add in `ThreadView.tsx`: `import { groupTranscript } from "../transcript/groupTranscript";`, `import { ActivityGroup } from "./activity/ActivityGroup";`, `import { TurnChanges } from "./activity/TurnChanges";`, `import { TransientActivity } from "./activity/TransientActivity";`, `import { Avatar } from "./shared/Avatar";`.

Replace the `state.messages.map(...)` block with:

```tsx
            <TranscriptItems messages={state.messages} turnActive={state.turnActive} queuedIds={state.queuedIds}
              planVersions={planVersionMap} onRewindRequest={setRewindMessageId} />
```

Replace the streaming bubble block (`!state.retryStatus && state.streaming && <AgentRow … />`) with: the streamed answer text (if any) still renders through `AgentRow` (content only, `streaming`), and the work renders as the transient line:

```tsx
            {!state.retryStatus && state.streaming && (
              <div className="flex min-w-0 items-start gap-2.5">
                <div data-testid="avatar-spacer" className="w-5 flex-shrink-0" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <TransientActivity bubble={state.streaming} startedAt={streamStartedAt} />
                </div>
              </div>
            )}
            {!state.retryStatus && state.streaming?.text && (
              <AgentRow content={state.streaming.text} streaming showAvatar={false} />
            )}
```

with `const streamStartedAt = useMemo(() => Date.now(), [state.streaming === null]);` near the other hooks (it resets when a new bubble starts).

- [ ] **Step 4: Run tests**

Run: `npx vitest run src/components/activity src/test/thread-activity.test.tsx` — PASS. Then the whole webview suite `npx vitest run` and `npx tsc --noEmit -p .` — all PASS (update existing ThreadView tests whose assertions depended on the old streaming `ThinkingBlock`/`ToolTrack` markup to the transient line).

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src
git commit -m "feat(webview): render the transcript as narration, activity lines and a live transient line"
```

---

### Task 9: End-to-end verification (controller performs; no subagent)

- [ ] Root: `npm run build && npm run typecheck && npm run test` — all PASS.
- [ ] Backend: full `pytest` — PASS (known flaky: `test_backend_auth_script_helper` timeout; rerun alone).
- [ ] Install the build (`scripts/dev/install-local.sh --no-backend`), reload VS Code, open an existing long thread: activity lines, narration, changes card; expand a group; screenshot before/after.
- [ ] Run one real turn on gpt-6.1-sol ("add a short docstring to agentd/images.py's ImageAssetStore"): transient line updates live; after the turn the same items render as on reload; count `progress` notes per step.
