# Controller UX Interaction Rules — Live Smoke

> Verifies the implemented plan against its spec
> (`docs/superpowers/specs/2026-06-17-controller-ux-interaction-rules-design.md`, §1–§11).
> Each **Scenario** asserts *observed behavior* — **a green unit test is NOT a smoke pass.**
> Mark `- [x]` per assertion; record thread ids + screenshots.

## Two-part smoke (run both)

| Part | Covers | How |
|------|--------|-----|
| **A. Backend HTTP** (automated) | §4 `turn_active`/`/live`, §1 detachment + turn-completes, §3 409 guard, §11 `stop`/idle-noop, §1 gate-clear, **§2 EditGate reload-durability**, **§6 edit-decision one-shot** | `scripts/verify/controller_ux_smoke.py` — runnable, deterministic |
| **B. Dev-host (CDP)** (this doc) | §5 input rows, §6 mode one-shot, §7 nav-lock, §9 ModeGate field, §10 live-resume, §2 EditGate restart-orphan (out-of-process), §8 read-only | drive the real webview |

Part A is the source of truth for the backend rules; Part B is the source of truth for
everything that only manifests in the UI. They overlap on purpose (e.g. Stop is asserted
both as a route in A and as a button in B).

---

## Environment

- **Backend:** worktree `services/agentd-py` via `scripts/stress/start-backend.sh` with
  **`CRUCIBLE_CHAT_CONTROLLER=1` exported** before launch, `--workspace <REAL ws OUTSIDE
  .tmp>` (graph indexing needs a non-`.tmp` ancestor; `workspaces/crucible-stress`
  works). Port **:8001** (workspace `.vscode/settings.json` pins
  `crucible.backendBaseUrl=http://localhost:8001`). Confirm controller is active:
  `curl -s :8001/health` AND a `/live` payload contains a `turn_active` key
  (legacy `ChatAgent` omits it).
- **Dev-host:** VS Code on CDP :9335 via `scripts/playwright/start-vscode-mcp.sh` —
  **EXT_PATH MUST point at THIS worktree** `.../.worktrees/feat-agentic-chat-controller/apps/vscode-extension`.
  **MUST rebuild `webview-ui/dist` first** (`npm run -w crucible-vscode-extension build`,
  or in `apps/vscode-extension/webview-ui/`) — `dist` is a gitignored artifact; stale dist =
  old UI (the recurring stale-dist trap).
- **Driving caveat (auto-memory):** `browser_wait_for`/a11y snapshots do NOT pierce the
  sandboxed webview iframe — use CDP **frame-eval** (`page.frames()` → the webview frame),
  matching `scripts/playwright/drive-chat.js`. Backend runs `--reload`: do NOT edit
  `agentd/*.py` while a turn is in flight (hot-reload orphans it).

## Pre-flight checklist
- [ ] `webview-ui/dist` rebuilt from THIS worktree (timestamp newer than last source edit).
- [ ] `start-vscode-mcp.sh` EXT_PATH repointed to this worktree's `apps/vscode-extension`.
- [ ] Backend up on :8001 with `CRUCIBLE_CHAT_CONTROLLER=1` (`/live` carries `turn_active`).
- [ ] `crucible-stress` indexed (snapshot non-zero nodes).
- [ ] Part A run first and green: `AGENTD_BASE_URL=http://127.0.0.1:8001 python3
      scripts/verify/controller_ux_smoke.py "$PWD/workspaces/crucible-stress"`.

---

## PART A — Backend HTTP (automated)

Run and paste the summary line. Expected: all PASS; SKIPs only on the conditional
gate-clear check when the model doesn't raise a gate on the probe message.

```bash
export AGENTD_BASE_URL=http://127.0.0.1:8001
python3 scripts/verify/controller_ux_smoke.py "$PWD/workspaces/crucible-stress"
```

