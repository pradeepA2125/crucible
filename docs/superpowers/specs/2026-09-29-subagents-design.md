# Sub-agents (P5) — design

**Date:** 2026-09-29
**Status:** design approved in brainstorming; spec revision 11 (rev 10 approved by review, cycle 9)

**Rev 11 (2026-10-01):** reconciles the spec with what Phase 1 actually shipped or planned (Plan 1A, committed;
Plan 1B, tested end to end in scratch worktrees). The design is unchanged; interfaces were adjusted where
implementation proved the rev-10 shape wrong or unbuildable in Phase 1. Changed sections: §3 (`AgentContext`
wiring), §4.4, §4.5, §4.6.4, §4.7.2, §7.4, §16. Each change is marked *(rev 11)*.
**Roadmap:** P5 in `docs/superpowers/2026-06-29-feature-roadmap-copilot-parity.md`
**UI wireframe (approved):** `.superpowers/brainstorm/11903-1790698215/content/subagent-ui-v3.html`

## 1. Goal

Let the chat controller delegate work to **sub-agents**: child agents with their own context window that
run **in parallel**, can **edit files**, and return a report to the agent that dispatched them. This
matches Claude Code's sub-agent model (definitions, built-ins, parallel dispatch, nesting, gates surfacing
in the main session) and adds guarantees Claude Code lacks: attributed staleness refusals on shared files,
and a `files_changed` list returned to the parent.

**Success looks like:** from one chat message, the parent fans out N agents that edit disjoint parts of the
workspace concurrently. A child that touches a file a sibling changed is refused with the sibling's name
and re-reads before editing. Every child's full report reaches the parent verbatim. The user watches each
agent live (inline or in a floating window), answers each agent's approval cards independently, and can
stop any single agent.

### Decisions (from brainstorming)

| # | Decision |
|---|---|
| D1 | Built on the **chat controller path**. The dormant task path is treated as absent. |
| D2 | **Cloud providers are the target.** TurboQuant's single-slot server is out of scope. |
| D3 | **Shared workspace, instant promote** (same as the main agent), protected by a **thread-wide write log + staleness guard** with attribution. No per-child shadow isolation in v1. |
| D4 | **Batch dispatch, wait-for-all** (`dispatch_agents([...])`) in v1. Background spawn is v2 on the same child runtime. |
| D5 | **Stacked gate cards**, one per pending gate, keyed by `gate_id`, each labeled with its agent; plus a per-agent `permissions` field. |
| D6 | **Claude Code-compatible agent definitions**: `.crucible/agents/` > `.claude/agents/` > `~/.claude/agents/`. Built-ins: `explore`, `general-purpose`. |
| D7 | **Nesting allowed**: max depth 2; concurrency cap 8. |
| D8 | **Reports are never truncated.** Nothing the model reads is capped. UI-only previews may be shortened. |
| D9 | UI: **agent roster card** in the thread; each row expands into an **inline scroll box**, with a **⤢** button next to it that opens a **floating window** (tabs across siblings, Stop, Esc to close). |
| D10 | `CRUCIBLE_SUBAGENTS_ENABLED` **defaults ON** in the shipped end state (kill-switch). While phases 1–4 are being built the code default is OFF; phase 5 flips it (§16). |

## 2. Research basis (Claude Code)

From code.claude.com docs (sub-agents, worktrees, agent-teams), fetched 2026-09-29:

- Definitions are Markdown + YAML frontmatter; only `name`/`description` are required. Optional fields:
  `tools`, `disallowedTools`, `model` (`inherit`), `permissionMode`, `maxTurns`, `skills`, `mcpServers`,
  `isolation`, `background`, `effort`, `memory`, `hooks`, `color`.
- A child starts with its own system prompt, the parent's handoff prompt and CLAUDE.md/AGENTS.md, **not**
  the parent's history. It returns only the final report, an agent id, and a partial flag.
