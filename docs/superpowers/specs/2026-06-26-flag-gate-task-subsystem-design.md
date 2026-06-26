# Flag-Gate the Task Subsystem (default OFF)

**Status:** Design approved 2026-06-26. Implementation plan to follow (writing-plans).

## Goal

Put the entire task-based path behind a single startup flag, **default OFF**. When OFF, the
reactive chat controller handles everything inline — small *and* large changes (large via the
todo ledger) — and nothing task-related is offered, prompted, or shown. When ON, today's
behavior is preserved: `create_task`/`resume` are offered, the task prompts are injected into
the controller system prompt, and the task UI (cards, review panel, `startTask`) is visible.

Separately and unconditionally, re-frame the controller prompt so **inline `edit` is no longer
cast as the "small change" path** — it is the primary path for changes of any size.

**Out of scope (explicitly deferred):** turning the task path into a sub-agent-driven execution
path. That is a large separate feature; this spec only flag-gates the existing task path.

## The Flag

- **Env var:** `AI_EDITOR_TASK_SUBSYSTEM` — truthy (`1`/`true`) = ON, anything else = **OFF (default)**.
- **Read once at startup**, mirroring `AI_EDITOR_CHAT_CONTROLLER`. Single resolver in
  `agentd/chat/controller_factory.py`: `is_task_subsystem_enabled() -> bool` (sibling to the
  existing `select_chat_handler` flag logic). One source of truth for backend + the config endpoint.
- **Coherence constraint:** OFF requires `AI_EDITOR_CHAT_CONTROLLER=1` (the controller is the only
  inline-large path; the legacy `ChatAgent.large_change` branch has nowhere to go without
  `create_task`). If `task_subsystem=off` **and** `chat_controller=off`, log a **startup WARNING**
  (incoherent config) — do not hard-fail; legacy behavior is left as-is for that combo.

## Architecture — what each layer does

The flag is process-fixed, so it is resolved once and consulted (not threaded per-request) by:

### 1. Controller system prompt (the "injected into controller prompt" requirement)

`format_controller_system_prompt(tool_definitions, *, task_subsystem_enabled: bool = ...)`
(`controller_prompts.py:323`) gains a keyword arg defaulting to the resolver. The `CONTROLLER_SYSTEM_PROMPT`
`propose_mode` section is assembled conditionally:

- **OFF:** `propose_mode` teaches and offers only `edit | explain`. The `create_task` and `resume`
  modes, their option examples, and the "plan it as a task" framing are **omitted** from the prompt.