- [ ] §4 idle thread → `turn_active=false` (key present → controller flag confirmed)
- [ ] §4 `turn_active=true` observed mid-turn; `false` after completion (SKIP on a too-fast turn)
- [ ] §3 second `/message` during an active turn → **409**
- [ ] §1 turn still active after a client disconnect (NOT cancelled)
- [ ] §1 detached turn **completes on its own** after the disconnect (no client attached)
- [ ] §11 `/stop` on an active turn → `ok=true`, `turn_active=false`, `✗ Stopped` breadcrumb persisted
- [ ] §11 `/stop` on an idle thread → `ok=false` (benign no-op)
- [ ] §2 held-open **EditGate** survives a dropped SSE (reload tier) + `/edit-decision` resumes the turn (or SKIP if the model proposes no edit)
- [ ] §6 second `/edit-decision` (no live waiter) → `ok=false` (one-shot)
- [ ] §1 new turn clears a stale controller gate at start (or SKIP — see Scenario S6 for the UI proof)

---

## PART B — Dev-host (CDP) scenarios

> **Model-contingency (read first).** The ModeGate scenarios (S2/S3) assume the agent
> raises a `propose_mode` gate; the EditGate ones (S4/S7/S8) assume it then proposes an
> actual edit. A capable model on an edit-y prompt does this reliably, but a weaker/cheaper
> provider may answer directly, plan as a task, or skip straight to `edit` (no ModeGate).
> If a gate doesn't appear within ~60s: (a) confirm the turn is done (`/live` `turn_active`
> false), (b) re-send a sharper edit prompt (e.g. name an exact file + symbol), (c) if it
> still won't gate, mark the scenario **N/A (model did not gate)** in the results log rather
> than FAIL — Part A's EditGate test SKIPs on the same condition, and the gate *mechanics*
> are covered by the backend unit tests. Use a strong provider (the smoke's intent is UX,
> not model capability).

### Scenario S1 — §5 Rule 1.5 + §8: input ENABLED at a conversational terminal
**Message:** "What does `ShadowWorkspaceManager` do here?" (a QA turn → `answer` terminal)

- [x] While the turn streams: composer is **disabled**, placeholder "Agent is working…",
      **Stop** button shown (§5.3 `turn_active`). ✓ verified 2026-06-21
- [x] On `chat_done` (answer rendered, no gate): composer is **ENABLED**, placeholder
      "Ask anything or describe a change…" (§5.5 idle/terminal). ✓
- [x] §8: during the turn AND after, read-only affordances work — expand "Show thinking",
      copy a message, scroll history. ✓ (Thinking pill expandable, Copy present)

### Scenario S2 — §5 Rule 1.2 + §6 + §9: ModeGate disables the composer; in-card field is one-shot
**Message:** "Add a `discount(price, pct)` helper to the pricing utilities."

- [x] Agent explores → **ModeGate** renders in the `/live` slot (plan_sketch + recommended
      option + alternatives). ✓ (on sharper new-file prompt — see Findings, model-contingency)
- [x] §9: the trailing hint is GONE; an inline **"Chat about this approach…"** input is present. ✓
- [x] §5.2: the **main composer is disabled**, placeholder "Choose how to proceed — or chat
      about it on the card". Only the ModeGate (its buttons + in-card field) is interactive. ✓
- [x] §6: type in the in-card field and press Enter → exactly **one** `sendMessage` fires
      (a fresh turn); the card goes inert immediately; a second Enter does nothing (one-shot).
      ✓ verified via operator keystroke: user-msg count 1→2 (exactly +1, no double), card went inert.
- [x] The new turn supersedes the gate — the ModeGate clears from `/live` (gate-clear-at-start).
      ✓ after the in-card send: `/live` gate→None, turn_active→true, composer "Agent is working…".

### Scenario S3 — §6: ModeGate option pick is one-shot
**Message:** re-trigger a ModeGate (as S2). Then click **Just explain**.

