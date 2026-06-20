# Controller UX Interaction Rules — Live Smoke

> Verifies the implemented plan against its spec
> (`docs/superpowers/specs/2026-06-17-controller-ux-interaction-rules-design.md`, §1–§11).
> Each **Scenario** asserts *observed behavior* — **a green unit test is NOT a smoke pass.**
> Mark `- [x]` per assertion; record thread ids + screenshots.

## Two-part smoke (run both)

| Part | Covers | How |
|------|--------|-----|
| **A. Backend HTTP** (automated) | §4 `turn_active`/`/live`, §1 detachment, §3 409 guard, §11 `stop`/idle-noop, §1 gate-clear | `scripts/verify/controller_ux_smoke.py` — runnable, deterministic |
| **B. Dev-host (CDP)** (this doc) | §5 input rows, §6 one-shot, §7 nav-lock, §9 ModeGate field, §10 live-resume, §2 EditGate reload durability + restart-orphan, §8 read-only | drive the real webview |

Part A is the source of truth for the backend rules; Part B is the source of truth for
everything that only manifests in the UI. They overlap on purpose (e.g. Stop is asserted
both as a route in A and as a button in B).

---

## Environment

- **Backend:** worktree `services/agentd-py` via `scripts/stress/start-backend.sh` with
  **`AI_EDITOR_CHAT_CONTROLLER=1` exported** before launch, `--workspace <REAL ws OUTSIDE
  .tmp>` (graph indexing needs a non-`.tmp` ancestor; `workspaces/shadow-forge-stress`
  works). Port **:8001** (workspace `.vscode/settings.json` pins
  `aiEditor.backendBaseUrl=http://localhost:8001`). Confirm controller is active:
  `curl -s :8001/health` AND a `/live` payload contains a `turn_active` key
  (legacy `ChatAgent` omits it).
- **Dev-host:** VS Code on CDP :9335 via `scripts/playwright/start-vscode-mcp.sh` —
  **EXT_PATH MUST point at THIS worktree** `.../.worktrees/feat-agentic-chat-controller/apps/vscode-extension`.
  **MUST rebuild `webview-ui/dist` first** (`npm run -w @ai-editor/vscode-extension build`,
  or in `apps/vscode-extension/webview-ui/`) — `dist` is a gitignored artifact; stale dist =
  old UI (the recurring stale-dist trap).
- **Driving caveat (auto-memory):** `browser_wait_for`/a11y snapshots do NOT pierce the
  sandboxed webview iframe — use CDP **frame-eval** (`page.frames()` → the webview frame),
  matching `scripts/playwright/drive-chat.js`. Backend runs `--reload`: do NOT edit
  `agentd/*.py` while a turn is in flight (hot-reload orphans it).

## Pre-flight checklist
- [ ] `webview-ui/dist` rebuilt from THIS worktree (timestamp newer than last source edit).
- [ ] `start-vscode-mcp.sh` EXT_PATH repointed to this worktree's `apps/vscode-extension`.
- [ ] Backend up on :8001 with `AI_EDITOR_CHAT_CONTROLLER=1` (`/live` carries `turn_active`).
- [ ] `shadow-forge-stress` indexed (snapshot non-zero nodes).
- [ ] Part A run first and green: `AGENTD_BASE_URL=http://127.0.0.1:8001 python3
      scripts/verify/controller_ux_smoke.py "$PWD/workspaces/shadow-forge-stress"`.

---

## PART A — Backend HTTP (automated)

Run and paste the summary line. Expected: all PASS; SKIPs only on the conditional
gate-clear check when the model doesn't raise a gate on the probe message.

```bash
export AGENTD_BASE_URL=http://127.0.0.1:8001
python3 scripts/verify/controller_ux_smoke.py "$PWD/workspaces/shadow-forge-stress"
```

