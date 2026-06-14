# Agentic Chat Controller — Design (v1)

> Status: **design approved in brainstorm, pending spec review** · Date: 2026-06-15 · Owner: pradeep
> Supersedes the chat pipeline in `chat/agent.py` (explore → classify → route).

## 1. Motivation

Today's chat path (`ChatAgent.handle_message`) is a rigid 3-phase pipeline:
**explore** (`for _ in range(max_explore_calls=5)` — hard cap) → **classify** (`IntentClassifier`, a separate discriminative LLM call returning `qa | small_change | large_change | resume | clarify`) → **route** (a big `if/elif` delegating to `generate_text` / `run_inline_change` / `create_task_from_chat` / `resume_from_execute`).

Problems:
- **The classifier is the wrong abstraction.** A separate discriminative step silently decides the mode and routes; it under-scopes multi-file edits and can't adapt mid-turn.
- **Cache-hostile.** Each explore iteration rebuilds a `user_payload` dict with a *growing* `tool_results` array, so nothing caches; the classifier is yet another cold call.
- **Not extensible.** Tools are a closed enum (`_EXPLORE_SCHEMA`); MCP/skills/background-process tools have nowhere to plug in.

This spec replaces that with a single **dynamic agentic controller** that owns its turn loop, surfaces mode choice to the user instead of deciding silently, edits with ACID semantics, and is prefix-cache-friendly by construction.

## 2. Scope

This spec is **sub-project #1** of a larger decomposition. It builds the controller **and the extensibility seams**, deferring the integrations:

