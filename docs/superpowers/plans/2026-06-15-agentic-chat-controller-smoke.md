# Agentic Chat Controller — Live Dev-Host Smoke (Phase J)

> Drive the real VS Code dev-host (worktree extension) via Playwright MCP (CDP frame-eval) against a live backend with `AI_EDITOR_CHAT_CONTROLLER=1`. Each **Scenario** asserts observed UI behavior — **never trust a green unit test as a smoke pass.** Mark `- [x]` per assertion; record task/thread ids + screenshots.

## What changed vs the Tier-B/narrative smoke (so old scenarios don't apply)

The controller **replaces** `explore → classify → route`. Therefore these old paths are GONE and must NOT be smoked as before:
- ❌ **`IntentClassifier`** — no `intent_classified` event in the controller path; no silent qa/small_change/large_change routing.
- ❌ **`run_inline_change` / `small_change` inline diff card** — the controller does NOT call `run_inline_change`. Inline edits now go through the controller's **EDIT phase** (ACID instant-promote + `EditGate`), not the old DiffCard/`diff_ready` path.
- ❌ The "include a NEW file to force large_change" workaround — there is no classifier to fool; the agent **recommends** a mode via `propose_mode` and the user picks.

**New surfaces to smoke (the whole point of Phase J):**
- `propose_mode` → **ModeGate** card (plan_sketch + recommended/alternative mode buttons) in the `/live` slot.
- **EDIT phase**: `edit` action → ACID turn-shadow → **instant promote to the REAL workspace** (`shadow==real` invariant). Per-edit review via **EditGate** (when "Review each edit" on) or auto-accept (off).
- Mode dispatch: `edit`/`explain` re-enter the loop (streamed `/mode-decision`); `create_task` hands off to the **full existing task pipeline** (plan gate → step gates → ReviewCard → narrative); `resume` is degraded (v1).
- **Soft-terminal gate**: instead of picking a mode, the user may type a follow-up → loop resumes with appended history (discuss/refine, mirrors clarify/feedback).
- Controller gates render **purely from the `/live` poll** (NO SSE poke) at the **thread** level (`pending_controller_gate`) — they have no task; durable across reload.
- `answer` / `clarify` terminals (text only, no gate).

## Environment

- **Backend:** worktree `services/agentd-py` via `scripts/stress/start-backend.sh` with **`AI_EDITOR_CHAT_CONTROLLER=1` exported** before launch, `--workspace <REAL ws OUTSIDE .tmp>` (graph indexing needs a non-`.tmp` ancestor). Port :8001 (workspace `.vscode/settings.json` pins `aiEditor.backendBaseUrl=http://localhost:8001`).
- **Dev-host:** VS Code on CDP :9335 via `scripts/playwright/start-vscode-mcp.sh` — **EXT_PATH MUST point at THIS worktree** `.../.worktrees/feat-agentic-chat-controller/apps/vscode-extension` (the committed script points at a DELETED worktree — fix before launch). **MUST rebuild `webview-ui/dist` first** (`npm run -w webview-ui build` or in `apps/vscode-extension/webview-ui/`) — dist is a gitignored artifact; stale dist = old UI (the sess.3 stale-dist trap).
- **Driving caveat (auto-memory):** `browser_wait_for`/a11y snapshot do NOT pierce the sandboxed webview iframe — use CDP **frame-eval** (`page.frames()` → the `fake.html`/webview frame), matching `scripts/playwright/drive-chat.js`. Backend runs `--reload`: do NOT edit `agentd/*.py` while a turn is in flight (hot-reload orphans it).

## Pre-flight checklist
- [ ] `webview-ui/dist` rebuilt from this worktree (timestamp newer than last source edit).
- [ ] `start-vscode-mcp.sh` EXT_PATH repointed to this worktree's `apps/vscode-extension`.
- [ ] Backend up on :8001 with `AI_EDITOR_CHAT_CONTROLLER=1` (confirm: `curl -s :8001/health`; confirm controller active in logs / by absence of `intent_classified`).
- [ ] `shadow-forge-stress` indexed (snapshot non-zero nodes).

---

## Scenario J1 — QA (answer terminal), no gate
**Message:** "What does the ShadowWorkspaceManager do in this codebase?" (a question, no change)

