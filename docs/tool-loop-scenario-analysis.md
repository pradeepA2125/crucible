# Tool Loop — Scenario Analysis

> Manual tester / analyst perspective. Each scenario describes what a real agent could encounter
> mid-execution, what the system does today, and what it should do instead.
>
> Format: **Scenario → Current Behavior → What Needs To Be Done**
>
> **Updated:** step_context now carries the full `PlanStep` — `implementation_details`,
> `edge_cases`, `design_rationale`, `testing_strategy`, `risk`, and target intents.
> Impact on each scenario is noted.

---

## Scenario 1 — Implementation approach is wrong for the actual code structure

**What happens in real life**

The plan's target file is correct — it passed plan validation (structural grounding check +
`critique_json_plan` LLM critique, up to 3 rounds). By the time the tool loop runs, `allowed_files`
contains files that exist and have been validated.

The problem is subtler: the plan's *approach* assumes something about the code that isn't true.

Examples:
- Plan says "add logging to `process_request` in `routes.py`". Agent reads the file, finds
  `process_request` was renamed to `handle_request` in a recent commit. The symbol is gone.
- Plan says "update the `build_auth` function". Agent reads it and finds `build_auth` is a
  5-line wrapper that immediately delegates to `AuthService.build()` in `auth/service.py`.
  Adding logic here would be wrong — it needs to go in the service, which is outside
  `allowed_files`.
- Plan says "insert a logging call at the top of `process_batch`". Agent reads the function
  and finds it's 300 lines with 12 early returns. The logging strategy the plan assumed
  (one call at the top) doesn't cover the real control flow.

**Impact of step_context fix**

`design_rationale` and `implementation_details` now reach the agent. If the planner wrote
"build_auth is a pass-through wrapper; logging must go into auth/service.py:AuthService.build",
the agent enters the loop already knowing this — it doesn't need a tool call to discover it.
Probability of this scenario drops significantly for well-specified plans.

It still occurs when:
- The plan's `implementation_details` was itself based on stale retrieval (wrong before
  execution started — see Scenario 13 interaction)
- The LLM planner didn't populate these optional fields (they're all `None`)
- The code changed between planning and execution

**Current behavior**

Depends on what the agent does:

- If the agent emits a `search_replace` with the old symbol name (`process_request`) →
  `ANCHOR_MISSING` preflight failure → `last_failure` with `guidance: "search text not found,
  check if content has changed"` → attempt 2 gets this hint and can re-search with the correct
  name. **This case is partially handled.**

- If the agent figures out mid-loop (via tools) that the entire approach is wrong — before
  emitting anything — it has no signal to send. It can only:
  - Emit a best-effort patch that may be semantically wrong (silently passes if tests don't
    cover it)
  - Exhaust the tool budget → `ToolBudgetExceededError` → retry from scratch, same context
  
  The engine cannot distinguish "agent found the approach is architecturally wrong" from
  "agent ran out of tool calls". No structured feedback flows to the replanner.

**What needs to be done**

Add a `revise_plan` action type for the case where the agent determines mid-loop that the
step's approach needs rethinking — not just a symbol rename, but a structural revision:

```json
{
  "type": "revise_plan",
  "thought": "build_auth delegates immediately to AuthService.build(); logging must go there, not here",
  "reason": "Correct implementation location is outside allowed_files; step needs retargeting",
  "evidence": {
    "searched_for": "build_auth logic",
    "found_in": "auth/service.py:AuthService.build",
    "current_target_issue": "routes.py:build_auth is a pass-through wrapper"
  }
}
```

`ToolLoop.run()` raises `PlanRevisionRequestedError` (carries reason + tool trace as evidence).
The engine feeds this into the existing `plan_validation_feedback` replanning path — the same
mechanism already used when `_validate_plan_grounding()` finds issues. The replanner receives
richer evidence than plan critique alone could produce.

---

## Scenario 2 — Step scope is too narrow: agent needs to touch more files

**What happens in real life**

Step S2 says "add `user_id` field to `TaskRecord`". The agent reads the file, makes the change,
then calls `search_code` looking for all usages and finds that `TaskView`, `TaskResult`, and
`http-backend-client.ts` all need the same field added. The step's `allowed_files` lists only
`models.py`.

