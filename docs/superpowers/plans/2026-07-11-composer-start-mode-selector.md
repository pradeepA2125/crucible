# Composer Start-Mode Selector Implementation Plan

> **Status: NOT IMPLEMENTED — on hold (2026-07-11).** Design only; no code was written. Shelved
> as more complex than the intended feature (a simple toggle over allowed actions/tools within
> `DECIDE`, no state-machine change). See the spec's "On hold" note before resuming.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `Agent | Edit` composer toggle so a chat turn can start directly in the `EDIT` phase (patch ops + `run_command` from turn 1) instead of always starting in `DECIDE`.

**Architecture:** The controller's per-turn `ControllerPhaseSM` already gates action types by phase and already supports entering `EDIT` directly (`_run_loop(phase="EDIT")`). This feature is (1) adding `answer` to the `EDIT` vocabulary, (2) plumbing a `start_mode` flag from the composer through to `_run_loop`'s `phase` argument — mirroring the existing `step_review` flag end-to-end, (3) preserving the per-turn skill-check instruction when a turn starts directly in `EDIT`, and (4) a webview toggle with last-used persistence. A direct-`EDIT` start is a *fresh* entry, behaviorally identical to the existing `resolve_mode("edit")` path.

**Tech Stack:** Python 3 / FastAPI / pytest (`services/agentd-py`); TypeScript / React / Vitest (`apps/vscode-extension`, `apps/editor-client`).

**Spec:** `docs/superpowers/specs/2026-07-11-composer-start-mode-selector-design.md`

## Global Constraints

- `start_mode` wire values are exactly `"agent"` | `"edit"`; any other value (or absent) means Agent/`DECIDE`. Copy this validation verbatim at every boundary.
- **Omit-when-default:** never emit `startMode`/`start_mode` for Agent mode — only serialize it when `"edit"`. This keeps the Agent-mode message byte-identical to today and preserves existing exact-match tests.
- A direct-`EDIT` start must pass `edit_is_resume=False` (it is a fresh entry, not a clarify-resume). Never route `start_mode` through the `resume_phase` variable.
- `propose_mode` stays `DECIDE`-only; `EXPLAIN` is unchanged and out of scope.
- Default mode is `"agent"`.

---

### Task 1: Backend — allow `answer` in the `EDIT` phase vocabulary

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py:90` (the `"EDIT"` entry of `_PHASE_TYPES`)
- Modify (test): `services/agentd-py/tests/test_controller_schema.py:76`
- Create (test): `services/agentd-py/tests/test_controller_loop_answer_in_edit.py`

**Interfaces:**
- Produces: `_PHASE_TYPES["EDIT"]` now equals `["tool_call", "answer", "edit", "clarify", "submit_changes"]`. Consumed by `controller_response_schema(phase="EDIT")` and `ControllerPhaseSM.allowed_types()`.

- [ ] **Step 1: Update the schema gating test to expect `answer` in EDIT (failing)**

In `services/agentd-py/tests/test_controller_schema.py`, change the `test_phase_gating_trims_type_enum` EDIT assertion (line 76) and its comment:

```python
    edit = controller_response_schema(phase="EDIT")["properties"]["type"]["enum"]
    # clarify is allowed in EDIT (ask when blocked mid-edit); answer is allowed so a
    # quick-edit turn can respond without a patch; propose_mode is not (DECIDE-only).
    assert set(edit) == {"tool_call", "answer", "edit", "clarify", "submit_changes"}
