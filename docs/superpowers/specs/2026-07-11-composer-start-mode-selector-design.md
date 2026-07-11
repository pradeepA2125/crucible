# Composer start-mode selector — `Agent` / `Edit` — design

**Date:** 2026-07-11
**Status:** NOT IMPLEMENTED — on hold. Design only; no code was written.
**Parent:** chat controller phase model (`DECIDE` → `EDIT`/`EXPLAIN`)

> **On hold (2026-07-11):** shelved as more complex than the intended feature. The wanted
> behavior was a simpler toggle that only changes which actions/tools are allowed within the
> existing `DECIDE` phase (add `run_command`; swap `propose_mode` for `edit`/`submit_changes`)
> with **no** state-machine change. Before resuming, verify whether `edit`/`submit_changes` can
> run without the `EDIT` phase's `TurnEditSession` shadow setup — that determines whether the
> simpler "just change allowed actions" approach truly avoids touching the state machine.

## Context

The chat controller runs a per-turn phase state machine (`ControllerPhaseSM`,
`agentd/chat/controller_phase.py`). Every turn starts in `DECIDE`; the allowed action
`type`s are a pure function of the phase (`_PHASE_TYPES`, `controller_prompts.py:84`), so the
response schema is filtered per turn and the model literally cannot emit an action its phase
forbids:

- **`DECIDE`**: `tool_call`, `answer`, `clarify`, `propose_mode`
- **`EDIT`**: `tool_call`, `edit`, `clarify`, `submit_changes`
- **`EXPLAIN`**: `tool_call`, `answer`, `clarify`

Today a fresh turn always enters `DECIDE`. The only way to reach `EDIT` is the `propose_mode`
gate: the agent explores read-only, proposes "Edit vs Just-explain", the user picks, and the
turn re-enters as `EDIT` (or `EXPLAIN`). `run_command` and patch ops (`edit`/`submit_changes`)
are `EDIT`-only — `DECIDE` rejects them.

This forces a round-trip even when the user already knows they want a code change. The mechanism
to *start* in `EDIT` already exists: `ChatController._run_loop(phase="EDIT")` (`controller.py:337`)
calls `sm.enter_edit_mode()` and builds the `TurnEditSession` immediately. What is missing is a
**user-facing way to choose the starting phase**, plus a coherent framing of what the two entry
modes are.

## Mental model — shared base + additive capability

The two modes share a read/reason base and differ only by what capability is switched on:

| Mode | shared base | mode-specific |
|------|-------------|---------------|
| **Agent** (`DECIDE`) | `tool_call` (read/search), `answer`, `clarify` | `propose_mode` — the *gateway*: must ask before touching code |
| **Edit** (`EDIT`) | same `tool_call`, `answer`, `clarify` | `edit` / `submit_changes` (patch ops) + `run_command` — the *hands* |

**Edit is not a separate restricted mode. It is the same agent loop with patch ops + `run_command`
switched on from turn 1, dropping the `propose_mode` gateway** (you are already editing, there is
nothing to propose). It keeps full exploration, `answer`, and todos-if-needed. Agent =
read-only-until-it-asks; Edit = read/write immediately.

The distinction is exactly the write/exec capability: `run_command` + patch ops. Nothing else.

## Design

### 1. UX — persistent composer toggle

A segmented `Agent | Edit` control on the chat composer
(`apps/vscode-extension/webview-ui/src/components/InputArea.tsx`), near the model picker
(`ModelMenu.tsx`).

- **Default:** `Agent` — byte-for-byte today's behavior, zero regression.
- **Persistence:** remembered **per-thread**; the **last-used** mode also seeds newly-created
  threads (so a habitual quick-editor is not re-toggling every new thread). Both live in the
  webview's own persisted state (VS Code webview `getState`/`setState`) — the **backend stays
  stateless** on start-mode; it is sent per message.