**Impact of step_context fix**

Makes this scenario *more detectable earlier*. If `implementation_details` says "update
models.py, task_view.py, and http-backend-client.ts" but `allowed_files` only contains
`models.py`, the agent can see this inconsistency at iteration 0 — before any tool call. It
can immediately emit `revise_plan` citing "implementation_details describes 3 files but
allowed_files only lists 1". Previously the agent had to discover all the callers via
`search_code` first before realising the scope was wrong.

**Current behavior**

Agent emits patch ops for models.py (allowed) and optionally for the other files (not allowed).
Ops for non-listed files fail preflight silently — they are dropped or cause the entire candidate
to be rejected. The result is a partial change that breaks the build, which validation catches.
Step fails, retries, ultimately fails because the constraint hasn't changed.

**What needs to be done**

Two sub-options:

1. **`request_scope_expansion`** action — agent signals it needs additional files added to
   `allowed_files`. Engine pauses the step, validates the request (are the files plausibly
   related?), expands the target list, resumes. Risky if not constrained.

2. **Multi-file op validation relaxation** — preflight allows ops on files not in
   `allowed_files` if they are read-only (no destructive op) OR if the agent explicitly tagged
   them in the patch with `"intent": "related_change"`. The engine adds these to
   `modified_files` and tracks them in the step trace.

The simpler near-term fix: include related-symbol files in `allowed_files` during plan
generation by having the planner look at import graphs, not just the primary target file.

---

## Scenario 3 — Step is already done (idempotent / stale plan)

**What happens in real life**

A prior run partially succeeded. The task was resumed via `POST /resume`. Step S1 was marked
complete. Step S2 starts, but S2's change (e.g. "add `TASK_EVENTS_ROUTE = True` to routes.py")
is already present in the shadow workspace because a prior attempt applied it before crashing.

Or: the user made the change manually in the real workspace between sessions, and the shadow was
synced from it.

**Current behavior**

Agent calls `read_file`, sees the change is already there. The only output it can produce is
`emit_patch`. It emits a `search_replace` op where the search string is the new content (already
present) — op fails in `PatchEngine` because the search string doesn't match. Step fails.
Alternatively it emits a no-op (empty `patch_ops`) which fails Pydantic validation.

**What needs to be done**

Add `"type": "skip_step"` action:

```json
{
  "type": "skip_step",
  "thought": "read_file confirms the change is already present in the file",
  "reason": "Step already applied; shadow workspace matches target state"
}
```

Engine marks the step `id` in `completed_step_ids`, emits an SSE event
`{"type": "step_skipped", "step_id": "S2", "reason": "..."}`, and continues to the next step.
This makes resume idempotent and handles stale plans gracefully.

---

## Scenario 4 — Agent discovers a breaking-change risk mid-execution

**What happens in real life**

Step plan: "change `load_context()` signature to accept a second `goal` argument". Agent runs
`search_code` for all callers of `load_context` and finds 12 call sites — 4 internal, 8 in
external tests. The new signature would silently break all 8 test call sites.

**Impact of step_context fix**

`edge_cases` might say "update all callers of load_context to pass the goal argument". If it
does, the agent is primed to search for callers *before* emitting and understands the risk. If
those callers are in non-`allowed_files`, the agent can immediately signal scope mismatch via
`revise_plan` without wasting tool calls. The `risk: "high"` field also tells the agent to be
more thorough before committing.

The fundamental gap remains: no way to attach a `warnings` list to the emitted patch when the
agent proceeds despite the risk.

**Current behavior**

Agent has no way to surface this. It either:
- Emits the patch anyway (breaking the callers) → validation fails if the test runner is
  configured → step retries → may succeed if validation is not thorough enough
- Emits a patch that also updates all 12 callers (if within `allowed_files`) — this might work
  but the plan only listed 1 file, so preflight rejects the extra ops

**What needs to be done**

Two mechanisms needed:

1. **Risk annotation in `emit_patch`** — allow the agent to attach a `warnings` list to the
   patch document:
   ```json
   {"type": "emit_patch", "patch_ops": [...], "warnings": ["8 external callers not in scope"]}
   ```
   These surface in the task's `diagnostics` with `level: "warning"` so the human reviewer
   sees them in the review panel before promoting.