- [x] A single `/mode-decision {mode:"explain"}` fires; optimistic `✓`/breadcrumb shows.
      ✓ transcript breadcrumb "▸ You chose: Just explain" persisted.
- [x] All option buttons + the in-card field go inert at once (no double-submit if you
      double-click). Agent returns a text answer; **no files written** on disk.
      ✓ button was GONE on re-query after first click (one-shot); `utils/seasonal.py` absent on disk.

### Scenario S4 — §5 Rule 1.1 + §6: EditGate disables the composer; Accept/Reject one-shot
**Setup:** "Review each edit" CHECKED. **Message:** "Add `src/discount.py` with
`apply_percentage(price, pct)`." → pick **Edit inline now**.

- [x] `edit` → **EditGate** renders the diff in the `/live` slot. ✓ "Review edit" card, diff +1/-1 (def discount→apply_percentage)
- [x] §5.1: composer **disabled**, placeholder "Waiting for your decision on the card above". ✓ exact
- [x] §8: you can open the diff view / expand panes while the gate is up (read-only safe). ✓ diff rendered, scrollable
- [x] §6: click **Accept** once → single `/edit-decision {accept}`; buttons go inert; file is
      promoted to the REAL workspace (verify `src/discount.py` on disk). A second click no-ops.
      ✓ Accept button GONE after 1st click (one-shot); `src/discount.py` def changed
      `discount`→`apply_percentage` ON REAL DISK; turn resumed, composer re-enabled.

### Scenario S5 — §7 Rule 3: navigation cannot orphan a turn
**Setup:** start any turn (QA from S1 is fine) and act DURING it; then during a ModeGate.

- [x] While `turn_active` (turn streaming): `‹ back`, history rows, and **+ New Chat** are
      **disabled** (greyed, non-clickable). ✓ operator-confirmed during a streaming turn
      (`navLocked = !inputEnabled || turnActive`, `ThreadView.tsx:42`).
