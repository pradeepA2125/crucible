# Tier B + Task Narrative — Live Dev-Host Smoke (clubbed)

> Drive the real VS Code dev-host (worktree extension) via Playwright MCP against a live backend. Each **Run** is one task that clubs multiple target changes so coverage is dense. Mark `- [x]` per assertion as verified; record the commit/observation. **Never trust a green unit test as a smoke pass — this doc tracks observed UI behavior only.**

## Environment

- **Backend:** worktree `services/agentd-py` via `scripts/stress/start-backend.sh --backend turboquant --workspace <REAL ws OUTSIDE .tmp>` (graph indexing needs a non-`.tmp` ancestor — CLAUDE.md gotcha). Port :8001.
- **Dev-host:** VS Code on CDP :9335 (`scripts/playwright/start-vscode-mcp.sh` starts it + the Playwright MCP), second window with the **worktree** `--extensionDevelopmentPath` + same `--user-data-dir`, `aiEditor.backendBaseUrl=http://localhost:8001`.
- **Caveats (auto-memory):** `browser_wait_for` does NOT pierce webview iframes — use `browser_snapshot` + grep. Backend runs `--reload`: do NOT edit `agentd/*.py` while a task is in flight (hot-reload orphans it). Classifier under-scopes 2-existing-file goals to `small_change` — **include a NEW file in every goal to force `large_change`**.

## Target → Run coverage map

| Target | Runs |
|---|---|
| T-B1 Stop & keep (ABORTED, changes kept) | C |
| T-B2 Stop & revert (ABORTED, workspace rolled back) | B |
| T-B3 Discard all changes = true revert at review | D |
| T-B4 Finish → SUCCEEDED (kept) | A |
| T-B5 Durable run_summary on ReviewCard (survives reload) | A, D |
| T-B6 Durable failure_summary + run_summary on ErrorCard (survives reload) | E |
| T-B7 Dynamic "Review each step" toggle mid-run (both directions) | A |
| T-B8 ABORTED breadcrumb not clobbered by "Execution failed" | B, C |
| T-N1 ReviewCard shows narrative (headline + points) | A, D |
| T-N2 ErrorCard shows narrative ("attempted X, stopped at Y") | E |
| T-N3 Narrative outcome=aborted on abort | B, C |
| T-N4 Narrative mentions course-correction on delta replan | F (best-effort) |
| T-N5 Narrative as next-turn chat context | A |
| T-N6 Rich per-step note → distilled narrative (artifacts/run_events) | A (inspect) |

---

## Run A — Happy path: dynamic pref → review → Finish → next-turn context
**Goal (forces large_change):** "Add a `src/discount.py` module with `apply_percentage(price, pct)` and a `src/tax.py` module with `with_tax(price, rate)`, and a `tests/test_pricing.py` covering both." (new files ⇒ large_change; ≥2 steps.)

- [ ] **Submit** in a new chat thread; thread auto-titles.
- [ ] Plan card appears at `AWAITING_PLAN_APPROVAL`; click **Implement**.
- [ ] **T-B7a** mid-run with "Review each step" CHECKED: a **StepGate** card appears after step 1 with tabbed diff panes.
- [ ] **T-B7b** UNCHECK "Review each step" in the composer mid-run → next step **auto-accepts** (no gate; `✓ Step completed` breadcrumb).
- [ ] Re-CHECK mid-run → the following step shows a StepGate again. (toggle both directions live)
- [ ] Accept remaining steps → reach **READY_FOR_REVIEW**; ReviewCard appears.
- [ ] **T-B5** ReviewCard shows "**N of M steps**" + any deviations (run_summary).
- [ ] **T-N1** ReviewCard shows a **narrative headline + bullet points** describing the change (names discount.py / tax.py).
- [ ] **Reload Window** (Cmd+Shift+P → Developer: Reload Window) → reopen chat → ReviewCard **still** shows run_summary + narrative (durable, not extension-memory).
- [ ] **T-B4** Click **Finish** → status SUCCEEDED; `src/discount.py`, `src/tax.py`, `tests/test_pricing.py` exist in the real workspace; `✓ Task finished` breadcrumb.
- [ ] **T-N6** Inspect `<ws>/.agentd/artifacts/<task>/…` or the task record `run_events`: per-step `note`s are detailed accounts (not one-liners); narrative `points` are distilled from them.
- [ ] **T-N5** Send follow-up: "what did you just change?" → agent's reply references the prior task (narrative in history/recent_task), not a blank explore.

## Run B — Stop & revert + narrative aborted + no-clobber
**Goal:** "Add a `src/inventory.py` with a `restock(item, qty)` function and a `tests/test_inventory.py`." (new files ⇒ large_change)

- [ ] Submit, approve plan, let execution begin (1 step lands / partial promote visible).
- [ ] Click work-bar **Stop & revert**.
- [ ] **T-B2** Status → ABORTED; `src/inventory.py` (and any partial files) **gone** from the workspace (rolled back to pre-task state).
- [ ] **T-B8** Transcript shows `✗ Run reverted — workspace rolled back…` and **NO** "Execution failed: …" line after it.
- [ ] **T-N3** ErrorCard (or transcript narrative) shows outcome=aborted, "attempted … stopped".