2. **`revise_plan` with risk reason** — if the risk is severe enough that the agent judges the
   step shouldn't proceed, it should be able to signal that. Falls back to Scenario 1's
   mechanism.

---

## Scenario 5 — Tool budget exhausted without enough context gathered

**What happens in real life**

The codebase is large. The agent needs to find the right auth handler across 40 files. It uses
all 8 tool calls just tracing the call graph (`search_code` → follow imports → `read_file` →
more imports) and runs out of budget before identifying the right location.

**Impact of step_context fix**

`implementation_details` often tells the agent exactly where to look. "Edit
`auth/service.py:AuthService.build` at line ~47 where the token is first used" means the agent
can go directly to `read_file("auth/service.py", start_line=40, end_line=55)` on iteration 0
instead of tracing the call graph. Budget-exhaustion due to re-discovery is significantly
reduced. Budget exhaustion still happens for genuinely complex or poorly-documented steps.

**Current behavior**

`ToolBudgetExceededError` is raised. The engine treats this as a failed attempt, same as a bad
patch. It retries — but the new tool loop starts fresh with the same budget and the same starting
context. It will make the same choices and exhaust the budget again. After `max_iterations`
retries, task `FAILED`. The agent's partial findings (which files it DID explore) are lost.

**What needs to be done**

Two improvements:

1. **Carry tool trace across retries** — when a step is retried after `ToolBudgetExceededError`,
   inject the prior attempt's `AgentToolTrace` (tool calls + results) into the new loop's
   initial context. The agent doesn't re-explore what it already found.

2. **Budget escalation on retry** — `max_tool_calls_per_step` should increase on each retry
   (e.g. 8 → 12 → 16) up to a cap, since budget-exhaustion failures indicate genuine complexity.
   The `last_failure` dict already flows into the context; add a
   `tool_budget_exhausted: true` flag and `previous_tool_trace` to it.

---

## Scenario 6 — Validation failure doesn't tell the agent what broke

**What happens in real life**

Step S3 emits a patch that passes preflight but fails validation (pytest returns non-zero).
The engine retries with `last_failure` context. But `last_failure` is a coarse dict:

```python
{"reason": "validation_failed", "diagnostics": [...], "validation_output": "..."}
```

The agent's second attempt has to parse free-text pytest output from `validation_output` to
understand which test failed and why. This is error-prone, especially for long test output.

**Current behavior**

Agent receives the `last_failure` dict in its step context and tries to interpret raw test
output. This works sometimes but is fragile — the agent may focus on the wrong part of the
output or misidentify the root cause.

**What needs to be done**

Structure the `last_failure` payload for validation failures:

```python
class StructuredValidationFailure(BaseModel):
    failed_tests: list[str]       # ["test_auth.py::test_login_invalid_token"]
    error_lines: list[str]        # lines containing "FAILED" or "ERROR"
    assertion_messages: list[str] # extracted AssertionError text
    full_output_truncated: str    # fallback for the agent
```

