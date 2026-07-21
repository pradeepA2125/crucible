# Chat controller live-smoke findings — 2026-07-17

## Context

Started as a single question — "alpha-forge's latest thread is struggling to enter edit mode" — and
turned into a full day of live-fire debugging against a real backend (`CRUCIBLE_CHAT_CONTROLLER=1`,
TurboQuant/qwen3.6:35b-a3b-q4_K_M) driving the `executing-plans` skill against a real multi-task
implementation plan in a separate workspace (`~/projects/alpha-forge`). Driven via raw CDP-over-
WebSocket against a second, disposable VS Code Extension Development Host (see
`smoke_controller_cdp_driving_recipe` in memory), with a background bash loop auto-resolving
edit/command/mode gates via the direct decision APIs so long unattended runs could be observed.

Nine confirmed bugs found and fixed, all TDD'd against `services/agentd-py`'s test suite (1336
passed / 0 failed at session end, up from 1333 at session start). Two more findings are real but
left open (context-compaction miscalibration, `.gitignore` gap). All fixes are uncommitted on
`main` in this repo as of session end.

**End state of the smoke test itself**: Phase 1 of the alpha-forge plan (7 tasks: scaffold, uv
deps, TimescaleDB models, Redis client, EventBus, ingestion service, Docker/CI config) is complete
and committed (`b04c7e5`), with 18/18 pytest tests genuinely passing — independently re-verified by
running `pytest` directly, not by trusting the agent's own claims.

---

## Fixed this session (all in `services/agentd-py`)

### 1. `edit`/`propose_mode` misrouted as `tool_call`

**File**: `agentd/chat/controller_loop.py`

**Symptom**: the model emitted `{"type":"tool_call","tool":"edit",...}` instead of the required
top-level `{"type":"edit",...}`. `edit` (and `propose_mode`) are phase-gated top-level response
types, never real tools — but the `tool_call` schema's `tool` field is a free string, not
constrained to the registry, so this passes schema validation. The registry's generic
`Error: unknown tool 'edit'` (26 chars) gave the model no actionable signal, and — critically — this
path never incremented `consecutive_malformed`, so it could retry the identical illegal call for
the full `max_iters` budget (500). Observed live: 35+ minutes of retries burning a full ~13.5k-token
regeneration each time, zero progress.

**Root cause**: nothing in the prompt distinguished "a real tool" from "one of this schema's own
response types," and the model generalized the `tool_call`/`write_todos` shape (which it had just
used correctly) onto `edit`.

**Fix**: `_reserved_tool_name_correction()` — a dispatch guard mirroring the existing
`run_command`-in-DECIDE guard, intercepting `tool_call` where `tool ∈ {answer, clarify,
propose_mode, edit, submit_changes}` with a message naming the fix. Wired into the existing
malformed-response correction chain so it now correctly bounds via `_MAX_MALFORMED`. Plus a
negative few-shot example in the `tool_call` variant block of the system prompt.

**Verified**: `tests/test_controller_reserved_tool_name.py` (3 tests). Confirmed live 3+ times
across the session — the guard fired for `edit` twice and `propose_mode` once, each time
self-correcting within one retry.

---

### 2. Narrate-without-act via `answer`

**File**: `agentd/chat/controller_prompts.py`

**Symptom**: the model repeatedly used `type: "answer"` to describe an action it was about to
take ("Let me start by reading the design spec...") instead of taking it. `answer` is terminal —
it ends the turn — so every one of these narrations wasted an entire round-trip, requiring the
user to say "go ahead" again just to get the actual action.

**Root cause**: nothing in the prompt distinguished "narrating an intent" from "delivering a real
answer." Also found: after a `read_skill` call returns the skill's instructions, the model would
summarize those instructions back as an `answer` instead of continuing to act on them in the same
turn.

