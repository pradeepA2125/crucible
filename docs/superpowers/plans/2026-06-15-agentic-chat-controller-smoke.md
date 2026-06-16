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
- **Restart-durable conversation substrate** (session 3): the controller loop's verbatim turn history AND its frozen retrieval seed are now persisted on the thread (`controller_conversation_history` / `controller_retrieval_seed`, both kept OFF the wire) and rehydrated as `seed_history` on a backend restart — mirrors the planner's `planning_conversation_history` / `planning_initial_context`. The `create_task` handoff derives `pre_explored_context` (uncapped) from that same history (the separate `_explore_by_thread` accumulator is gone). Unit-locked byte-identity of the cacheable head across restart; the **live KV-cache reuse against TQP is J11**.

## Environment

- **Backend:** worktree `services/agentd-py` via `scripts/stress/start-backend.sh` with **`AI_EDITOR_CHAT_CONTROLLER=1` exported** before launch, `--workspace <REAL ws OUTSIDE .tmp>` (graph indexing needs a non-`.tmp` ancestor). Port :8001 (workspace `.vscode/settings.json` pins `aiEditor.backendBaseUrl=http://localhost:8001`).
- **Dev-host:** VS Code on CDP :9335 via `scripts/playwright/start-vscode-mcp.sh` — **EXT_PATH MUST point at THIS worktree** `.../.worktrees/feat-agentic-chat-controller/apps/vscode-extension` (the committed script points at a DELETED worktree — fix before launch). **MUST rebuild `webview-ui/dist` first** (`npm run -w webview-ui build` or in `apps/vscode-extension/webview-ui/`) — dist is a gitignored artifact; stale dist = old UI (the sess.3 stale-dist trap).
- **Driving caveat (auto-memory):** `browser_wait_for`/a11y snapshot do NOT pierce the sandboxed webview iframe — use CDP **frame-eval** (`page.frames()` → the `fake.html`/webview frame), matching `scripts/playwright/drive-chat.js`. Backend runs `--reload`: do NOT edit `agentd/*.py` while a turn is in flight (hot-reload orphans it).
- **Cache-behavior verification (do this whenever a scenario is multi-turn — J10, J11, and any time a thread takes ≥2 turns):** never *assume* a hit or miss — actively measure it. Before driving, tail the TQP/llama-server log; per turn, capture **(1)** the slot's reused-prefix size (`n_past`) and prompt tokens *evaluated* vs *sent*, and **(2)** the turn's time-to-first-token (agentd request-duration log or wall-clock the SSE). A warm continuation reuses the prefix (large `n_past`, small eval, fast TTFT); a cold/diverged prefix re-prefills (`n_past≈0`, full eval, slow TTFT). Log these numbers next to the scenario result so a regression in prefix stability is visible, not silent.

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

## Scenario J11 — restart-durable history + KV-cache reuse (TQP stays alive)
**Premise:** the agentd backend restarts (uvicorn `--reload` fires, or a manual kill+relaunch) but **TQP/llama-server stays up**, so its prefix KV cache survives. The rehydrated prompt head must be byte-identical so TQP reuses the cached prefix instead of re-prefilling.
**Setup:** a thread with ≥1 explored turn already (reuse the thread from J1 or J3 — it has history + a pinned seed). Tail TQP's server log for prompt-eval/cache lines before driving.

- [ ] **Restart agentd between turns** (NOT mid-turn): wait for the prior turn's `chat_done`, then either touch an `agentd/*.py` to trigger `--reload` or `kill` + relaunch `start-backend.sh` (same `AI_EDITOR_CHAT_CONTROLLER=1`, same workspace). Confirm `curl -s :8001/health` back up. **Do NOT restart TQP.**
- [ ] In the SAME thread, send a follow-up ("what did you look at / change so far?"). The agent **references the prior turn** (history rehydrated from the DB → `seed_history`), not a blank re-explore — the J10 continuity, now proven across a process restart.
- [ ] **KV reuse on TQP — detect hit vs miss with ≥1 method (ideally 2; you must actively measure, not assume):**
  - **(a) TQP/llama-server logs (most direct).** Tail the llama.cpp server log across the post-restart turn. A **hit** shows the cached prefix reused: `slot … | n_past = P` with **P large** (≈ the head length), `kv cache rm [P, end)` removing only the tail, and `prompt eval time = … (M tokens …)` with **M ≪ total prompt tokens** (M = only the new/uncached suffix). A **miss/cold** re-prefill shows `n_past ≈ 0` and M ≈ the full prompt. Record M and total for the turn just before vs just after the restart.
  - **(b) Backend / round-trip timing.** Time the follow-up turn (agentd request-duration log line, or wall-clock the SSE stream to first `tool_call`/`chat_response`). A reused multi-k-token prefix collapses the prompt-eval phase → the post-restart turn's time-to-first-token should be ≈ a warm in-process continuation, **not** a cold full-prefill. Compare against a deliberately cache-busted turn (e.g. a brand-new thread same size) to calibrate hit vs miss.
  - **(c) Byte-identity self-check across the restart.** There is **no controller prompt artifact today** (the planner dumps `plan-turn-NN`; `create_controller_step` does not). To self-verify the head bytes live, EITHER capture TQP's request body (`instructions` + `input`) pre- and post-restart via a logging proxy / llama-server verbose request log and `diff` the common prefix, OR add a one-off `_debug_dump` in `create_controller_step` (mirror `engine.py:201`) for the session and diff the two `controller-turn` dumps. The prefix up through `conversation_history` must be byte-identical (that's exactly what the unit test asserts — this confirms it end-to-end on the wire).