- **Semantics per turn:** `ControllerPhaseSM` is already constructed fresh each turn
  (`_run_loop`, `controller.py:343`), so the toggle simply selects the starting phase of the
  *next* send. Flipping mid-thread affects only subsequent turns — no cross-turn state to manage.

### 2. Phase types — add `answer` to `EDIT`

`_PHASE_TYPES["EDIT"]` becomes:

```python
"EDIT": ["tool_call", "answer", "edit", "clarify", "submit_changes"],
```

`answer` completes the shared base so an Edit turn has Agent's full expressive range plus the
write tools — it can respond conversationally (after editing, or when the ask turns out not to
need a patch) instead of being forced through `submit_changes`. `run_command` stays gated as a
`tool_call` subtype (`EDIT`-only) exactly as today — no separate wiring. `propose_mode` remains
**`DECIDE`-only**, so a turn started in Edit can never re-open mode selection (mirrors the
existing `EXPLAIN` guard).

### 3. Backend plumbing (`agentd-py`)

Mirror the existing per-turn `step_review` flag. **Verified against the code:** a direct-Edit start
is a *fresh EDIT entry* and must behave **identically to `resolve_mode("edit")`** (`controller.py:966`),
which already enters `EDIT` via `_run_loop(phase="EDIT")` with `edit_is_resume=False`. That path
already gets the correct `edit_entry` steering hint (`controller_loop.py:361` — `phase==EDIT and no
todos and no edit applied and not edit_is_resume`), and **there is no hard todo gate in `EDIT`** — the
entry hint suggests `write_todos` but the model has discretion. So direct-Edit needs **no special
signal, no hint suppression, no gate removal** — it just enters `EDIT` fresh.

- **Route** `POST /chat/threads/{thread_id}/message` (`agentd/api/routes.py:1350`): read
  `start_mode = request.get("start_mode")` (validated to `"agent" | "edit"`, else `None`) next to
  the existing `step_review` read, and pass `start_mode=start_mode` to the **ChatController-branch**
  `handle_message` call (`routes.py:1386`) only.
- **`ChatController.handle_message`** (`controller.py:272`): accept `start_mode: str | None`. Compute
  `start_phase = "EDIT" if start_mode == "edit" else None` and pass `phase=start_phase` into
  `_run_loop(...)`. **Trap to avoid:** the existing call passes `edit_is_resume=(resume_phase ==
  "EDIT")` where `resume_phase` is always `None` on this path. Do **not** route `start_phase` through
  `resume_phase` — that would flip `edit_is_resume` to `True` and *kill* the entry hint. Keep
  `edit_is_resume=False` for a fresh direct-Edit send.
- **Orchestrator guard**: if `start_mode == "edit"` but `self._orchestrator is None`, fall back to
  `start_phase=None` (Agent/DECIDE). `resolve_mode("edit")` hard-raises in this case; the direct
  entry degrades gracefully instead (in practice the managed runtime always has an orchestrator).
- **`ChatAgent.handle_message`** (legacy path, `agent.py:147`): **unchanged.** It is a *separate*
  call site (`routes.py:1423`) with a narrower signature (`step_review` only) and no phase model;
  `start_mode` is simply not passed there.

### 4. Frontend wiring (webview → extension host → editor-client → backend)

**Verified path** (the extension imports `HttpBackendClient` from `@crucible/editor-client`, so
editor-client *is* in the send path):

1. `InputArea.tsx` (webview React) — owns `startMode` state + persistence; posts
   `vscode.postMessage({ type: "sendMessage", text, stepReview, startMode? })`.
2. `webview-ui/src/types.ts:146` — add `startMode?: "agent" | "edit"` to the `sendMessage` union
   member.
3. `apps/vscode-extension/src/chat-panel.ts:193` — read `m["startMode"]`, forward to `onMessage`.
4. `apps/vscode-extension/src/extension.ts:101` — thread `startMode` into `controller.sendChatMessage`.
5. `apps/vscode-extension/src/controller.ts:626` — `sendChatMessage(..., startMode?)` folds it into
   the `client.sendChatMessage` options (conditionally, like `stepReview`).