**Fix**: explicit rule + negative few-shot in the `answer` variant block ("`answer` ENDS THE TURN
— never use it to describe a next step you haven't taken"), plus a follow-through rule appended
right after the `read_skill` teaching.

**Verified**: 211 controller/prompt tests pass, no regressions (prompt-only change, not
independently unit-testable for behavior — this is the one fix in the session without a
behavioral test, since it's a live-LLM-adherence question). Live: the pattern *did* recur once
after this fix (this thread's history was already saturated with the bad pattern, biasing
continuation) — noted as a limitation, not a failure of the fix.

---

### 3. Dedup cache (`seen`) never clears — permanent deadlock on legitimate retry

**File**: `agentd/chat/controller_loop.py`

**Symptom**: the model ran `uv add fastapi ...`, hit a real build failure, correctly diagnosed and
fixed the root cause (`pyproject.toml` missing `[tool.hatch.build.targets.wheel]`), then needed to
retry the *identical* `uv add` command — the objectively correct next step. The dedup guard (`seen`,
a per-turn `(tool, args)` cache) rejected it as `DUPLICATE BLOCKED` every single iteration, forever,
with no way to signal "the workspace state changed, this isn't a mindless repeat." Observed live:
**35+ consecutive wasted iterations (~30 minutes), zero progress**, until manually stopped.

**Root cause**: `seen` is populated once per `run()` call and never cleared, anywhere. A
successful `edit` (which changes workspace state) had no effect on it.

**Fix**: `seen.clear()` fires whenever an edit is accepted — mirrors the existing
`emit_patch`-dedup-clears-on-every-transition pattern already used in `verify_phase_sm.py` for the
task-execution pipeline (a sibling ReAct loop that had already solved this exact class of problem).

**Verified**: `tests/test_controller_loop_dedup_clears_on_edit.py` (2 tests: fix confirmed, and a
negative-control confirming back-to-back identical calls with *no* edit in between are still
correctly blocked). This was the single highest-value fix in the session — it's what actually let
the smoke test complete Phase 1.

---

### 4. No escalation on repeated identical rejections

**File**: `agentd/chat/controller_loop.py`

**Symptom**: after a `continue` message started a fresh turn (which resets to `DECIDE` phase by
design — see open items below), the model tried `run_command` immediately, 5 times in a row,
getting the identical `run_command is not available while deciding...` rejection each time,
without ever adapting to call `propose_mode`. This exhausted `_MAX_MALFORMED` (3) and failed the
whole turn: `ControllerLoopExhausted: 4 consecutive malformed responses`.

**Root cause**: the correction message was static — same wording every time, no signal that the
retry budget was shrinking.

**Fix**: from the 2nd consecutive malformed response onward, prepend `⚠ Retry N/3 — M attempt(s)
left before this turn fails outright.` to whatever correction is already being returned.

**Verified**: `tests/test_controller_loop_escalating_correction.py` (2 tests). This is a generic
safety net — it doesn't guarantee the model adapts, but it fails fast and visibly instead of
silently burning the whole budget.

---

### 5 & 6. PyTorch MPS deadlock — two distinct races, both in the memory harness

**Files**: `agentd/memory/harness.py`, `agentd/memory/embedder.py`, `agentd/memory/reranker.py`

**Symptom**: the backend process would freeze or silently die (no Python traceback — a native-level
fault) shortly after starting, or during a chat turn. Reproduced **3 times** across the session.
Diagnosed via two independent `sample <pid>` snapshots taken seconds apart showing **identical**
leaf stack frames — proof of a true deadlock, not just slow computation — both stuck inside
PyTorch's `MetalShaderLibrary` internal shader cache (`at::native::mps::copy_cast_kernel_mps` →
`std::__hash_table::__emplace_unique_key_args`).

**Root cause, part 1**: `build_memory_harness()` spawned **two separate daemon threads
simultaneously** — one warming the embedder (`SentenceTransformer`), one warming the reranker
(`CrossEncoder`) — both racing to initialize PyTorch's MPS backend for the first time
concurrently, with zero synchronization between them. PyTorch's MPS backend is documented as not
thread-safe for concurrent lazy first-init.

**Fix, part 1**: consolidated into one thread (`_warmup_all()`) that warms the embedder then the
reranker sequentially — never two racing threads.

**Root cause, part 2** (found after the first fix still didn't fully resolve the crash — a second,
separate race): `Embedder._encode()` and `Reranker._score()` both did an **unlocked**
double-checked-locking pattern: `if self._model is None: self._model = Construct(...)`. The
background warmup thread and a *real turn's* demand-triggered call could both see `None`
concurrently and both construct a model — reopening the same MPS race via a completely different
pair of call sites.

**Fix, part 2**: `threading.Lock` around the lazy-construct-and-use path in both `Embedder` and
`Reranker`, with a proper re-check inside the lock.

**Verified**: `tests/test_memory_harness_warmup_no_race.py` and
`tests/test_memory_lazy_load_thread_safety.py` (4 tests total, all reproduce cleanly pre-fix —
4 concurrent constructions instead of 1 — and pass post-fix). Live: survived a 90-second stress
window of `/live` polling that had reliably crashed the process within ~15-20s before, twice.

**Note**: even after both fixes, one further crash occurred that was traced to something else
entirely (see Open Items — corrupted managed-runtime venv, unrelated to MPS). Per the
systematic-debugging skill's "3+ fixes failed → question the architecture" rule, this was
escalated to the user rather than guessed at a 4th time; the user's call was to move off the
manually-managed runtime venv onto the canonical `scripts/stress/start-backend.sh` (repo-local
`.venv`, `--reload`), which resolved the recurrence entirely for the rest of the session.

---

### 7. Todo-list compression silently drops the TDD verification step

**Files**: `agentd/chat/controller_prompts.py`, `agentd/chat/todo_source.py`

**Symptom**: across the *entire* session (60+ agent iterations, several completed plan tasks), the
agent created multiple `tests/test_*.py` files as part of file-creation edit batches but **never
once ran pytest** — 0 pytest invocations out of 8 total `run_command` calls, all `uv`-related.

**Root cause** (independently confirmed by a Fable-5 subagent cross-check, see below): when the
model translates the plan's explicit TDD sub-steps ("write the failing test / run it / implement /
run it again") into its own `write_todos` checklist, it compresses each Task into one item **per
file to create** — e.g. `"Create Configuration Module (core/core/config.py) and its test"`. The
word "test" appears only as a noun (the file being created), never as an action. Since the todo
list is the model's de facto per-turn execution driver, and the reconcile-checkpoint instruction
**actively proposed** ("Did this edit COMPLETE it? → mark 'done', cite this edit in 'note'")
treating an applied edit as sufficient completion evidence, the verification step silently fell out
of the effective checklist. The one item that *was* action-shaped ("Run lint check and verify")
was the one verification that actually ran — direct proof that action-shaped items drive behavior
correctly when present.

The sibling task-execution pipeline (`agentd/tools/verify_phase_sm.py`) had already solved this
exact class of problem — schema-level filtering that removes `verify_done` from the allowed action
set until the state machine passes through a real test run — but the newer chat-controller
`write_todos` path never adopted the equivalent principle.

**Fix** (3 coordinated prompt changes, all in the same negative-example style as fix #1):
- TODO LIST POLICY: *"An applied edit proves a file EXISTS, not that it WORKS... the verification
  RUN is its own todo item — never folded into the create-the-file item"* + worked WRONG/RIGHT
  example.
- Reconcile checkpoint (both the named-item and generic variants): appended *"— but if `<title>`
  includes tests or a verify step, an edit is NOT completion: run_command the verification first,
  or leave it 'in_progress'"* to the YES branch.
- `write_todos` tool description: *"A 'run tests/verify' step... is always its OWN item; its
  'done' evidence is the command output, never an edit result."*

**Verified**: 202 controller/todo tests pass, no regressions (prompt-only). **Live validation is
the strongest evidence in this session**: immediately after this fix landed (hot-reloaded via
`--reload`, no restart needed), the very next turn ran `uv run pytest tests/ -v` for the first
time ever — surfaced two real, previously-invisible bugs (see #8 below) — and the model then
self-directed multiple further `run_command pytest` calls across the rest of the session to verify
its own work, reaching 18/18 passing tests.

---

### 8. Real application bugs found once tests actually ran (confirms the earlier static review)

Not a controller/prompt bug — these are bugs in the *generated application code*, surfaced only
once fix #7 made pytest actually run. Listed because a full static plan review (done earlier,
before any code executed) had already predicted several of these from reading alone, and having
live execution confirm them is itself a finding about the value of the static review:

- `ModuleNotFoundError: No module named 'core.config'` — `core/core/config.py` vs. every import
  site expecting `core.config` (predicted in the static review).
- `ImportError: cannot import name 'TIMESTAMPTZ' from 'sqlalchemy.dialects.postgresql'` — a
  hallucinated SQLAlchemy symbol, invisible until an actual import was attempted (new finding, not
  predicted).
- `AttributeError: 'AsyncConnection' object has no attribute '_run_ddl_visitor'` — invalid
  SQLAlchemy 2.0 async usage, calling `Table.create(conn)` / `metadata.create_all(engine)` directly
  instead of `conn.run_sync(...)` (predicted in the static review, specific mechanism confirmed
  live). The model iterated through 3 different wrong approaches before landing on a working
  sync-engine sidestep for the test.
- `NameError: name 'pytest' is not defined` — a self-inflicted regression where a `search_replace`
  edit accidentally dropped the `import pytest` line while `@pytest.mark.asyncio` decorators
  remained in use. Self-corrected on the next iteration.
- `TypeError: 'set' object...` in `EventBus.publish()` — a genuine application bug, set
  comprehension used where a dict comprehension was needed for the message payload. Found and
  fixed via real test failure, not predicted by the static review (it's a runtime logic bug, not
  visible from reading).
- Missing `[tool.hatch.build.targets.wheel]` in `pyproject.toml`, discovered when `uv add` failed
  to build the project as an editable package (hatchling can't auto-detect the wheel layout for
  this project structure). Root cause of finding #3 above.

All are now fixed in the `alpha-forge` workspace and covered by the 18 passing tests.

---

## Open items — real, not fixed this session

### A. `CRUCIBLE_MEMORY_WINDOW_TOKENS` defaults to 128000 but doesn't match the real serving context

Neither `CRUCIBLE_MEMORY_WINDOW_TOKENS` nor `CRUCIBLE_MEMORY_COMPACT_TRIGGER_FRAC` were set for
this deployment, so the memory harness used the documented default (128000), while the actual TQP
server was started with `-c 98304` (`scripts/start-tqp.sh`). Compaction's trigger math
(0.65 × window) was calibrated against the wrong ceiling. Corrected mid-session
(`CRUCIBLE_MEMORY_WINDOW_TOKENS=90000` on restart via `start-backend.sh`), but the underlying
long-running thread **still hit context overflow again** even after the correction
(`98577 > 98304`), and `grep -c "memory] compacted"` against the full session log returned **0** —
compaction may never have fired for this thread at all across its entire multi-hour lifetime. Not
root-caused. Worth a dedicated investigation: is this a genuine compaction bug (never triggers),
or does a single fat turn's payload (large retrieval seed + already-large carried-forward history)
simply blow past the ceiling before compaction gets a chance to run, given it's only checked at
iteration start?

### B. `.gitignore` gap — `.crucible/` internal state got committed

The Phase 1 completion commit (`b04c7e5` in `alpha-forge`) swept up `.crucible/state/agentd.log`,
`agentd.sqlite3` (+`-shm`/`-wal`), and dozens of per-turn debug artifact JSON files via `git add
-A`. The plan's own `.gitignore` step (Task 1) never anticipated `.crucible/` since it predates the
plan. Should be added to `.gitignore` and removed from history if this workspace is used further.
This is specific to the `alpha-forge` test workspace, not a bug in this repo.

### C. Lint claimed "done" without being fully clean

The "Run lint check and verify" todo item was marked `done` earlier in the session, but a final
`ruff check core/ tests/` at session end still showed 18 errors (15 auto-fixable). This is the same
class of problem as finding #7 (evidence-of-done not matching reality) but for lint rather than
tests — the fix for #7 targets tests/verify steps specifically; lint-claim accuracy wasn't
independently addressed and may need the same treatment if it recurs.

### D. Fresh-turn phase reset forces `propose_mode` every message, by design — friction observed live

Every new user chat message starts a brand-new `ControllerLoop.run()` in `DECIDE` phase (documented,
intentional — each turn is a fresh mode-selection point). This is *not* a bug, but it was the
direct trigger for finding #4 (the model, mid-plan-execution, tried to skip straight to
`run_command` after a `continue` message rather than re-proposing mode) and added real friction —
several `continue` messages needed an explicit "propose mode... pick edit" nudge to reliably avoid
the DECIDE-phase rejection loop. Worth considering whether a resumed multi-task execution should
have a lower-friction re-entry path, but that's a bigger design question than was addressed this
session.

### E. Managed-runtime venv corruption (found, worked around, not root-caused)

Mid-session, `~/.crucible/runtime/venv` was found stripped back to a bare shell (no `pip`, no
`uvicorn`, 0 installed packages) despite `install-state.json` claiming `agentd: "0.2.1"` was
installed — a real state/reality mismatch in the P4 managed-runtime installer's resume logic.
Worked around by reinstalling via `uv pip install -e .[memory]` directly and, ultimately, by
switching to the canonical `scripts/stress/start-backend.sh` (repo-local `.venv`) instead of the
managed runtime venv for the rest of the session. The actual trigger (what stripped the venv) was
never identified — plausibly the extension's own `RuntimeManager` attempting an automatic
reinstall/repair after repeated health-check failures, but not confirmed.

---

## Process notes (how this was actually found)

- **`sample <pid>` with two independent snapshots, comparing leaf frames**, is the technique that
  actually distinguished "genuinely deadlocked" from "just slow" for the MPS crash — two samples
  taken seconds apart showing byte-identical leaf frames is unambiguous proof of a hang.
- **Direct decision APIs** (`POST /edit-decision`, `/command-decision`, `/mode-decision`) turned
  out far more reliable for a long unattended run than CDP-clicking the UI buttons each time — a
  ~40-line bash polling loop auto-resolved every gate kind and let the session run for hours
  without manual intervention per-gate.
- **Never trust the model's own "done" claims** — every "N/N done" or "tests pass" claim in this
  session was independently re-verified by directly running `pytest`/`ruff`/`git status` myself.
  This caught two of the most important findings (#3 dedup deadlock, #7 TDD gap) that the model's
  own turn summaries gave no indication of.
- **A second, independent model (Fable 5) doing root-cause analysis** on finding #7 was genuinely
  useful — it confirmed the hypothesis but sharpened it materially (the reconcile checkpoint
  *proposes* citing an edit as done-evidence, not just *permits* it — a stronger, more precisely
  fixable claim than the original hypothesis), and did the legwork of confirming the sibling
  task-execution pipeline had already solved the same problem, which shaped the fix toward "port
  the principle as prompt text" rather than "port the state machine."
