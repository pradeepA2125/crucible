# Memory Harness Phase 2 — Live Smoke Test Plan

**Date:** 2026-06-29 · **Branch:** `feat/memory-harness` · **Provider:** TurboQuant (weak local model — the highest-risk surface)

## Why live (what the unit tests can NOT prove)

The 80+ unit/integration tests prove the *logic* with fake embedders and stub engines. They do
**not** prove the things that actually bite in production:

- **Consolidation prompt quality on a real weak model** — the single biggest risk. Phase 1 taught
  us the weak model echoed its JSON payload as the "summary." The consolidator uses the
  schema-constrained `generate_json` path which *should* prevent that, but classification quality
  (episodic vs semantic vs procedural), entity extraction, importance sanity, and the
  "what-NOT-to-remember" noise filter are all unverified live.
- **Real embeddings** — tests use orthogonal fake vectors. Does `bge-small` actually rank relevant
  memories above irrelevant ones for real coding queries?
- **The real wiring path** firing end-to-end: factories → harness → controller loop → tools →
  prompt, with `sqlite-vec` loaded in the real backend process and `bge-small` actually loading.
- **KV-cache stability** — does `recalled_memories` in the tail keep the prefix cache warm
  turn-over-turn (finding #13)?
- **No event-loop freeze** — daemon warmup + `to_thread` embedding under a real turn.

## Setup

```bash
export $(cat .env | grep -v '^#' | grep '=' | sed 's/"//g' | xargs)
export CRUCIBLE_MEMORY_ENABLED=1
export CRUCIBLE_MEMORY_WINDOW_TOKENS=84000   # 0.65 trigger ≈ 54.6k — makes compaction fire fast
bash scripts/stress/start-backend.sh --backend turboquant \
  --workspace "$PWD/workspaces/shadow-forge-stress" --validation-profile none
```
- Workspace: **`shadow-forge-stress`** (outside ignored-dir ancestors; ~4.3k indexed nodes).
- Confirm `CRUCIBLE_CHAT_CONTROLLER=1` (memory requires the controller path).
- DB to watch: `services/agentd-py/.crucible/state/memory.sqlite3` (or workspace `.crucible/state/memory.sqlite3` —
  confirm which the process opens via `CRUCIBLE_MEMORY_DB_PATH`).

## Observation toolkit

```bash
# memories written
sqlite3 .crucible/state/memory.sqlite3 "SELECT kind, importance, substr(content,1,80), entities,
  source_kind, source_seq_lo, source_seq_hi FROM memories WHERE valid_to IS NULL;"
# supersede chains
sqlite3 .crucible/state/memory.sqlite3 "SELECT id, valid_to, superseded_by FROM memories WHERE valid_to IS NOT NULL;"
# vec + fts populated
sqlite3 .crucible/state/memory.sqlite3 "SELECT count(*) FROM vec_memories; SELECT count(*) FROM memories_fts;"
# logs
tail -f .tmp/stress-*/logs/agentd.log | grep -iE "\[memory\]|compacted|consolidat|recall|sqlite-vec"
# per-turn artifacts (recalled_memories should appear in user_payload TAIL)
ls workspaces/shadow-forge-stress/.crucible/state/artifacts/chat/<thread>/<turn>/controller-turn-*.json
```

---

## Test matrix

### A. Startup & flag-gating
- [ ] **A1** Backend boots clean; `memory.sqlite3` has `memories` + `vec_memories` + `memories_fts`.
- [ ] **A2** Log shows sqlite-vec loaded (NOT the `[memory] sqlite-vec unavailable` warning).
- [ ] **A3** Embedder warmup runs in background — no multi-second freeze on the first turn; backend
  responsive immediately after boot.
- [ ] **A4** A controller turn's `controller-turn-00.json` `system_instructions` contains the
  **MEMORY block** and `tool_definitions` include **`remember`** + **`recall`**.
- [ ] **A5 (gate off)** Restart with `CRUCIBLE_MEMORY_ENABLED` unset → no MEMORY block, no
  memory tools, `memory.sqlite3` untouched, controller behaves exactly as before.

### B. Write path — automatic consolidation triggers
- [ ] **B1 (compaction trigger)** Drive a long conversation (reuse the `drive_memory_*.py` pattern:
  read big files) until compaction fires → within seconds a `[memory] ...consolidat...` log fires
  → new rows in `memories` (scope=workspace, source_kind=consolidation, `source_seq_lo/hi` set).
- [ ] **B2 (edit-promote trigger)** Make an inline edit via the controller (a real `submit_changes`
  turn) → consolidation fires for that turn → a memory reflecting the edit/decision.
- [ ] **B3 (QnA does NOT trigger)** Ask a pure question ("what does X do?") → **no** new memory,
  **no** consolidation log. (Confirms QnA exclusion.)
- [ ] **B4 (best-effort)** Consolidation never blocks the turn — the turn's `chat_done` arrives
  before/independent of the consolidation log line.

### C. Consolidation QUALITY — the high-risk surface (mirror the summarizer smoke)
Pull the written memories and judge each:
- [ ] **C1 (form)** Memories are coherent prose, NOT echoed JSON / payload fragments. (The Phase-1
  failure mode — verify the `generate_json` path actually prevents it live.)
- [ ] **C2 (classification)** `kind` is right: a fact about the code = semantic; a session event =
  episodic; a how-to = procedural. Spot at least one of each if the session warrants.
- [ ] **C3 (atomicity)** One fact per memory, not paragraphs.
- [ ] **C4 (entities)** `entities` are verbatim paths/symbols actually present in the content.
- [ ] **C5 (importance)** 1–10 and sane (project-wide fact high, trivia low); all within [1,10].
- [ ] **C6 (noise filter)** No spam memories about chit-chat / tool mechanics / obvious-from-code.
- [ ] **C7 (dedup)** State the same fact across two turns → only ONE live memory (cosine ≥ 0.92).
- [ ] **C8 (supersede)** Establish a fact, then change it ("actually we now use Y, not X") → old
  memory `valid_to` set + `superseded_by` → new; only the new one recalls. Episodic never retires.

### D. Read path — recall
- [ ] **D1 (injection)** A turn whose query matches a stored memory → `controller-turn-*.json`
  `user_payload` has a `recalled_memories` key **in the tail** (after `conversation_history`).
- [ ] **D2 (relevance)** Recalled memories are actually relevant to the query (real bge-small
  ranking), not random.
- [ ] **D3 (floor)** An unrelated query → `recalled_memories` absent/empty (relevance floor).
- [ ] **D4 (cross-session — the headline)** Close the thread; open a **NEW** thread in the **same
  workspace**; ask about a fact stored earlier → it is recalled. ("Sturdy between windows.")
- [ ] **D5 (recall tool)** Prompt that makes the model call `recall(query)` explicitly → returns
  memories; `verbatim=true` appends the linked segment source text.
- [ ] **D6 (remember tool)** Prompt that makes the model call `remember(...)` → memory stored with
  `source_kind=agent_tool`. (Confirms the prompt teaching actually steers tool use.)

### E. Failure / degradation paths
- [ ] **E1 (consolidation LLM fail)** Force a provider error mid-consolidation (or observe a real
  one) → no memory written that round, turn unaffected, segments still on disk (re-distillable).
- [ ] **E2 (malformed output)** If the model emits a bad candidate → it's skipped, others kept,
  turn fine. (Hard to force; watch for it.)
- [ ] **E3 (sqlite-vec absent)** Restart with a Python lacking the extension (or monkeypatch) →
  `[memory] sqlite-vec unavailable` warning, backend boots, recall still works via FTS5+structural,
  no crash. (Validates the Phase-1-store-safety guard live.)
- [ ] **E4 (embedder absent)** Simulate model-load failure → recall degrades to FTS5+structural,
  consolidation stores `embedding=NULL`, no freeze/crash.

### F. KV-cache & performance
- [ ] **F1 (cache stability)** With TQP `timings{prompt_n, cache_n}`: across consecutive turns where
  only the tail (`recalled_memories`/`goal`) changes, `cache_n` stays high — recalled memories in
  the tail do NOT break the cached prefix.
- [ ] **F2 (no freeze)** First recall/consolidation (model load) does not stall other turns/SSE
  (daemon warmup + `to_thread` working).
- [ ] **F3 (recall latency)** Per-turn recall adds acceptable latency (embed one query + ANN + FTS).

### G. Scoping
- [ ] **G1** Memories carry `scope_kind=workspace`, `scope_id=<this workspace>`.
- [ ] **G2** Point the backend at a DIFFERENT workspace (separate `memory.sqlite3` or scope_id) →
  the first workspace's memories are NOT recalled. `global` scope has zero rows (never written).

---

## Pass / fail bar

**Must pass to call Phase 2 live-verified:** A1–A5, B1, B3, C1, C2, C7, D1, D4, E3. These cover the
wiring, the trigger cadence, the no-echo guarantee, dedup, tail injection, the headline
cross-session recall, and the don't-crash guard.

**Known risk going in (budget time, expect iteration):** **C2/C6 — classification + noise filter on
the weak model.** Phase 1 needed prompt iteration to stop the JSON echo; expect the consolidation
prompt to need a similar tuning pass (few-shot tweaks, sharper "what NOT to remember"). Capture
bad examples; they drive the next prompt revision exactly like the summarizer fix.

**Out of scope (deferred, dormant):** task-terminal consolidation trigger + task-loop recall
injection (task subsystem off by default).