- Nesting: "By default, subagents can spawn their own subagents up to **3 layers deep**"
  (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`). The concurrency default is 20. A child's permission prompts surface in the
  main session.
- **Same-file edits are not coordinated**: "Two teammates editing the same file leads to overwrites. Break the
  work so each teammate owns a different set of files." The only safety net is the Edit tool's
  "File has been modified since read" refusal. `isolation: worktree` is opt-in, git-only, and merge-back is manual.

Crucible adopts partition + staleness (D3), made attributed, race-free (the check is repeated at promote), and git-independent.

## 3. Architecture overview

```
ChatController (parent turn = root agent "main", depth 0)
 └─ ControllerLoop(phase=ACTIVE, agent=None)            channel chat:{thread}
     └─ tool_call dispatch_agents([{agent, prompt, label}, ...])
          └─ SubAgentToolSource ──► SubAgentRuntime (one per ChatController / process)
               ├─ AgentRegistry         agent_id → AgentHandle
               ├─ BoundedSemaphore(MAX_CONCURRENT)   process-wide; "main" holds no slot
               └─ per child: ControllerLoop(phase=AGENT, agent=AgentContext)
                    channel chat:{thread}:agent:{agent_id}
                    ├─ filtered registry (+ dispatch_agents if depth < MAX_DEPTH)
                    ├─ TurnEditSession(shadow chatturn-{thread}-{agent_id}) ─► WorkspaceWriteLog
                    └─ gates ──► ChatController multi-gate (gate_id, agent tag)
```

**`AgentContext`** is the single object that carries per-agent configuration everywhere, instead of a separate
kwarg for each concern. It's a frozen dataclass in `agentd/subagents/context.py`:
`agent_id, name, label, depth, parent_agent_id, permissions, allowed_types: tuple[str,...], persona: str,
tool_filter, skills_catalog_enabled: bool, memory_mode: "recall_only", artifact_agent_id`.
`ControllerLoop` takes `agent: AgentContext | None = None`. *(rev 11)* `ReasoningEngine.create_controller_step` does
**not** take the `AgentContext`: it takes the two things derived from it, `render_ctx: RenderContext` and
`persona: str | None` (§4.4). The loop builds both once via `RenderContext.for_agent(agent)` (Phase 2). `None` means
the parent, and the parent's behavior stays byte-identical to today (regression-tested, §15).

New package `services/agentd-py/agentd/subagents/`: `context.py`, `definitions.py`, `runtime.py`
(`SubAgentRuntime`, `AgentRegistry`, slot token), `tool_source.py` (`SubAgentToolSource`), `write_log.py`,
`config.py`. The `chat_agents` table lives in `chat.sqlite3`, so its methods go on `ChatThreadStore`.

## 4. Foundations (changes to existing code that everything else depends on)

### 4.1 Allowed types drive the schema
- `controller_response_schema(phase, *, allowed_types: Sequence[str] | None = None, ...)`: when given, the tight
  (`oneOf`/`anyOf`) branches and the flat `type` enum are built from `allowed_types` instead of `_PHASE_TYPES[phase]`.
- `create_controller_step(..., allowed_types=...)` passes it through. `ControllerLoop` always sends its own
  `_allowed_action_types()`.
- This also **fixes a latent bug**: ACTIVE with the task subsystem enabled appends `propose_mode` in
  `_allowed_action_types` (`controller_loop.py:706-709`), but the schema never offered it.
- `_PHASE_TYPES["AGENT"] = ["tool_call", "edit", "progress", "report"]`. `ControllerPhaseSM` accepts `start="AGENT"`.
- `report` is added to `_VARIANT_SPECS` (tight: required `thought`, `summary`), the flat schema (`summary` already
  exists), `_RESERVED_ACTION_TOOL_NAMES`, and `_empty_action_correction`. **An empty `summary` is corrected and retried**,
  because the report is the whole deliverable (D8).
- `MALFORMED_CORRECTION` gains the allowed type list for the current iteration
  ("Allowed types right now: tool_call, edit, progress, report"), so a flat-schema model has a signal to recover.

### 4.2 System prompt: one tagged template per text, rendered per audience
- **Mechanism: tagged templates (§4.7).** `CONTROLLER_SYSTEM_PROMPT` stays **one source text**. Regions that aren't for
  every audience are wrapped in audience tags (`<<main>>…<</main>>`, `<<child>>…<</child>>`, `<<type:propose_mode>>…`,
  `<<tool:write_todos>>…`, `<<perm:dontAsk>>…`, …) and rendered by `render_prompt(template, ctx)`. Shared text is
  untagged, so it's **shared by default**, and a future improvement to it reaches children automatically. There are no
  separate parent/child copies.
- What gets tagged (the full content is in §4.6):
  - the preamble's "WHEN THE REQUEST NEEDS A CHANGE … submit_changes … Plan Mode" is `main`, with `child:edit` and
    `child:readonly` alternatives;
  - each variant's teaching is wrapped in `type:<variant>`, so `answer`/`clarify`/`propose_mode`/`submit_changes`
    teaching vanishes for AGENT (replacing the old per-variant split);
  - the TODO text is wrapped in `tool:write_todos`;
  - the new `report` segment is `type:report`.
- The render context comes from the **agent's base type set** (`AgentContext.allowed_types`), **never** the per-iteration
  set. The §6.5 final-iteration restriction narrows only the response schema, not the system prompt, so the prefix stays
  byte-stable across the run. This is the same principle the parent follows across PLAN→ACTIVE
  (`controller_loop.py` ~69-73).
- **Loop correction strings use the same mechanism** (inline tags, same resolver):
  - `_reserved_tool_name_correction` (`controller_loop.py:191-198`): the Plan Mode/propose_mode clause is `main`;
  - `_progress_repeat_correction` (`:397-401`) and `_progress_dedup_correction` (`:416-418`): the terminal list is
    `main` (`submit_changes`/`answer`) vs `child` (`report`);
  - the empty-edit redirect (`:1359-1367`): `main` "…emit type='submit_changes'" vs `child` "…emit type='report'";
  - the ledger block text (`:1507-1516`): `report`'s own wording ("report is BLOCKED — N todo item(s) still open …")
    is a `child` region.

  These are module-level functions today with no agent parameter. Each gains `ctx` (the render context), threaded
  through the correction chain in `_iterate` (~`controller_loop.py:1096-1107`).
- **This list is not the whole child prompt surface.** §4.6 is the complete, audited inventory of every model-facing
  string a child receives and what it renders to. It supersedes any narrower list here.
- **Parent regression rule:** for `agent=None`, the assembled prompt **and every correction string** must be
  **byte-identical** to today's (golden tests). The only exceptions are two deliberate factual fixes: the
  `run_command` description's "shadow workspace" (it rides `tools_json` into the system prompt) and the unreachable
  `edit_session.py:58` message (§4.6.3, §4.6.4).
- For `agent != None`, appended to the rendered `CONTROLLER_SYSTEM_PROMPT`, in this order (single placement,
  §4.7.6):
  - first, an `_AGENT_ROLE_BLOCK`: identity (sub-agent `{label}` dispatched by another agent), your only terminal is `report`
    and it's everything the dispatcher gets, plus the full precedence/translation rules of §4.6.6 (you can't ask, but
    humans can approve/reject via cards; shared workspace; no VCS mutations);
  - the persona body (labeled block, `.replace` not `.format`);
  - `_MEMORY_BLOCK` rendered for the child: its `remember` sentences are `<<tool:remember>>` regions and its `recall`
    sentence a `<<tool:recall>>` region (§4.7.6), so a child is taught only the memory tools it has;
  - AGENTS.md;
  - the skills catalog **only if** `agent.skills_catalog_enabled` (i.e. `read_skill` survived the tool filter);
  - MCP block (when MCP tools are present, §4.6.2).

  So the one order is: role block → persona → memory → instructions → skills → MCP (today's memory → instructions →
  skills → MCP order, with the role block and persona first).
- The prompt is stable per agent definition, so it caches.

### 4.3 AGENT branch in the per-iteration payload
`build_controller_step_payload` gets an explicit `elif phase == "AGENT":` branch. Today anything that isn't ACTIVE
falls into `else:  # PLAN` (`controller_prompts.py:735`), which would tell children they're in Plan Mode. Its hints:
- **Entry** (`iteration == 0`): ground first (locate → read); your task is in `goal`; stay inside the files
  named in it; finish with `report`.
- **Mid-turn:** reflect → continue (tool_call/edit) or `report`. When `pending_reconcile_files` is set, the AGENT
  reconcile checkpoint of §4.6.5 leads the hint (never the ACTIVE checkpoint text).
- **Final step** (`iteration == max_iters`, the same iteration §6.5 restricts to `["report"]`): "budget reached — emit
  `report` now with everything you found and changed, and list what's unfinished." At `iteration == max_iters - 1`
  the hint is a softer "one step left: finish your current action, then report". Hint and restriction line up.

### 4.4 ReasoningEngine protocol
`ReasoningEngine` (`reasoning/contracts.py`), `DefaultReasoningEngine` and `ScriptedReasoningEngine` gain:
- *(rev 11, as shipped in Plan 1A)* `create_controller_step(..., allowed_types: Sequence[str] | None = None,
  render_ctx: RenderContext | None = None, persona: str | None = None)`. `render_ctx=None` renders for the main agent
  (byte-identical). The loop passes each new kwarg **only when the engine's signature accepts it**
  (`react_common.accepts_kwarg`), so pre-existing test fakes keep working. Consequence for child tests: a fake that
  lacks `render_ctx` silently gets main-agent behavior, so every child-path test uses a fake that accepts it.
- `with_model(model: str) -> ReasoningEngine`. `DefaultReasoningEngine` returns a new instance that **shares its
  transport and loaders**. `ScriptedReasoningEngine` returns `self`. *(rev 11)* The returned engine is **not**
  registered with `ProviderRuntime`, so a provider hot-swap (`PUT /v1/config/provider`) that lands mid-dispatch does
  not reach a running child with a model override; the next dispatch picks it up. Accepted for v1.
- `ScriptedReasoningEngine` gets **per-agent scripts** (*(rev 11)* `agent_scripts: dict[str, list[dict]]`, keyed by
  `render_ctx.agent_label`, falling back to the shared script). The shared response index (`scripted_engine.py:140`) would otherwise make
  concurrent-children tests nondeterministic.

### 4.5 Multiple gates (replaces the single gate)
**Model and storage:**
- `ChatThread.pending_controller_gate: PendingGate | None` becomes **`pending_controller_gates: list[PendingGate]`**.
- `PendingGate` gains `gate_id: str` (uuid hex) and `agent: {id, label, name} | None` (`None` = parent). For edit
  gates the payload also records `shadow_key`.
- Storage migration: a stored single gate becomes a one-item list. *(rev 11)* Its id is the deterministic
  `legacy-{kind}`, not a freshly minted uuid: the migration runs on every read, so a minted id would differ per poll
  and no decision could ever address the gate (the `ChatMessage.id` lesson, CLAUDE.md "Chat rewind"). For the same
  reason `gate_id` is **never** a pydantic `default_factory`: `""` means "not stored yet", `add_controller_gate`
  assigns a uuid, and callers that must key a future before storing mint one with `PendingGate.new(...)`.
- New `ChatThreadStore` methods: `add_controller_gate(thread_id, gate)`, `remove_controller_gate(thread_id, gate_id)`,
  `clear_controller_gates(thread_id, *, agent_id=None)`. They're synchronous read-modify-write with no `await`, so race-safe.
- `set_controller_gate` is removed; every caller migrates.

**Futures:** `_pending_command`, `_pending_edit` and `_pending_mcp` are re-keyed from `thread_id` to **`gate_id`**. Every
raise site adds its gate, awaits, and removes **its own** gate in `finally`. Consumers that act on "every pending gate
of a thread" (`set_review_pref`, `stop_turn`, child stop) enumerate the thread's `pending_controller_gates` list and
look up each future by `gate_id`. The list is the index, so no separate `gate_id → thread_id` map is needed.

**Every consumer to update** (the full list; the implementation plan must touch each one):
- **Backend (`chat/controller.py`):** `_edit_decision_cb`, `_command_approval_cb`, `_mcp_approval_cb`, `resolve_edit`,
  `resolve_command`, `resolve_mcp`, `resolve_mode`, `resolve_clarify`, `_promote_orphaned_edit`,
  `set_review_pref` (resolve **every** pending edit gate as accept), `stop_turn`, and `handle_message`
  (its clear-at-start becomes `clear_controller_gates`).
- **Backend (other):**
  - `chat/live_state.py` (`resolve_thread_live` and its early return; `ThreadLiveState`);
  - `chat/storage.py` (`restore_controller_state` for rewind, `get_thread`, `list_threads`);
  - `chat/models.py`;
  - `api/routes.py`: the chat decision routes get request bodies with an optional `gate_id`. `/command-decision`
    gets a chat-only wrapper model, since `CommandDecision` is shared with `/tasks/{id}/command-decision`.
- **Route semantics:**
  - a missing `gate_id` with exactly one pending gate of that kind uses it (keeps `scripts/verify` working);
  - a missing `gate_id` with more than one returns **409**;
  - an unknown `gate_id` returns 404. This is **benign by design**: a card click can race an auto-accept (`/review-pref`)
    or a child stop that just removed the gate. *(rev 11)* The extension treats a 404 **or** 409 from a
    gate-addressed chat decision (`/edit-decision`, `/command-decision`, `/mcp-decision`) as "already resolved":
    no error toast, the next `/live` poll redraws. A 404 from any other route (e.g. "task not found") still surfaces.
  - *(rev 11)* The chat request bodies (`ChatCommandDecisionRequest`, `ChatMcpDecisionRequest`) are imported at
    **module level** in `routes.py`: the module uses `from __future__ import annotations`, so a model imported inside
    `build_router` is invisible to FastAPI's annotation resolution and every call answers 422.
- *(rev 11)* **Decision routing in the extension is by gate id**, not by guessing from `activeTaskId`: a `task:` id
  resolves on the task route, any other id on the chat route with `gate_id`. Each rendered gate also carries its own
  owner id — the active task for `task:` gates, the **thread** for controller gates — so a controller gate listed
  next to a task gate still posts to the thread.
- *(rev 11)* **Staged removal of the single-gate field.** Plan 1B ships the backend first with `/live` returning both
  `pending_gates` and the legacy `pending_gate` (= the first gate), so the pre-1B frontend keeps working between
  commits; the frontend then moves to the list and a final task deletes `pending_gate` everywhere. The end state is
  exactly this section's.
- *(rev 11)* **`shadow_key` on edit gates is Phase 2**: only the parent's shadow (`chatturn-{thread_id}`) exists in
  Phase 1, so Phase 1 edit payloads are unchanged and Phase 2 adds `shadow_key` together with child shadow naming (§7.5).
- *(rev 11)* **Stop.** Parent `stop_turn` needs no gate code: each raise site pops its own future and removes its own
  gate in `finally`, which runs when the turn task is cancelled. A **child** stop (Phase 2) must cancel the child's
  task (so those `finally` blocks run) and then call `clear_controller_gates(thread_id, agent_id=…)` as a backstop.
- **`/live` shape:** `ThreadLiveState.pending_gate` is **replaced** by `pending_gates: list[PendingGate]`. It holds the
  controller gates plus the task-derived gate (when the task subsystem produces one), which gets a synthetic
  `gate_id = "task:{task_id}:{kind}"`. Task-derived decisions keep using the task routes.
- **editor-client:**
  - `task-contracts.ts`: `PendingGateSchema` (+`gate_id`, `agent`), `ThreadLiveStateSchema` (`pending_gates`);
  - `http-backend-client.ts`: the explicit field mapping; `postEditDecision`, `postChatCommandDecision`,
    `postChatMcpDecision` gain `gateId`.
- **Extension `controller.ts`:**
  - `pollThreadLiveState` and `lastLiveSignature` (`gate` → `gates`);
  - `channelActive` (`live.pendingGate?.kind` checks, ~line 1863);
  - the `ControllerUI` `renderLiveGate`/`clearLiveGate` → `renderLiveGates(list)`;
  - the `chat-panel.ts` host messages.
- **Webview:**
  - `types.ts` (`liveGate` → `liveGates`, `PendingGate`);
  - `hooks/useAppState.ts` (~323-327);
  - `components/LiveSlot.tsx` (stack; key by `gate_id`);
  - `inputAvailability.ts` (any gate pending → locked);
  - every gate component in `components/messages/gates/` posts its `gate_id` back and renders an agent chip when
    `agent` is set.
- **Tests:** every test that reads or sets `pending_controller_gate` (about 17 files) migrates to the list API.

**Phase-scoping rule:** `clarify` and `mode` gates are only ever raised by the parent (children's `AGENT` type set
excludes them).

### 4.6 Child prompt surface: complete inventory (from the cycle-5 leakage audit)

Every source of model-facing text was audited line by line. A child must never receive text that:
- names an action it can't emit, or Plan Mode;
- tells it to ask or wait for "the user";
- references a tool it lacks;
- assumes it's the sole actor, or has a retrieval seed or conversation it doesn't have;
- contradicts other text it receives.

**Mechanism:** every row below is implemented as **tagged regions in one template** (§4.7), never as a separate
parent/child copy. "Parent text" is what renders for `main`; "child form" is what the `child*`/`perm:*`/`tool:*`
regions render to. Child prompts are per agent (they never share the parent's KV prefix), so permission-specific text
costs nothing. The main rendering is byte-identical to today (§4.7.4).

#### 4.6.1 `CONTROLLER_SYSTEM_PROMPT` (`controller_prompts.py` 218-380)
| Parent text (line) | Child form |
|---|---|
| "You are an agentic coding assistant in a chat turn. You own this turn's loop." (219) | "You are a sub-agent carrying out one task for another agent (your *dispatcher*). You own this task's loop." |
| `{"type":"answer"}` as the invalid bare-object example (222) | `{"type":"report"}` |
| GROUND block: "Your retrieval seed … is a map", "answer or propose from the seed", "…verbatim in the seed excerpts" (226-236) | "Your task text tells you what to look at; it does not contain the code. LOCATE then READ before you claim anything." All seed references dropped. |
| "A purely conversational message … may be answered directly" (238-239) | removed |
| "…would not change your answer" (240) | "…your report" |
| "WHEN THE REQUEST NEEDS A CHANGE … submit_changes … Plan Mode" (243-248) | editing children: "ground, then emit `edit` actions, then `report`". **Effective-`plan` children:** "You are read-only: investigate and put your findings in `report`." |
| "run_command and every other tool are directly available … The ONLY exception is Plan Mode" (254-257) | per permission: `default`/`acceptEdits`: "commands may pause for a human approval card; if rejected, adapt"; `dontAsk`: chosen by the shell policy at spawn time. Under `ask`: "commands run only if a remembered rule allows them; otherwise they're refused and nobody is asked". Under `allow_all`: "commands run without approval". `plan`: sentence removed (tool not offered) |
| `tool_call` WRONG example `{"type":"tool_call","tool":"edit",…} ← INVALID … To write a file, emit a top-level {"type":"edit",…}` (263-264) | editing children: kept; **effective-`plan` children: the `edit` example and the "To write a file…" sentence are removed** (they can't edit) |
| progress: "tell the user …", "shown to the user immediately", "'answer' is for the finished reply" (283-289) | "Progress notes are shown to a human watching, but **they do not reach your dispatcher; only `report` does.** Put every finding in the report." |
| edit header "…no permission step required" (308) | `default`: "edits may pause for a human review card; if rejected, revise"; `acceptEdits`/`dontAsk`: "edits apply immediately" |
| Edit-segment batching paragraph "use the todo list (see TODO LIST POLICY) and do ONE item per edit" (331-334), TODO sequencing "your FIRST action MUST be write_todos … submit_changes stays BLOCKED" (335-340), and the whole TODO LIST POLICY (350-375) | **All three included only if `write_todos` is in the child's tools** (no dangling "see TODO LIST POLICY" otherwise). Sequencing child form: "…before your first edit, write the todo list … `report` stays BLOCKED …" ("before your first edit", not "FIRST action", so it doesn't collide with "ground first") |
| submit_changes variant's "re-run lint/tests before finishing" (342-348) | moved into the `report` segment |
| "…a plain answer, or a clarification — just edit directly and submit" (363-364) | "…just edit directly and report" |
| "Every change must serve the user's original goal" (367-368) | "…must serve the task you were given" |
| run-item evidence via run_command (375) | TODO segment gated as above; for `plan` children run_command isn't offered |
| "prefer live tools … over the retrieval seed" (377-378) | "…prefer live tools" (seed clause dropped) |
| answer / clarify / propose_mode / submit_changes variants + `_PROPOSE_MODE_MODES_*` | omitted (not in the base type set) |

**`report` segment** (new): `{type:"report", thought, summary}`. The summary is the **only** thing the dispatcher
receives, never truncated, so make it complete. Before reporting, re-run the relevant lint/tests if you edited code.
Structure: **Summary / Changes** (files + what changed) **/ Verification** (commands run + results) **/ Assumptions
made / Open questions for the dispatcher / Unfinished**.

#### 4.6.2 Appended blocks
- `_MEMORY_BLOCK`: its `remember` sentences are `<<tool:remember>>` regions, so children are taught `recall` only (§4.7.6).
- `_SESSIONS_BLOCK`: never appended for children (PTY tools excluded). Clean.
- **`_MCP_BLOCK`** (425-438), child form: "…calls may pause for a human approval card; if rejected or refused, adapt and
  note it in your report — you cannot ask." For `dontAsk`: "…run only if previously approved; otherwise refused."
  **Not appended for effective-`plan`** (MCP tools filtered out, §5.6).
- **`_SKILLS_BLOCK_HEADER`** (464-514), child form: keep "check the catalog; `read_skill` when one matches your task";
  drop the "UNCONDITIONAL / FIRST action: read_skill" framing and the answer/propose_mode references (use edit/report);
  the "run bundled scripts with run_command" sentence only when `run_command` is offered. No per-iteration skill check
  exists for AGENT, and the child text doesn't promise one.
- **`_INSTRUCTIONS_BLOCK_TEMPLATE`** (418-422), child form: "Project instructions (AGENTS.md), written for an agent working
  with a human. They apply to you **below** the sub-agent rules above and your dispatcher's task; read 'the user'
  there as your dispatcher."

#### 4.6.3 Tool definitions (tools_json), per agent
- **`write_todos`** (`chat/todo_source.py` 16-27), child description: "…a 'run tests/verify' step from **your task**",
  "**report** is BLOCKED while any item is pending or in_progress". `TodoLedger.render()` and outputs are clean.
- **`run_command`** (`tools/registry.py` 141-153):
  - Child description: "Runs in the **real, shared workspace**. Other agents may be editing files concurrently, so
    test results can reflect their in-progress work." Plus the per-permission approval sentence (§4.6.1).
  - **Parent fix too:** the description is shared with the task-path `ToolLoop`, where the CWD really is the shadow,
    but on the controller path it's wrong (`BuiltinToolSource` passes the real workspace as `shadow_root`). The
    neutral "in the workspace" is true on both paths. This is a deliberate parent-text change (§15 golden
    exception). Because the text rides `tools_json` into the system prompt, it causes a **one-time KV-prefix break**
    for existing threads on the first turn after upgrade. Accepted.
- **`dispatch_agents`** description (new): addressed to the dispatching agent; never says "the user".
- Clean for children: `search_code`, `read_file`, `list_directory`, `read_env_profile`, `search_semantic`,
  `query_graph`, `recall`, `read_skill`. `remember` and PTY tools are excluded. MCP descriptions are server-supplied
  third-party text (§4.6.6 precedence).

#### 4.6.4 Strings appended to history
- **Command denials:** today `tools/registry.py:377-379` says "Command rejected **by user**" for every
  `CommandDecision(approve=False)`, which is false for `dontAsk` auto-denials and timeouts. The command approval
  callback instead returns an internal `ApprovalOutcome` carrying `denied_by` (defined in the MCP bullet below; kept
  off the `CommandDecision` request model), and the registry words each case. *(rev 11: exact shipped strings,
  produced by `tools/approvals.py::denial_text(outcome, subject=…, user_text=…)`; `{cmdline}` is the command plus
  its args)*:
  - `user`: unchanged, byte-identical to today (`run_command`: "Command rejected by user: {cmdline}. Try a different
    approach (e.g. a static check)."; `start_session`: "Command rejected by user: {command}. Do not retry the same
    command — adapt or ask."; MCP: the tagged `_MCP_REJECTED_TEMPLATE`);
  - `policy`: "Command `{cmdline}` is not permitted for this agent (no remembered rule allows it); nobody was asked.
    Work without it or note the need in your report." (MCP: "MCP tool `{server}.{tool}` is not permitted …");
  - `timeout`: "No decision arrived in time; command `{cmdline}` was not run." (MCP: "No decision arrived in time; MCP
    tool `{server}.{tool}` was not run.").
- **MCP rejection** (`mcp/tool_source.py:67-68`, "…adapt your approach or ask"), child form: "…adapt your approach,
  or note the blocker in your report." Today the MCP tool source only receives a `bool`
  (`ApprovalCallback = Callable[[str, str, dict], Awaitable[bool]]`, `mcp/tool_source.py:22`;
  `_mcp_approval_cb` returns `decision.approve`). So **`ApprovalCallback` changes to return an internal
  `ApprovalOutcome{approved: bool, denied_by: "user"|"policy"|"timeout"|None}`** *(rev 11: one model for commands and
  MCP — `ApprovalOutcome{approved, denied_by, decision: CommandDecision | None}` in `domain/models.py`, built with
  `ApprovalOutcome.from_command(decision, denied_by=…)`, `.allow()`, `.deny(by)`)*, and `_mcp_approval_cb` and
  `McpToolSource.execute` are updated to word policy and timeout denials truthfully.
- **`denied_by` stays server-internal.** `CommandDecision` and `McpToolDecision` are **request bodies**
  (`/tasks/{id}/command-decision` `routes.py:1021`, chat `/command-decision` `:1751`, `/mcp-decision` `:1764`), so a
  client must not be able to post `denied_by: "policy"`. Instead of adding the field to those models, the approval
  callbacks return the internal `ApprovalOutcome`. For commands it is `ApprovalOutcome{decision: CommandDecision,
  denied_by}`, which keeps the existing decision (and `rule_from_decision`) intact. Every command-approval producer
  and consumer migrates together: the producers are the task engine's `_build_command_approval_callback`
  (`orchestrator/engine.py`) and the chat `_command_approval_cb`; the consumers are `ToolRegistry`'s run_command path
  (`tools/registry.py`) and `ExecSessionToolSource.start_session` (`exec_sessions/tool_source.py`). Routes still build a plain `CommandDecision`/`McpToolDecision` from the request, which maps
  to `denied_by="user"`. Sites that set the other values:
  - `"policy"`: the child `dontAsk` auto-deny;
  - `"timeout"`: the `TimeoutError` branches of `_command_approval_cb`/`_mcp_approval_cb` (`controller.py`), and the
    task path's timeout (`orchestrator/engine.py` ~2064), which today also misreads as "rejected by user".

  `rule_from_decision` (`tools/command_rules.py:20`) and the editor-client `CommandDecisionSchema` are unaffected.
- **"REJECTED by user: {reason}. Revise and re-emit."** (`controller_loop.py:1496`): accurate for a gated child edit
  (a human did review it). It stays; the role block (§4.6.6) states that humans can approve or reject.
- **Retrieval delta** (`controller.py:656-658`): **not passed to children** (it references the seed they don't have).
  For the parent's §6.4 dispatch case, a variant reads "Workspace changed by sub-agents: {files}…".
- **`edit_session.py:58`** "emit at least one op or submit_changes": made neutral ("emit at least one op") for everyone
  (unreachable today; harmless parent change).
- Everything else the loop/registry/patch engine appends is verified clean (parse/edit/salvage/duplicate/retry/applied/
  PATCH FAILED/shell/files/preflight).

#### 4.6.5 Payload (`build_controller_step_payload`)
- AGENT branch (§4.3). ACTIVE/PLAN text is unreachable for AGENT.
- **Reconcile checkpoint:** the loop sets `pending_reconcile_files`/`reconcile_item` after any accepted edit while the
  ledger is open (phase-independent). The AGENT branch renders its **own** checkpoint: "Your current todo item is
  '{title}'. Did this edit complete it? If yes, write_todos marking it done (cite this edit), then continue or report.
  If not, continue it."
- Field names (`goal`, `conversation_history`, `todo_status`, …) are neutral labels. `goal` **content** is
  parent-authored and is governed by §4.6.6 and §6.6.

#### 4.6.6 `_AGENT_ROLE_BLOCK`: identity, precedence and translation rules
Third-party text (skill bodies, AGENTS.md, MCP descriptions, and parent-pasted templates such as SDD's
`implementer-prompt.md`) can't be rewritten, so the role block tells the child how to read it:
- **Identity:** "You were dispatched as a sub-agent by another agent." (This also activates markers like
  `<SUBAGENT-STOP>` in `using-superpowers`.)
- **Precedence:** tool/safety constraints > these sub-agent rules > your dispatcher's task > project instructions
  (AGENTS.md) > skill bodies. Text addressed to "the user" / "your human partner" means **your dispatcher**.
- **You can't ask:** you can't ask questions or wait for replies. When any instruction says ask, confirm, get approval,
  or present options:
  - a reasonable, reversible default exists → take it and list it under **Assumptions made**;
  - consequential or irreversible → don't do it; list it under **Open questions** and finish everything else.
- **Humans can still approve:** a human may approve or reject your commands and edits via cards. If rejected, adapt.
- **Shared workspace:** other agents are editing concurrently. Stay inside the files your task assigns; if you must
  touch another file, re-read it first and mention it in your report.
- **No VCS mutations:** never run version-control changes (commit, add, checkout/switch, stash, reset, rebase, merge,
  pull, push, worktree, branch, restore, clean). **The main agent commits after the work is done.** (Not "your
  dispatcher": a depth-2 child's dispatcher is itself a VCS-blocked child.) This is **mechanically enforced**; see
  the VCS guard below.

- **Dispatch:** if a skill says to dispatch sub-agents and `dispatch_agents` isn't in your tools, do the work yourself.
- **Only `report` reaches your dispatcher.** If told to write a report file, also put its full content in `report`.
  Skip "announce"/narration steps.

**VCS guard (children only).** It runs inside the child's `run_command` path **before the approval callback**, so a
human is never shown a card for a command that will be refused. The parse:
1. Tokenize the final shell line built by `tools/shell.py` (`_split_command` → `_build_shell_command_line`) with
   `shlex.shlex(line, posix=True, punctuation_chars=True)`, so operators split even without spaces (`a&&git commit`)
   and subshell parens are separate tokens (`( git commit )`).
2. Split into segments on the control operators `&&`, `||`, `;`, `|`, `&`, `(`, `)`, newline. (The tool's own
   description teaches `&&`/`|` chaining, so checking only the first command is trivially bypassed, e.g.
   `cd sub && git commit`.)
3. Per segment, strip leading `VAR=val` assignments and the wrappers `env`, `command`, `exec`, `sudo`, `nohup`, `time`.
4. **Any token in a segment whose basename is `git` starts a git invocation**, checked from that token (so `timeout 60
   git …`, `nice git …`, `stdbuf -o0 git …` and the `xargs`/`find -exec` forms are all caught; false positives such as
   `echo git commit` are accepted, since the guard fails closed). From that token: skip the global options `-C <p>`, `-c <k=v>`, `--git-dir[=]`, `--work-tree[=]`,
   `--no-pager`, `-P`, `--exec-path`, then check the subcommand against an **allow-list of read-only subcommands**:
   `status, diff, log, show, blame, grep, ls-files, ls-tree, rev-parse, describe, shortlog, cat-file`, and
   `config` **only** when every following argument is one of `--get`, `--get-all`, `--list` (plus their key
   operands). Anything else is refused, which covers user-defined aliases like `git ci` that a deny-list could never
   enumerate.
5. **Any token in a segment whose basename is `sh`/`bash`/`zsh` followed by `-c`, or any `eval` token, has its string
   argument(s) checked recursively by steps 1–5**, wherever it appears in the segment (so `timeout 5 bash -c "git commit"`,
   `nice eval "git reset --hard"`, `xargs sh -c 'git commit -am x'` and `find . -exec bash -c 'git stash' \;` are caught).
6. **Indirect invocations are checked, not skipped:**
   - command substitution (`$(…)`, backticks): the inner command text is extracted with a balanced scan and checked
     recursively by steps 1–5 (so `echo $(date)` passes and `$(git commit -am x)` is refused);
   - a line that can't be tokenized (unbalanced quotes or substitution) is **refused** (fail closed).
7. Refusal message: "Version-control changes are blocked for sub-agents: other agents are editing this workspace, and
   the main agent commits after the work is done. Read-only git (status/diff/log/show) is fine."

**Residual gap (documented, accepted):** VCS calls inside scripts, `make` targets, or `npm run` hooks aren't visible to
the parse. The §4.6.6 prompt rule is the backstop.

### 4.7 Tagged prompt templates

One source text per prompt/string, with audience-tagged regions, rendered by one pure function. This replaces every
"parent form / child form" pair in §4.2 and §4.6. New module: `agentd/prompting/tagged.py`.

#### 4.7.1 Syntax and vocabulary
- The open marker is `<<tag>>`; the close marker is `<</tag>>`. Regions may nest. Untagged text renders for everyone.
- `<<` never occurs in any current prompt source (checked: `{{` and `[[` do occur, `<<` doesn't), and the validator
  (§4.7.3) rejects any `<<` that isn't a well-formed tag, so a future collision fails loudly.
- **Closed vocabulary.** A region renders iff its own tag holds **and** every enclosing region renders.

| Tag | Holds when |
|---|---|
| `main` | the render context is the parent (`agent is None`) |
| `child` | the render context is a sub-agent |
| `child:edit` | a sub-agent whose effective permission ≠ `plan` |
| `child:readonly` | a sub-agent whose effective permission = `plan` |
| `perm:default` / `perm:acceptEdits` / `perm:dontAsk` / `perm:plan` | a sub-agent with that effective permission |
| `shell:ask` / `shell:allow_all` | a sub-agent, with that shell policy (resolved at spawn time) |
| `tool:<name>` | **main: always**; sub-agent: `<name>` is in its tool definitions |
| `type:<variant>` | **main: always** (every parent variant, e.g. `answer`, `propose_mode`, `submit_changes`, …; never `report`); sub-agent: `<variant>` is in its **base** type set |

- **Main is never gated by capability tags.** The parent's prompt today teaches every variant in every phase (the
  cache-stable PLAN→ACTIVE prefix) and doesn't vary with `write_todos`/`remember` presence. So `tool:*` and `type:*`
  always render for main (except `type:report`), and `perm:*`/`shell:*`/`child*` never do. Main's rendering is
  therefore a pure function of today's inputs, which is what makes byte identity provable (§4.7.4).
- **`tool:`/`type:` tags are only for text that is unconditional for main today.** Main's existing presence-based
  appends stay code-level, exactly as now: `_MEMORY_BLOCK` iff memory is enabled, `_MCP_BLOCK` iff an `mcp__` tool
  exists, `_SESSIONS_BLOCK` iff `start_session` exists, the skills catalog and instructions iff present, and the
  payload's `_graph`/`skills_available` gating. Tagging any of these would make them render unconditionally for main.
- There's no `else`, no negation, and no expressions. Alternatives are written as sibling regions
  (`<<main>>…<</main>><<child:edit>>…<</child:edit>><<child:readonly>>…<</child:readonly>>`).

#### 4.7.2 Rendering rules (`render_prompt(template: str, ctx: RenderContext) -> str`)
- *(rev 11, as shipped)* `RenderContext` is a **flat, frozen, hashable** dataclass (`prompting/tagged.py`):
  `audience: "main" | "child"`, `permission`, `shell_policy`, `tools: frozenset[str]`, `base_types: frozenset[str]`,
  `agent_id`, `agent_label`. It does not embed the `AgentContext` (which did not exist in Phase 1, and embedding it
  would make the render cache key depend on unhashable fields). Built once per loop. The parent's is
  `RenderContext.main()`; a child's is `RenderContext.for_agent(agent, tools=…)` (added in Phase 2), which copies
  `permissions → permission`, `allowed_types → base_types`, `agent_id`, `label`.
- **Resolution runs on the raw template before any placeholder substitution** (`{tools_json}`, `{propose_mode_modes}`,
  persona, AGENTS.md, skills catalog, skill bodies). Injected user and third-party content is never parsed for tags,
  so an AGENTS.md containing `<<main>>` is inert text.
- **Whitespace rules (the only ones):**
  - a marker that is **alone on its line** (only spaces/tabs around it) is removed **together with that line's
    newline**;
  - **end-of-template case:** a marker-only line with **no newline of its own** (the template's last line, which
    happens because `_MEMORY_BLOCK`, `_INSTRUCTIONS_BLOCK_TEMPLATE` and both `_PROPOSE_MODE_MODES_*` constants end
    without one) is removed together with the newline **immediately before it** in the output. So
    `…automatically.\n<</tool:remember>>` renders `…automatically.` for main (byte-identical) and ends cleanly for a
    child;
  - a marker **inline** with other text is removed with no other change;
  - a dropped region's content is removed verbatim.
  
  There's no trimming, collapsing, or normalization anywhere.
- An **inline** region (open and close marker on one line) must not contain a newline; a **block** region has both
  markers alone on their own lines. **A line holding more than one marker (e.g. `<</main>><<child>>`) is forbidden**:
  sibling alternatives go on separate lines. The validator enforces all of this, so every region is unambiguously
  inline or block and the whitespace outcome is predictable from the source.
- **Blank-line authoring convention:** where a droppable block region (e.g. `type:answer`, `type:clarify`,
  `type:propose_mode`, `type:submit_changes`, `tool:write_todos`) sits between two blank-line-separated sections, the
  region **includes one of its adjacent blank lines**. Dropping it then leaves exactly one separator for children,
  and main is unchanged because the blank renders inside the region. Test, **per template**: for each tagged template, the
  child rendering's `\n\n\n` count is ≤ that template's main rendering's count. (Not "zero": main already has `\n\n\n` at block joins, e.g. the
  `{tools_json}\n` + `\n\nMEMORY` join.)
- **Cache key:** `(template_id, "main" | agent_id)`. `AgentContext` needn't be hashable.
- The output for any context is fully determined by the template and the context. Rendering is a pure function,
  cached per `(template id, ctx)`.

#### 4.7.3 Validation (fails at import time and in tests)
The following raise `PromptTemplateError` naming the template and line, at **module import**, so a bad template can't
ship: unbalanced or mis-nested markers, a tag outside the §4.7.1 vocabulary, an unknown `perm:`/`shell:`/`type:`
value, an inline region containing a newline, a block marker sharing its line with text, or any `<<` not forming a
valid tag.

#### 4.7.4 Byte-identity procedure (main must render exactly today's bytes)
The conversion is done in **three separate commits** so that the conversion itself is proven pure:
1. **Capture goldens first, against unchanged code.** Record today's exact outputs across the main matrix:
   - `format_controller_system_prompt` for phase {PLAN, ACTIVE} × task subsystem {on, off} × memory {on, off} ×
     AGENTS.md {absent, sample containing `{ }` and `<<main>>`} × skills catalog {absent, sample} × tool set {builtin
     only, +MCP, +exec sessions};
   - every tagged correction string (§4.2) with representative arguments;
   - the tool descriptions (`definitions()` JSON) of every tool source that becomes tagged;
   - the MCP and command denial strings, and `build_controller_step_payload` output for the ACTIVE/PLAN branches;
  - the **task-path** `ToolRegistry.definitions("explore")` and `definitions("verify")` output (the task `ToolLoop`
    shares these descriptions).
   
   These goldens are committed and green on the unmodified code.
2. **Convert to tagged templates.** The goldens must pass **unchanged**. Any diff means the conversion is wrong, never
   that the golden needs updating. Tests that inspect **raw constants** can break even though the rendered output is
   identical, because markers now sit inside the text. They migrate to asserting on
   `render_prompt(CONSTANT, RenderContext.main())`. Known sites: `test_controller_prompts_progress_narration.py:97`,
   `test_controller_loop_submit_requires_fresh_verify.py:13-17` (slices by `"Variant —"`),
   `test_controller_payload.py:87-203`, `test_controller_schema.py:59-60`. So step 2's bar is "goldens unchanged";
   it is **not** "every test unchanged".
3. **Apply the deliberate model-facing fixes** in their own commit, updating exactly those golden entries and nothing
   else:
   - the `run_command` description (§4.6.3). This also changes the **task** `ToolLoop`'s explore/verify descriptions;
   - `edit_session.py:58` (§4.6.4);
   - the task engine's timeout denial now reading "No decision arrived in time" instead of "rejected by user"
     (§4.6.4). This is a task-path model-facing change, named here.

Plus a render-level property test: for every template, `render(template, main)` equals the template with all
non-`main` regions (`child*`, `perm:*`, `shell:*`, `type:report`) deleted and all markers stripped, per §4.7.2. This
checks the resolver independently of the goldens.

#### 4.7.5 Structural leak lint (source level)
A test scans every tagged template (not rendered output) for main-only markers:
- `submit_changes`, `propose_mode`, `Plan Mode`, `retrieval seed`, `"type":"answer"`, `type='answer'`,
  `ask what they want`, `or ask`;
- `clarify` **only in identifier forms**: `type='clarify'`, `"clarify"`, `` `clarify` ``, `clarify variant`, and
  the `/clarify/` list form. That way a future English sentence like "clarify your assumptions in the report" isn't
  flagged.

Each occurrence must lie inside a region that **can't render for any sub-agent**: `main`, or
`type:{answer|clarify|propose_mode|submit_changes}` (never in AGENT's type sets). `_PROPOSE_MODE_MODES_*` satisfy this
by being wholly `<<main>>` (§4.7.6). An untagged mention fails CI. On current text the lint produces no false positives
(verified in review).

So a future prompt edit that adds "…then submit_changes" to shared text is caught at the source, whether or not any
rendered-output test happens to cover that combination. The per-permission rendered-output tests (§15) remain as the
second layer.

**Unreachable by construction (outside the lint's scope, listed so the scope is explicit):** the ACTIVE and PLAN
branches of `build_controller_step_payload`, the answer/clarify branches of `_empty_action_correction`,
`PROPOSE_MODE_CORRECTION`, `STATE_CHANGING_DECIDE_CORRECTION`, `_answer_intent_divergence_correction`, the loop's
"'answer' is BLOCKED …" text (`controller_loop.py:1185`), and the unused `PARSEFAIL_CORRECTION`. None of
these can fire for the AGENT phase (§4.3, §4.6).

#### 4.7.6 What is converted
Everything with a main-vs-child difference (§4.6), via this one mechanism:
- `CONTROLLER_SYSTEM_PROMPT` (§4.6.1).
- The appended blocks (§4.6.2). `_MEMORY_BLOCK`'s `remember` sentences become `<<tool:remember>>` regions and its
  `recall(query)` sentence a `<<tool:recall>>` region (both render for main as today; a child is taught only the
  memory tools it actually has), so no separate recall-only block is needed. `_PROPOSE_MODE_MODES_*` are wrapped
  wholly in `<<main>>` and are **themselves rendered** with `render_prompt(…, RenderContext.main())` **before** injection
  via `.replace("{propose_mode_modes}", …)`. They're code-owned templates, not third-party content, and the end-of-template
  rule makes them render byte-identical (`…}]}`). The same applies to
  `_MCP_BLOCK`'s approval/"or ask" sentences, `_SKILLS_BLOCK_HEADER`'s FIRST-action/answer/propose_mode sentences,
  and `_INSTRUCTIONS_BLOCK_TEMPLATE`'s framing line.
- Tool descriptions (§4.6.3): `write_todos`, `run_command`, and the new `dispatch_agents` (all-audience). Tool sources
  receive the `RenderContext` at construction (registries are already built per loop, §5.3), and `definitions()`
  renders with it.
- History strings (§4.6.4): command/MCP denial wording, the correction strings (§4.2).
- The child-only additions:
  - the `report` segment lives inside `CONTROLLER_SYSTEM_PROMPT` as a `type:report` region (with the other variants);
  - **`_AGENT_ROLE_BLOCK` is a separate child-only constant, appended by `format_controller_system_prompt` right after
    `CONTROLLER_SYSTEM_PROMPT` and before every other appended block**. Child append order (the one order, same as §4.2):
    role block → persona → memory → instructions (AGENTS.md) → skills catalog → MCP.
    This is its single placement. It precedes the instructions block, so the AGENTS.md child line ("the sub-agent
    rules above") reads correctly. Being child-only, it needs no tags;
  - the AGENT payload branch is new child-only code with no tags needed.

**Construction sites that receive the `RenderContext`** (defaulting to `RenderContext.main()`, so the task path and
every existing test are unchanged):
- `ToolRegistry`, at `tools/sources.py:38` (`BuiltinToolSource`, controller) and `tools/loop.py:1218` (task
  `ToolLoop`, which stays main);
- `TodoToolSource` (`controller.py:240`), `SkillToolSource` (`:246`), `McpToolSource` (`:252`), and
  `MemoryToolSource` via `memory/harness.py:239`;
- `ExecSessionToolSource` (`:446`) isn't converted (excluded for children).

`_WRITE_TODOS_DEF` is a module-level `ToolDefinition` constant today. It keeps the raw template and renders inside
`definitions()` with the source's context. `PlanningToolRegistry` (`planning/registry.py`) is unaffected: it has its
own definitions and no run_command.

**Silent-denial risk:** `exec_sessions/tool_source.py:146-147` reads `getattr(decision, "approve", False)`. It **must**
migrate to `ApprovalOutcome` (typed, not `getattr`), or every `start_session` would be silently denied. §15 has a test.

## 5. Child runtime

### 5.1 A child is a `ControllerLoop`
It runs with `phase="AGENT"`, `agent=AgentContext`, `channel_id="chat:{thread_id}:agent:{agent_id}"`, and inherits
every existing mitigation (`_MAX_MALFORMED`, dedup, parse guidance, thought-strip). The todo-ledger hard block
applies to `report` exactly as it does to `submit_changes`, **except on the final iteration** (§6.5), where `report`
bypasses the block. The loop appends the still-open items to the report as an "Unfinished:" list, and the result is
`partial`.

An effective-`default` child receives the **parent turn's `ChatTurnControl`** (`_turn_controls[thread_id]`), so it reads
the live "Review each edit" preference at every edit dispatch, exactly as the parent does. Other modes get a fixed
control (§5.6).

### 5.2 Context the child starts with
- `goal` = the handoff `prompt`. **No parent history, no retrieval seed.**
- Its own in-memory `TodoLedger` (never persisted to thread columns).
- **Skills:** its own `active_skills` dict with **no persist callback**, so a child never overwrites the thread's
  persisted active skill. `skills:` frontmatter pre-seeds **all** listed skills (each body capped by
  `CRUCIBLE_SKILLS_BODY_MAX_CHARS`; unknown names skipped with a warning). `SkillToolSource` gains `additive: bool`;
  children use `additive=True`, so `read_skill` adds rather than replaces. `skill_catalog_loader=None` is passed to
  the child loop, so `_maybe_force_required_subskill` is inert for children.
- **Memory:**
  - The child runs under `run_id = "{thread_id}:{agent_id}"`, so it gets its own compaction anchor.
  - `MemoryHarness.prepare_turn(..., consolidate: bool = True)`. Children pass `False`, so no consolidation is
    scheduled even when they compact (`harness.py:137-145` would otherwise schedule it).
  - `MemoryHarness.memory_tool_source(run_id, *, allow_remember: bool = True)`. Children get **recall only**, and
    the source is returned whenever a recall engine exists (independent of the consolidator).
  - The recall cache key becomes **per run_id** (`dict[run_id, key]`, replacing the single `_recall_key` slot, which
    thrashes under concurrency). `harness.release_run(run_id)` drops a finished child's cache entries.
- **Artifacts:** `plan_context["artifact_agent_id"]` nests the engine's `controller-turn-NN.json` dumps and the loop's
  `memory-recall-NN.json` dumps under `chat/<thread>/<turn>/agents/<agent_id>/`.

### 5.3 Tools
- Start from the parent's sources (builtin, todo, memory recall-only, skills additive, MCP), filtered by
  `tools`/`disallowedTools` (§9.3).
- `dispatch_agents` is included while `depth < CRUCIBLE_SUBAGENT_MAX_DEPTH`.
- PTY exec-session tools are **excluded** in v1 (thread-scoped; one child could kill a sibling's process).
- If `edit` isn't in the filtered set, `edit` is removed from `allowed_types`.

### 5.4 Model
- `model: inherit` (or absent) reuses the parent's engine instance.
- A full model id uses `parent_engine.with_model(id)`.
- **Claude Code family aliases (`sonnet|opus|haiku|fable`) map to `inherit` with a warning**, because they have no
  meaning on non-Anthropic providers and imported CC agents use them routinely.
- There's no pre-validation, since there's no model catalog. A bad id fails on the child's first call and surfaces
  as `status: failed` with the provider's error.

### 5.5 Callbacks and where child output goes
- **Live events:** the child loop's existing `self._broadcaster.broadcast(self._channel_id, …)` calls go to the
  **child's own channel** unchanged. No loop-level tagging is needed, children's `token_progress` and
  `tool_thinking_chunk` never touch the thread channel, and each child channel has its own 50-event replay buffer
  (no flooding). When the child finishes, the runtime calls `broadcaster.clear_replay(child_channel)` (the replay map is
  never pruned otherwise; late viewers backfill from `GET …/agents/{id}`).
- **Event identity:** `call_index` is segment-relative (`controller_loop.py:1267`) and resets at every pills
  boundary, so it can't be a dedupe key. Instead:
  - Every event **broadcast on a child channel** carries a **monotonic per-loop `seq`**, stamped by a thin
    broadcast wrapper the child loop uses. That's every child-channel event, not only tool events: `chat_progress`,
    `diff_ready`, breadcrumbs, `edit_failed` too. The parent's channel is untouched.
  - Every message appended to the child's `transcript_json` records the `seq` of the event that produced it.
  - `GET …/agents/{id}` returns `last_seq` (a cursor), defined as **the highest `seq` recorded on a persisted
    transcript message**, not the loop's current broadcast counter. So an in-flight `tool_call`, broadcast but not yet
    persisted, is never skipped. `streamAgentChannel` skips any replayed event with `seq <= last_seq`, so
    backfill-then-subscribe, including each reconnect, never renders a duplicate.
- **The thread channel** receives only low-volume events: `agent_started`, `agent_finished`, `agent_status`
  (running/waiting/queued changes), plus the existing gate pokes (`command_approval_requested`,
  `mcp_approval_requested`) with an `agent` field.
- **Durable:** the child gets child-scoped callbacks that write into its `chat_agents.transcript_json`
  (a `list[ChatMessage]`, same model and metadata shapes as thread messages):
  - `on_pills_update` and `pills_seal_cb` upsert and seal the child's in-flight pills message (same segmentation logic);
  - `progress_note_cb` appends a progress message;
  - `edit_record_cb` appends an inert `diff_card` and broadcasts `diff_ready` on the **child** channel;
  - gate decision breadcrumbs go to the **child** transcript and channel, never the parent transcript;
  - the final `report` is appended as the last message.
- **Pills boundaries:** today `_edit_record_cb` and `_write_breadcrumb` call `_mark_pills_boundary(thread_id)`, which marks
  `_active_loops[thread_id]` (the **parent** loop). The child-scoped versions mark the **child's own loop** (held in its
  `AgentHandle`), never the parent's.

### 5.6 Permissions
| value | commands / MCP | edits |
|---|---|---|
| `default` | gate (remembered rules apply) | follow the live "Review each edit" toggle |
| `acceptEdits` | gate | auto-accept |
| `dontAsk` | allowed only via a remembered rule or `CRUCIBLE_SHELL_POLICY=allow_all`; otherwise auto-denied as a tool error (no gate) | auto-accept |
| `plan` | **read-only**: `edit` is removed from `allowed_types`; `run_command` and every `mcp__*` tool are **filtered out of the child's tool list** (so `_MCP_BLOCK` isn't appended either), with a guard as defense-in-depth | — |

- The guard is a new permission-driven correction, `_permission_correction(resp, agent)`, next to
  `_decide_state_change_correction`. The existing PLAN guard only covers `run_command` in PLAN (`controller_loop.py:74-92`)
  and doesn't cover MCP. Its text: "You are read-only for this task: `{tool}` isn't available to you. Investigate with
  the read tools and put your findings in `report`." It's routed through the normal correction chain.
- **VCS guard (all children):** a child's `run_command` refuses any `git` invocation outside the read-only allow-list
  (§4.6.6), before any approval card, returning the §4.6.6 message as a tool error. Parent unaffected.
- `allow_all` widens only *approval* (skipping gates). **It never widens `plan`'s read-only restriction.**
- **No escalation through nesting:** what propagates is **read-only-ness**, not the whole mode. A child's effective
  permission is `plan` if the child itself or **any ancestor agent** is `plan`; otherwise it's the child's own mode.
  `main` imposes nothing. A `plan` agent can still dispatch (e.g. `explore` fan-outs), but everything below it is
  read-only. (The modes aren't a single restrictiveness scale: `dontAsk` is stricter than `default` on commands and
  looser on edits, so a "most restrictive" rule would be ill-defined.)
- **Which children follow the live review toggle:** only effective-`default` children share the parent turn's
  `ChatTurnControl` (§5.1). `acceptEdits`/`dontAsk` children get a fixed `ChatTurnControl(auto_accept_edits=True)`
  that `/review-pref` never touches.
- CC's `bypassPermissions` maps to `default`. An unknown value maps to `default` with a warning.

## 6. Dispatch

### 6.1 Tool
```
dispatch_agents({"agents": [{"agent": "explore", "prompt": "...", "label": "auth survey"}, ...]})
```
- Validation errors (unknown agent, empty prompt, empty list, duplicate label) return `ToolOutput(is_error=True)`
  listing the valid agents.
- `label` is optional (default `{agent}-{n}`) and must be unique within the call.
- **At dispatch start**, before any child runs, the runtime calls a controller hook that:
  1. writes the parent transcript's `ChatMessage(type="agent_dispatch", metadata={"agent_ids":[...], "turn_id":...})`
     (so a mid-run reload shows the roster);
  2. calls `_mark_pills_boundary`;
  3. inserts the `chat_agents` rows (`status: queued`);
  4. broadcasts `agent_started`s.
- The `dispatch_agents` tool pill is **suppressed** in the tool track (filtered by tool name), so the
  `agent_dispatch` message is the only thing that renders the roster.

### 6.2 Concurrency
- **One process-wide `asyncio.BoundedSemaphore(CRUCIBLE_SUBAGENT_MAX_CONCURRENT)`**, owned by `SubAgentRuntime`.
  Process-wide because the cap exists to protect the provider's rate limit. `main` never holds a slot.
- Each child has a **slot token** `{held: bool}`:
  - it acquires before its loop starts (`status: queued → running`);
  - before awaiting its own `dispatch_agents` it releases (`held=False`), and after the batch returns it re-acquires
    (`held=True`);
  - its `finally` releases **only if `held`**.
  
  A cancellation during re-acquire raises before `held` is set, so there's never an over-release.
  `BoundedSemaphore` turns any accounting bug into a loud `ValueError`.
- Results are gathered with `return_exceptions=True`. An exception becomes `status: failed` for that entry only.

### 6.3 Result (one tool result, one entry per child)
```json
{"agent_id": "agent-3f9a…", "agent": "general-purpose", "label": "limiter impl",
 "status": "completed | partial | failed | stopped",
 "report": "<full text, never truncated>",
 "files_changed": ["api/limiter.py", "api/middleware.py"],
 "stale_refusals": 1}
```
`files_changed` is the **union over the child's subtree** (files promoted by the child or its descendants,
from the write log). Per-agent attribution stays in `chat_agents`.

### 6.4 After a dispatch returns (parent loop)
The generic mechanism is a new field, **`ToolOutput.workspace_changes: list[str]`**, defaulting to empty
(`tools/registry.py:18`). `SubAgentToolSource` sets it to the union `files_changed`; any future file-writing tool can
reuse it. The loop never parses tool output. In the `tool_call` branch, if `out.workspace_changes` is non-empty, the loop
(parent or child, since a child can dispatch too):
- `seen.clear()` (re-reads are no longer duplicates);
- sets `_edit_applied = True`;
- calls `retrieval_delta_cb(files)`.

### 6.5 Budget and status
- `maxTurns` maps to `max_iters` (default `CRUCIBLE_SUBAGENT_MAX_ITERS=100`).
- On the **final iteration** (`iteration == max_iters`), the child's per-iteration `allowed_types` (schema only; §4.2)
  is restricted to `["report"]`, the §4.3 final-step hint applies, and the todo-ledger block is bypassed (§5.1). A
  report emitted then is `status: partial`; a report emitted voluntarily earlier is `completed`.
- If the child still produces no report (malformed exhaustion or crash), it's `status: failed` with a **deterministic
  fallback report**: the error, `files_changed`, the open todo items, and the last 10 tool calls (name + args). That
  fallback is synthesized text, not a truncation of anything the model wrote.
- The AGENT type set has no `tool_call`-budget branch to hit, since the final iteration forbids `tool_call`.
- Status vocabulary for UI/`/live`: `queued | running | waiting` (at a gate) `| completed | partial | failed | stopped`.

### 6.6 Parent teaching
- An agent catalog block (`- name: description`), appended to the system prompt when `dispatch_agents` is in the tool
  definitions (the same keyed-off-tools pattern as the MCP/sessions blocks), with a worked few-shot dispatch example:
  self-contained prompts, **one set of files per child**, verify with `files_changed`.
- Parent teaching also states:
  - **children can't ask you anything**, so put every decision, constraint and file assignment in the prompt;
  - **don't tell children to commit or use VCS** (they're blocked from it); commit after the batch yourself, using
    `files_changed`;
  - templates pasted into prompts (e.g. a skill's implementer prompt) are fine: children translate their
    "ask"/"commit" lines per their role rules, and you get assumptions and open questions back in the report.
- The ACTIVE **entry hint** (`controller_prompts.py`, "BIG / multi-part … START A TODO LIST FIRST") gains, when
  `dispatch_agents` is available: "…or, for independent parts touching disjoint files, dispatch them in parallel
  with `dispatch_agents`."
- **Conflict resolution:** the parent's system prompt keeps (byte-identical) "for a multi-part change your FIRST
  action MUST be write_todos" (line 335). The dispatch catalog block, appended only when `dispatch_agents` is present,
  therefore says: "When the parts are independent and touch disjoint files, `dispatch_agents` may be your first action
  instead; the todo list stays yours to reconcile afterwards."

### 6.7 Registry
`AgentRegistry`: `agent_id → AgentHandle{context, thread_id, turn_id, status, task, loop, slot, started_at, ended_at}`
(`loop` is the child's `ControllerLoop`, used for its pills-boundary marking, §5.5).
It supports lookup by thread and by turn for `/live`, stop and the cancel summary (§11).

## 7. Shared-workspace write guard

### 7.1 `WorkspaceWriteLog`
One per thread, in memory, living for the thread's lifetime in the process:
- `seq: int` increases on every promote.
- `last_write[path] = (agent_id, label, name, seq)`.
- Per agent: `spawn_seq`, `last_seen[path]`.
- `main`'s `spawn_seq = 0`, and its `last_seen` persists across turns. So a parent editing a file a child changed in an
  earlier turn, without re-reading, is refused.
- After a backend restart the log is empty. Protection restarts from zero; no false refusals.

### 7.2 Canonical path key
`canonical_path(workspace_root, raw) -> str | None` in `write_log.py`:
- resolves `raw` (relative, `./`, or absolute inside the workspace) to a workspace-relative POSIX path;
- returns `None` for paths outside the workspace.

Used for reads (after `tools/arg_aliases.py` has normalized `file`/`filepath`/`file_path` → `path`) and for patch-op `file`.

### 7.3 Rules
- **Read:** a **successful** `read_file` (not `is_error`; line ranges count) sets `last_seen[path] = seq`.
  `search_code` doesn't count. Both the parent's and children's builtin sources are wrapped.
- **Own promote:** sets the promoter's `last_seen[path]` to the new seq.
- **Stale:** `last_write[path].agent_id != me and last_write[path].seq > max(spawn_seq, last_seen.get(path, -1))`.

### 7.4 Enforcement
- New `PatchFailureCode.STALE_READ` and *(rev 11)* `StaleWriteError(PatchPreflightFailed)` in `chat/edit_session.py`,
  constructed as `StaleWriteError(path, message)` and carrying `.path` and
  `issues=[PatchPreflightIssue(code=STALE_READ, file=path, message=...)]` (the same shape `_edit_failure_guidance`
  reads). Subclassing the preflight error means a raise from `apply()` lands in the loop's existing PATCH FAILED
  branch with no new code. **Catch-order consequence:** it is a `RuntimeError`, so any `except RuntimeError` /
  `except Exception` wrapped around the two `accept()` call sites would swallow it — the new `"stale"` branch must
  catch `StaleWriteError` **first** (Phase 2 pins this with a test). `_EDIT_GUIDANCE_BY_CODE[STALE_READ]` = "Another agent changed this file after your last read. read_file it
  again, then re-emit your edit against its current content."
- **Check 1**, in `TurnEditSession.apply` before any patching: stale raises `StaleWriteError` → the loop's existing
  PATCH FAILED branch with the correct guidance.
  - Message: "`{path}` was modified by agent `{label}` ({name}) after your last read — read it again before editing."
- **Check 2**, in `TurnEditSession.accept`, immediately before `promote_files`, re-check every pending file.
  - The check and the synchronous copy run with **no `await` between them** (race-safe in single-process asyncio).
  - This covers an edit held at a review gate while a sibling promotes.
  - On failure, `accept()` calls `reject()` internally (restoring the shadow from real) and raises `StaleWriteError`.
- **New loop branch:** both `accept()` call sites in the edit branch (`controller_loop.py` ~1427-1437) are wrapped:
  - on `StaleWriteError` → `edit_record_cb(diff, "stale", <stale message>, was_gated)`;
  - append the edit intent (no patch_ops) plus a `PATCH FAILED: <stale message> <guidance>` tool_result;
  - `continue`.
  
  This holds even if the user clicked Accept. `EditRecordCb`'s decision argument gains a third value, **`"stale"`**
  (alongside `"accept"`/`"reject"`). For `"stale"`, `_edit_record_cb` persists the inert diff card as
  `resolved: "discarded"` and always writes the breadcrumb "✗ Not applied: {path} changed since it was read (by {label})",
  whether or not the edit was gated.
- Every stale refusal increments that agent's `stale_refusals` and logs `[subagent] stale-refusal path=… by=…`.
- The parent (`main`) is subject to all of this. The guard is active whenever the write log exists, which is whenever
  the flag is on. With no children it never fires.

### 7.5 `TurnEditSession` changes (`chat/edit_session.py`)
- **Re-seed touched files from real on every `apply`**, not only on first touch.
  - If the file no longer exists in real, the stale shadow copy is **removed**.
  - This fixes the lost-update bug. It's reachable today too, e.g. a formatter run via `run_command` between two edits.
- **Shadow naming:** the parent keeps **`chatturn-{thread_id}`** unchanged, so `_promote_orphaned_edit` keeps working.
  Children use **`chatturn-{thread_id}-{agent_id}`**. Edit gate payloads record `shadow_key`.
- Takes an optional `write_guard` (`WorkspaceWriteLog` + `agent_id`).
- The rewind `checkpoint_cb` is unchanged. Children's edits land in the parent turn's checkpoint (first-seen-wins),
  so rewinding before the turn reverts the whole tree.

**Accepted tradeoff:** children's commands and tests run against real, including siblings' in-progress edits
(Claude Code has the same property). Mitigated by partitioning (teaching + refusal message). External edits by the
user or a formatter aren't in the log (out of scope for v1).

## 8. Gates from children
Children raise command, MCP and edit gates through the §4.5 machinery with `agent` set, subject to §5.6. A child
waiting at a gate is `status: waiting` (`agent_status` event). Stopping a child removes its gates
(`clear_controller_gates(agent_id=…)`) and cancels its futures.

## 9. Agent definitions

### 9.1 Discovery
- Precedence: `.crucible/agents/`, then `.claude/agents/` (project), then `~/.claude/agents/` (user), then built-ins.
  A name wins at its highest-precedence source.
- Directories are scanned recursively; identity comes from `name`.
- The cache is mtime-based (the same discipline as `SkillCatalogLoader`): a new file takes effect next turn with no restart.
- A malformed file is skipped with a warning; loading never raises.

### 9.2 Frontmatter
- **Honored:** `name` (required), `description` (required), `tools`, `disallowedTools`, `model` (§5.4),
  `permissionMode` (§5.6), `maxTurns`, `skills`.
- **Parsed and ignored:** `mcpServers`, `isolation`, `background`, `effort`, `memory`, `hooks`, `color`,
  `omitClaudeMd`, `initialPrompt`, `experimental`.
- The body is the persona.

### 9.3 Tool-name mapping (CC → Crucible)
- `Read → read_file`
- `Grep → search_code`
- `Glob|LS → list_directory`
- `Bash → run_command`
- `Edit|Write|MultiEdit|NotebookEdit → edit` (the action type)
- `Agent|Task → dispatch_agents`
- `TodoWrite → write_todos`
- `Skill → read_skill`
- `mcp__…` unchanged; `mcp__<server>` or `mcp__<server>__*` = all of that server's tools
- `WebFetch|WebSearch → ` no mapping (ignored with a warning; list the MCP names explicitly)

Crucible-native names pass through. `tools` absent = inherit all. `disallowedTools` is applied first, then `tools`.

### 9.4 Built-ins
- **`explore`**: `permissions: plan`; tools `search_code, read_file, list_directory, query_graph, search_semantic`;
  persona: locate then read, write a dense but complete report with `path:line` citations. Never raises gates.
- **`general-purpose`**: all tools (minus §5.3 exclusions), `permissions: default`.

## 10. UI (`apps/vscode-extension/webview-ui` + extension host)

The wireframe `subagent-ui-v3.html` is the source of truth for layout.

- **`AgentRosterCard`** renders from the `agent_dispatch` message.
  - Header: agent count + done/running/waiting tags.
  - Rows: status dot, label, agent chip, current action (`now`), tool/file counts, elapsed (computed client-side from
    `started_at`), **▸** and **⤢**.
  - A nested dispatch renders its own roster inside the parent agent's transcript.
- **Inline expansion (▸):** a scroll box, `height: min(240px, 40vh)`, `overscroll-behavior: contain`, reusing
  `scroll-pinning.ts` (follows newest only while at the bottom).
- **Floating window (⤢):** `.surface-card` over `.scrim`, nearly full panel height.
  - Header: status, elapsed, tool/file/depth meta, **■ Stop**, ✕ / Esc.
  - **Tabs** switch between siblings of the same dispatch.
  - One window at a time.
- **`AgentTranscript`** renders the child's `list[ChatMessage]` with the existing message components (pills,
  progress, inert diff cards, report). The same component is used in both containers. The report is rendered in full.
- **Data for an open agent:** handled by a **new dedicated handler** in `controller.ts`,
  `streamAgentChannel(threadId, agentId)`. `streamTurn` isn't reused: it writes into the parent bubble and stops on
  `chat_done`.
  1. Backfill with `GET …/agents/{agent_id}` and post `agentTranscript {agentId, messages}` to the webview.
  2. Consume `streamChannel("chat:{thread}:agent:{id}")` and post agent-scoped webview messages
     (`agentEvent {agentId, event}`), skipping events with `seq <= last_seq` from the backfill (§5.5). The webview
     reducer merges them into that agent's transcript state.
  3. **Reconnect:** `streamChannel` ends on its 120s idle timeout (`http-backend-client.ts:74`, e.g. a child queued or
     waiting at a gate). When the stream ends while the view is still open and the agent isn't terminal, repeat steps
     1 and 2. When the agent is terminal, stop.
  
  The subscription opens when a row is expanded or the window shows that agent, and closes (abort) when neither does.
  One subscription per open agent.
- **Stacked gates** (§4.5) with agent chips; the composer is locked while any gate is pending.
- Violet-cool design system primitives; never `--color-bg-*`.

## 11. Data flow, persistence, lifecycle

### 11.1 `/live`
`ThreadLiveState.agents`: **every agent of the in-flight turn's dispatch tree** (running and finished), each
`{agent_id, parent_agent_id, depth, name, label, status, now, tool_count, files_changed_count, started_at, ended_at,
report_preview}`.
- `report_preview` is the first ~200 chars, UI-only (D8 covers the model path).
- `now` is the latest tool name + path.
- After the turn ends, `agents` is empty and the roster reads `GET …/agents?turn_id=`.
- **`agents` must be added to `lastLiveSignature` in `controller.ts`.**

### 11.2 Stream events
- **Thread channel:** `agent_started {agent_id, parent_agent_id, depth, name, label}`,
  `agent_status {agent_id, status}`, `agent_finished {agent_id, status, files_changed}`. All three are added to the
  editor-client `StreamEvent` union and handled in `controller.ts` `streamTurn`/`resumeLiveOverlay` as a
  "re-poll `/live`" poke only.
- **Child channels:** the loop's existing event types, unchanged.

### 11.3 Durable storage
- `chat_agents` columns: `agent_id, thread_id, turn_id, parent_agent_id, depth, name, label, prompt, status, report,
  files_changed_json, stale_refusals, transcript_json, started_at, ended_at`.
- `ChatMessage.type` gains **`agent_dispatch`**, in `chat/models.py`, editor-client Zod, `HttpBackendClient`'s mapping,
  and webview `types.ts` (an unknown type in the Python Literal would break every thread read).
- Routes:
  - `GET /v1/chat/threads/{id}/agents?turn_id=` (summaries);
  - `GET …/agents/{agent_id}` (row + transcript + full report + `last_seq` cursor, §5.5);
  - `POST …/agents/{agent_id}/stop`.
- editor-client gains `listAgents`, `getAgent`, `stopAgent`.
- The dispatch tool result (full reports) goes into `controller_conversation_history` like any tool result.

### 11.4 Stop
- **`/stop` on the turn:** cancellation propagates through the gather into every child. Each child's `finally` closes
  its edit session, clears its gates, releases its slot if held, and persists `stopped`.
  - **Parent memory of the dispatch:** `_run_loop`'s `CancelledError` branch asks the runtime for the in-flight
    dispatch and appends a synthetic `assistant` tool_call + `tool_result` to the partial history before persisting.
  - That result holds each child's status, `files_changed` and report (full, for children that finished).
  - The next turn therefore knows what the children already promoted.
- **Stop one agent:** that child's task is cancelled (its subtree too); the gather reports `stopped` for that slot;
  siblings continue.

### 11.5 Restart
At startup a reap (next to the exec-session reap in `main.py`):
- marks `chat_agents` rows in `queued|running|waiting` as `failed ("backend restarted")`;
- removes **child** gates (`agent` set) from every thread with a breadcrumb. Child gates are **not** recoverable: the
  write log is gone, so promoting would bypass the guard;
- deletes leftover `chatturn-*-agent-*` shadow directories.

The parent's orphaned edit recovery (`_promote_orphaned_edit`) is unchanged.

### 11.6 Rewind
In addition to today's behavior, rewinding to a checkpoint:
- deletes `chat_agents` rows whose `turn_id` is in the rewound span;
- deletes compaction anchors (`MemoryStore.clear_anchor`, exists) and segments (a **new**
  `MemoryStore.delete_segments(run_id)`) for those agents' `run_id`s (`{thread}:{agent_id}`);
- resets the thread's `WorkspaceWriteLog` (drops the whole log). Reverted files would otherwise draw refusals
  attributed to agents rewind just erased, and an empty log is always safe (§7.1);
- `RewindStore.preview` also counts `run_command` calls from those agents' `transcript_json`s in its confirm dialog.

### 11.7 Failures
- A crash or `ControllerLoopExhausted` becomes `failed` + the §6.5 fallback report; the parent continues.
- 429s go through transport retries.
- Stale refusals go through PATCH FAILED.
- If every child fails, the parent gets all the statuses.
- Reports are uncapped, so a large batch can overshoot the compaction trigger in one iteration. Accepted; the future
  lever is prompting for dense reports, never truncation.

## 12. Configuration

| Env | Default | Meaning |
|---|---|---|
| `CRUCIBLE_SUBAGENTS_ENABLED` | **on** (end state, D10) | Kill-switch (`0/false/no/off`). Controller-only; inert when `CRUCIBLE_CHAT_CONTROLLER` is off. `warn_if_incoherent_flags` warns only when this is **explicitly** set on while the controller is off. |
| `CRUCIBLE_SUBAGENT_MAX_DEPTH` | `2` | Max nesting below the parent turn. |
| `CRUCIBLE_SUBAGENT_MAX_CONCURRENT` | `8` | Process-wide running-agent cap. |
| `CRUCIBLE_SUBAGENT_MAX_ITERS` | `100` | Child loop budget when the agent has no `maxTurns`. |

`/v1/config` gains `subagents_enabled`. (No VS Code `when`-context: nothing command-level is gated on it.)

## 13. Observability
- Log lines: `[subagent] start|finish id=… parent=… depth=… name=… status=… files=…`, `[subagent] stale-refusal path=… by=…`,
  `[subagent] slot acquire|release id=… held=…`.
- Per-agent artifacts (§5.2).

## 14. Test fixtures note
Phases 1–4 need nothing: the code default is OFF, and sub-agent tests opt in with `monkeypatch.setenv(
"CRUCIBLE_SUBAGENTS_ENABLED", "1")` (the same opt-in pattern `CRUCIBLE_TASK_SUBSYSTEM` tests use). **In phase 5**,
when the default flips ON, `dispatch_agents` and the catalog block would appear in every controller prompt/tool list.
So phase 5 **creates** `services/agentd-py/tests/conftest.py` (it doesn't exist today) with an autouse fixture setting
`CRUCIBLE_SUBAGENTS_ENABLED=0`. Sub-agent tests keep opting in explicitly.

## 15. Testing
- **Parent regression:** the `agent=None` system prompt is byte-identical to a golden captured before the change; the
  ACTIVE/PLAN schemas are unchanged except the `propose_mode` latent-bug fix.
- **Python unit (TDD), chat stores via `ChatThreadStore(tmp_path)`:**
  - AGENT schema (tight/anyOf/flat) + `report` empty-summary correction + final-iteration `["report"]` restriction
    → `partial`; the AGENT payload branch;
  - write log: path canonicalization, errored reads not counted, stale detection, `main`'s cross-turn `last_seen`;
  - Check 2 with an edit held at a gate while a sibling promotes → reject + breadcrumb + PATCH FAILED;
  - the lost-update regression; re-seed deleting a vanished file;
  - slot token: queueing, a nested dispatch releasing its slot, cancel during re-acquire (no over-release —
    `BoundedSemaphore` would raise);
  - one child crashing without affecting siblings; `/stop` cascade + synthetic dispatch result in the partial history;
    single-agent stop;
  - depth limit; multi-gate raise/resolve in reverse order; `gate_id` fallback (1 → ok, >1 → 409, unknown → 404);
  - review-pref resolving all edit gates; the legacy single-gate migration;
  - permissions per mode incl. `plan` blocking MCP and `allow_all` not widening `plan`;
  - definitions: CC frontmatter, tool mapping, precedence, malformed skip, CC model aliases → inherit;
  - memory: no consolidation for children, recall-only source, per-run recall key;
  - skills: additive child `read_skill` + multi-skill preload, thread active skill untouched;
  - restart reap; rewind deletes child rows/anchors/segments, resets the write log, and counts child commands;
  - final-iteration `report` with open todos → bypasses the ledger block, `partial`, with an "Unfinished:" list;
  - child system prompt identical across iterations incl. the restricted final one (base type set);
    no `submit_changes`/`answer`/Plan Mode text in any child-form segment **or child-form correction string**;
  - permission inheritance: a `plan` dispatcher (or any `plan` ancestor) → a `general-purpose` child can't
    edit/run/MCP; an `acceptEdits`/`dontAsk` child under `main` keeps its own mode (auto-accepts; a `/review-pref`
    flip doesn't gate it);
  - `ToolOutput.workspace_changes` → parent `seen.clear()`/`_edit_applied`/retrieval delta;
  - stale at accept → `edit_record_cb(..., "stale", ...)` → "✗ Not applied" breadcrumb even when the user accepted;
  - child record callbacks mark the child loop's pills boundary, not the parent's;
  - a child `default` agent honors a mid-dispatch `/review-pref` flip (shared `ChatTurnControl`);
  - `clear_replay` on child finish;
  - **leakage guard:** for each effective permission (`default`, `acceptEdits`, `dontAsk`, `plan`), with skills, MCP
    and AGENTS.md all present, a child's assembled system prompt + tools_json + every correction string contains
    none of: `submit_changes`, `"type":"answer"`, `clarify`, `propose_mode`, `Plan Mode`, `retrieval seed`,
    `ask what they want`, `rejected by user` (except the edit-rejection string, which is true);
  - `dontAsk` and timeout denials never produce "rejected by user"; an effective-`plan` child's tools_json has no
    `run_command` or `mcp__*`; the VCS guard **refuses** `git commit -am x`, `git checkout .`, `git stash`,
    `cd x && git commit`, `ls ; git stash`, `git -C . commit`, `git -c user.name=x commit`, `env GIT_DIR=. git reset`,
    `/usr/bin/git checkout .`, `bash -c "git stash"`, and an alias subcommand (`git ci`); it **allows** `git status`,
    `git diff`, `git log`, `git -C sub status`; a refused command raises no approval card;
  - `dontAsk` MCP denial and command/MCP timeouts produce the truthful wording (`ApprovalOutcome.denied_by`);
    a client posting `denied_by` in a decision body has no effect;
  - golden exceptions, both named: the `run_command` "shadow workspace" description fix (system-prompt golden,
    §4.6.3) and the neutral `edit_session.py:58` message (correction-string golden, §4.6.4). Nothing else in the parent
    changes;
  - **tagged templates (§4.7):**
    - the main-matrix goldens are captured **before** conversion and pass unchanged after it (§4.7.4 commits 1→2);
    - the render property test: main render = template minus non-main regions, markers stripped;
    - validator cases: unbalanced, mis-nested, unknown tag/value, inline region with newline, block marker sharing a
      line, stray `<<` → `PromptTemplateError` at import;
    - whitespace-rule cases: block regions leave no blank line when dropped; inline regions leave surrounding text
      untouched;
    - injected content containing `<<main>>` (AGENTS.md sample) renders verbatim;
    - the end-of-template rule: `_MEMORY_BLOCK` (no trailing newline, last line tagged) renders byte-identical for
      main and ends cleanly for a child; a multi-marker line is rejected by the validator;
    - blank lines, per template: each child rendering's `\n\n\n` count ≤ that template's main rendering's;
    - `ExecSessionToolSource.start_session` approves under `ApprovalOutcome` (the silent-denial regression);
    - VCS guard extras: `a&&git commit`, `( git commit )`, `$(git commit -am x)`, `xargs git checkout`,
      `find . -exec git rm {} \;`, `timeout 10 git commit`, `eval "git stash"`, `timeout 5 bash -c "git commit"`, `xargs sh -c 'git stash'`, and an
      unbalanced quote are refused; `echo $(date)`, `git config --get user.name`
      are allowed; `git config user.name x` is refused;
    - the structural leak lint (§4.7.5) over every template.
- **Contract:** Zod round-trip of `/live` with `pending_gates` + `agents`; `agent_dispatch` message parse; a
  `lastLiveSignature`-includes-`agents` test.
- **Webview (vitest):** stacked gates keyed by `gate_id`; the roster card states; `AgentTranscript` in both
  containers; floating window tabs/Esc/Stop; the channel subscribe/unsubscribe on expand/close; `seq` dedupe across a
  backfill + replayed stream; reconnect after an idle-timeout end while open; 404 on a decision POST → silent re-poll.
- **Live smoke** (cloud provider, CDP-driven dev host):
  1. 3-agent fan-out on separate files;
  2. a deliberate same-file collision producing the attributed stale refusal;
  3. stop one agent mid-run;
  4. stacked gates from two agents;
  5. reload mid-dispatch;
  6. depth-2 nested dispatch;
  7. `/stop` mid-dispatch, then a follow-up message whose answer shows the parent knows the children's changes.

## 16. Implementation phases
The flag's code default is **OFF** through phases 1–4 (dev runs opt in via `.env`/`start-backend.sh`); **phase 5 flips
it ON**. Each phase is green on its own.
1. **Foundations** (§4), no children yet. *(rev 11)* Shipped as two plans: **1A** prompt foundations (tagged templates,
   goldens, leak lint, allowed-types schema, AGENT variants/payload, engine seams) and **1B** gates & approvals
   (multi-gate, `ApprovalOutcome`, `StaleWriteError`, re-seed fix, and the frontend gate list including a basic agent
   chip):
   - **tagged templates first** (§4.7), in the three-commit order of §4.7.4: main-matrix goldens on unchanged code →
     resolver + validator + conversion of every §4.7.6 text (goldens unchanged) → the two deliberate parent fixes;
     plus the structural leak lint;
   - allowed-types schema + the `report`/AGENT variants;
   - AGENT payload branch;
   - `ReasoningEngine` additions + scripted per-agent scripts;
   - multi-gate model/routes/client/extension/webview with the full consumer list;
   - `StaleWriteError`/`STALE_READ`;
   - the internal `ApprovalOutcome` (`denied_by`) returned by command + MCP approval callbacks + truthful denial wording; the `run_command`
     description fix;
   - `TurnEditSession` re-seed fix.
2. **Child runtime + dispatch + write guard** (§5–§8, §11.3–§11.7 backend), including the whole §4.6 child prompt
   surface, the role block, and the VCS guard: `AgentContext`, runtime, slot token,
   `dispatch_agents`, write log + both checks + the loop accept branch, memory/skills child modes, permissions,
   `chat_agents` + routes, `/live agents`, thread-channel agent events, stop/cancel summary, restart reap, rewind,
   hard-coded built-ins.
3. **Agent definitions** (§9): discovery/precedence/parse/mapping, persona wiring, catalog block + teaching + entry-hint
   mention; make `_NON_EXECUTABLE_SUBSKILLS` **conditional**: empty when the flag is on (`subagent-driven-development`
   becomes executable), unchanged when it's off (no dispatch tool exists then).
4. **UI** (§10): roster card, inline box, floating window + tabs, per-agent channel subscription, agent-chip polish,
   and the `agents` field in `lastLiveSignature`. *(rev 11)* Stacked gate cards keyed by `gate_id`, the `gates` field
   in the signature, and a plain `label · name` agent chip already ship in Phase 1 (Plan 1B).
5. **Live smoke** (§15), then **flip the default ON** (+ the §14 conftest), and update CLAUDE.md's architecture section.

## 17. Out of scope (v1)
- Background spawn + completion notices + resume: v2 on the same runtime (D4).
- Exec-session tools in children.
- Per-child shadow isolation / merge-back.
- External-edit detection.
- `memory`/`hooks`/`mcpServers`/`isolation`/`background`/`effort` frontmatter.
- Agent teams.
- An agents section in the Settings panel.