6. `apps/editor-client/src/contracts/task-contracts.ts:428` — options gain `startMode?: "agent" |
   "edit"`.
7. `apps/editor-client/src/client/http-backend-client.ts:709` — POST body adds `start_mode` **only
   when set** (`...(options?.startMode ? { start_mode: options.startMode } : {})`).

**Do-not-break-React rule — omit-when-default:** `InputArea` includes `startMode` in the posted
message **only when it is `"edit"`** (non-default), matching the codebase's existing conditional-field
idiom. This keeps the Agent-mode message shape byte-identical to today, so the three **exact-match**
webview assertions stay green untouched: `views.test.tsx:324`, `views.test.tsx:361`,
`InputArea.test.tsx:65` (all `{ type:"sendMessage", text, stepReview:true }` with no other keys).

**Persistence:** per-thread + global last-used via the webview's `getState`/`setState`; new threads
seed from last-used. Default `"agent"`.

### 5. ReAct-loop correctness (verified, no changes needed)

- Adding `"answer"` to `_PHASE_TYPES["EDIT"]` is sufficient for the loop: the per-turn reject gate
  (`controller_loop.py:384`, `atype not in allowed_types()`) then admits `answer`; the DECIDE-only
  state-change guard (`_decide_state_change_correction`) is inert for non-DECIDE/non-tool_call; the
  `answer` dispatch (`controller_loop.py:410`) is phase-agnostic and returns a clean terminal
  `ControllerOutcome(kind="answer")`, which `_run_loop`/`_finish` already handle.
- Direct-EDIT entry at iteration 0 is identical to the working `resolve_mode("edit")` re-entry —
  same `_run_loop(phase="EDIT")`, same `edit_entry` steering hint.

## Out of scope

- Changing what `DECIDE`/`Agent` does — unchanged, including its `propose_mode` proposal and the
  `EXPLAIN` branch (reachable only through Agent, as today).
- A third toggle option for `EXPLAIN` — Explain stays an internal outcome of Agent.
- Backend persistence of the chosen mode — memory lives in the webview only.
- Any change to `run_command` gating, clarify-resume, or the mode-decision endpoints.

## Testing

**Backend (`agentd-py`, pytest):**
- Update `test_controller_schema.py:71-74` — the `EDIT` `type` enum now carries `answer`
  (`propose_mode` still absent from `EDIT`, `edit`/`submit_changes` still absent from `DECIDE`).
- New controller test: `handle_message(start_mode="edit")` enters `EDIT` on turn 1 with
  `edit_is_resume=False` — the agent can `edit` / `answer` / `submit_changes`, cannot `propose_mode`,
  and the `edit_entry` steering hint fires; the `TurnEditSession` is built.
- `handle_message(start_mode="agent")` and `start_mode=None` are byte-identical to today (starts
  `DECIDE`). Orchestrator-None + `start_mode="edit"` falls back to `DECIDE`.
- A clarify-resumed `EDIT` (existing `test_controller_clarify_gate.py` path) is unaffected.

**Frontend (vitest/RTL):**
- Existing exact-match assertions (`views.test.tsx:324,361`, `InputArea.test.tsx:65`) stay green
  because Agent-mode omits `startMode` (see §4 do-not-break rule) — confirm they still pass unchanged.
- New: flipping to Edit posts `startMode: "edit"`; toggle persists per-thread and seeds a new thread
  from last-used; `start_mode` reaches the POST body only when `"edit"`.

## Effort

Small. Backend is one enum entry + a per-turn flag mirroring `step_review`, plus a direct-edit
entry branch in `_run_loop`. Frontend is one segmented control + persistence + threading one
optional field through the existing send path. No new endpoints, no schema/state migrations.
