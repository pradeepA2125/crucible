# Controller Todo Ledger — Design

**Date:** 2026-06-23
**Status:** Approved (design); implementation plan to follow
**Scope:** The reactive chat controller (`CRUCIBLE_CHAT_CONTROLLER=1`). Within-turn / within-request only.
**Related:** `2026-06-15-agentic-chat-controller-design.md`, `2026-06-17-controller-ux-interaction-rules-design.md`

**Provenance:** Synthesized against an alternative proposal, `docs/todo_memory_plan.md` ("Chat-Owned Mutable Agenda With Prompt-Level Decision Policy"). That proposal is richer on working-memory structure (recursive agenda, op-delta mutations, blocked/cancelled states, audit event log, manual-approval gate, full nested UI) but has **no deterministic completion gate** — enforcement is prompt-level, which the grounded analysis below proves is exactly what fails on weak models. This design keeps the Agenda plan's best ideas (extra states, evidence-on-done, live visibility, distilled decision policy) and welds them to a hard gate + full-list-rewrite mutation model. Decision-by-decision rationale is recorded inline.

---

## 1. Problem

On a multi-feature request, the reactive controller does **one feature, emits `submit_changes`, and stops** — it does not loop until the whole request is complete. The user must hand-pump "next feature" repeatedly. This is the gap versus Claude Code / Cline / Cursor, whose agents maintain a persistent todo list and grind it to completion.

### Grounded root cause (validated against thread `chat-58a5e144cf5a`, workspace `3d-game-test`)

The transcript + controller artifacts (`.crucible/state/artifacts/chat/<thread>/<turn>/controller-turn-*.json`) prove a precise behavioral law:

> **The model executes exactly the scope `plan_sketch` defines, then emits `submit_changes`. Every EDIT turn, without exception.**

Evidence (every EDIT-phase turn in the thread):

| Turn | `plan_sketch` scope | actions before submit |
|------|--------------------|------------------------|
| `0cd2a248` | bodyMesh bug (1 thing) | `edit` → `submit_changes` |
| `bba353b6` (Jump) | Jump only (4 sub-steps) | `edit(4 ops)` → verify×3 → `edit(3 ops)` → `submit_changes` |
| Enemies/Timer/Juice/Sound | **one feature each** | `edit` → `submit_changes` |