- **ON:** the full block is injected (today's text).

Because the flag is fixed per process, the system prompt is stable per process → no KV-cache concern.
The propose_mode "enumerate every part" + TODO LIST POLICY blocks stay in both modes (they belong to
the inline path).

### 2. Controller mode gating (loop + decision handler)

- `controller_loop.py`: the **offered** mode set is `{edit, explain}` when OFF. `_propose_mode_correction`
  rejects any `create_task`/`resume` option when OFF (defense-in-depth: even if the model emits one
  despite the prompt omission, it is corrected, not dispatched). `_VALID_MODES` stays the full set as a
  vocabulary constant; the *gating* is the offered set + the correction check.
- `controller.py` `mode-decision` handler: defensively reject `create_task`/`resume` when OFF
  (returns an error rather than handing off to the orchestrator).

### 3. `/v1/tasks` routes — dormant, not guarded

Routes stay **registered but unreferenced** when OFF (sub-decision A, approved). Rationale: least
invasive, and the task engine is the machinery the future sub-agent path will reuse. The controller
never creates a task when OFF, and the UI never calls these routes when OFF, so they are dormant.
No hard-404 guarding in this spec.

### 4. Frontend — flag delivery + UI gating

- **New endpoint:** `GET /v1/config` → `{ "task_subsystem_enabled": bool, "chat_controller_enabled": bool }`
  (sub-decision B, approved — a capabilities endpoint over a VS Code setting, which could desync from
  the backend). Reads the same resolver(s).
- **Extension:** fetch `/v1/config` on activation; set a `when`-context key
  `aiEditor.taskSubsystemEnabled`. Gate on it:
  - `aiEditor.startTask` command registration / command-palette visibility.
  - `task_card` rendering (`MessageRow.tsx`, `controller.ts` task_card handling) — belt-and-suspenders,
    since the controller emits no `task_card` when OFF.
  - Review-panel entry points tied to the task flow.
- editor-client gains a `getConfig()` client method + a Zod `BackendConfigSchema`.

### 5. The edit-inline re-frame (ships regardless of the flag)

- `controller_prompts.py` `propose_mode`: rewrite so `edit` is the primary path for changes of **any
  size** — small and large (large via the todo ledger). Replace the "small new file" example bias with
  (or add) a large/multi-part example; remove the implication that large ⇒ `create_task`.
- `classifier.py` (legacy `ChatAgent`): soften the `small_change`=inline / `large_change`=task language.
  Legacy-only; minimal edit (this path is not used when the controller is ON, which OFF requires).
- `CLAUDE.md`: update the stale small/large mapping notes and document the new flag + default + constraint.

## Data flow (OFF, the new default)

```
startup: AI_EDITOR_TASK_SUBSYSTEM unset -> is_task_subsystem_enabled() = False
         (warn if CHAT_CONTROLLER also off)
chat turn: format_controller_system_prompt(task_subsystem_enabled=False)
           -> propose_mode offers {edit, explain} only
           -> large request -> edit (inline) + write_todos ledger -> submit
extension activation: GET /v1/config -> task_subsystem_enabled=false
           -> context key off -> startTask hidden, task_card/ review panel gated
/v1/tasks routes: present but never called
```

## Testing

**Backend (pytest):**
- `format_controller_system_prompt` includes the `create_task`/`resume` block when ON, omits it when OFF.
- Controller offered-mode set = `{edit, explain}` when OFF; `_propose_mode_correction` rejects a
  `create_task` option when OFF, accepts it when ON.
- `mode-decision` rejects `create_task`/`resume` when OFF.
- `GET /v1/config` returns both flags; resolver default is OFF; truthy env flips it.
- Startup warning emitted for the incoherent `task_off + controller_off` combo.

**Frontend (vitest):**
- `getConfig()` maps `/v1/config` snake→camel.
- Context-key gating: `startTask` command + task UI hidden when `task_subsystem_enabled=false`.

## File-touch list

**Backend (`services/agentd-py`):**
- `agentd/chat/controller_factory.py` — `is_task_subsystem_enabled()` resolver + coherence warning.
- `agentd/chat/controller_prompts.py` — conditional `propose_mode` assembly; edit-inline re-frame.
- `agentd/chat/controller_loop.py` — offered-mode set + `_propose_mode_correction` gating.
- `agentd/chat/controller.py` — `mode-decision` guard.
- `agentd/api/routes.py` — `GET /v1/config`.
- `agentd/chat/classifier.py` — legacy small/large language softening.
- `tests/` — new/extended tests per above.

**Frontend:**
- `apps/editor-client/src/contracts/task-contracts.ts` — `BackendConfigSchema`.
- `apps/editor-client/src/client/http-backend-client.ts` — `getConfig()`.
- `apps/vscode-extension/src/extension.ts` — fetch config, set context key, gate `startTask`.
- `apps/vscode-extension/src/controller.ts` / `webview-ui/.../MessageRow.tsx` — gate task_card/review surfaces.
- `apps/vscode-extension/package.json` — `when` clauses for the gated command.

**Docs:**
- `CLAUDE.md` — flag + default + constraint; update stale small/large mapping.

## Deferred

- Task path → sub-agent-driven execution path (large separate feature).
- Hard-404 guarding of `/v1/tasks` (kept dormant instead).
