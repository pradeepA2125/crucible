# Controller skill-handoff gap — findings and fixes — 2026-07-24

## Context

Started from a user report — "the latest chat for Kafka clone 4 ran with skills but the way it's
executing the plan isn't following it properly" — investigated by reading the actual chat SQLite
DB and per-turn debug artifacts in `workspaces/kafka-clone-4` (`.crucible/state/chat.sqlite3` +
`.crucible/state/artifacts/chat/<thread>/`), not by guessing. That first pass turned up four related
symptoms. To confirm they were systematic rather than a one-off, the workspace was wiped bare
(kept only `.crucible/skills/` and `.crucible/mcp.json`), the backend restarted, and a second,
independent brainstorm→spec→plan→execute run was driven end-to-end via direct `curl` calls against
the chat API (`/message`, `/clarify-decision`, `/command-decision`), polling `/live` — reproducing
all four. Root-cause + fix work was then delegated to a second, independent model (Fable 5) with
the full evidence trail, cross-checking against the actual current code on
`feat/merged-decide-edit-phase` rather than trusting prose descriptions of the mechanism.

Backend: TurboQuant / `qwen3.6:35b-a3b-q4_K_M`, `CRUCIBLE_CHAT_CONTROLLER=1`,
`CRUCIBLE_SKILLS_ENABLED=1`, the `superpowers` skill pack installed (`brainstorming`,
`writing-plans`, `executing-plans`, `subagent-driven-development`, `using-git-worktrees`,
`test-driven-development`, etc.).

The four findings collapse to **two independent root causes**, both in
`services/agentd-py/agentd/chat/controller_loop.py`. Fixes for both are implemented and
unit/integration-tested, sitting uncommitted on branch `investigate/skill-handoff-gap` in an
isolated worktree — **not yet merged, and not yet live-reverified against a real model** (see Open
Items).

---

## Findings & fixes

### 1. `executing-plans` never loads on execution handoff

**Symptom**: the model uses `writing-plans` correctly (via a real `read_skill` call) to author an
implementation plan. That plan's own header — written by the model, following `writing-plans`' own
required template — says:

```
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
```