## Run C — Stop & keep
**Goal:** "Add a `src/coupon.py` with `redeem(code)` and `tests/test_coupon.py`." (new files)

- [ ] Submit, approve, let ≥1 step complete + partial-promote.
- [ ] Click work-bar **Stop & keep**.
- [ ] **T-B1** Status → ABORTED; the already-applied file(s) **remain** in the workspace.
- [ ] **T-B8** Transcript shows `✗ Run stopped — changes applied so far were kept.` (no "Execution failed").

## Run D — Discard all changes = true revert at review
**Goal:** "Add a `src/shipping.py` with `cost(weight, zone)` and `tests/test_shipping.py`." (new files)

- [ ] Submit, approve, reach READY_FOR_REVIEW.
- [ ] **T-B5/T-N1** ReviewCard shows run_summary + narrative (re-verify).
- [ ] Click **Discard all changes** → enter a reason → confirm.
- [ ] **T-B3** Status → ABORTED; `src/shipping.py` **gone** (true revert, not "keep"); `✗ All changes discarded — workspace rolled back…` breadcrumb.

## Run E — Failure → durable ErrorCard + narrative  *(harder to force)*
**Goal (provoke a failure):** validation-profile `full` on a workspace whose suite the agent can't make pass, OR a goal that exhausts retries. Fallback: kill the backend mid-execution, restart, reload — verify the persisted ErrorCard.

- [ ] Drive the task to a **FAILED** terminal.
- [ ] **T-B6** ErrorCard shows `failure_summary` (failing step + error class) **and** `run_summary` ("got through k of n").
- [ ] **T-N2** ErrorCard shows a narrative ("attempted …, stopped at step k — <reason>").
- [ ] **Reload Window** → ErrorCard still shows failure_summary + run_summary + narrative (durable).

## Run F — Delta replan → narrative course-correction  *(best-effort, non-deterministic)*
**Goal:** something where the first step's approach is provably wrong so the execution agent emits `revision_needed`.

- [ ] Observe a `revision_needed` / delta-replan in the live stream (work-bar / tool pills).
- [ ] **T-N4** Final narrative mentions the course-correction (e.g. "initially …, then revised to …"), and reverted-step work is NOT listed as done.

---

## Results log

### 2026-06-14 — env set up + Run A executed (worktree build, turboquant/qwen3.6, :8001, shadow-forge-stress)
Driving via Playwright **CDP frame-eval** (`page.frames()` → `fake.html` webview frame) — the a11y snapshot can't pierce the sandboxed webview; this matches the repo's `drive-chat.js`.

**Verified live (worktree extension + worktree backend):**
- Env: worktree `EXT_PATH` dev-host on :9335 + worktree agentd-py backend on :8001 (Tier B routes present), real indexed `shadow-forge-stress`.
- Tier A history enrichment chips (Review/Running/Failed) + counts + dates render.
- Composer **"Review each step"** toggle present and **stays enabled during EXECUTING** (Tier B dynamic checkbox).
- Full `large_change` pipeline: explore → classify → **plan card (Implement/Give feedback)** → approve → execution **work-bar timer** + `✓ Plan approved` breadcrumb → **step gates (Accept)** + **command gates (Allow once, shell-policy=ask)** + step diff records ("Changes ready / Applied") → READY_FOR_REVIEW.
- **T-B Stop & keep / Stop & revert** buttons render on the work-bar during EXECUTING (cooperative-abort affordance).
- **T-B4 + T-B3** ReviewCard shows **Finish** + **Discard all changes** (relabeled).
- **T-B5** ReviewCard "3 of 3 steps completed".
- **T-N1** ReviewCard renders the **task narrative** (headline + 4 distilled points naming pricing.py/`__init__.py`/13 pytest cases/ruff) — screenshot `smoke-runA-reviewcard-narrative.jpeg`.
- **T-N6** per-step notes are detailed; narrative points are distilled from them (rich-capture → summarize design confirmed).
- **T-N5 substrate** narrative also persisted as a standalone `agent/text` transcript message (feeds next-turn history).

**🐞 Smoke-found bug (FIXED):** the **live** Review/Error card missed the durable `run_summary`+`task_narrative` on first render (it used ephemeral fallbacks; narrative absent). Root cause confirmed via `/live` (both present there): the engine saves `status=READY_FOR_REVIEW` **before** the `finally` synthesizes the narrative (an LLM call, seconds later), so it arrives on a poll with unchanged status; `pollThreadLiveState`'s dedup signature `{taskId,status,gate,plan}` locked and never re-rendered. **Fix `7013044`:** include `runSummary`/`task_narrative`/`failure_summary` in the signature (+ regression test `late-arriving task_narrative re-renders…`). Verified: after fix + reload, the narrative renders on the ReviewCard. *(unit tests exposed the data but missed the live timing/dedup interaction — classic smoke catch, like Tier A's `e7b5f39`.)*

**Not yet exercised (need more full task runs / timing):** T-B1 (Stop&keep terminal), T-B2 (Stop&revert rollback), T-B3 (Discard *executed* revert), T-B6 + T-N2 (failure → ErrorCard durable + narrative), T-B7 (toggle *flipped* mid-run both ways), T-N3 (narrative aborted), T-N4 (delta replan), T-B8 (no-clobber). Buttons/affordances for these are all rendering; the terminal behaviors remain to drive.