Parse this from `ValidationResult.diagnostics` + raw output in `_run_step_with_retries` before
constructing `last_failure`. The agent now has structured signal to act on ("test
`test_login_invalid_token` failed because `token` was None") rather than parsing raw text.

---

## Scenario 7 — Partial patch application: some ops succeed, some fail

**What happens in real life**

Step emits 3 patch ops: create file A (success), search_replace in file B (success), apply_diff
to file C (fails — diff context doesn't match actual file). `PatchEngine.apply_patch_candidate()`
rolls back A and B via the checkpoint. Step is retried from scratch.

**Current behavior**

The agent's next attempt has no knowledge of which ops succeeded and which failed. It will
likely re-generate all 3 ops, including the 2 that worked fine, and the same op that caused the
failure — potentially with the same error unless the agent happens to change it.

**What needs to be done**

Include per-op failure information in `last_failure`:

```python
{
  "reason": "patch_apply_failed",
  "failed_op_index": 2,
  "failed_op": {"op": "apply_diff", "file": "C.py", ...},
  "error": "diff context mismatch at line 47: expected 'def foo():' got 'def foo(x:int):'"
}
```

Agent can then emit a revised patch that keeps ops 0 and 1 unchanged and only fixes op 2.
This is particularly important for multi-op steps where only one op is wrong.

---

## Scenario 8 — Ambiguous implementation location: multiple valid targets found

**What happens in real life**

Plan says "add error handling to the task creation endpoint". Agent searches for "task creation"
and finds 3 handlers: `POST /v1/tasks` in `routes.py`, a legacy `POST /tasks` in `legacy.py`,
and an internal `create_task()` in `orchestrator.py`. All 3 are plausible. The plan only listed
`routes.py`.

**Impact of step_context fix**

`design_rationale` can disambiguate directly: "the canonical endpoint is `POST /v1/tasks` in
`routes.py`; the legacy route in `legacy.py` is deprecated and must not be modified". The agent
enters the loop with the answer, making this scenario rare for explicit plans. Still occurs when
`design_rationale` is absent or the planner itself was ambiguous.

**Current behavior**

Agent picks one (likely the first search result or the one matching `allowed_files`) and emits a
patch for it. It may be the wrong one. The patch applies and validation passes (if tests don't
cover this path), and the task succeeds with the wrong file changed. The human reviewer may
catch it, or may not.

**What needs to be done**

Add `"confidence"` to `emit_patch`:

```json
{
  "type": "emit_patch",
  "thought": "Found 3 candidate locations; routes.py most likely but uncertain",
  "confidence": "low",
  "patch_ops": [...],
  "alternatives": ["legacy.py:POST/tasks", "orchestrator.py:create_task"]
}
```

When `confidence == "low"`, the orchestrator:
1. Applies the patch to the shadow workspace
2. Surfaces the patch to the human reviewer with the alternatives listed prominently in the
   review panel (not just the diff)
3. Optionally blocks automatic promotion until the reviewer explicitly confirms

This turns an invisible silent error into a visible decision point.

---

## Scenario 9 — Search results are too noisy: budget drained on irrelevant files

**What happens in real life**

Plan: "add logging to the `build_auth` function". Agent calls `search_code` with pattern
`build_auth`. Returns 200 matches across vendor files, test fixtures, documentation strings,
and the actual source. Agent uses 5 of its 8 tool calls reading irrelevant files before finding
the right one.

**Impact of step_context fix**

`implementation_details` like "modify `auth/service.py:AuthService.build`" tells the agent the
exact file and symbol — it can call `read_file("auth/service.py")` directly instead of
`search_code("build_auth")` with 200 results. Broad noisy searches become unnecessary when the
planner was specific. Still occurs when `implementation_details` is vague ("add logging
somewhere in the auth module") or absent.

**Current behavior**

`search_code` returns all matches, truncated to `~8000` chars of output. The agent must
distinguish signal from noise using language understanding alone. In large codebases, this burns
budget fast and produces poor patches when budget runs out before the right file is found.

**What needs to be done**

Two improvements:

1. **Smart result filtering in `search_code`** — automatically exclude vendor dirs, test
   fixtures, and documentation from results:
   ```python
   DEFAULT_EXCLUDE_DIRS = ["node_modules", ".venv", "vendor", "dist", "__pycache__"]
   ```
   Configurable via env var. The plan targets (`allowed_files`) can also bias the results —
   show matches in `allowed_files` first.

2. **Search refinement in the prompt** — `TOOL_LOOP_SYSTEM_PROMPT` should instruct the agent
   to use `path_filter` to scope searches before broadening:
   ```
   Start narrow: search within target files first. Broaden only if no match found.
   ```

---

## Scenario 10 — Semantic drift: plan prose vs. actual code structure

**What happens in real life**

Plan (written by LLM) says "update the authentication middleware". The agent searches for
"authentication middleware" and finds nothing — the codebase calls it `AuthGuard`, it's a
FastAPI dependency, not middleware, and the file is named `guards.py`. The plan's language
doesn't match the codebase's naming conventions.

**Impact of step_context fix**

`design_rationale` is where the planner would write "the auth layer is implemented as
`AuthGuard` (a FastAPI dependency) in `guards.py`, not as middleware". If the planner was
precise, this scenario is eliminated entirely — the agent never needs to search for
"authentication middleware" because it already has the correct class name and file. This is
the scenario most directly addressed by the fix: the planner already resolved the naming
during plan creation; that knowledge just wasn't flowing through. Now it does.

**Current behavior**

`search_code` with pattern "authentication middleware" returns zero results. Agent tries
variations, exhausts budget, step fails. The `search_semantic` tool could find it, but the
agent may not think to try it, or may try it too late (after budget is half spent on ripgrep).

**What needs to be done**

1. **Tool selection guidance in the prompt** — `TOOL_LOOP_SYSTEM_PROMPT` should instruct the
   agent: "If exact-string search returns nothing, immediately try `search_semantic` with the
   same concept before trying variations". Semantic search is robust to naming drift.

2. **Zero-result escalation** — `search_code` should return a hint when it finds no matches:
   ```
   "No matches found. Suggestion: try search_semantic with query 'authentication middleware'"
   ```
   This costs nothing but prompts the agent toward the right next step.

3. **Pre-loop semantic priming** — before the tool loop starts, inject the top-3 semantic
   search results for the step's `goal` text into `step_context["semantic_hints"]`. The agent
   enters the loop already knowing the most likely relevant symbols/files. Costs 1 tool call's
   worth of computation but saves 3-4 from the budget.

---

## Scenario 11 — Plan step order is wrong: S2 depends on what S3 creates

**What happens in real life**

Plan: S1=create schema, S2=import schema in service, S3=create the schema file. S2 runs before
S3, reads the shadow workspace, can't find the file it needs to import from, and fails.

**Current behavior**

S2's tool loop reads the shadow workspace (which doesn't have S3's file yet), finds a missing
import target, may emit a bad patch. Step fails. There's no mechanism to detect or signal
dependency inversion.

**What needs to be done**

1. **Dependency annotation in the JSON plan schema** — steps should be able to declare
   `"depends_on": ["S3"]`. The engine validates the DAG during plan approval and reorders
   steps topologically before execution.

2. **`revise_plan` with reorder reason** — if the agent detects "I need file X which should be
   created by a later step", it can emit `revise_plan` with a reorder suggestion. Engine feeds
   this back to the planner.

---

## Scenario 12 — Agent's patch is syntactically valid but semantically wrong

**What happens in real life**

Agent patches a Python function but introduces a subtle logic error — e.g., inverts a condition,
uses the wrong variable name (both exist in scope), or adds an `await` where none is needed.
The patch passes preflight, applies cleanly, and validation passes (tests don't cover this
exact case). Task succeeds and the change is promoted to the real workspace.

**Impact of step_context fix**

`edge_cases` and `testing_strategy` directly reduce semantic errors: "token=None must raise
immediately before any service call" tells the agent where NOT to put the check, and "verify
with test_auth.py::test_invalid_token" points to the right test to run with `run_command`
before emitting. Subtle errors become less likely when the agent has explicit correctness
criteria. Self-review mechanism still needed for cases where edge_cases was incomplete.

**Current behavior**

No mechanism to catch semantic errors that pass syntactic and test validation. This is invisible
until a human reviewer notices or a production incident occurs.

**What needs to be done**

1. **Self-review step after `emit_patch`** — after the agent emits a patch, before the loop
   returns, the engine sends the diff back to the agent with the prompt "Review this diff
   critically. Is the logic correct? If not, revise." One additional LLM call, but catches a
   large class of errors.

2. **Test coverage check** — if `run_command pytest` is available, the agent should run tests
   targeting the changed files before emitting. If tests pass, confidence is higher. If tests
   don't exist for the changed code, surface a `warning` in the patch document.

---

## Scenario 13 — Retrieval context is stale: plan was made before a recent commit

**What happens in real life**

User submits a task at 10am. The retrieval snapshot was taken at 9am. Between 9am and 10am, a
teammate pushed a refactor that renamed `TaskRecord` to `AgentTask` in 20 files. The plan
references `TaskRecord` everywhere. During execution, `search_code` for `TaskRecord` returns
nothing. `read_file` on the targeted files shows `AgentTask` instead.

**Impact of step_context fix — makes this scenario slightly worse**

`implementation_details` generated during planning also used `TaskRecord` (it was correct at
plan time). The agent now has MORE confident wrong information: not just the goal saying
"TaskRecord" but also `implementation_details` saying "search for `class TaskRecord` and
replace...". The agent may act on stale `implementation_details` more directly, emitting the
wrong `search_replace` pattern immediately without first calling `read_file` to verify.

Previously, the agent had to call `read_file` or `search_code` to discover the symbol name —
that tool call would have revealed `AgentTask` and prompted a course correction. With
`implementation_details`, the agent may skip verification because it believes it already knows.

This makes injecting `retrieval_warnings` into `step_context` *more* important, not less:
the agent needs an explicit signal that its pre-loaded context might be stale.

**Current behavior**

The agent sees a disconnect but has no way to know it's due to stale retrieval. It proceeds
with wrong names, emits a patch using `TaskRecord` (not found in search_replace), step fails.
The retrieval staleness warning appears in `task.diagnostics` but the agent doesn't see it —
it's not injected into `step_context`.

**What needs to be done**

1. **Inject retrieval staleness into step context** — `step_context["retrieval_warnings"]` should
   include any `level: "warning"` diagnostics from the retrieval phase. The agent prompt should
   instruct: "If retrieval warnings are present, validate file/symbol names via search before
   patching."

2. **Auto-refresh on stale detection** — if `snapshot_age_sec > CRUCIBLE_RETRIEVAL_MAX_AGE_SEC`
   AND the task has been waiting for more than N minutes, trigger a re-index before execution
   starts. Currently re-index is only triggered at task creation time.

---

## Summary

### What the step_context fix (implemented) changes

| Scenario | Before fix | After fix |
|----------|-----------|-----------|
| 1 — Wrong approach | Agent re-discovers via tools | Agent enters loop with planner's reasoning; fewer tool calls needed |
| 2 — Scope too narrow | Agent discovers via search_code | Agent can detect mismatch at iteration 0 from implementation_details vs allowed_files |
| 4 — Breaking change risk | Agent discovers during search | edge_cases primes agent to search callers early; risk field signals caution |
| 5 — Budget exhaustion | Agent traces call graph from scratch | implementation_details points directly to file:symbol; budget largely preserved |
| 8 — Ambiguous location | Agent guesses from search results | design_rationale disambiguates before any search |
| 9 — Noisy search results | Agent wastes calls on noise | Agent reads specific file directly when implementation_details is precise |
| 10 — Semantic drift | Agent searches wrong name | design_rationale carries the correct codebase name; scenario largely eliminated |
| 12 — Semantically wrong patch | Agent guesses at correctness | edge_cases and testing_strategy define correctness criteria explicitly |
| 13 — Stale retrieval | Agent discovers staleness via tools | **Worse**: agent now acts on stale implementation_details more confidently |

Scenarios 3, 6, 7, 11 are **unchanged** by the fix.

---

### Remaining gaps — new action types needed

Today the agent can only output `tool_call` or `emit_patch`. The scenarios above still call for:

| New Action Type | Triggered When | Engine Response |
|----------------|----------------|-----------------|
| `revise_plan` | Approach wrong, scope insufficient, order wrong | Feed back into replanner with agent evidence |
| `skip_step` | Step already applied / idempotent | Mark step complete, continue |
| `emit_patch` + `confidence: low` | Multiple valid targets, agent unsure | Surface alternatives to human reviewer |
| `emit_patch` + `warnings` | Breaking change risk discovered | Add to task diagnostics; show in review panel |

### Remaining gaps — engine-side changes (no new action types)

| Change | Addresses |
|--------|-----------|
| `retrieval_warnings` in step context | Scenario 13 (stale retrieval — now more urgent) |
| Carry tool trace across retries | Scenario 5 (budget exhaustion) |
| Structured `last_failure` payload | Scenario 6 (opaque validation failure), Scenario 7 (partial patch) |
| Exclude vendor dirs from `search_code` | Scenario 9 (noisy search — residual after fix) |
| Step dependency DAG in plan schema | Scenario 11 (wrong execution order) |