- [ ] §4 idle thread → `turn_active=false` (key present → controller flag confirmed)
- [ ] §4 `turn_active=true` observed mid-turn; `false` after completion
- [ ] §3 second `/message` during an active turn → **409**
- [ ] §1 turn still active after a client disconnect (NOT cancelled)
- [ ] §11 `/stop` on an active turn → `ok=true`, `turn_active=false`, `✗ Stopped` breadcrumb persisted
- [ ] §11 `/stop` on an idle thread → `ok=false` (benign no-op)
- [ ] §1 new turn clears a stale controller gate at start (or SKIP — see Scenario S6 for the UI proof)

---

## PART B — Dev-host (CDP) scenarios

### Scenario S1 — §5 Rule 1.5 + §8: input ENABLED at a conversational terminal
**Message:** "What does `ShadowWorkspaceManager` do here?" (a QA turn → `answer` terminal)

- [ ] While the turn streams: composer is **disabled**, placeholder "Agent is working…",
      **Stop** button shown (§5.3 `turn_active`).
- [ ] On `chat_done` (answer rendered, no gate): composer is **ENABLED**, placeholder
      "Ask anything or describe a change…" (§5.5 idle/terminal).
- [ ] §8: during the turn AND after, read-only affordances work — expand "Show thinking",
      copy a message, scroll history.

### Scenario S2 — §5 Rule 1.2 + §6 + §9: ModeGate disables the composer; in-card field is one-shot
**Message:** "Add a `discount(price, pct)` helper to the pricing utilities."

- [ ] Agent explores → **ModeGate** renders in the `/live` slot (plan_sketch + recommended
      option + alternatives).
- [ ] §9: the trailing hint is GONE; an inline **"Chat about this approach…"** input is present.
- [ ] §5.2: the **main composer is disabled**, placeholder "Choose how to proceed — or chat
      about it on the card". Only the ModeGate (its buttons + in-card field) is interactive.
- [ ] §6: type in the in-card field and press Enter → exactly **one** `sendMessage` fires
      (a fresh turn); the card goes inert immediately; a second Enter does nothing (one-shot).
- [ ] The new turn supersedes the gate — the ModeGate clears from `/live` (gate-clear-at-start).

### Scenario S3 — §6: ModeGate option pick is one-shot
**Message:** re-trigger a ModeGate (as S2). Then click **Just explain**.

- [ ] A single `/mode-decision {mode:"explain"}` fires; optimistic `✓`/breadcrumb shows.
- [ ] All option buttons + the in-card field go inert at once (no double-submit if you
      double-click). Agent returns a text answer; **no files written** on disk.

### Scenario S4 — §5 Rule 1.1 + §6: EditGate disables the composer; Accept/Reject one-shot
**Setup:** "Review each edit" CHECKED. **Message:** "Add `src/discount.py` with
`apply_percentage(price, pct)`." → pick **Edit inline now**.

- [ ] `edit` → **EditGate** renders the diff in the `/live` slot.
- [ ] §5.1: composer **disabled**, placeholder "Waiting for your decision on the card above".
- [ ] §8: you can open the diff view / expand panes while the gate is up (read-only safe).
- [ ] §6: click **Accept** once → single `/edit-decision {accept}`; buttons go inert; file is
      promoted to the REAL workspace (verify `src/discount.py` on disk). A second click no-ops.

### Scenario S5 — §7 Rule 3: navigation cannot orphan a turn
**Setup:** start any turn (QA from S1 is fine) and act DURING it; then during a ModeGate.

- [ ] While `turn_active` (turn streaming): `‹ back`, history rows, and **+ New Chat** are
      **disabled** (greyed, non-clickable).
- [ ] While a **ModeGate/EditGate** is pending: the same nav controls remain **disabled**.
- [ ] After the terminal (`answer`/gate resolved): nav controls re-enable.

### Scenario S6 — §10 + §1: live-resume after a FE reload mid-turn (durable, no double-render)
**Message:** a multi-step QA that streams for several seconds. **Mid-stream**, reload the
webview (Cmd+Shift+P → *Developer: Reload Window*, or reload just the webview).

- [ ] After reload: the transcript is **reconstructed** (prior messages/pills present from the
      thread fetch).
- [ ] `/live` reports `turn_active=true` → composer stays **disabled** across the reload
      (durable signal, not the ephemeral flag).