| # | Subsystem | This spec |
|---|---|---|
| 1 | **Agentic Chat Controller** | ✅ built here |
| 2 | Self-testing in the chat/inline path | deferred (seams built here) |
| 3 | Background process manager + UI tab | deferred (`ToolSource` seam built here) |
| 4 | Live testing (drive the running app) | deferred (depends on #3) |
| 5 | MCP tool support | deferred (`ToolSource` seam built here) |
| 6 | Skills loading | deferred (same seam) |

**In scope (v1):** the dynamic loop, its action union + phase state machine, the ACID per-turn edit/promote/rollback mechanics, the cache-friendly payload layout, and the `ToolSource` aggregator with a single `BuiltinToolSource`.

**Out of scope (v1):** self-verification/testing of edits (#2), background processes (#3), live testing (#4), MCP/skills integrations (#5/#6), cross-session memory & context compaction (its own future module).

## 3. Architecture

**New component:** `ChatController` (`chat/controller.py`). The message route selects it when `AI_EDITOR_CHAT_CONTROLLER=1`, else the legacy `ChatAgent`.

**One dynamic loop replaces three phases.** Exploration, answering, mode-selection, and editing are all actions the model picks per iteration until a terminal action:

```
handle_message(thread, msg, channel):
  msgs    = append-only message list   # see §6 for layout
  shadow  = None                       # lazy — created on first edit
  phase   = DECIDE                     # DECIDE → EDIT (see §5)
  for i in range(MAX_ITERS ~32):       # safety bound; model self-terminates
     action = reasoning.create_controller_step(plan_context, history, tool_defs)
     match action.type:
       tool_call      → registry.execute(...); append result; continue
       answer         → stream text; persist; chat_done; RETURN          (non-mutating)
       clarify        → ask; persist; chat_done; RETURN                  (non-mutating)
       propose_mode   → show plan_sketch + mode-choice card; PAUSE for a pick OR discussion (§4)
                          ├ create_task → create_task_from_chat → task_card → await_plan_ready → RETURN
                          ├ resume      → resume_from_execute → task_card → RETURN
                          ├ explain     → resume loop (agent emits answer)
                          └ edit        → phase = EDIT; resume loop
       edit           → §5 apply-promote transaction; continue           (EDIT phase only)
       submit_changes → discard turn-shadow; chat_done; RETURN           (EDIT terminal)
  # budget exhausted → settle: clean → apologize.
```

**Reasoning seam.** The controller calls a **new `create_controller_step` on `ReasoningEngine`** (alongside `create_plan` / `create_tool_step` / `create_planning_step`), with prompt + schema in **`chat/controller_prompts.py`**. This makes the loop scriptable via `ScriptedReasoningEngine` (the current `ChatAgent` bypasses the engine and calls `transport.generate_json` directly — harder to test). The reasoning engine is wired into the chat path (currently it only receives `transport`+`model`).

**Reuses, unchanged:** `ToolRegistry`/tool impls, `PatchEngine`, `ShadowWorkspaceManager.prepare_lightweight` + `promote`, the Tier-B `_baselines/` checkpoint + `_rollback_to_pre_execution`, `_compute_diff_entries`, `create_task_from_chat`, `resume_from_execute`, `await_plan_ready`, `EventBroadcaster`, `ChatThreadStore`, `resolve_diff_card`, the pending-decision + future gate machinery.

## 4. Action union & the mode-recommendation gate

`create_controller_step` returns one **flat-union** action — a `type` enum plus all variant fields as optional siblings. This is **NOT** a JSON-schema `oneOf`/`anyOf` (Gemini deadlocks on those — see `planning/prompts.py:11`); it mirrors `PLANNING_STEP_RESPONSE_SCHEMA`. Per-phase variants are gated by deep-copying the schema and trimming the `type` enum (exactly how `planning_response_schema(allow_plan_patch)` conditionally adds `emit_plan_patch`).

| `type` | Fields | Mutating | Effect |
|---|---|---|---|
| `tool_call` | `thought, tool, args` | no | `registry.execute`; append; continue. `tool` is a free string ∩ `registry.definitions()`. |
| `answer` | `thought, answer` | no | stream; persist; done. |
| `clarify` | `thought, question` | no | ask; done (awaits next user msg). |
| `propose_mode` | `thought, plan_sketch, recommended, reason, options[]` | — | gate: shows the approach **sketch** + mode choice; PAUSE for a mode pick **OR** further discussion. |
| `edit` | `thought, patch_ops[]` | yes | §5 transaction; continue. |
| `submit_changes` | `thought, summary` | — | EDIT-phase terminal; discard shadow; done. |

**Mode enum** (`propose_mode.recommended` + each `options[]` + gate resolution): `edit | create_task | resume | explain`. `resume` is offered only when a resumable `recent_task` exists; `explain` maps to a follow-up `answer`.

**`propose_mode` carries an approach sketch — the controller's analog of the plan-approval gate.** Before any mutating mode, the agent shows the user *what it intends to do* as a **lightweight `plan_sketch`** (a short natural-language "here's my approach": the areas/files it would touch and the intended changes at a high level) — **NOT** the concrete, step-by-step plan that `create_task` later produces at its own approval gate. The user then either **picks a mode** (≈ approve) or **keeps chatting to refine** (≈ feedback). This is the direct mirror of planning's `AWAITING_PLAN_APPROVAL`: approve → execute; feedback → regenerate.

**Never auto-enter a mutating mode.** The agent does not silently `edit` or `create_task`; it emits `propose_mode` with a `plan_sketch`, a recommended mode (+ reasoning), and alternatives. Resolution:
- **Pick a mode** via the new **`/mode-decision`** route (reusing pending-decision + future): `edit`/`explain` re-enter the loop; `create_task`/`resume` are handoffs.
- **Discuss further** — the user simply sends a chat message instead of picking. That message resumes the loop with `seed_history = prior history + the user's turn` (the **same mechanism as clarify/feedback**, §12); the agent reconsiders and may emit a refined `propose_mode`, an `answer`, or a `clarify`. So the gate is *soft-terminal*: it waits for a mode pick **or** a follow-up message, exactly like the plan-approval gate waits for approve-or-feedback.

`propose_mode` card payload:
```json
{ "plan_sketch": "I'd add a `rate_limit` decorator in `api/deps.py` and apply it to the three public routes in `api/routes.py`; no schema change. Tests would cover the 429 path.",
  "recommended": "create_task",
  "reason": "Touches 4 files across api/ and domain/ with a schema change.",
  "options": [
    {"mode": "create_task", "label": "Plan it as a task",
     "description": "Explore → draft a plan you approve → execute step-by-step with review gates."},
    {"mode": "edit", "label": "Edit inline now",
     "description": "I edit the files directly in this chat; you accept or reject each change."},
    {"mode": "explain", "label": "Just explain",
     "description": "No changes — I describe what would need to happen."}
  ] }
```
The card also surfaces a **"Discuss / refine"** affordance (or the user just types) for the discuss path.

## 5. Phase state machine & ACID edit mechanics

**Phase SM** (reuses the `verify_phase_sm` enforcement pattern: schema-level filtering of the action `type` enum + tool enum, plus a defense-in-depth handler check):

```
DECIDE  → allowed types: tool_call, answer, clarify, propose_mode
   └─[mode-decision == edit]→ EDIT → allowed types: tool_call, edit, submit_changes
```

The model **literally cannot** emit `edit` before the user chose edit mode. Mutating tools (future: a write-capable MCP tool) are gated to EDIT the same way.

**Edit = ACID, one shadow per turn, instant per-patch promote.**

Invariant: **`shadow == real` at every patch boundary.** (Every accepted patch is promoted; every rejected patch is reverted to real.) Therefore `real` is always the valid "before" state.

Per `edit` action (no batching — each patch is its own transaction):
1. Derive touched files from `patch_ops`.
2. Apply ops to the turn-shadow (`prepare_lightweight` lazily on the first edit; reused thereafter).
3. `diff = _compute_diff_entries(real, shadow_after)` for touched files — `real` is the correct before.
4. Gate: **auto-accept** (promote immediately) or **review** (show per-patch diff card → Accept / Reject+reason). This reuses `step_review_auto_accept` semantics, applied per *edit* (the "Review each step" → "Review each edit" toggle, dynamic, reject-with-reason feeds back).
5. **Accept** → `promote(shadow→real)`; invariant restored; continue.
6. **Reject** → restore the rejected patch's touched files in the shadow **from real** (modified → overwrite from real; created → delete from shadow; deleted → copy back from real); append reason to history; agent revises; continue.

The shadow is created lazily on first edit, reused for the whole turn, kept in sync by promote/restore, and **discarded at turn end** (`submit_changes` / turn completion). **Reads always hit the real workspace** — there is no `use_shadow_for_reads` in the chat path; because edits instant-promote, subsequent reads/searches see them on real.

**Invariant shift (explicit):** today "the real workspace is only written on `PROMOTING`." The chat controller departs from this — its edits write to real *immediately*, protected by **checkpoint-backed rollback** rather than by staging. The full *task* pipeline keeps its shadow-until-accept model unchanged; only the chat-controller direct-edit path is instant-promote. This matches how mainstream coding agents (Cursor/aider) behave.

**Two state mechanisms, distinct jobs:**
1. **Turn-shadow** (ACID, one per turn) — per-patch apply/promote/revert. *Not* a diff baseline.
2. **Turn-start snapshot** (Tier-B `_baselines/` checkpoint) — **only** for *whole-turn* rollback (explicit "undo this turn"; in #2, agent-initiated test-fix rollback). Never used for per-patch diffs.

**Durability:** the turn-shadow + a manifest persist to disk (mirroring `.inline-manifest.json`) so a reload while a per-patch review gate is pending can still resolve. Abandonment = **kept** (each patch was individually accepted/auto-accepted and is already on real); whole-turn rollback is only ever explicit.

## 6. Cache-friendly payload & information flow

Mirror the planning split (traced from `reasoning/engine.py::create_planning_step`):

- **`format_controller_system_prompt(tool_definitions, …)`** → the system string: prompt text + `tools_json`. **No retrieval here.**
- **`build_controller_step_payload(plan_context, history, tool_definitions)`** → the user payload.

Both pass independently to `generate_json(system_instructions=…, user_payload=…)`.

**Payload key order (cache discipline — varying content LAST, per `build_planning_step_payload`):**
```
system_instructions = prompt + tools_json                  (FROZEN — cached)
payload = {
  goal, workspace,                                         (stable)
  retrieval_seed,        # FROZEN at session start; pointers only; before history — CACHED
  conversation_history,  # append-only: user msgs + tool results (incl. file bodies) + retrieval DELTAS
  instruction,
  budget_status          # LAST (varies every turn)
}
```

**Retrieval = seed + delta (NOT a static block, NOT a per-turn full block):**
- **Seed**: compact pointer-set (graph neighbors, symbol seeds, file paths, diagnostics), computed **once at session start**, placed **before** `conversation_history`, **never rewritten** → stays cached across all turns.
- **Delta**: when an edit changes files, append a **compact retrieval-refresh entry into `conversation_history`** (synthetic `tool_result`: "after editing X, neighbors/diagnostics now: …"). Append-only ⇒ cached from the next turn; only the newest delta is in the fresh tail. Deltas supersede; the agent uses the latest + live tools.
- **Bodies never go in seed or delta** — file contents ride in `read_file`/`search_code` tool results in `conversation_history` (cached). Seed/deltas are pointers only. This is the structural fix for the 72K-payload bloat (which was bodies inside `retrieval_context`).

Rationale for rejecting the alternatives:
- **Mutable retrieval before history** → any refresh invalidates the entire history cache after it. Rejected.
- **Full retrieval in the tail (after history)** → reprocessed *every* turn (tail is never cached) → per-turn tax. Rejected.
- **Seed+delta** → each retrieval fact paid once then cached; fresh via append-only deltas + live tools. Chosen.

**Why the controller can't pin a static retrieval head like planning does:** planning's `initial_context` is safely static *only because the planning loop is read-only* — nothing mutates the workspace mid-loop, so the snapshot can't drift. The controller mutates, so it needs the seed+delta split.

**Determinism (cache correctness is byte-exact):** tool definitions render in a **stable order with sorted JSON keys**; **no volatile content** (timestamps, run-ids, reordered sets) anywhere in the cached prefix. Enforced by test.

**Retrieval freshness backstops:** on each accepted promote, nudge an **incremental re-index of touched files** (the self-updating watcher already does this async; the nudge tightens lag) so deltas/`query_graph` reflect edits; live tools (`read_file`/`search_code` on real) are the always-current path.

**No history truncation (full cache).** v1 keeps the entire conversation in the cached prefix (single moving breakpoint after the latest stable content). The consequence — very long sessions approach the context limit — is **deliberately deferred** to a future **agent-memory + compaction module**; v1 just provides the clean append-only substrate it will operate on.

**Per-provider cache application (edge transform):** the controller emits provider-agnostic breakpoint hints; each transport applies them (Anthropic `cache_control` markers; OpenAI automatic prefix caching; Gemini implicit), or ignores them. Exact thresholds/discounts verified against provider docs at implementation time (trace the actual builders — system prompt and user payload are built by separate functions carrying different fields).

## 7. Tool source seam (built in v1; integrations deferred)

Today `ToolRegistry.definitions()` is one hardcoded list and `execute()` a big `if/elif`; MCP would mean appending to that `if/elif` (non-DRY). v1 makes the registry an **aggregator over sources**.

```python
class ToolSource(Protocol):
    name: str                                       # "builtin" | "mcp:<server>" | "skill" | "bgproc"
    def definitions(self) -> list[ToolDefinition]: ...
    def owns(self, tool: str) -> bool: ...
    async def execute(self, tool: str, args: dict) -> ToolOutput: ...
```

- Registry holds an ordered `list[ToolSource]`; `definitions()` = concat; `execute(name, args)` routes to the owning source; **unique names enforced at startup** (collision = hard error).
- **v1 builds exactly one source: `BuiltinToolSource`** — today's tools moved behind the protocol, reusing `tools/*.py` verbatim. The loop is unchanged (`registry.definitions()` / `registry.execute()`).
- **Namespacing** (mirrors `mcp__playwright__browser_click`): builtin tools keep bare names; external sources are prefixed `mcp__<server>__<tool>`, `skill__<name>`, `bgproc__<verb>`. The phase SM gates on aggregated names, so a mutating external tool can later be confined to EDIT phase with no loop change.
- **Deferred extension points** (each just a `ToolSource` impl): `McpToolSource` (#5), `SkillToolSource` (#6), `BackgroundProcessToolSource` (#3).

## 8. Migration

`AI_EDITOR_CHAT_CONTROLLER` is a **temporary migration flag, not a long-term setting.**
- Ship the controller behind it, running parallel to the legacy `handle_message` pipeline.
- Default `1` once smoke-verified.
- The legacy explore→classify→route pipeline (and its dead branches) is **deleted** in a follow-up cleanup at `=0` retirement — the flag exists only to de-risk the cutover.
- `run_inline_change` is **retained but decoupled** (the controller stops calling it) as a reusable mechanism, **not** the `=0` fallback.

## 9. Invariants & tests

1. **`shadow == real` at every patch boundary** — test: accept promotes & restores invariant; reject restores shadow from real (modified/created/deleted cases).
2. **Never auto-enter a mutating mode** — test: from DECIDE the schema forbids `edit`; `edit` only reachable after a `mode-decision == edit`.
3. **Cache prefix immutability** — test: a retrieval refresh **appends** to history and **never rewrites** `retrieval_seed` or any prior payload field; tool defs serialize with sorted keys + stable order; no volatile content in the prefix.
4. **Per-patch instant promote, no batching** — test: each `edit` promotes (or reverts) before the next `edit` is processed.
5. **Gate teardown in place** (Class-A rule) — `/mode-decision` and the per-edit review gate clear `pending_*` + transition the caller's object, never a re-fetched copy.
6. **Reads always hit real in the chat path** — test: `use_shadow_for_reads` is never invoked by the controller.
7. **Scriptability** — `ScriptedReasoningEngine.create_controller_step` drives the full loop deterministically (use `SQLiteTaskStore` where production copy-semantics matter).

## 10. Risks / open questions

- **`PatchEngine` on the turn-shadow vs in-memory** — v1 uses the shadow+promote path (transactional). No in-memory PatchEngine needed.
- **Reindex-nudge cost** — incremental re-index of touched files on each promote must be cheap enough not to stall the turn; fall back to the async watcher if so.
- **Long-session context limit** — accepted; owned by the future memory/compaction module.
- **Provider cache thresholds** — verify exact numbers at implementation (do not hard-code from memory).

## 11. Reuse map

| Need | Existing |
|---|---|
| Tool spec / call / result | `ToolDefinition`, `ToolOutput` (`tools/registry.py`) |
| Apply patches | `PatchEngine` |
| Transient shadow + promote | `prepare_lightweight`, `promote` (`workspace/shadow.py`) |
| Whole-turn rollback | Tier-B `_baselines/` checkpoint + `_rollback_to_pre_execution` |
| Diff card | `_compute_diff_entries`, `_cap_unified_diff`, `resolve_diff_card` |
| Handoffs | `create_task_from_chat`, `resume_from_execute`, `await_plan_ready` |
| Gates | pending-decision + future pattern (step/command/scope) |
| Per-edit review toggle | `step_review_auto_accept` semantics |
| Reasoning pattern | `create_planning_step` + `build_planning_step_payload` (payload/caching discipline) |
| SSE | `EventBroadcaster` |
| Persistence | `ChatThreadStore` |

## 12. Planning-loop mirror, DRY/SOLID & design patterns

The controller is built to **mirror the planning path** (`PlanningAgent` → `PlanningLoop._run_single_pass` → `planning_response_schema` → feedback resume in `continue_task`). Match it bit-for-bit, then diverge only where noted.

**Structural mirror (`ControllerLoop` ↔ `PlanningLoop`):** `for iteration in range(max+1)`, append-only `history`, `_assistant_turn` (strip `thought` — repetition-attractor mitigation), retry-on-parse-failure with correction injection, `_consecutive_malformed` counter, duplicate-call guard (`_seen_calls`, canonical `sort_keys` args key, no echo of the rejected call), `AgentToolTrace`, `_broadcast` to task+chat channels, `seed_history` replay as the cache prefix. These weak-model mitigations are **inherited, not reimplemented**.

**Clarify ≈ planning feedback** (same shape, two differences):
- Same: append the user's text as the final history turn, replay via `seed_history`, re-persist the grown conversation. (Planning: `_format_feedback_turn` + `task.planning_conversation_history`; controller: the persisted chat thread history.)
- Diff 1: **agent-initiated** — the agent emits `clarify` (a question) and the user's answer becomes the appended turn — vs planning's **user-initiated open feedback** on a plan card.
- Diff 2: the controller appends **retrieval deltas** into history after edits; planning never does (read-only ⇒ pinned-static retrieval).

**`propose_mode` ≈ the plan-approval gate.** Its two resolutions mirror `AWAITING_PLAN_APPROVAL` exactly: **pick a mode** ≈ approve (feedback=null → proceed); **discuss/refine** ≈ feedback (string → re-explore). The discuss path reuses the *same* clarify/feedback resume (append the user's turn to `seed_history`, re-run the loop). The `plan_sketch` it shows is the lightweight intent preview — distinct from the concrete `create_task` plan, which still goes through its own full approval gate downstream if that mode is chosen.

**Schema (Gemini-compat):** flat `type` enum + optional sibling fields, **not** `oneOf`/`anyOf`; per-phase gating by deep-copy + enum-trim (mirrors `planning_response_schema`).

**DRY — staged extraction (KISS/YAGNI: do not big-bang refactor the proven loops):**
- v1 extracts the genuinely-shared, low-risk primitives into a shared module (e.g. `reasoning/react_common.py`): `_assistant_turn`, the dedup-key builder, the malformed/parse-fail correction text, the trace+broadcast helpers. `ControllerLoop` consumes them; `PlanningLoop`/`ToolLoop` can migrate opportunistically.
- **Target (follow-up, not v1):** unify `PlanningLoop`/`ToolLoop`/`ControllerLoop` under a **Template Method** base `ReActLoop` (skeleton owns iterate→step→retry/correct→dedup→dispatch→append; subclasses override `allowed_actions()` and `handle_terminal_action()`). Staged to keep v1 blast radius small.

**Design-pattern map (applied where they earn their place, per SOLID):**
| Pattern | Where | Why |
|---|---|---|
| **Template Method** (behavioral) | `ReActLoop` base (target) | DRY the shared loop skeleton; subclasses vary only terminal handling — OCP. |
| **State** (behavioral) | DECIDE→EDIT phase SM | Each state owns allowed actions/transitions (mirrors `verify_phase_sm`); no boolean mode flags — matches the "no multi-mode booleans" rule. |
| **Strategy** (behavioral) | per-edit review vs auto-accept | Pluggable accept policy (reuses `step_review_auto_accept`); dynamic per turn. |
| **Composite** (structural) | `ToolRegistry` over `ToolSource`s | Registry treats one-or-many sources uniformly; `definitions()`/`execute()` compose children. |
| **Adapter** (structural) | `McpToolSource`/`SkillToolSource`/`BackgroundProcessToolSource`; per-provider cache transform | Adapt foreign protocols/dialects to the `ToolSource` / breakpoint-hint interface — ISP/DIP: loop depends on the abstraction, not the integration. |
| **Factory** (creational) | source/registry assembly; reasoning-engine selection | Centralize construction; controller depends on interfaces, not concretions (DIP). |

**SOLID checkpoints:** SRP — `ChatController` (orchestration) vs `ControllerLoop` (ReAct) vs `ToolRegistry`/`ToolSource` (tools) vs phase SM (control) are separate single-purpose units. OCP — new tools/modes/providers extend via a source or an enum entry, not loop surgery. LSP — every `ToolSource` is substitutable. ISP — `ToolSource` is a 4-method seam. DIP — the loop and registry depend on `ReasoningEngine`/`ToolSource` protocols.