- [ ] **Off-the-wire check:** `curl -s :8001/v1/chat/threads/<id> | python3 -m json.tool` does NOT contain `controller_conversation_history` or `controller_retrieval_seed` (internal substrate excluded), yet the follow-up still had context.
- [ ] **Handoff durability (optional):** after the restart, drive the same thread to a `create_task` pick → the planner receives the prior turns' tool results as `pre_explored_context` (uncapped, `read_file`/`search_code` results full) — i.e. it does NOT re-explore cold. (Confirms history-derived `explore_context` survives restart.)
- [ ] **Negative (optional, if a re-index can be forced):** if the snapshot is re-indexed during the restart window, the seed is STILL the pinned bytes (retrieval delta rides the history tail, never the seed) → head still matches. A divergent seed here would mean the pin regressed.

---

## Priority order for this session
Core (must pass): **J1, J3, J4, J5, J7, J9.**
Secondary (best-effort): J2, J6, J8, J10, J11.

## Results log

### 2026-06-15 — Phase J session 1 (tqp/qwen3.6 :11435, controller flag ON, backend :8001, worktree ext dev-host CDP :9335)

**Env notes:** TQP = llama-server (llama.cpp) on :11435 serving qwen3.6:35b-a3b (OpenAI-compatible `/v1/...`, NOT ollama `/api/tags`). `start-vscode-mcp.sh` EXT_PATH was stale (dead worktree) — repointed to this worktree. Extension `dist/extension.js` + `webview-ui/dist` must be BUILT (`npm run -w @ai-editor/vscode-extension build`) before launch; the dev-host needs a window reload after a build. **webview-ui has its OWN node_modules** (separate `npm install`). Command palette: `fill` overwrites the auto `>` prefix → must type `>Command`. The a11y `browser_snapshot` DOES pierce the webview iframes now (refs usable for `browser_click`); `browser_evaluate` (main-frame only) does NOT reach the cross-origin webview DOM.

**VERIFIED working live:**
- Controller active (no `intent_classified`/classifier in path). QA `answer` grounded (cited `PlanningLoop` `loop.py:70`).
- **J1** QA answer terminal ✓. **J9** gate durability across full window reload ✓ (gate re-rendered from `/live`). ACID instant-promote ✓ — picking "edit" landed a correct `src/mathutil.py clamp()` on the **real** workspace.

**🐞 Smoke-found + FIXED (commit `05e057c`):**
1. **No live thinking/tool pills** — `ControllerLoop` never broadcast `chat_agent_thinking`/`tool_call`/`tool_result` (frontend already maps them). Blank UI during turns. → broadcast added (first-iter thinking + per-tool pills). Verified live (`read_file ✓`/`search_code ✓` pills).
2. **Repeated "Thinking…"** entries → emit only on iteration 0. Verified ("Thinking (N steps)" single header).
3. **ModeGate never rendered** — `editor-client` `PendingGateSchema` Zod enum missing `mode`/`edit` (I1 changed TS types only); `ThreadLiveState.parse()` threw → `pollThreadLiveState` `catch` swallowed it silently. → added to enum + rebuilt editor-client. Verified (renders + survives reload).
4. **propose_mode invalid mode vocab** (qwen3: `recommended=None`, `options[].type` not `.mode`) → unusable gate. → validate options against `{edit,create_task,resume,explain}` w/ correction-retry (SM-style); normalize the non-blocking `recommended` to first option; tightened prompt w/ explicit format+example. Verified: gate now shows **Create inline now / Plan it as a task / Just explain** + discuss hint.
5. **Tool pills died on reload** — controller persisted nothing. → accumulate `AgentToolTrace`, persist `metadata.tool_events` (mirror `ChatAgent`/`trace_to_tool_events`).