```

- [ ] **Step 2: Add a loop test proving `answer` terminates a turn in EDIT (failing)**

Create `services/agentd-py/tests/test_controller_loop_answer_in_edit.py`:

```python
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_answer_terminates_turn_in_edit_phase(tmp_path: Path):
    """A turn started in EDIT may answer directly (no patch needed) instead of being
    forced through submit_changes. Before `answer` was added to _PHASE_TYPES["EDIT"],
    the loop rejected it as out-of-vocabulary and retried until exhaustion."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "answer", "thought": "nothing to change", "answer": "Already correct."},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm, edit_session=sess)
    out = await loop.run(
        {"goal": "check f.py", "workspace_path": str(real)}, max_iters=4,
        auto_accept_edits=True)
    assert out.kind == "answer"
    assert out.text == "Already correct."
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_controller_schema.py::test_phase_gating_trims_type_enum tests/test_controller_loop_answer_in_edit.py -v`
Expected: FAIL — schema test asserts a set missing `answer`; loop test fails because the scripted `answer` is rejected as malformed (out-of-vocabulary for EDIT) and the loop exhausts.

- [ ] **Step 4: Add `answer` to the EDIT vocabulary**

In `services/agentd-py/agentd/chat/controller_prompts.py`, change the `"EDIT"` entry (line 90):

```python
    # EDIT keeps `clarify` so the agent can ask when a genuine ambiguity blocks it
    # mid-edit (reading the workspace can't resolve it); the user's reply resumes the
    # loop in EDIT (ChatController._edit_clarify_pending). It still cannot re-open mode
    # selection — `propose_mode` stays DECIDE-only. `answer` lets a quick-edit turn
    # respond conversationally (after editing, or when no patch is needed) instead of
    # being forced through submit_changes.
    "EDIT": ["tool_call", "answer", "edit", "clarify", "submit_changes"],
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_controller_schema.py::test_phase_gating_trims_type_enum tests/test_controller_loop_answer_in_edit.py -v`
Expected: PASS (both).

- [ ] **Step 6: Run the full schema + loop test files to catch regressions**

Run: `cd services/agentd-py && pytest tests/test_controller_schema.py tests/test_controller_loop_edit.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py \
        services/agentd-py/tests/test_controller_schema.py \
        services/agentd-py/tests/test_controller_loop_answer_in_edit.py
git commit -m "feat(controller): allow answer action in EDIT phase vocabulary"
```

---

### Task 2: Backend — `start_mode` selects the starting phase (route → `handle_message` → `_run_loop`)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py:271-275` (signature) and `:326-335` (phase computation + `_run_loop` call)
- Modify: `services/agentd-py/agentd/api/routes.py:1353-1355` (parse) and `:1386-1388` (pass to ChatController branch only)
- Create (test): `services/agentd-py/tests/test_controller_start_mode.py`

**Interfaces:**
- Consumes: `ChatController._run_loop(..., phase, edit_is_resume, ...)` (existing).
- Produces: `ChatController.handle_message(..., start_mode: str | None = None)`. The route parses `start_mode` from the request dict and forwards it only on the ChatController-branch call.

- [ ] **Step 1: Write the start-mode routing test (failing)**

Create `services/agentd-py/tests/test_controller_start_mode.py`:

```python
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _make_ctrl(tmp_path: Path, *, orchestrator):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "answer", "thought": "t", "answer": "ok"}]),
        thread_store=store, orchestrator=orchestrator, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    return ctrl, thread


async def _capture_phase(ctrl, thread, monkeypatch, **kwargs) -> dict:
    captured: dict = {}

    async def fake_run_loop(*_args, **kw):
        captured.update(kw)
        return None

    async def fake_finish(*_args, **_kw):
        return None

    monkeypatch.setattr(ctrl, "_run_loop", fake_run_loop)
    monkeypatch.setattr(ctrl, "_finish", fake_finish)
    await ctrl.handle_message(thread.thread_id, "bump x", channel_id="c1", **kwargs)
    return captured


@pytest.mark.asyncio
async def test_start_mode_edit_enters_edit_as_fresh_entry(tmp_path, monkeypatch):
    ctrl, thread = _make_ctrl(tmp_path, orchestrator=object())
    captured = await _capture_phase(ctrl, thread, monkeypatch, start_mode="edit")
    assert captured["phase"] == "EDIT"
    assert captured["edit_is_resume"] is False  # fresh entry keeps the edit_entry hint


@pytest.mark.asyncio
@pytest.mark.parametrize("start_mode", ["agent", None])
async def test_start_mode_agent_or_absent_starts_decide(tmp_path, monkeypatch, start_mode):
    ctrl, thread = _make_ctrl(tmp_path, orchestrator=object())
    captured = await _capture_phase(ctrl, thread, monkeypatch, start_mode=start_mode)
    assert captured["phase"] is None  # None == DECIDE


@pytest.mark.asyncio
async def test_start_mode_edit_without_orchestrator_falls_back_to_decide(tmp_path, monkeypatch):
    ctrl, thread = _make_ctrl(tmp_path, orchestrator=None)
    captured = await _capture_phase(ctrl, thread, monkeypatch, start_mode="edit")
    assert captured["phase"] is None  # edit needs a patch engine; degrade to Agent
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_controller_start_mode.py -v`
Expected: FAIL — `handle_message() got an unexpected keyword argument 'start_mode'`.

- [ ] **Step 3: Add the `start_mode` parameter to `handle_message`**

In `services/agentd-py/agentd/chat/controller.py`, extend the signature (currently lines 271-275):

```python
    async def handle_message(
        self, thread_id: str, message: str, channel_id: str, step_review: bool | None = None,
        forced_skills: list[str] | None = None,
        mentioned_files: list[dict[str, str]] | None = None,
        start_mode: str | None = None,
    ) -> None:
```

- [ ] **Step 4: Compute the starting phase and pass it to `_run_loop`**

In the same method, replace the block at lines 326-335 (from `resume_phase = None` through the `_run_loop`/`_finish` calls):

```python
        # supersedes any pending gate (cleared above) and re-enters DECIDE.
        resume_phase = None
        # start_mode="edit" (composer "Edit" toggle) enters the EDIT phase directly — a
        # FRESH entry identical to resolve_mode("edit"), NOT a clarify-resume. Keep
        # edit_is_resume False so the edit_entry steering hint still fires. EDIT needs an
        # orchestrator (patch engine); without one, degrade gracefully to Agent/DECIDE.
        start_phase = "EDIT" if start_mode == "edit" else None
        if start_phase == "EDIT" and self._orchestrator is None:
            start_phase = None
        # One id for this turn's in-flight pills message — lets the loop upsert it per
        # tool result and _finish finalize the SAME message (no duplicate). Finding 5.
        turn_id = uuid4().hex
        outcome = await self._run_loop(
            thread_id, channel_id, turn_message, seed_history=seed_history,
            step_review=step_review, phase=start_phase, turn_id=turn_id,
            edit_is_resume=(resume_phase == "EDIT"), forced_skills=forced_skills)
        await self._finish(thread_id, channel_id, outcome, step_review, turn_id=turn_id)
```

- [ ] **Step 5: Run the controller test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_controller_start_mode.py -v`
Expected: PASS (all four cases).

- [ ] **Step 6: Parse `start_mode` in the chat message route**

In `services/agentd-py/agentd/api/routes.py`, inside `post_chat_message`, add the parse right after the `step_review` parse (currently lines 1353-1354):

```python
            _raw_step_review = request.get("step_review")
            step_review = _raw_step_review if isinstance(_raw_step_review, bool) else None
            _raw_start_mode = request.get("start_mode")
            start_mode = _raw_start_mode if _raw_start_mode in ("agent", "edit") else None
```

- [ ] **Step 7: Forward `start_mode` on the ChatController-branch call only**

In the same route, update the active-branch `handle_message` call (currently lines 1386-1388) to pass `start_mode`. Leave the legacy branch (`~1423`) unchanged:

```python
                    _chat_agent.handle_message(
                        thread_id, message, channel_id=channel_id,
                        step_review=step_review, forced_skills=forced_skills,
                        mentioned_files=mentioned_files, start_mode=start_mode),
```

- [ ] **Step 8: Run the backend chat/route test suites to confirm no regression**

Run: `cd services/agentd-py && pytest tests/test_controller_start_mode.py tests/test_chat_controller_qa.py tests/test_controller_clarify_gate.py -v`
Expected: PASS (all). Confirms direct-edit routing works and the clarify-resume EDIT path is unaffected.

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/chat/controller.py \
        services/agentd-py/agentd/api/routes.py \
        services/agentd-py/tests/test_controller_start_mode.py
git commit -m "feat(controller): start_mode selects starting phase (Agent/Edit)"
```

---

### Task 3: Backend — preserve the per-turn SKILL CHECK when a turn starts directly in EDIT

**Why:** The dynamic per-turn `skill_check` instruction (the emphatic "your FIRST action MUST be `read_skill`" intent-triage) is currently emitted **only in the DECIDE branch** of `build_controller_step_payload` (`controller_prompts.py:671-695`). A turn that starts directly in EDIT skips DECIDE, so it loses that enforcement. Under the "Edit = full agent + hands" model, a direct-Edit turn must still run skill triage before editing. The static system-prompt guidance ("load it BEFORE … editing", `controller_prompts.py:465`) stays regardless; this task restores the *dynamic* first-move enforcement.

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` — hoist `skill_check` above the `if phase == "EDIT":` branch (currently ~line 583) and prepend it to the EDIT-entry hint (currently line 594).
- Create (test): `services/agentd-py/tests/test_controller_skill_check_edit_entry.py`

**Interfaces:**
- Consumes: `build_controller_step_payload(ctx, tool_definitions, history, phase, ..., skills_available=...)` (existing signature at `controller_prompts.py:535`).
- Produces: when `phase == "EDIT"`, `edit_entry`/zero-history, and `skills_available=True`, the returned payload's steering hint begins with the same "SKILL CHECK —" text used by the DECIDE entry.

- [ ] **Step 1: Confirm the builder signature, then write the skill-check-in-EDIT-entry test (failing)**

First read the `build_controller_step_payload` signature at `controller_prompts.py:535` and the DECIDE call at `:693-695` to confirm the exact keyword names (`plan_context`, `skills_available`, `history`, `phase`) and how `plan_context` is threaded. Then create `services/agentd-py/tests/test_controller_skill_check_edit_entry.py` (adjust the kwargs to match the confirmed signature):

```python
from agentd.chat.controller_prompts import build_controller_step_payload


def _payload_text(payload) -> str:
    """Assert on the whole serialized payload so the test is robust to which key holds
    the per-turn steering hint."""
    return str(payload)


def test_edit_entry_includes_skill_check_when_skills_available():
    ctx = {"workspace_path": "/ws", "goal": "rename foo to bar", "active_skills": []}
    payload = build_controller_step_payload(
        ctx, [], [], phase="EDIT",
        plan_context={"edit_entry": True}, skills_available=True)
    text = _payload_text(payload)
    assert "SKILL CHECK" in text
    assert "EDIT mode" in text  # still carries the edit-entry decision guidance


def test_edit_entry_omits_skill_check_when_no_skills():
    ctx = {"workspace_path": "/ws", "goal": "rename foo to bar", "active_skills": []}
    payload = build_controller_step_payload(
        ctx, [], [], phase="EDIT",
        plan_context={"edit_entry": True}, skills_available=False)
    text = _payload_text(payload)
    assert "SKILL CHECK" not in text
    assert "EDIT mode" in text


def test_decide_entry_still_includes_skill_check():
    ctx = {"workspace_path": "/ws", "goal": "how does auth work?", "active_skills": []}
    payload = build_controller_step_payload(
        ctx, [], [], phase="DECIDE",
        plan_context={"decide_entry": True}, skills_available=True)
    assert "SKILL CHECK" in _payload_text(payload)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_controller_skill_check_edit_entry.py -v`
Expected: FAIL — `test_edit_entry_includes_skill_check_when_skills_available` fails (no "SKILL CHECK" in the EDIT payload today).

- [ ] **Step 3: Hoist `skill_check` above the phase branch**

In `services/agentd-py/agentd/chat/controller_prompts.py`, move the `skill_check = (...) if skills_available else ""` assignment (currently at lines 671-692, inside the `else: # DECIDE` block) to just before `if phase == "EDIT":` (line 583). The text is unchanged; only its location moves so both branches can read it. After the move, the DECIDE branch keeps using `skill_check` exactly as before (line 695 `skill_check + ...`).

- [ ] **Step 4: Prepend `skill_check` to the EDIT-entry hint**

In the EDIT-entry branch (currently line 594, `hint = (` inside `if plan_context.get("edit_entry") or not history:`), prepend `skill_check` and open with a "skills first, then edit" framing:

```python
        if plan_context.get("edit_entry") or not history:
            hint = (
                skill_check +
                "EDIT mode — approved to edit; this is your FIRST action and nothing is started "
                "yet. If the SKILL CHECK above matched a skill, read_skill it BEFORE editing. "
                "Then decide the approach:\n"
                "• BIG / multi-part (spans 3+ files, OR several independent parts, OR >~2 edit "
                "cycles): START A TODO LIST FIRST. write_todos is a TOOL — emit "
                "type='tool_call', tool='write_todos', args={\"items\":[{\"title\":...,"
                "\"status\":\"pending\"}, …]} listing EVERY part. Do NOT emit type='edit' with an "
                "empty patch_ops to 'do the todos' — that applies nothing and wastes the turn. "
                "After the list exists, edit items ONE AT A TIME (submit_changes is BLOCKED until "
                "none are pending).\n"
                "• SMALL / cohesive (one file, or a few related ops): SKIP the list — emit "
                "type='edit' now with a NON-EMPTY patch_ops.\n"
                f"Read the target region of any EXISTING file before changing it (search_code{_graph} "
                "→ read_file); a brand-new file needs no read. Finish with type='submit_changes'."
            )
```

- [ ] **Step 5: Run the skill-check test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_controller_skill_check_edit_entry.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Run the prompt/loop suites to confirm no regression**

Run: `cd services/agentd-py && pytest tests/ -k "controller_prompt or controller_loop or schema or skill" -q`
Expected: PASS (all). Confirms the DECIDE skill-check text still emits and the EDIT hint still carries its decision guidance.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py \
        services/agentd-py/tests/test_controller_skill_check_edit_entry.py
git commit -m "feat(controller): run skill-check on direct-EDIT entry (parity with DECIDE)"
```

---

### Task 4: editor-client contract — `startMode` option → `start_mode` POST body

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts:428` (interface method options)
- Modify: `apps/editor-client/src/client/http-backend-client.ts:701` (impl signature) and `:709` (POST body)
- Modify (test): `apps/editor-client/test/http-backend-client.test.ts`

**Interfaces:**
- Produces: `sendChatMessage(threadId, message, signal?, options?: { stepReview?; forcedSkills?; mentionedFiles?; startMode?: "agent" | "edit" })`. When `startMode === "edit"`, the POST body includes `start_mode: "edit"`; otherwise the key is absent. Consumed by `apps/vscode-extension/src/controller.ts` (Task 5).

- [ ] **Step 1: Add body-shape tests for `start_mode` (failing)**

Append to `apps/editor-client/test/http-backend-client.test.ts` (inside the existing `describe` block):

```typescript
  test("sendChatMessage includes start_mode when startMode is edit", async () => {
    let sentBody = "";
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (_url, init) => {
        sentBody = (init?.body as string) ?? "";
        return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
      },
    });
    const iter = client.sendChatMessage("t1", "hi", undefined, { startMode: "edit" });
    await iter[Symbol.asyncIterator]().next();
    expect(JSON.parse(sentBody).start_mode).toBe("edit");
  });

  test("sendChatMessage omits start_mode when not provided", async () => {
    let sentBody = "";
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (_url, init) => {
        sentBody = (init?.body as string) ?? "";
        return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
      },
    });
    const iter = client.sendChatMessage("t1", "hi");
    await iter[Symbol.asyncIterator]().next();
    expect(JSON.parse(sentBody).start_mode).toBeUndefined();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/editor-client && npx vitest run test/http-backend-client.test.ts`
Expected: FAIL — the `startMode: "edit"` case yields `start_mode === undefined` (TypeScript also flags `startMode` as an unknown option property).

- [ ] **Step 3: Add `startMode` to the contract interface**

In `apps/editor-client/src/contracts/task-contracts.ts`, line 428, extend the options object:

```typescript
  sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[]; startMode?: "agent" | "edit" }): AsyncIterable<StreamEvent>;
```

- [ ] **Step 4: Add `startMode` to the impl signature and the POST body**

In `apps/editor-client/src/client/http-backend-client.ts`, line 701, extend the options type to match:

```typescript
  async *sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[]; startMode?: "agent" | "edit" }): AsyncIterable<StreamEvent> {
```

Then add the conditional body field alongside the others (after the `step_review` spread at line 709):

```typescript
          ...(options?.stepReview !== undefined ? { step_review: options.stepReview } : {}),
          ...(options?.startMode === "edit" ? { start_mode: options.startMode } : {}),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/editor-client && npx vitest run test/http-backend-client.test.ts`
Expected: PASS (all, including the pre-existing `forced_skills`/`mentioned_files` tests).

- [ ] **Step 6: Commit**

```bash
git add apps/editor-client/src/contracts/task-contracts.ts \
        apps/editor-client/src/client/http-backend-client.ts \
        apps/editor-client/test/http-backend-client.test.ts
git commit -m "feat(editor-client): thread startMode into sendChatMessage body"
```

---

### Task 5: Extension host — thread `startMode` webview → controller → client

**Files:**
- Modify: `apps/vscode-extension/src/chat-panel.ts:14-19` (`ChatMessageHandler` type) and `:193` (read `startMode`)
- Modify: `apps/vscode-extension/src/extension.ts:100-102` (callback)
- Modify: `apps/vscode-extension/src/controller.ts:626-628` (signature) and `:662-668` (options)
- Modify (test): `apps/vscode-extension/test/controller.test.ts`

**Interfaces:**
- Consumes: `client.sendChatMessage(..., { startMode })` from Task 4.
- Produces: `CrucibleController.sendChatMessage(text, stepReview?, forcedSkills?, mentionedPaths?, startMode?: "agent" | "edit")`. The webview→host message may carry `startMode`; `ChatMessageHandler` gains a 5th `startMode?` argument.

- [ ] **Step 1: Write a controller test asserting `startMode` reaches client options (failing)**

Append to `apps/vscode-extension/test/controller.test.ts` a test modeled on the existing "sendChatMessage with mentionedPaths" test (line 533). Capture the 4th `options` arg of the stub client's `sendChatMessage`:

```typescript
  test("sendChatMessage forwards startMode into client options", async () => {
    let capturedOptions: unknown;
    const workspace = await fsp.mkdtemp(path.join(os.tmpdir(), "cruc-startmode-"));
    const client = {
      ...makeStubClient(),
      createChatThread: async () => ({ threadId: "t1" }),
      sendChatMessage: async function* (
        _threadId: string, _message: string, _signal?: AbortSignal, options?: unknown,
      ) {
        capturedOptions = options;
        // no events
      },
    };
    const controller = new CrucibleController(
      makeUi({ workspacePath: workspace }),
      client as unknown as BackendTaskClient,
      new MemorySessionStore(),
      makeSettings(),
    );
    await controller.sendChatMessage("quick fix", undefined, undefined, undefined, "edit");
    expect((capturedOptions as { startMode?: string }).startMode).toBe("edit");
  });
```

> Reuse the file's existing stub-construction helpers (`makeStubClient` / `makeUi` / `makeSettings` or the inline equivalents used by the neighbouring `sendChatMessage` tests at lines 481-569). If those helpers are inlined rather than factored, copy the same inline `client`/`ui`/`settings` construction those tests use — the only additions here are `capturedOptions` and the 5th `sendChatMessage` argument.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/vscode-extension && npx vitest run test/controller.test.ts -t "forwards startMode"`
Expected: FAIL — `controller.sendChatMessage` ignores a 5th argument, so `capturedOptions` is `undefined` (or lacks `startMode`).

- [ ] **Step 3: Add `startMode` to `CrucibleController.sendChatMessage`**

In `apps/vscode-extension/src/controller.ts`, change the signature (line 626):

```typescript
  async sendChatMessage(
    text: string, stepReview?: boolean, forcedSkills?: string[], mentionedPaths?: string[],
    startMode?: "agent" | "edit"
  ): Promise<void> {
```

Then fold `startMode` into the client options (replace the options block at lines 662-668):

```typescript
    await this.streamTurn(
      client.sendChatMessage(
        threadId,
        text,
        this.turnAbort.signal,
        stepReview !== undefined || forcedSkills?.length || mentionedFiles?.length || startMode === "edit"
          ? {
              ...(stepReview !== undefined ? { stepReview } : {}),
              ...(forcedSkills?.length ? { forcedSkills } : {}),
              ...(mentionedFiles?.length ? { mentionedFiles } : {}),
              ...(startMode === "edit" ? { startMode } : {}),
            }
          : undefined,
      ),
    );
```

- [ ] **Step 4: Run the controller test to verify it passes**

Run: `cd apps/vscode-extension && npx vitest run test/controller.test.ts -t "forwards startMode"`
Expected: PASS.

- [ ] **Step 5: Extend the `ChatMessageHandler` type and read `startMode` in chat-panel**

In `apps/vscode-extension/src/chat-panel.ts`, add the 5th parameter to the handler type (lines 14-19):

```typescript
export type ChatMessageHandler = (
  message: string,
  stepReview?: boolean,
  forcedSkills?: string[],
  mentionedPaths?: string[],
  startMode?: "agent" | "edit"
) => Promise<void>;
```

Then read `startMode` from the posted message and pass it through (line 193):

```typescript
        const startMode = m["startMode"] === "edit" ? "edit" : undefined;
        p = this.onMessage(m["text"] as string, m["stepReview"] === true, forcedSkills, mentionedPaths, startMode);
```

- [ ] **Step 6: Wire `startMode` through the `extension.ts` callback**

In `apps/vscode-extension/src/extension.ts`, update the `ChatPanel` chat-message callback (lines 100-102):

```typescript
    (message, stepReview, forcedSkills, mentionedPaths, startMode) =>
      controller.sendChatMessage(message, stepReview, forcedSkills, mentionedPaths, startMode),
```

- [ ] **Step 7: Typecheck + run the extension test suite**

Run: `cd apps/vscode-extension && npx tsc --noEmit && npx vitest run test/controller.test.ts`
Expected: PASS — typecheck clean, all controller tests green.

- [ ] **Step 8: Commit**

```bash
git add apps/vscode-extension/src/chat-panel.ts \
        apps/vscode-extension/src/extension.ts \
        apps/vscode-extension/src/controller.ts \
        apps/vscode-extension/test/controller.test.ts
git commit -m "feat(extension): thread startMode from webview to backend client"
```

---

### Task 6: Webview — `Agent | Edit` toggle with last-used persistence

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/types.ts:146` (`sendMessage` message member)
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx` (state, both post sites, toolbar control)
- Modify (test): `apps/vscode-extension/webview-ui/src/test/views.test.tsx`

**Interfaces:**
- Consumes: the `sendMessage` webview→host message shape (Task 5 reads `m["startMode"]`).
- Produces: an `Agent | Edit` control; `startMode` state defaults to `"agent"`, seeds from `vscode.getState().startMode` (last-used), persists to `vscode.setState` on change, and is included in the posted `sendMessage` **only when `"edit"`**.

**Scope note:** this delivers *global last-used* persistence (last mode seeds every new thread). True per-thread divergence is deferred — `InputArea` has no `threadId` prop today, and last-used alone delivers the approved "habitual quick-editor isn't re-toggling" benefit.

- [ ] **Step 1: Add `startMode` to the `sendMessage` message type**

In `apps/vscode-extension/webview-ui/src/types.ts`, line 146:

```typescript
  | { type: "sendMessage"; text: string; stepReview?: boolean; forcedSkills?: string[]; mentionedPaths?: string[]; startMode?: "agent" | "edit" }
```

- [ ] **Step 2: Write webview tests for the toggle (failing)**

Append to `apps/vscode-extension/webview-ui/src/test/views.test.tsx`, in the same describe block as the existing stepReview tests:

```typescript
  it("defaults to Agent mode: sendMessage omits startMode", () => {
    render(
      <InputArea availability={makeAvailability()} draft="do it" onDraftChange={vi.fn()} />,
    );
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });
    const call = postMessage.mock.calls.map((c) => c[0]).find((m) => m.type === "sendMessage");
    expect(call).toBeDefined();
    expect("startMode" in call).toBe(false);
  });

  it("Edit mode: flipping the toggle sends startMode: 'edit'", () => {
    render(
      <InputArea availability={makeAvailability()} draft="do it" onDraftChange={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /^edit$/i }));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: false });
    expect(postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "sendMessage", text: "do it", startMode: "edit" }),
    );
  });
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/views.test.tsx`
Expected: FAIL — no `radio` named "Edit" exists; the flip/assert test cannot find the control.

- [ ] **Step 4: Add `startMode` state with last-used persistence**

In `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`, add below the `stepReview` state (line 36). Use the `getState`/`setState` cast pattern established in `src/graph/GraphApp.tsx`:

```typescript
  // Composer start mode: "agent" = start in DECIDE (explore, may propose Edit/Explain);
  // "edit" = start directly in EDIT (patch ops + run_command from turn 1). Persisted as
  // last-used via the webview state handle so it seeds the next thread. Default "agent".
  const [startMode, setStartMode] = useState<"agent" | "edit">(() => {
    const persisted = (vscode as { getState?: () => unknown }).getState?.() as
      | { startMode?: "agent" | "edit" }
      | undefined;
    return persisted?.startMode === "edit" ? "edit" : "agent";
  });

  function chooseStartMode(next: "agent" | "edit") {
    setStartMode(next);
    const api = vscode as { getState?: () => unknown; setState?: (s: unknown) => void };
    api.setState?.({ ...((api.getState?.() as object) ?? {}), startMode: next });
  }
```

- [ ] **Step 5: Include `startMode` in both send post sites (omit when default)**

In the same file, update the skill/plain post site (lines 137-144):

```typescript
      if (skill) {
        vscode.postMessage({
          type: "sendMessage",
          text: skill.message,
          stepReview,
          forcedSkills: skill.forcedSkills,
          ...(startMode === "edit" ? { startMode } : {}),
        });
      } else {
        vscode.postMessage({
          type: "sendMessage",
          text: original,
          stepReview,
          ...(startMode === "edit" ? { startMode } : {}),
        });
      }
```

And the main send post site (lines 242-247):

```typescript
    vscode.postMessage({
      type: "sendMessage",
      text: trimmed,
      stepReview,
      ...(mentionedPaths.length ? { mentionedPaths } : {}),
      ...(startMode === "edit" ? { startMode } : {}),
    });
```

Add `startMode` to the effect dependency array that closes over it (line 151): change `[onDraftChange, stepReview, skillNames]` to `[onDraftChange, stepReview, skillNames, startMode]`.

- [ ] **Step 6: Render the `Agent | Edit` segmented control in the footer**

In the same file, add the control immediately after `<ModelMenu />` (line 327):

```tsx
        <ModelMenu />
        {/* Start-mode: Agent = explore/decide first; Edit = patch ops from turn 1. */}
        <div
          role="radiogroup"
          aria-label="Start mode"
          className="flex items-center rounded-[7px] border border-border p-[1px] text-[10px]"
        >
          {(["agent", "edit"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={startMode === mode}
              onClick={() => chooseStartMode(mode)}
              className={
                "h-5 px-2 rounded-[6px] capitalize transition-colors duration-150 cursor-pointer " +
                (startMode === mode
                  ? "bg-surface-2 text-text"
                  : "text-text-3 hover:text-text")
              }
              title={
                mode === "agent"
                  ? "Agent: explore and decide first (may propose Edit/Explain)"
                  : "Edit: start editing immediately — patch ops and commands available"
              }
            >
              {mode}
            </button>
          ))}
        </div>
```

- [ ] **Step 7: Run the new webview tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/views.test.tsx`
Expected: PASS (new toggle tests + all pre-existing stepReview exact-match tests, which stay green because Agent mode omits `startMode`).

- [ ] **Step 8: Run the full webview + InputArea suites to confirm no regression**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/InputArea.test.tsx src/test/views.test.tsx src/test/assembly.test.tsx`
Expected: PASS (all). Confirms the exact-match assertions at `views.test.tsx:324,361` and `InputArea.test.tsx:65` are untouched.

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/types.ts \
        apps/vscode-extension/webview-ui/src/components/InputArea.tsx \
        apps/vscode-extension/webview-ui/src/test/views.test.tsx
git commit -m "feat(webview): Agent/Edit start-mode toggle in composer"
```

---

## End-to-end verification (after all tasks)

- [ ] **Backend:** `cd services/agentd-py && pytest tests/ -k "controller or schema or skill" -q` → PASS.
- [ ] **editor-client:** `cd apps/editor-client && npx vitest run` → PASS.
- [ ] **extension host + webview:** `cd apps/vscode-extension && npx tsc --noEmit && npx vitest run && npx vitest run --root webview-ui` → PASS.
- [ ] **Manual smoke (real app):** launch the extension, open Crucible chat, flip the composer to **Edit**, send "rename X to Y". Confirm: (a) the turn starts editing immediately (no "Edit vs Just-explain" proposal); (b) if a project skill's trigger matches, the first action is a `read_skill` before editing (skill-check parity); (c) a no-op ask (e.g. "is this file correct?") in Edit mode returns an `answer` rather than an empty patch. Flip back to **Agent**; confirm the propose-mode flow returns. Open a new thread; confirm it defaults to the last-used mode.

---

## Spec self-review

- **Coverage:** every spec section maps to a task — `answer` in EDIT (T1), `start_mode` routing (T2), skill-check parity for direct-EDIT (T3, added after the hint-mechanism review), editor-client contract (T4), extension host (T5), webview toggle + persistence (T6).
- **Placeholders:** none — every code step shows complete code; the two `>` notes point at real signatures to confirm, not TBDs.
- **Type consistency:** `startMode: "agent" | "edit"` and `start_mode` used consistently across the TS boundaries; `start_mode`/`phase`/`edit_is_resume` consistent across the Python boundary.