- [ ] The live overlay **resumes**: new streaming chunks/pills continue to appear (channel
      re-subscribe via `streamChannel`).
- [ ] **No double-rendering**: each event appears once — confirms the `turnAbort===null`
      live-resume guard (the regression fixed post-implementation). Watch for duplicated
      bubbles/pills; there must be none.
- [ ] Turn completes normally; composer re-enables.

### Scenario S7 — §2: held-open EditGate is durable across a FE reload
**Setup:** "Review each edit" CHECKED. Reach an **EditGate** (as S4) but DO NOT decide.
Reload the webview.

- [ ] After reload: the **EditGate card re-renders** from `/live` (the gate persisted; the
      turn is still parked on its future because the backend stayed up).
- [ ] Composer remains **disabled** (§5.1) across the reload.
- [ ] Click **Accept** → `/edit-decision` fires the surviving future → the turn **resumes**
      and promotes. (EditGate = `step_review` parity.)

### Scenario S8 — §2 backend-restart orphan: stale EditGate unwedges
**Setup:** reach an EditGate (as S4), DO NOT decide. **Restart the backend** (kill + relaunch
`start-backend.sh`; the sqlite gate persists, the in-memory `_pending_edit` waiter is gone).

- [ ] After restart, `/live` (poll) still shows the EditGate briefly (gate persisted), but
      `turn_active=false` (the running turn died with the process).
- [ ] Click **Accept/Reject** → `resolve_edit` finds **no waiter** → it **clears the stale
      gate** and writes a breadcrumb "Previous turn ended — please re-send your request."
- [ ] The UI **unwedges**: gate disappears, composer **re-enables** (turn_active already
      false). Re-issuing the edit works. (Matches the orphaned-task degradation; ModeGate has
      no such caveat — its turn completes before the gate, so it is fully restart-durable.)

### Scenario S9 — §11: Stop button posts /stop (not SSE-disconnect)
**Message:** start a longer turn; click the **Stop** button.

- [ ] Stop posts `POST /chat/threads/{id}/stop` (verify in the backend log / network), NOT a
      mere SSE disconnect.
- [ ] The turn ends; a **`✗ Stopped`** breadcrumb appears in the transcript (durable — still
      there after a reload).
- [ ] Composer re-enables; `turn_active=false` on the next `/live` poll.

---

## Spec coverage map

| Spec § | Rule | Part A (HTTP) | Part B (CDP) |
|--------|------|---------------|--------------|
| §1 detach turn from SSE | detachment | ✓ disconnect survives | S6 (reload mid-turn) |
| §1 clear gate at turn start | one new turn clears stale gate | ✓ (conditional) | S2 |
| §2 durable held-open EditGate | reload parity | — | S7 |
| §2 backend-restart orphan | resolve_edit clears stale gate | — (needs restart) | S8 |
| §3 in-flight guard | 409 | ✓ | (input-disable prevents it: S2/S5) |
| §4 `turn_active` via `/live` | durable signal | ✓ idle+lifecycle | S1/S6 (composer state) |
| §5 Rule 1 input availability | per-row precedence | — | S1 (1.5), S2 (1.2), S4 (1.1), S6 (1.3) |
| §6 Rule 2 one-shot | mode/edit/chat-about | — | S2, S3, S4 |
| §7 Rule 3 navigation lock | nav disabled | — | S5 |
| §8 Rule 4 read-only safety | always safe | — | S1, S4 |
| §9 ModeGate component | "chat about this" field | — | S2 |
| §10 reload reconnect | live-resume | — | S6 |
| §11 Stop endpoint | /stop + breadcrumb | ✓ stop+idle-noop | S9 |

**Known v1 limitations to observe, not flag as bugs (§Risks):** no message queueing while
busy; live events older than the 50-event broadcaster replay buffer are not re-delivered on
reconnect (reconstructed from the transcript instead); a backend restart orphans the in-flight
turn (S8 is the accepted degradation).

---

## Results log

_Date / driver / backend provider:_

_Part A summary line:_

_Part B per-scenario notes + screenshots:_