**🐞 Smoke-found + FIXED (commit `1651b34`):**
6. **plan_sketch echoed input / no exploration** → prompt nudge: explore existing code first; make sketch concrete (path+signature+integration).
8. **Mode-choice breadcrumb not persisted** — `resolve_mode` broadcast-only → lost on reload. → persist+broadcast `"▸ You chose: <label>"` (mirror `write_chat_breadcrumb`).

**🔴 OPEN:**
7. **ModeGate "really ugly"** — needs a visual pass to match the other cards.

**Not yet driven:** J2 (clarify), J7 (create_task handoff end-to-end — step_review now wired, untested), J8 (discuss/refine), J10 (multi-turn context).

### 2026-06-16 — Phase J session 2 (durable-edit parity + live-render fixes; controller :8001 tqp/qwen3.6, worktree ext)

**Decision revised:** keep EditGate (live, interactive) + a durable INERT `diff_card` record (Class-A, mirrors StepGate) — did NOT drop EditGate. Chosen over "DiffCard canonical" to avoid forking DiffCard's button routing.

**🐞 Finding #9 — FIXED.** Root-caused into persistence (server-side) vs live-render (FE/broadcast):
- **Durable per-edit record**: `ControllerLoop.edit_record_cb` → `ChatController._edit_record_cb` persists an inert `diff_card` (resolved=applied/discarded, temp_path dropped) for every resolved edit; `submit_changes` persists summary+pills; `_present_mode_choice` now persists pills+thinking via shared `_turn_metadata`. Verified: DB + reload reconstruct the full edit turn (diff card "Changes ready/Applied" + breadcrumbs + summary).
- **`step_review` threaded** through `/mode-decision`→`resolve_mode` (per-thread stash) → edits honor "Review each step" (EditGate appears) ✓. Also wired into the `create_task` handoff.
- **Live-render gap (the "wasn't persisted" report)**: persistence was fine; live was dropping it. (a) `streamTurn` had no `chat_breadcrumb` branch → mode-choice/edit breadcrumbs only on reload → **added live render**; (b) review-mode wasn't broadcasting the inert card live → **now broadcasts in both modes** (fills the hole the cleared EditGate leaves).
- **Live streaming**: `ControllerLoop` now passes `on_thinking` (streams `tool_thinking_chunk`) + accumulates `thinking_log`.
- **Observability**: `ControllerLoop` now logs `[controller] iter/action`, tool_call/result, edit ops (had none — turns were invisible in logs).

**VERIFIED live (session 2):**
- **J1** QA ✓ (grounded, live pills/thinking). **J3** ModeGate ✓.
- **J4** review edit ✓: step_review gated → EditGate → Accept → **instant-promote to real disk** (`src/discount.py`, `src/taxutil.py`). Breadcrumbs + inert card render **live** AND on reload; DB single-copy (no dup).
- **J6** reject ✓: EditGate Reject → file unchanged on disk (shadow restored) → `✗ Edit rejected` breadcrumb live → agent revised + re-emitted → re-accept applied.
- **Multi-edit / multiple review screens** ✓: a 4-file docstring task with explicit "separate edit per file" → 5 EditGates (sq/discount/taxutil✗/taxutil✓/mathutil), traced in `[controller]` logs (`action=edit` per file + `submit_changes`). Batched task (no instruction) → 1 multi-file gate (model choice).
- Malformed `files=[None]` edit ops (qwen3.6) → caught by `except → PATCH FAILED → retry`, persisted nothing (DB diff_card count correct).

**create_task handoff completed** (post-session): goal = the agent's `plan_sketch` (conversation-aware synthesis, not the bare last message); `step_review` threaded through; `explore_context` now forwards the controller's accumulated tool results (ToolEventView → `{tool,args,result,is_error}`) as the planner's `pre_explored_context` — parity with the old ChatAgent large_change path.