- [ ] Agent streams thinking + **tool pills** (`tool_call` explore on the REAL ws via the controller's BuiltinToolSource).
- [ ] Terminates with a **text answer** (`chat_response` chunk) describing the class; **no ModeGate**, no task card.
- [ ] `chat_done`; composer re-enabled. (Confirms the controller's `answer` terminal + tool loop.)

## Scenario J2 — clarify terminal → reply resumes the loop
**Message:** "fix the bug" (deliberately ambiguous)

- [ ] Agent emits **`clarify`** → a question renders as an agent text message ("which bug / where?").
- [ ] Reply with a concrete answer in the SAME thread → the loop **resumes with seed_history** (prior turn + reply) and proceeds (answer or propose_mode), demonstrating clarify≈feedback resume.

## Scenario J3 — propose_mode gate renders + "explain" pick
**Message:** "Add a `discount(price, pct)` helper to the pricing utilities."

- [ ] Agent explores then emits **`propose_mode`** → **ModeGate** renders in the `/live` slot with: the **plan_sketch** text, a **recommended** option (highlighted/primary) + alternatives (Edit inline / Plan as task / Just explain), and the "keep typing to discuss/refine" hint.
- [ ] Click **Just explain** → POST `/mode-decision {mode:"explain"}` (streamed) → breadcrumb **`▸ Proceeding: explain`** → agent returns a **text answer** describing what it would change; **NO files written** on disk.
- [ ] ModeGate clears from the `/live` slot (gate resolved in place).

## Scenario J4 — edit mode, "Review each edit" ON → EditGate → accept → instant promote
**Setup:** composer "Review each step/edit" **CHECKED**. **Message:** "Add a `src/discount.py` with `apply_percentage(price, pct)`."

- [ ] propose_mode → ModeGate → click **Edit inline now** → `/mode-decision {mode:"edit"}` → breadcrumb `▸ Proceeding: edit`; loop re-enters in **EDIT phase**.
- [ ] Agent emits **`edit`** → **EditGate** renders the per-edit **diff** (file row + tabbed diff panes) in the `/live` slot.
- [ ] Click **Accept** → POST `/edit-decision {decision:"accept"}` → the patch is **promoted to the REAL workspace immediately**: `src/discount.py` **exists on disk** with the function (verify via filesystem, not just UI).
- [ ] Agent emits **`submit_changes`** → `chat_done`. (Confirms ACID instant-promote + `shadow==real`.)

## Scenario J5 — edit mode, "Review each edit" OFF → auto-accept (no EditGate)
**Setup:** "Review each edit" **UNCHECKED**. **Message:** "Add a `src/tax.py` with `with_tax(price, rate)`."

- [ ] propose_mode → pick **Edit inline now** → EDIT phase → `edit` action **auto-promotes with NO EditGate** (instant).
- [ ] `src/tax.py` **exists on disk**; `submit_changes` → `chat_done`. (Confirms the auto-accept Strategy path.)

## Scenario J6 — EditGate reject → shadow restored from real → agent revises
**Setup:** "Review each edit" CHECKED. **Message:** a change to an existing file (e.g. "add a docstring to `apply_percentage` in src/discount.py").

- [ ] `edit` → EditGate → click **Reject** → POST `/edit-decision {decision:"reject"}`; the rejected patch's file is **NOT changed on disk** (turn-shadow restored from real; `shadow==real` holds).
- [ ] The rejection reason feeds back; the agent either revises (new `edit` → new EditGate) or `submit_changes`. (Confirms reject-restore mechanics.)

## Scenario J7 — create_task handoff → full task pipeline still works from the controller
**Message:** "Refactor the pricing module into a package with separate discount/tax submodules and tests." (clearly multi-file → recommended `create_task`)

- [ ] propose_mode → ModeGate (recommended **Plan it as a task**) → click it → `/mode-decision {mode:"create_task"}` → **task_card** appears → `await_plan_ready` → **plan card** at `AWAITING_PLAN_APPROVAL`.
- [ ] Click **Implement** → execution work-bar → step gates / command gates as before → **READY_FOR_REVIEW** → **ReviewCard** with run_summary + **task narrative**.
- [ ] **Finish** → SUCCEEDED; files on disk. (Confirms the controller correctly hands off into the unchanged task pipeline — the existing Tier-B/narrative behavior rides along.)

## Scenario J8 — discuss/refine (soft-terminal gate)
**On an open ModeGate** (from J3-style message), **do NOT pick** — instead type a follow-up: "actually, keep it minimal, no new file — just inline it."

- [ ] The typed message resumes the loop with appended history; the agent emits a **refined `propose_mode`** (or `answer`/`clarify`), and the prior gate is superseded. (Confirms the gate is soft-terminal ≈ plan-approval feedback.)

## Scenario J9 — gate durability across reload
**With a ModeGate (and separately an EditGate) pending:** Cmd+Shift+P → **Developer: Reload Window** → reopen chat.

- [ ] The pending gate **still renders** (driven by the 1s `/live` poll at the thread level, survives the reload + has no task id). Resolve it post-reload → it works (decision routes still fire).

## Scenario J10 — multi-turn context continuity (cache prefix)
**After J4/J5:** ask "what did you just add?"

- [ ] Agent references the prior edits (history replayed as seed_history / live tools on real), not a blank re-explore. (Confirms append-only history substrate.)

---

## Priority order for this session
Core (must pass): **J1, J3, J4, J5, J7, J9.**
Secondary (best-effort): J2, J6, J8, J10.

## Results log

### 2026-06-15 — Phase J kickoff
(to be filled in as scenarios run)