- Per-feature `propose_mode` payloads (#6–#10) each enumerated **exactly one feature** in `plan_sketch`.
- No EDIT turn ran near the 32-iter cap (longest = 6). The msg-42 "budget exhausted" line was **confabulation**, not real exhaustion.
- The Jump turn shows the model *can* grind (multi-op + verify + fix) — it stops because the **contract (`plan_sketch`) ended**, not because effort ran out.

The deeper cause: the user gave a contradiction ("do one at a time" + "don't stop looping until all done"). The model cannot reconcile it because **nothing persists the list of remaining items across the one-at-a-time steps**. Each turn regenerates a fresh, narrow `plan_sketch` with no memory that N items remain. `plan_sketch` is functioning as an informal, frozen, throwaway todo list.

### Secondary findings (informing scope, not all fixed here)

- **Failure B (propose_mode per feature):** each feature was a separate user-pumped turn → separate `propose_mode` → mode-click. Collapses to a single click once one EDIT loop grinds all items.
- **Failure C (confabulated completion):** when asked "what's done?", the model answered from memory without reading the file (msg 96 → user correction). Relevant to any completion check.
- **Task-path ceiling:** the `create_task` path died on "Runtime budget exceeded" after ~1 step, three times. Out of scope here (separate budget model).

---

## 2. Goals / Non-goals

**Goals**
- A multi-feature request completes in one EDIT loop instead of bailing after one feature.
- "Do one at a time" and "loop until all done" coexist: the model edits one item per step, a durable contract keeps the loop alive until all items are resolved.
- Discretionary: the model creates a todo list only when it judges the work large/multi-part. Simple edits are untouched.
- **Visibility:** the user can see the live checklist (progress / active item / blocked items) — the half competitors (Cursor/Cline) expose and the original Ledger draft omitted. Surfaced via `/live` + a flat live card.

**Non-goals (explicitly deferred)**
- **Completion backstop** (the request-anchored grounded check). Deferred for cost — see §7.
- **Cross-turn / cross-task durable memory + context compaction.** A separate "agent memory module" design. This spec is within-request only.
- Changing the `create_task` (structured-task) path or its runtime budget.
- **Deferred from the Agenda-plan synthesis (see §9):** op-delta mutation model, recursive nested items, `write_todos`-as-first-class-action, manual mutation-approval gate + `/agenda-decision` route, append-only mutation event log, nested/approval UI.

---

## 3. Behavioral model (chain of authority)

```
original request   →  plan_sketch (seed)  →  TodoLedger (enforced contract)
   (ground truth)        (model-authored,         (deterministic gate)
                          enumerates all parts
                          when large)
```

- `plan_sketch` **seeds** scope (model-authored; must enumerate all parts when large).
- `TodoLedger` **enforces**: `submit_changes` is blocked while items are pending.
- The model still does **one item per edit** ("one at a time" preserved); the ledger keeps the loop running until every item is `done` or explicitly `dropped`.

Two soft anti-confabulation guards (adopted from the Agenda plan, prompt-level — *not* the deferred hard backstop): marking an item `done` must cite **evidence** (a tool/edit result), stored in the item's `note`; and a `blocked` state lets the model park an un-completable item with an unblock reason rather than fake-completing it. `blocked` items are **excluded from the gate's pending set** so the loop can never deadlock on something genuinely stuck (a flaw the original 4-state model had — its only escape was "dropped").

Known v1 gap: with the backstop deferred, completeness rests entirely on (a) the model *choosing* to create a ledger for large work, and (b) the gate enforcing it once created. If the model never calls `write_todos`, the turn falls back to today's `plan_sketch` behavior. We lean on discretion + prompt steering; the backstop (§7) is the later closure.

---

## 4. Components

### 4.1 `TodoLedger` — new `chat/todo_ledger.py`
Plain per-turn state object, no I/O. **Five states** (the extra two adopted from the Agenda plan):
```
status ∈ {pending, in_progress, done, blocked, cancelled}
TodoItem:   {title: str, status: str = "pending", note: str = ""}   # note holds evidence/reason/unblock
TodoLedger:
  items: list[TodoItem]
  replace(items)            # full-list rewrite (TodoWrite semantics — no id bookkeeping)
  pending() -> list[TodoItem]   # status in {pending, in_progress} ONLY — blocked/cancelled/done do NOT block
  render() -> str           # e.g. "3 items (1 done) — [✓ Enemies] [▶ Jump] [ ] Timer" — '' when empty
  to_json() -> str / from_json(s) -> TodoLedger   # for §4.6 persistence
```
- **Full-list-rewrite** (vs the Agenda plan's 10 op-deltas): the model resends the whole list with updated statuses each call (like Claude Code `TodoWrite`). No `item_id`/`parent_id`/`after_id` for a weak model to get wrong; split/insert/reorder/cancel/mark-done all collapse to "resend the list in the new shape." The Agenda plan's own "2–6 items" sizing makes the op model's only edge (efficient large-list mutation) moot. Forward-compatible with future nesting (resend a tree) — so deferring recursion costs no rework.
- **`blocked`** carries an unblock reason in `note`; excluded from `pending()` so it can't deadlock the gate. **`cancelled`** (vs my earlier "dropped") is kept in the list (rendered struck), preserving an audit trail — the model never deletes a visible item.

### 4.2 `write_todos` tool + `TodoToolSource` — new `chat/todo_source.py`
A `ToolSource` (the `tools/sources.py` seam — "adding a source never touches the loop"). Owns one tool, `write_todos`; `execute()` mutates the shared `TodoLedger` via `replace(...)` and returns the rendered list as `ToolOutput`. `ToolDefinition.parameters`:
```
{ "items": [ { "title": string, "status": "pending"|"in_progress"|"done"|"dropped" } ] }
```
- **No response-schema change** — invoked via the existing `tool_call` action, so the flat + tight `oneOf` schema is untouched.
- Available in both DECIDE and EDIT phases.
- Dropping an item = re-sending the list with that item's status `"dropped"` (the escape hatch for an infeasible/abandoned item; surfaces as a breadcrumb).

### 4.3 `ControllerLoop` (`controller_loop.py`) — gate + re-surfacing
- Holds `self._ledger: TodoLedger` (ctor param; the same instance the `TodoToolSource` mutates).
- **Gate** — in the `submit_changes` branch: if `self._ledger.pending()` is non-empty, append a correction to history (*"You have N pending todo items: […]. submit_changes is blocked until they are done or dropped (with a reason). Continue with the next item, or call write_todos to drop one."*) and `continue`. This is **not** counted toward `_MAX_MALFORMED` (legitimate redirect, not a parse failure). Only the iteration budget bounds the loop.
- **Re-surface** — at the top of each iteration: `plan_context["todo_status"] = self._ledger.render()`.

### 4.4 `controller_prompts.py` — steering only
- `build_controller_step_payload`: append `todo_status` to the **tail** (KV-cache-safe, beside `instruction`/`budget_status`). Make the EDIT mid-turn hint ledger-aware (*"next pending item: **Timer**; submit_changes is blocked until the ledger clears"*).
- `CONTROLLER_SYSTEM_PROMPT`: add a `write_todos` teaching block, and the rule *"when proposing a large/multi-part change, `plan_sketch` MUST enumerate every distinct part"* (the lever the grounded analysis proved).

### 4.5 `controller.py` — wiring
In `_run_loop`: build/rehydrate a `TodoLedger` for the request, add `TodoToolSource(ledger)` to `_build_registry`, pass `ledger` into `ControllerLoop`. Persist on terminal/clear per §4.6.

### 4.6 Request-scoped persistence (fork **A**)
The "turn" splits across loop runs: DECIDE `_run_loop` → `propose_mode` gate → separate EDIT `_run_loop` (and another on clarify-resume). The ledger must survive these intra-request boundaries.
- New `chat_threads` column `controller_todo_json` (same light pattern as `controller_gate_json` / `controller_history_json`).
- `storage.py`: `set_controller_todos(thread_id, json)` / `get_controller_todos(thread_id)`; `ChatThread.controller_todos` field populated in `get_thread`/`list_threads` (feeds §4.8 `/live`).
- Rehydrated (`from_json`) into each resumed loop's `TodoLedger`; written after each loop run; **cleared on terminal** (`submit_changes` passes / `answer`).
- This is **not** the cross-task memory module — it is intra-request persistence only, reusing the existing column pattern.

### 4.7 Budget knob
`max_iters` currently defaults to `32` in `ControllerLoop.run`. Add `CRUCIBLE_CONTROLLER_MAX_ITERS` (read in `controller.py`, passed to `loop.run`), default raised to **500** as a "for now" bridge so the *cap* never stops a legitimate loop. **Note:** within a turn the real binding limit is the context window (a weak local model on a large file hits it well before 500 iters); removing that ceiling is the deferred memory module's job, not this counter.

### 4.8 Live exposure (`/live`) — adopted from the Agenda plan
The original Ledger draft made the list invisible to the user. Surface it the same way gates/plan already are (Class-A render-from-`/live`, regardless of an active task):
- `ChatThread.controller_todos: list[dict] | None` — populated by `storage.get_thread`/`list_threads` from `controller_todo_json` (the column added in §4.6).
- `ThreadLiveState.todos: list[dict] | None`.
- `resolve_thread_live` includes `todos` in **both** return paths (controller-gate branch AND task-derived branch), so the checklist shows whether or not a gate/task is active.
- `editor-client` `ThreadLiveStateSchema` gains `todos` (nullable/optional array).

### 4.9 Live card (flat checklist) — lite version of the Agenda plan's UI
A **flat** checklist card in the pinned live slot (NOT the Agenda plan's nested tree with per-mutation approval buttons — those are deferred, §9):
- `webview-ui` `TodoCard.tsx` — renders each item with a status glyph (✓ done / ▶ in_progress / ☐ pending / ⛔ blocked+reason / ~~struck~~ cancelled) and a "N of M done" header; read-only (no buttons in v1).
- `LiveSlot.tsx` renders it from a new `liveTodos` view; `types.ts` gains `LiveTodosView` + the `renderLiveTodos` webview message; `useAppState.ts` gains the reducer case.
- `controller.ts` `pollThreadLiveState` maps `live.todos` → `renderLiveTodos`. **INVARIANT (CLAUDE.md `/live` dedup):** `todos` MUST be included in `lastLiveSignature`, or a checklist change is deduped away and the card never updates (the same bug class that bit `runSummary`/`turnActive`).

---

## 5. End-to-end (the cited thread, post-change)

1. User: "make the game more complex — add Enemies, Jump, Timer, Juice, Sound."
2. DECIDE: explore → `propose_mode` with `plan_sketch` enumerating **all 5** parts.
3. User picks "edit" → EDIT loop.
4. Model calls `write_todos([Enemies, Jump, Timer, Juice, Sound])` (judges it large) → ledger persisted.
5. `edit` Enemies → mark Enemies `done` → `submit_changes` → **blocked (4 pending)** → continue.
6. … Jump, Timer, Juice, Sound, each one-at-a-time, each re-surfaced with status.
7. All `done` → `submit_changes` **passes**. No "next feature" hand-pumping.

---

## 6. Testing (mirrors existing `tests/test_controller_*`)

- `test_todo_ledger.py` — `replace` / `pending` / `render` / drop / json roundtrip.
- `test_controller_todo_gate.py` — submit blocked while pending; passes when all done/dropped; block **not** counted as malformed; **cited-thread regression**: scripted engine emits `write_todos([5 items])` → 1 edit → submit (blocked) → … → 5 edits → submit (passes).
- `test_controller_todo_tool.py` — `write_todos` via `tool_call` mutates the ledger and re-surfaces in the next payload.
- Extend `test_controller_payload.py` — `todo_status` lands in the tail.
- `test_controller_durable_*` style — `controller_todo_json` rehydrates across the DECIDE→EDIT boundary.

---

## 7. Deferred — completion backstop (future)

The originally-designed request-anchored completion check (catch the case where the model **never** creates a ledger and bails, and catch confabulated "done" marks) is deferred for cost. When revisited, the cost-correct shape is:
- **Ledger-first / deterministic** — no LLM call when a ledger exists.
- **Fires only on a no-ledger bail** — `submit_changes` with no ledger on a multi-part request.
- **Reasons over the turn's accumulated DIFFS, not full file bodies** — compact, multi-file-safe, and grounded in actual changes (fixes Failure C without re-reading files). Diffs cover only the current turn — cross-turn grounding belongs to the memory module.

---

## 8. Deferred — agent memory module (next design)

Context compaction + cross-turn/cross-task durable memory. This is what removes the within-turn context ceiling (§4.7) and makes truly long horizons work. Separate spec.

## 9. Deferred from the Agenda-plan synthesis (`docs/todo_memory_plan.md`)

Considered and consciously NOT taken for v1, each with rationale:

| Agenda-plan feature | Why deferred |
|---------------------|--------------|
| **Op-delta mutations** (`add_child`/`insert_after`/`reorder`/… with ids) | Replaced by full-list-rewrite (§4.1). Id bookkeeping is weak-model-hostile; rewrite reshapes natively. |
| **Recursive nested items** (`children`) | YAGNI for the cited bug (a flat 5-item list). Full-rewrite is tree-forward-compatible, so adding nesting later is additive. |
| **`write_todos` as a first-class action** (new `oneOf` schema variant) | Kept as a `tool_call` for v1 to avoid reopening the provider-sensitive tight `oneOf` schema; the action's per-op validation is unneeded under full-rewrite. **Revisit via smoke** if the model under-uses the tool. |
| **Manual mutation-approval gate** (`PendingGate.kind="agenda"`, `POST /agenda-decision`) | The Agenda plan itself defaults to auto-apply; manual approval of *list edits* is heavy surface for marginal value. Reuses existing gate machinery if added later. |
| **Append-only mutation event log** (`agenda_events`) | Pairs naturally with the memory module (§8); within-request, the current list suffices. |
| **Nested UI + approval buttons** (`AgendaCard` tree) | v1 ships a flat read-only checklist (§4.9). Nesting/approval follow the deferrals above. |
| **Thread-persistent (cross-turn) agenda** | = the memory module (§8). v1 is request-scoped (clear-on-terminal). |

Adopted from the Agenda plan instead: `blocked`/`cancelled` states (§4.1), evidence-on-`done` + cancel-not-delete (§3), `/live` exposure (§4.8), a flat live card (§4.9), and a distilled decision policy in the prompt (when-to-create / anti-drift / evidence).