**Still open:** ModeGate visual pass (#7); J7 end-to-end, J2/J8/J10 not driven.

### 2026-06-16 — Session 3 prep (restart-durable substrate + KV-cache head; backend changes, NOT yet smoked)

**What changed (code + unit tests, all green — `pytest` 782 passed/3 skipped):**
- **Durable seed_history.** `ChatThread.controller_conversation_history` persists the controller loop's verbatim turn history; `ChatController._seed_for` rehydrates it from the store on a cache miss (the restart path). Mirrors `TaskRecord.planning_conversation_history`. Was in-memory only → a backend restart dropped the conversation even though the transcript still showed it.
- **Pinned retrieval seed.** `ChatThread.controller_retrieval_seed` freezes the cache-prefix head; `_retrieval_seed` rehydrates the pinned bytes instead of recomputing (a re-indexed snapshot must NOT recompute it — that would break the KV prefix). Retrieval changes ride the history tail as delta notes. Mirrors `planning_initial_context`.
- **History-derived `explore_context`.** The `create_task` handoff now derives `pre_explored_context` from the verbatim history (uncapped, one source of truth) via `_explore_context_from_history`; the separate 4000-capped `_explore_by_thread` accumulator is **deleted**. Caveat: `is_error` defaults False (history shape carries no flag; error text is in the result).
- Both new fields are **excluded from all 3 thread serializers** (`_THREAD_INTERNAL_FIELDS` in `routes.py`) — off the wire, no strict-Zod risk, no bloat.

**Unit-locked (NOT a smoke pass — see line 3):** `tests/test_controller_durable_history.py` — persist, restart-rehydrate, uncapped history-derived explore_context, off-the-wire exclusion, **and `test_cacheable_head_byte_identical_across_restart`** (real builders `format_controller_system_prompt`+`build_controller_step_payload`; DB snapshotted at the post-turn-1 restart point; asserts the serialized head is byte-identical no-restart vs restart). Proven non-vacuous: disabling the seed pin → RED, disabling the history rehydrate → RED. The first run also caught a real test-design bug (shared DB doubled B's history).

**What J11 must still prove that units can't:** the actual **live KV-cache reuse on TQP** across a real agentd restart (byte-identical head ⇒ prefix hit ⇒ fewer prompt tokens evaluated). Units assert the head bytes the controller produces; they do NOT exercise the transport→tokenizer→llama-server slot match.

### 2026-06-16 — Phase J session 3 (LIVE) — J5 driven, CRITICAL edit-op bug found + fixed

Driven via raw-CDP-over-WebSocket against the chat webview OOPIF (`/tmp/cdp.js`): Playwright's `page.frames()` does NOT surface the `vscode-webview://` content frame (it's a `type:iframe` CDP target, not a Page), and a11y `browser_snapshot` stops at the nested iframe. The reliable driver resolves the webview target's `webSocketDebuggerUrl` dynamically (id churns on reload) and `Runtime.evaluate`s in it; sends use trusted `Input.insertText` + `Input.dispatchKeyEvent` (⌘↵, modifiers=4). Composer buttons are icon-only (match aria-label), history-list uses "New Chat" vs in-thread "New chat".

**🔴🔴 Finding #10 (CRITICAL — data integrity) — FIXED.** **J5 first run: false-success + real-disk pollution.** qwen3.6 emitted every `create_file` op with the **file body in the `file` (path) field** (`file=None` or `file="<code>"`). POSIX allows newlines in filenames → `TurnEditSession.apply` created a **garbage-named file** (name = the whole Python source) at the workspace root, auto-promoted it to **real disk**, and the transcript reported **"Applied / Created src/tax.py"** (summary comes from the model's `submit_changes`, NOT the diff). `shadow==real` held only for the junk file; the user's intent was unmet and the transcript lied. Compounded by a **repetition attractor**: `controller_loop.py:312` re-appended the malformed op into history → model copied its own bad op for **19 iterations** (good runs: 2–7) before `submit_changes` (still malformed). Three root defects, three-part fix:
  1. **No op-shape teaching (primary):** `controller_prompts.py` schema item was a bare `{"type":"object"}` and the EDIT prompt never showed the op shape. → constrained the `patch_ops` item schema (`op/file/content/search/replace/reason`) + added two concrete examples (create_file + search_replace) stressing *"file is a one-line workspace-relative PATH; code goes in content; every op needs a reason"*.
  2. **No path validation:** added `_validate_patch_ops` in `edit_session.py` — rejects ops whose `file` is missing, contains newlines/NUL, >255 chars, is absolute, or has `..` → routes to PATCH FAILED with actionable text instead of creating junk. (3 new unit tests in `test_turn_edit_session.py`.)
  3. **Echo attractor:** `controller_loop.py` PATCH-FAILED branch no longer echoes the malformed `patch_ops` into history (strips it before `assistant_turn`), and the feedback now teaches the file/content split instead of the misleading "check the exact search text".
  - **Secondary self-inflicted catch:** the first prompt example omitted the **required** `reason` field → `CreateFileOpV2` validation raised on every op (history-only PATCH FAILED, invisible in logs) → a new loop. Added `reason` to both examples. Lesson: prompt examples must be valid against the pydantic op models.

**✅ J5 (core) VERIFIED live after fix.** Review-OFF, "Add src/tax.py with with_tax": ModeGate (grounded sketch refs `src/taxutil.py`) → pick edit → **iter=0 emits `files=['src/tax.py']`, iter=1 `submit_changes`** (2 iters, no attractor) → **`src/tax.py` on REAL disk with correct content**, no EditGate (auto-accept), diff card records `paths=['src/tax.py']`, zero garbage files. Full backend suite **785 passed / 3 skipped**; ruff clean on touched files.

**Driving caveat learned:** the backend runs `--reload`; editing `agentd/*.py` between a send and its completion orphans the in-flight turn server-side and wedges the webview SSE ("A turn is in progress"). Do code edits only when no turn is in flight, then **Developer: Reload Window** to clear stale client SSE before re-driving.

**🟡 J7 (core) — handoff + pipeline VERIFIED live; task FAILED on a real workspace collision (not a controller/pipeline bug).** `task-5526ba947eb3`, review OFF. Confirmed live: ModeGate (recommended "edit" — minor: a 4-file refactor arguably should recommend create_task) → pick **"Plan as a task"** → `[chat→task] create_task_from_chat` → planning → **plan card ("Plan / 4 steps") with Implement/Give feedback in /live** → Implement → `plan/feedback` 200 → **`plan_document` markdown→JSON gen** (the "stuck at PLANNED" was this big structured call, `user_chars=78579`, ~2.5 min on TQP) → EXECUTING → **command gates** (run pytest) answered → VALIDATING → repair. **4/4 steps completed; `src/pricing/{__init__,discount,tax}.py` promoted to REAL disk.** Then FAILED: the workspace **already had** `services/agentd-py/tests/test_pricing.py`, so the new `tests/test_pricing.py` caused a pytest **"import file mismatch"**; the repair tried to delete the conflicting file → **scope gate** (out-of-plan-scope delete) → my gate-handler didn't click it (scope buttons are **"Approve"/"Deny"**, not command-gate "Allow once") → **600s scope timeout → auto-reject → FAILED**. So the controller→task handoff and the full gate/validation/repair/narrative/run_summary pipeline all work; the failure is a real env name-collision + a harness gap (now fixed: handler matches `Approve`). `run_summary` (4/4) + `failure_summary` (rich) + `task_narrative` all populated in `/result`.
  - **Minor finding:** `failure_summary` is `None` on `GET /v1/tasks/{id}` (TaskView) but rich on `/result` (TaskResult) — serializer inconsistency; not user-impacting (extension reads `/result`).
  - **NOT yet seen:** the READY_FOR_REVIEW → ReviewCard(narrative) → Finish → SUCCEEDED leg (run failed before review). A clean SUCCEEDED needs a non-colliding test filename + the `Approve` scope-handler.

**✅ J8 + J2 + J10 (secondary) VERIFIED live — one thread covered all three.**
- **J8 (soft-terminal gate):** sent "Add a helper to round prices…" → ModeGate. Instead of picking, typed "actually keep it minimal — no new file, just add it inline to the existing taxutil.py" → the loop **resumed** (`action=tool_call` re-explore → `clarify`), superseding the gate. Confirms the gate is soft-terminal ≈ plan-approval feedback.
- **J2 (clarify→resume):** the resumed loop emitted a **grounded `clarify`** — *"There is no file named taxutil.py in the workspace. The existing pricing utility is at …/util/pricing.py. Did you mean that file, or create a new taxutil.py?"* Replying "yes, use the existing pricing.py" **resumed the loop** → `propose_mode`. This is J2's core clarify≈feedback-resume assertion (J2's own first attempt, "fix the bug", couldn't trigger it — see finding #12).
- **J10 (multi-turn continuity):** the final `propose_mode` sketch read *"User confirmed using existing pricing.py"* / *"Add a new function inline to …/util/pricing.py"* — explicitly building on all **3 prior turns** (history replayed as seed_history). Append-only substrate confirmed live.

**🟡 Finding #12 (minor) — empty answer on ambiguous input.** **J2 first attempt:** "fix the bug" (no context) → controller emitted `action=answer` with an **empty `answer` string** → a blank agent bubble (just a Copy button, no text), not a `clarify`. The controller should guard against empty-answer terminals (fall back to clarify or a default message) and/or the DECIDE prompt should push `clarify` for under-specified change requests. Model-behavior-driven, but the empty bubble is a real UX gap. (Clarify itself works — proven by J8/J2 above.)

**Still not driven live:** J11 (restart KV-cache — heavy); J7 final review→Finish→SUCCEEDED leg (pre-existing Tier-B pipeline).