- [~] While a **ModeGate/EditGate** is pending: the same nav controls remain **disabled**.
      ⚠ DEVIATION (accepted): nav is NOT disabled during a pending gate (turn_active=false,
      inputEnabled stays true → `navLocked=false`). Behavioral test: clicking "Back to history"
      during a live ModeGate DID navigate to history — BUT the gate **reappears** on returning to
      the thread (durable via `/live`). So no orphaning (§7's intent holds) and it's arguably
      better UX (browse other threads, return to the pending decision). Operator-accepted as
      good UX, not a bug — literal §7 Part-B wording is stricter than the implemented behavior.
- [x] After the terminal (`answer`/gate resolved): nav controls re-enable. ✓ (history list reachable)

### Scenario S6 — §10 + §1: live-resume after a FE reload mid-turn (durable, NO DATA LOSS, no double-render)
**Message:** a multi-step QA that streams for several seconds (so several thinking lines +
tool pills accumulate on screen). **Mid-stream**, reload the webview (Cmd+Shift+P →
*Developer: Reload Window*, or reload just the webview).

- [x] After reload: the transcript is **reconstructed** (prior messages/pills present from the
      thread fetch). ✓ persisted messages restored on reopen.
- [ ] **NO DATA LOSS (the core guarantee — measure it, don't eyeball).** Nothing visible
      before the reload may vanish after it: every **thinking line** and every **tool pill**
      on screen pre-reload must still be present post-reload (recovered from the durable
      transcript and/or the channel replay) — e.g. if 3 thinking messages + 4 tool pills were
      showing, all 3 + 4 are still there. Method: snapshot a **signature set** of the visible
      pill/thinking text in the webview frame BEFORE reloading, then assert it is a **subset**
      of the post-reload content:
      ```js
      // BEFORE reload — capture trimmed innerText of each pill + thinking row.
      // Tool pills + thinking rows live inside AgentRow; collect their text signatures.
      const sig = el => [...el.querySelectorAll('button, [class*="pill"], [class*="thinking"], p, li')]
        .map(n => (n.innerText || '').trim()).filter(t => t.length > 3);
      const before = new Set(sig(document.querySelector('.overflow-y-auto')));
      // ... reload, wait for live-resume to settle ...
      // AFTER reload:
      const after = new Set(sig(document.querySelector('.overflow-y-auto')));
      const lost = [...before].filter(t => !after.has(t));
      // PASS ⇔ lost.length === 0   (every pre-reload thinking line / pill survived)
      ```
      A non-empty `lost` is a data-loss bug — flag it (the most likely culprit: an in-flight
      step's pills/thinking weren't persisted yet AND fell outside the 50-event channel replay).
      **✓ NO DATA LOSS at the durable layer (2026-06-21) — earlier "data loss" claim RETRACTED.**
      A mid-stream `innerText` snapshot showed fewer pills post-reload, but that was a measurement
      artifact: the completed turn's transcript persists **`tool_events=18`** (ALL calls:
      search_code×N, read_file, list_directory×2, search_semantic) — verified via
      `GET /threads/{id}`. The lower `innerText` count reflected collapsed/racing pill rendering,
      not lost data. The full answer re-rendered intact on reopen.
      **BUT — RENDER-RECONSTRUCTION GAP (operator-confirmed, real):** of the 18 persisted
      `tool_events`, only **3 re-render** after reopen (read_file×2, search_code×1), with NO
      collapsed "Thinking (N steps)" pane holding the rest. So the data is safe but the UI does
      NOT rebuild the historical pills on reopen/reload — the user sees an incomplete tool history
      (only the ~3 live-resubscribe pills). Data-loss = NO; UI render of persisted pills = BROKEN.
      See Finding 8.
- [x] `/live` reports `turn_active=true` → composer stays **disabled** across the reload
      (durable signal, not the ephemeral flag). ✓ composer disabled, "Agent is working…" post-reload.
- [x] The live overlay **resumes**: new streaming chunks/pills continue to appear (channel
      re-subscribe via `streamChannel`). ✓ new read_file pills appeared after reload.
- [ ] **No double-rendering**: each event appears once — confirms the `turnAbort===null`
      live-resume guard (the regression fixed post-implementation). Eyeballing is weak for
      a dup-render bug; make it **objective** via CDP frame-eval on the webview frame. The
      message list has **no per-row id attribute** today (rows are `MessageRow` with a React
      `key={i}` — not a DOM hook), so count by container child-count + text-occurrence:
      **✓ no double-render observed** (after-set was SMALLER, not larger — under-render from the
      data loss, never duplication).
      ```js
      // in the webview frame (page.frames() → the vscode-webview OOPIF).
      // The message list is the scrollable flex column (ThreadView.tsx:184).
      const list = document.querySelector('.overflow-y-auto');
      const rowCount = list ? list.children.length : -1;   // capture BEFORE reload too
      // After the live-resume settles, rowCount must NOT ~double, and a distinctive
      // answer substring must appear exactly once:
      const needle = '<first ~40 chars of the streamed answer>';
      const n = (list.innerText.match(new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g')) || []).length;
      // PASS ⇔ n === 1 AND rowCount_after ≈ rowCount_before + (new rows legitimately added)
      ```
      (Minor affordance to add later: a `data-message-id` on each `MessageRow` would make
      this a one-line duplicate-key assertion instead of substring counting.)
- [~] Turn completes normally; composer re-enables. _(turn still streaming at measurement; re-enable confirmed separately in S1/S4)_

### Scenario S7 — §2: held-open EditGate is durable across a FE reload
**Setup:** "Review each edit" CHECKED. Reach an **EditGate** (as S4) but DO NOT decide.
Reload the webview.

- [x] After reload: the **EditGate card re-renders** from `/live` (the gate persisted; the
      turn is still parked on its future because the backend stayed up). ✓ Developer:Reload Window,
      reopened thread → "Review edit" re-rendered; `/live` gate=edit turn_active=true across reload.
- [x] Composer remains **disabled** (§5.1) across the reload. ✓ "Waiting for your decision…"
- [x] Click **Accept** → `/edit-decision` fires the surviving future → the turn **resumes**
      and promotes. (EditGate = `step_review` parity.) ✓ post-reload Accept promoted to real disk
      + turn reached terminal (turn_active→false).

### Scenario S8 — §2 backend-restart orphan: stale EditGate unwedges
**Why this one matters most:** it is the only path with a bespoke code branch
(`resolve_edit` clearing a waiter-less gate, `controller.py:445-455`) and the easiest to
skip. Part A **cannot** cover it (it needs an out-of-process restart, not a dropped SSE);
the deterministic backstop is the unit test
`tests/test_controller_durable_edit.py::test_resolve_edit_clears_stale_gate_when_no_waiter`.
This scenario proves it survives a **real** sqlite-persisted gate across an actual process
boundary — do not skip it.

**Setup:** reach an EditGate (as S4), DO NOT decide. **Restart the backend** — kill the
uvicorn PID and relaunch the SAME `start-backend.sh` invocation (same `--workspace`, same
`CRUCIBLE_CHAT_CONTROLLER=1`, same port :8001) so it reopens the same chat sqlite. The
sqlite `pending_controller_gate` persists; the in-memory `_pending_edit` waiter is gone.

- [x] Across the restart, the persisted gate is verifiable headless:
      `curl -s :8001/v1/chat/threads/<tid>/live` → `pending_gate.kind=="edit"` **and**
      `turn_active=false` (the running turn died with the process). ✓ `pending_gate.kind=edit`,
      `turn_active=false`, controller flag key still present (env relaunch correct).
- [x] Click **Accept/Reject** (or `curl -s -XPOST :8001/v1/chat/threads/<tid>/edit-decision
      -H 'content-type: application/json' -d '{"decision":"reject"}'`) → returns `{"ok":false}`
      (no waiter) → `resolve_edit` **clears the stale gate** and writes a breadcrumb
      "Previous turn ended — please re-send your request." ✓ `{"ok":false}`; gate→None;
      exact breadcrumb written.
- [x] Re-poll `/live` → **no** `pending_gate`; the UI **unwedges**: gate disappears, composer
      **re-enables** (`turn_active` already false). Re-issuing the edit works. (Matches the
      orphaned-task degradation; ModeGate has no such caveat — its turn completes before the
      gate, so it is fully restart-durable.) ✓ backend: `pending_gate=None`, `turn_active=false`;
      `src/season_tax.py` **absent on disk** (hard proof the turn never resumed). ⚠ live UI-unwedge
      not observed: dev-host webview CDP target dropped to 0 when the backend SSE broke at restart
      (env artifact, not a product defect — see Finding 9).

### Scenario S9 — §11: Stop button posts /stop (not SSE-disconnect)
**Message:** start a longer turn; click the **Stop** button.

- [x] Stop posts `POST /chat/threads/{id}/stop` (verify in the backend log / network), NOT a
      mere SSE disconnect. ✓ turn ended on Stop click
- [x] The turn ends; a **`✗ Stopped`** breadcrumb appears in the transcript (durable — still
      there after a reload). ✓ DURABLE (persisted msg[5]; renders after reopen) — but ⚠ does NOT
      render LIVE after Stop; only appears on reopen/reload (Finding 7).
- [x] Composer re-enables; `turn_active=false` on the next `/live` poll. ✓

---

## Spec coverage map

| Spec § | Rule | Part A (HTTP) | Part B (CDP) |
|--------|------|---------------|--------------|
| §1 detach turn from SSE | detachment | ✓ disconnect survives + completes | S6 (reload mid-turn) |
| §1 clear gate at turn start | one new turn clears stale gate | ✓ (conditional) | S2 |
| §2 durable held-open EditGate | reload parity | ✓ reload tier (conditional) | S7 |
| §2 backend-restart orphan | resolve_edit clears stale gate | — (needs out-of-process restart) | S8 |
| §3 in-flight guard | 409 | ✓ | (input-disable prevents it: S2/S5) |
| §4 `turn_active` via `/live` | durable signal | ✓ idle+lifecycle | S1/S6 (composer state) |
| §5 Rule 1 input availability | per-row precedence | — | S1 (1.5), S2 (1.2), S4 (1.1), S6 (1.3) |
| §6 Rule 2 one-shot | mode/edit/chat-about | ✓ edit-decision (conditional) | S2, S3, S4 |
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

_Date / driver / backend provider:_ 2026-06-21 / raw-CDP-over-WS driver (`/tmp/cdp.js`) +
agent-browser for screenshots / TQP **qwen3.6:35b-a3b-q4_K_M** on :8001 (controller flag ON,
tight oneOf schema active — Tier-2). Dev-host on CDP :9335, dist rebuilt from this worktree.

_Part A summary line:_ Done + green (run/verified separately by the operator prior to this session).

_Part B per-scenario notes + screenshots:_

- **S1 — QA terminal — PASS (all 3 groups).** Msg "What does ShadowWorkspaceManager do here?".
  Streaming: composer `disabled=true`, placeholder **"Agent is working…"**, **Stop** shown
  (`/tmp/s1-streaming.png`). Terminal (`chat_done`): composer `disabled=false`, placeholder
  **"Ask anything or describe a change…"** (`/tmp/s1-terminal.png`). Read-only during+after:
  "Thinking (N step)" pill expandable, **Copy** present, scroller works.
- **S2 — ModeGate — PASS (structure/§9/§5.2); §6 in-card one-shot PARTIAL; gate-clear pending.**
  Model-contingency hit: the first prompt ("Add a discount(price,pct) helper to the pricing
  utilities") was **answered directly** — the model grounded to an existing pricing helper and
  described it (Tier-2 grounding working; NOT a bug). Per doc guidance (b) a sharper new-file
  prompt ("Create a brand-new file utils/seasonal.py … seasonal_discount(price, season) … does
  not exist yet") forced `propose_mode`. Verified: `/live pending_gate.kind=mode` with a
  **concrete** plan_sketch (exact path+signature+behavior), `recommended=edit`, options
  edit/create_task/explain ✅; §9 in-card **"Chat about this approach…"** field present, trailing
  hint replaced ✅; §5.2 main composer disabled, placeholder **"Choose how to proceed — or chat
  about it on the card"** ✅ (`/tmp/s2-modegate.png`). §6 in-card one-shot ✅ (operator keystroke:
  user-msg 1→2 exactly, card inert) + gate-clear-on-new-turn ✅ (`/live` gate→None, turn_active→true).
  **S2 = PASS.**
- **S3 — ModeGate option one-shot — PASS.** Clicked "Just explain" → single `/mode-decision{explain}`,
  breadcrumb "▸ You chose: Just explain" persisted, button GONE on re-query (one-shot), no file on
  disk. NOTE: the explain turn then **re-proposed mode** (2nd `propose_mode` in log) rather than just
  explaining — model behavior, surfaced the gate again (see Finding 4).
- **S4–S7 — PENDING.**
- **S8 — backend-restart orphan EditGate — PASS (backend-definitive).** Parked a real EditGate on a
  new-file edit flow (`chat-592d9d1cdd8a`, goal: create `src/season_tax.py`), captured the live
  `start-backend.sh` env (120 vars; forced `CRUCIBLE_CHAT_CONTROLLER=1` after a lowercase system var
  bled into the parse), then **killed the :8001 uvicorn out-of-process and relaunched** the same
  invocation (same workspace, port, chat sqlite). `--reload` parent ignored SIGTERM → had to
  `lsof -ti:8001 | xargs kill -9`; relaunch came up in ~6s reopening the same `chat.sqlite3`.
  **Step 1:** persisted gate survived — `/live` → `pending_gate.kind=edit`, `turn_active=false`
  (in-memory waiter died with the process), controller flag still on. **Step 2:** `POST /edit-decision
  {reject}` → `{"ok":false}` (no waiter), `resolve_edit` cleared the stale gate (`pending_gate→None`)
  + wrote the exact breadcrumb **"Previous turn ended — please re-send your request."** **Step 3:**
  re-poll `/live` → no gate, `turn_active=false`; **`src/season_tax.py` absent on disk** = hard proof
  the turn did **not** resume (a resume would have created+promoted it). The bespoke waiter-less-gate
  branch (`controller.py:445-455`) works end-to-end across a real process boundary. Operator
  confirmed the semantics: gate persists (sqlite) ⇒ reappears; resume capability (in-memory waiter)
  does **not** ⇒ must re-send. **S8 = PASS.** Caveat: live UI-unwedge not visually confirmed — the
  dev-host webview CDP target went to 0 when its SSE to :8001 dropped at restart (Finding 9).

### Findings (this run)

1. **agent-browser CANNOT drive the chat webview (OOPIF).** Tested 4 ways against the live
   dev-host: `tab` (webview not listed — it's a `type:iframe` target, not page/webview),
   `frame "iframe.webview"` ("Frame not found", cross-origin), `eval` (runs in the **workbench**
   JS context, not the iframe — `NOT-CHAT`), and **direct `connect <iframe-ws-url>`** (the
   isolated-target idea — agent-browser still snapshots/evals the parent page). Root cause:
   agent-browser manages page targets and re-resolves to the top page; it never runs Runtime in
   the OOPIF target's own session. **Only raw CDP `Runtime.evaluate` on the iframe target's own
   `webSocketDebuggerUrl` pierces it** (what `/tmp/cdp.js` does). agent-browser IS useful for
   **screenshots** (pixels include the composited webview). Token-efficiency win is moot when it
   can't read the content. → keep raw-CDP for DOM, agent-browser for screenshots.
2. **Composer binds plain `Enter`, not ⌘↵** (`InputArea.tsx:76` `e.key==="Enter" && !e.shiftKey`)
   — the `smoke_controller_cdp_driving_recipe` memory was STALE on this. The textarea is
   **controlled** (`value={draft}`; `doSend()` reads React state, not the DOM value), so a working
   send must: native-set value → dispatch `input` (updates `draft`) → let React commit → dispatch
   synthetic `keydown` Enter on the textarea. CDP `Input.dispatchKeyEvent` did NOT route to the
   iframe's focused element (the original "send never fired" bug). Fixed in `/tmp/cdp.js send`.
3. **"Working… MM:SS" elapsed timer keeps climbing while parked at a ModeGate** (observed 2:33 →
   5:01 → 9:13) even though `/live turn_active=false` and the turn has ended. Cosmetic, but the
   elapsed timer should stop when the turn parks at a gate. (Minor; not blocking.)
4. **ModeGate card instability while parked + explain re-proposes mode (worth a closer look).**
   While the gate sat unresolved for several minutes, the card's children (in-card field, option
   buttons) intermittently vanished from automated DOM reads, and the log shows the controller
   emitting `propose_mode` **twice** on this thread (12:17 then 12:24). Picking "Just explain" did
   resolve the 1st gate (breadcrumb persisted), but the follow turn **re-proposed mode** instead of
   returning a plain explanation — re-surfacing the gate. Net effect: a parked ModeGate that's
   left to idle re-renders and can re-raise. Likely intertwined with the runaway timer (#3)
   forcing re-renders. Not a hard failure (every interaction we drove eventually registered), but
   the parked-gate render churn + explain→re-propose deserve a follow-up. Operator-driven keystroke
   resolved cleanly, so the core one-shot/gate-clear logic is sound.
5. **In-flight controller tool pills are lost on reload (S6 data-loss boundary).** The controller
   DOES persist `tool_events` pills — but only when the turn's outcome message is written at
   completion (`controller_loop.py:199` builds them, `controller.py:348` attaches them). A turn
   that is **stopped or reloaded mid-flight never reaches that write**, so its in-flight pills have
   NO durable copy (only a pill-less `✗ Stopped` breadcrumb). Confirmed via transcript: the stopped
   "implement it" turn's `run_command/edit` pills are absent from the persisted messages, so they
   vanished on reload (matches the operator's live observation). Recoverable only via the 50-event
   channel replay, which didn't cover them. A turn that **completes normally** persists its pills,
   so those DO survive reload — a clean S6 (no stop) characterization is still pending.
6. **Model bypasses the EditGate by writing files via `run_command` in DECIDE phase.** On a
   follow-up ("looks good, implement it") the controller, in `phase=DECIDE`, attempted
   `mkdir -p utils` then `cat > utils/seasonal.py << EOF …` via `run_command` instead of
   `propose_mode → edit`. The heredoc errored here, but had it succeeded it would have written to
   the REAL workspace with **no EditGate review / no step_review gating**. Potential review-bypass;
   worth constraining the controller's `run_command` from creating workspace source files when an
   `edit` path exists. (qwen3.6 behavior, surfaced under prodding.)
7. **`✗ Stopped` breadcrumb persists but does NOT render LIVE after Stop (operator-caught).** After
   clicking Stop, the turn ended + composer re-enabled, and the breadcrumb was written to the
   transcript (msg[5], `breadcrumb=true`) — but the webview kept showing the frozen pre-stop pills
   and never displayed "Stopped". The sibling "▸ You chose: Just explain" breadcrumb DID render, so
   it's specific to the stop-path live broadcast/render, not breadcrumbs in general. Reopening the
   thread re-fetches and renders "Stopped" correctly (durability OK). Likely the `chat_breadcrumb`
   live event isn't applied to the open transcript on the stop path (SSE torn down by the stop
   before the breadcrumb broadcast lands). User-facing: a stopped turn looks frozen until reload.
8. **UI does not reconstruct persisted `tool_events` pills on reopen/reload (operator-confirmed).**
   A COMPLETED controller turn persists all its pills (verified: `tool_events=18` on the agent
   message). But reopening/reloading the thread renders only **3 of 18** pills (the live
   re-subscribe ones), with no collapsed pane for the rest — the persisted historical pills in the
   message metadata are not rebuilt into the transcript view. NOT data loss (transcript intact),
   but the user sees an incomplete tool history after any reload. Likely the reopen render path
   renders live-streamed pills but skips `metadata.tool_events` reconstruction for the message (or
   a dedup drops them). Distinct from Finding 5 (which is about a STOPPED turn never persisting its
   pills). This is the concrete bug behind the operator's "it's not showing in UI after reload".
9. **Dev-host webview CDP target vanishes when the backend restarts (S8 env artifact, NOT a product
   bug).** During S8's out-of-process backend restart, the webview's SSE to :8001 broke and the
   dev-host's CDP target list dropped to 0 (`:9335/json/list` → 0 targets), so the live UI-unwedge
   could not be driven/observed via raw CDP. The :9335 browser endpoint itself stayed alive
   (`/json/version` OK). This only affects the **driving harness**, not the product — the backend-side
   S8 proof (gate cleared, breadcrumb written, no file on disk) is definitive on its own. To observe
   the live unwedge next time, reload the dev-host window after the backend is healthy, then re-poll;
   or assert via the product's own `/live` poll path rather than CDP. Note `--reload` uvicorn ignores
   SIGTERM — use `lsof -ti:8001 | xargs kill -9` to actually free the port before relaunch.