The model then begins executing that very plan **in the same thread** and never calls
`read_skill(executing-plans)`. Confirmed on two independent full runs (different plan content,
different workspace state) — a full `tool_events` scan across both threads shows zero calls to
`read_skill(name="executing-plans")`, ever. The skill was present and fully described in the
injected system-prompt catalog both times (verified in the turn artifact's `system_instructions`),
ruling out a wiring or budget-truncation bug — this is the model not connecting "a plan I just
wrote says REQUIRED SUB-SKILL: X" to "call `read_skill(X)` now."

**Root cause** (`controller_loop.py:618`): `skill_check_due` is
`self._sm.phase == "ACTIVE" and iteration == 0` — deliberately **one-shot per top-level turn**, per
the merged-phase design's own stated intent (`docs/superpowers/specs/2026-07-21-merged-decide-edit-
phase-design.md:238-247`: skill triage is "genuinely redundant to re-run every iteration once it's
been done or missed"). But `ControllerLoop._iterate`'s inner loop can run up to
`CRUCIBLE_CONTROLLER_MAX_ITERS` (default 500) iterations without ever returning control to the
user — `edit` is non-terminal. So "write the plan, then execute it" happens **inside one `run()`**,
well past iteration 0. The one-shot design covers turn-start judgment; it was never meant to (and
doesn't) cover "the model's own edit, mid-turn, just wrote new information that names a required
skill." Contributing factor: `executing-plans`' own frontmatter description ("Use when you have a
written implementation plan to execute **in a separate session** with review checkpoints") doesn't
literally describe Crucible's single-continuous-loop execution model, which may reduce the model's
own trigger-matching confidence even on a hypothetical re-check.

**Fix**: rather than another round of prompt-only tuning (the P2 agent-skills judgment-gap fix
needed three iterations for a similar problem and is still "promising, not proven at scale" per
memory), this is a **deterministic force-load**, mirroring the existing `forced_skills` seeding
path (`/skill` command) that already bypasses model judgment entirely:

- `_extract_required_subskills` (`controller_loop.py:253`, pure regex) scans a just-applied edit's
  raw patch text for `REQUIRED SUB-SKILL: ... superpowers:<name>` directive lines, returning every
  named skill in order (a single directive line can name more than one candidate, e.g. the
  recommended/fallback pair above).
- `_pick_executable_required_subskill` (`controller_loop.py:273`) filters to skills this host can
  actually run. Crucible's chat controller has **no subagent-dispatch tool anywhere in `agentd/`**
  (verified by grep) — so `subagent-driven-development` is always skipped in favor of
  `executing-plans`, the only one of the pair this host can execute.
- `ControllerLoop._maybe_force_required_subskill` (wired in right after every accepted `edit`)
  resolves the picked name via the same `SkillCatalogLoader` the `/skill` path uses and force-loads
  its body directly into the shared `active_skills` dict — no `read_skill` call required. A
  synthetic `tool_result` is appended to history explaining the auto-load so the model isn't left
  guessing why its payload changed.
- New constructor params on `ControllerLoop` (`controller_loop.py:320-321`):
  `skill_catalog_loader` and `active_skill_persist_cb`, threaded from
  `ChatController._run_loop` (`controller.py:422-442`).

**Verified**: `tests/test_controller_required_subskill_autoload.py` (8 tests — pure
extraction/pick-logic unit tests, plus 3 integration tests through the real `ControllerLoop.run()`
using the exact plan-doc text from this investigation, a no-loader-wired negative control, and an
already-active no-op case). Targeted regression (`controller or skills or chat` filter): 317
passed, 0 failed. **Not live-reverified** — see Open Item A.

---

### 2. Step-granularity collapse — plan steps executed as one batch, not one at a time

**Symptom**: `executing-plans`' entire value proposition is "Follow each step exactly (plan has
bite-sized steps)" — one step, one tool call, verified, then the next. Without it loaded, both
dogfood runs executed at **task** granularity instead of **step** granularity:

- Run 1 (partitioned-topics plan): Task 1's steps were *write the failing test → run it to verify
  it FAILS → write the implementation → run it to verify it PASSES → commit*. The test file and the
  implementation file were created together in one edit; the "verify it fails" checkpoint never
  happened, for either of the two multi-file tasks observed.
- Run 2 (fresh kafka-clone plan, different content): Task 1 Step 1 was a **shell command**
  (`mkdir kafka-clone && cd kafka-clone && go mod init kafka-clone`) — the model silently
  substituted a direct file write for `go.mod` instead of running it. Steps 2 and 3 (create
  `crc.go`, then separately create `crc_test.go`) were collapsed into one edit again.

**Root cause**: this is a **downstream consequence of Finding 1**, not a separate bug —
`executing-plans`' step-discipline instruction is simply never in context, so there's nothing to
follow.

**Fix**: none separate from Finding 1's force-load. Whether force-loading the skill's text is
*sufficient* to restore step-by-step compliance is an open question — see Open Item A, this is the
most important unresolved piece of this whole investigation.

---

### 3. `answer` narrates a next action instead of taking it

**Symptom**: caught with the exact evidence in the turn artifact `raw_result` (run 2, right after
the plan was saved):

```json
{
  "type": "answer",
  "thought": "The user approved the plan. Now I need to execute it. The plan has 4 tasks spanning
    multiple files. This is a big multi-part change, so I should use write_todos first to track
    progress, then start implementing.",
  "answer": "Plan written and saved to `docs/superpowers/plans/2025-01-15-kafka-commit-log-
    phase1.md`. \n\nNow executing the plan. This spans 4 tasks across 8 files, so I'll track
    progress with a todo list and implement task by task.\n\nLet me start by initializing the Go
    module (Task 1)."
}
```

The model's own `thought` states the correct next action (call `write_todos`) — and then emits
`type: "answer"` anyway, a terminal action that ends the turn narrating intent without acting on
it. Required an explicit human nudge ("don't stop with a terminal answer mid-execution") to unstick
the session.

**Root cause**: the existing todo-ledger hard-block (`answer`/`submit_changes` gated while the
ledger has pending/in-progress items) only fires once a ledger *exists* with open items. In this
exact case, `write_todos` had never been called yet at all — there was nothing for the guard to
see as "open." `CONTROLLER_SYSTEM_PROMPT` already teaches this exact narrate-without-act pattern in
prose (WRONG/RIGHT examples), and it still reproduced live — proving a prompt-only fix wasn't
sufficient on its own.

**Fix**: `_answer_intent_divergence_correction` (`controller_loop.py:201`) — a narrow mechanical
backstop, not a blanket "answer is no longer terminal" change. It only fires when a response's
`type` is `"answer"` **and** its combined `thought`+`answer` text contains **both** a
forward-looking first-person intent phrase ("I should use", "I need to", "let me start", "next
I'll", etc.) **and** the literal name of a real tool from `AVAILABLE TOOLS`, together — narrower
than either signal alone, to keep false positives on ordinary explanatory answers low. When it
fires, the response is rejected with a message telling the model to actually take the described
action, wired into the same bounded `_MAX_MALFORMED`-limited correction chain already used for
other malformed responses (`controller_loop.py:675`) — no new retry primitive. An ordinary
legitimate terminal answer (a QA reply, a real final summary with no dangling intent) is
unaffected.

**Verified**: `tests/test_controller_answer_intent_divergence.py` (6 tests — the verbatim
smoking-gun text above as a fixture, plus explicit false-positive guards for ordinary explanatory
answers that mention a tool name without the forward-looking phrasing, or vice versa). Included in
the 317-test regression pass. **Not live-reverified** — see Open Item A.

---

### 4. Plan's commit steps silently never attempted

**Symptom**: both plans' every task ends with a "Commit" step (`git add && git commit`). Zero
`run_command` calls containing `git` anywhere across either full session (grepped every
`tool_event`). Neither workspace incarnation of `kafka-clone-4` ever had a `.git` directory —
`using-git-worktrees` (listed as a required workflow skill by both `writing-plans` and
`executing-plans`) was never invoked either time.

**Root cause**: downstream of Finding 1. `executing-plans`' own "When to Stop and Ask for Help"
instruction ("Hit a blocker... instruction unclear → stop and ask") would very likely have
surfaced the missing repo as a blocker had the skill actually been loaded — this wasn't
independently root-caused as its own separate gap.

**Fix**: none separate; expected to resolve as a side effect of Finding 1's fix, once live-verified.

**Verified**: not independently verified either way.

---

## Implementation

- `services/agentd-py/agentd/chat/controller_loop.py` (+181/-1)
- `services/agentd-py/agentd/chat/controller.py` (+11/-1)
- New: `services/agentd-py/tests/test_controller_required_subskill_autoload.py` (8 tests)
- New: `services/agentd-py/tests/test_controller_answer_intent_divergence.py` (6 tests)
- Currently uncommitted on branch `investigate/skill-handoff-gap`, in an isolated worktree at
  `.claude/worktrees/agent-aa9fd20186f12b845/services/agentd-py/`. The main checkout on
  `feat/merged-decide-edit-phase` is untouched.
- `ruff check` clean on both new test files and the added code; targeted regression (`controller or
  skills or chat`): 317 passed, 0 failed; full `pytest tests/` showed only one pre-existing,
  unrelated flaky failure (consistent with CLAUDE.md's documented order/environment-dependent
  flakiness), no new failures introduced.

---

## Open items — real, not fully resolved

### A. Neither fix has been live-reverified against a real model

Both fixes were verified via `ScriptedReasoningEngine` exercising the real `ControllerLoop` state
machine (correction chain, edit branch, force-load path) with the exact failing transcripts pinned
as permanent regression fixtures — not via a fresh live TQP dogfood run. That's reasonable evidence
for the mechanical parts (the force-load logic itself, the divergence-guard's pattern matching),
but it does **not** confirm the thing that actually matters most for Finding 2: does force-loading
`executing-plans`' text into context actually change the model's behavior toward genuine
step-by-step execution? That's a live compliance question — the fix guarantees the skill's
instructions are *present*, not that the model *follows* them, and this hasn't been checked against
a real turn yet. A follow-up live dogfood run (fresh scratch workspace, not `kafka-clone-4`, a
multi-task Go-style build) is the natural next step before considering this closed.

### B. The answer-divergence guard is deliberately narrow

It only catches the specific "forward-looking intent phrase + real tool name, together" shape. A
narrate-without-act response phrased differently (no first-person "I should/I need to" language, or
a tool referenced obliquely rather than by its literal name) would not trip it. This is a conscious
false-positive/false-negative tradeoff, not an oversight — but worth knowing as a boundary.

### C. Not yet merged

The fix branch (`investigate/skill-handoff-gap`) needs review and a merge decision before it takes
effect anywhere outside the investigation worktree.

---

## Process notes

- **Reproducing on a second, independent run** (fresh workspace, different plan content, same
  bugs) is what turned this from "one weird occurrence" into "confirmed systematic gap" worth
  fixing — the single-run evidence alone would have been much weaker grounds for a code change.
- **The original thread's raw DB/artifacts were lost** when the workspace was wiped bare for the
  reproduction run — mitigated by capturing full excerpts (plan text, tool_event dumps, exact
  message indices) inline before wiping, and recording them in project memory
  (`project_kafka_clone_4_skill_handoff_gap.md`). Future investigations of this shape should
  preserve a copy of `.crucible/state/` before any destructive reset, not just prose notes.
- **A second, independent model (Fable 5) doing the root-cause + fix work** was directed to verify
  file/function names against the actual current code rather than trust CLAUDE.md's prose (which
  documents the merged-phase redesign but can drift from HEAD) — this caught that the relevant
  mechanism (`skill_check_due`) is scoped per-`run()`-call, not per-message, which is the precise
  detail that explains why the existing P2 "decide_entry" fix (a similar but distinct
  once-per-fresh-turn judgment-gap fix) doesn't cover this case.
