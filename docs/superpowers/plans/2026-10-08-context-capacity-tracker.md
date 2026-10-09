# Main-chat Context-capacity Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an honest MAIN request context-capacity meter, prioritizing ChatGPT catalog capacity and supporting API-key Settings capacity, with recoverable ordered telemetry and accessible composer UX.

**Architecture:** Backend process-local per-thread ContextUsage snapshots feed dedicated `context_usage` SSE and identical `/live` telemetry. Transport observers estimate the assembled physical request and independently upgrade valid completed input, while the backend separately resolves capacity with trusted provenance. One guarded host acceptance path delivers dedicated webview state; UsageChip and compaction/account usage remain separate.

**Tech Stack:** Python backend/Pydantic/pytest; TypeScript editor-client/Zod and VS Code host/Vitest; React 18 webview/Testing Library/Vite; existing styling. No new dependency or DB migration required.

## Global Constraints

- Unknown is NOT zero.
- Unknown ChatGPT catalog capacity stays unknown, NEVER fallback to API-key settings or inherited model budget.
- Never pair old input with new-model capacity.
- Preserve UsageChip/account Manage usage/accounting and compaction policy.
- No arbitrary warnings/analytics/DB migration.
- No prompt/credentials/account IDs/token IDs in events.
- Do not change branches, reset, commit or merge.
- Preserve unrelated untracked README/landing plan and scripts/*devhost*.mjs.
- Exclude workspaces/, .worktrees/, build copies from investigation and edits.

---

## Status, ownership and dependencies

Approved design: `docs/superpowers/specs/2026-10-08-context-capacity-tracker-design.md`. P6 was adopted by team context-capacity-implementation; user already chose TEAM execution and branch `feat/context-capacity-tracker` is prepared. Do not ask another execution-choice question. Checkboxes record intended gates, not claims that tests have passed. Implementers maintain their own evidence ledgers and report fresh results.

- Lead owns only the design and this plan; coordinates wire consistency/coverage and final evidence.
- Backend owns `services/agentd-py/agentd/**` and `services/agentd-py/tests/**`.
- Frontend owns `apps/editor-client/**` and `apps/vscode-extension/**`.
- Review is independent/read-only. No shared source ownership.

Tasks 1–4 form backend delivery; tasks 5–7 form frontend delivery and can proceed independently against the locked wire fixture; task 8 integrates both. Owners may split disjoint files among subagents, must collect them before reporting, and must not rewrite another owner's source. Exact paths below identify seams, not permission to edit unread code. Existing large files are not an invitation to unrelated refactoring.

## Locked cross-owner interfaces

SSE `event: context_usage` data and `/live.context_usage` are the same snake-case object. `/live.process_id:string` establishes a process independently of whether context telemetry parses; nested process_id must match it.

```ts
// Wire definition shared by tasks 1–7; client may map camelCase internally.
type ContextUsageWire = {
  thread_id: string; provider: string; model: string; scope: 'main';
  process_id: string; generation: number; revision: number;
  turn_id: string | null; request_id: string | null;
  status: 'awaiting_request' | 'in_flight' | 'last_request' | 'invalidated' | 'unavailable';
  input_tokens: number | null;
  input_quality: 'estimated' | 'provider_reported' | 'unavailable';
  context_window: number | null;
  capacity_source: 'chatgpt_catalog' | 'configured' | 'unknown';
  observed_at: string | null;
};
```

All counts/versions are safe integers at most 9007199254740991; input and versions nonnegative, capacity positive. Bool/string/fractional/negative/nonfinite values invalid. Null input iff quality unavailable; null capacity iff source unknown. Awaiting/invalidated/unavailable have null input. observed_at is UTC ISO8601 or null for no observation; preserve it for capacity enrichment, finish and reconnect. Generation advances at EACH physical dispatch/retry/fallback and invalidation; revision advances on every publication, monotonically per thread within one process and is not reset across generations. Reject a rollback in either generation OR revision for the same thread/process, including higher-generation/lower-revision payloads; only a guarded /live handshake establishing a new process resets ordering. No account/auth identity is serialized.

Tracker interfaces are read from the in-progress backend; recovered observer/physical-handle signatures were reread in `agentd/chat/context_usage.py:163–234` (owner may refine local internals while preserving semantics):

```python
ContextUsageTracker(publish: Callable[[str, dict], None])
tracker.snapshot(thread: str) -> ContextUsage
tracker.select(*, provider: str, model: str, identity: tuple,
               window: int | None, source: str) -> None
tracker.enrich(identity: tuple, window: int | None, source: str) -> None
tracker.invalidate(thread: str, *, status: str = 'invalidated') -> None
tracker.observer(thread: str, turn: str | None) -> RequestObserver
observer.begin(model: str, body: dict) -> PhysicalObservation | None
physical.reported(value: object) -> None
physical.finish() -> None
# observer.reported/finish remain compatibility methods for single-attempt callers only.
```

`on_context_input` is an OPTIONAL transport/engine keyword carrying a RequestObserver factory; omitted for background/ping calls. begin takes assembled input-bearing body, not credentials/output limits, and returns a physical handle or None. Each physical transport attempt must capture that returned handle and invoke its reported/finish callbacks, never the mutable logical compatibility methods. The handle permanently binds one generation, becomes inert after finish or retirement, and cannot let a retired attempt publish after a newer begin or identity reset. Independent observation must preserve existing on_usage policy and isolate telemetry exceptions.

### Task 0: Establish baselines and tooling (each implementer)

**Files to read:** `CLAUDE.md:15–46`, root `package.json`, `apps/editor-client/package.json`, `apps/vscode-extension/package.json`, `apps/vscode-extension/webview-ui/package.json`, `services/agentd-py/pyproject.toml`. No config changes.

- [ ] Read env profile first. If unavailable (lead received “profile not yet built”), use existing binaries/scripts instead of installing or activating a shell venv. Lead verified `services/agentd-py/.venv/bin/{python,pytest,ruff,mypy}` exists.
- [ ] Backend baseline from `services/agentd-py`: `.venv/bin/pytest -q tests/test_openai_transport.py`; extend with relevant runtime/auth/controller tests located by backend. Capture pre-existing failures independently. Backend reported 51 passing OpenAI tests in team post 13; that is attributed baseline evidence, not final verification.
- [ ] Frontend baseline from root: `npm run -w @crucible/editor-client test`, `npm run -w crucible-vscode-extension test -- test/controller.test.ts test/composer-model-swap.test.ts`, `npm --prefix apps/vscode-extension/webview-ui test -- src/components/UsageChip.test.tsx`. Expected exit 0, or record exact existing failing cases before feature edits.
- [ ] Before every implementation slice write a failing test, run to observe red, implement only that slice, rerun green. New module/component names below are proposed unless stated read; fixture adapters use the owning project's existing test harness.

### Task 1: Process-local state and strict input semantics (backend)

**Files:** Create `services/agentd-py/agentd/chat/context_usage.py`, `services/agentd-py/tests/test_context_usage.py`. Backend has begun these under P6; reread before changing.

**Interfaces:** Consumes locked ContextUsageWire semantics. Produces ContextUsage, ContextUsageTracker and RequestObserver signatures above; publish callback receives `(thread_id, payload)` for dedicated SSE; snapshot is the /live source.

- [ ] Add direct unit tests for estimate→reported→finish, zero/invalid counts, replacement rather than sum, per-thread isolation, tombstones/retired observers, same identity enrichment preserving time, and publish callback failure isolation. Concrete state-test example using actual tracker signatures:

```python
from agentd.chat.context_usage import ContextUsageTracker

def test_zero_report_and_retired_request():
    events = []
    tracker = ContextUsageTracker(lambda thread, payload: events.append((thread, payload)))
    tracker.select(provider='openai', model='m', identity=('openai', 'm', 128000),
                   window=128000, source='configured')
    observer = tracker.observer('t', 'turn')
    physical = observer.begin('m', {'instructions': 'system', 'input': [{'content': 'hello'}]})
    assert physical is not None
    assert tracker.snapshot('t').input_quality == 'estimated'
    physical.reported(0)
    physical.finish()
    snap = tracker.snapshot('t')
    assert (snap.input_tokens, snap.input_quality, snap.status) == (0, 'provider_reported', 'last_request')
    tracker.invalidate('t')
    generation = tracker.snapshot('t').generation
    physical.reported(100)
    physical.finish()
    assert tracker.snapshot('t').generation == generation
    assert tracker.snapshot('t').input_tokens is None
    assert events[-1][1] == tracker.snapshot('t').model_dump()
```

- [ ] Run `.venv/bin/pytest -q tests/test_context_usage.py` in backend before implementation; expected failure for missing module/behavior, not an unrelated environment error.
- [ ] Implement guarded immutable snapshots: strict integer validator `type(value) is int and 0 <= value <= 2**53 - 1`; select changes effective identity and invalidates existing threads; enrichment updates current same-identity capacity without reviving captured input; begin serializes only input-bearing instructions/input/messages/tools/text/schema/system fields and does not retain body in state. Keep estimate honestly approximate.
- [ ] Test first snapshot AFTER successful selection: known denominator and awaiting_request/null input. Startup without an observation is unavailable/awaiting, never fake zero. Compare new generations on physical requests and lifecycle invalidation; old callbacks cannot restore them.
- [ ] Rerun state tests, then review emitted model_dump keys exactly against locked wire.

### Task 2: Full physical-request observation (backend)

**Files:** Modify `services/agentd-py/agentd/providers/openai_transport.py:194–486`, `services/agentd-py/agentd/providers/openai_compatible_transport.py`, `services/agentd-py/agentd/reasoning/engine.py` and actual transport protocol files located/read by owner. Test `services/agentd-py/tests/test_openai_transport.py`; create focused `services/agentd-py/tests/test_context_input_transport.py` for the API-compatible seams if existing suites lack coverage. Do not change token-progress exact/output semantics to infer input quality.

**Interfaces:** Consumes RequestObserver. Produces optional on_context_input propagation; begin/model/full body at each actual dispatch, reported/raw input only upon completed valid usage, finish on all terminal paths. Existing on_usage remains separate.

- [ ] Add a failing transport test reusing READ OpenAI FakeResponses/_transport/SCHEMA harness. Prove completed input is retained even without output and with action decode error:

```python
@pytest.mark.asyncio
async def test_context_input_survives_missing_output_and_decode_error():
    tracker = ContextUsageTracker(lambda thread, payload: None)
    tracker.select(provider='openai', model='m', identity=('openai', 'm'),
                   window=128000, source='configured')
    observer = tracker.observer('t', 'turn')
    event = SimpleNamespace(type='response.completed', response=SimpleNamespace(
        usage=SimpleNamespace(input_tokens=0)))
    fake = FakeResponses([event])
    with pytest.raises(RuntimeError):
        await _transport(fake).generate_json(
            model='m', schema_name='s', schema=SCHEMA,
            system_instructions='system', user_payload={}, on_context_input=observer)
    snap = tracker.snapshot('t')
    assert (snap.input_tokens, snap.input_quality) == (0, 'provider_reported')
```

- [ ] Run `.venv/bin/pytest -q tests/test_openai_transport.py -k context`; expected new behavior failure before wiring. Repeat targeted red/green cycle for native history/tails/tools and strict text schema contribution, retry/fallback generations, invalid bool/negative/missing/string/fractional input, stream failure/incomplete/cancel/first-action return.
- [ ] Assemble native request FIRST (`actions.tools`, SessionInputs.build/body input), then call begin just before network dispatch and capture its returned physical handle. On response.completed invoke that handle's reported(raw input) before output/action decode; missing output does not suppress it. Finally invoke that same handle's finish without downgrading valid report. Retry/fallback binds a new handle, and delayed callbacks retain the old handle rather than reaching the logical observer. Keep usage/cached recording/output ticker unchanged. API-compatible physical request observers sit outside winning decoded logical-call accounting.
- [ ] Run `.venv/bin/pytest -q tests/test_openai_transport.py tests/test_context_input_transport.py` after edits. Include existing provider/usage regression suites selected by owner. Inspect each supported API transport; unsupported observer paths must finish unavailable rather than reusing old input.

### Task 3: Authoritative capacity and auth-session identity (backend)

**Files:** Modify `services/agentd-py/agentd/providers/runtime.py`, `services/agentd-py/agentd/chatgpt_auth/service.py`, `services/agentd-py/agentd/chatgpt_auth/store.py`, `services/agentd-py/agentd/chatgpt_auth/oauth.py`, `services/agentd-py/agentd/chatgpt_auth/session.py`, `services/agentd-py/agentd/main.py` after owner reads actual wiring. Create `services/agentd-py/tests/test_context_capacity.py`; extend existing auth/runtime tests located/read by owner.

**Interfaces:** Consumes tracker.select/enrich and existing `ChatGPTAuthService.list_models(registration_id) -> list[CatalogModel]`, `ProviderRuntime.swap(...)` successful effective identity. Produces private auth epoch and guarded numeric catalog capacity; serialized meter sees only provider/model/window/source.

- [ ] Write failing capacity tests with controlled catalog future/clock: known catalog initializes awaiting, absent/malformed catalog remains null/unknown despite configured 128000, API Settings becomes configured, rejected ping preserves prior snapshot, effort-only/no-op preserves prior input, changed configured window clears prior input. Cache TTL expiry/coalescing and old fetch after active-account re-sign-in cannot enrich retired state.
- [ ] Write auth tests: legacy records load; successful OAuth creates new private epoch even with same registration; refresh preserves epoch although saved_at changes; active sign-out invalidates and late input fails; unrelated account sign-in/out does not clear active state. Unit guard oracle:

```python
tracker.select(provider='chatgpt', model='m', identity=('active', 'epoch-a', 'm'),
               window=None, source='unknown')
tracker.select(provider='chatgpt', model='m', identity=('active', 'epoch-b', 'm'),
               window=None, source='unknown')
tracker.enrich(('active', 'epoch-a', 'm'), 272000, 'chatgpt_catalog')
assert tracker.snapshot('t').context_window is None
```

- [ ] Run `.venv/bin/pytest -q tests/test_context_capacity.py` red; implement cache/private epoch/runtime selection wiring then green. Resolve catalog in background best effort, not by blocking chat or per-output tick. Private identity includes active account/session and model, API identity includes effective Settings capacity. Successful ping remains before mutation. Never change existing unknown-catalog compaction preservation.
- [ ] Record catalog network failure/unsupported model as unknown, not configured fallback. Guard post-await identity both before cache insertion and before enrichment. Publication carries no account/auth identity.

### Task 4: Main-only lifecycle, SSE and /live integration (backend)

**Files:** Modify `services/agentd-py/agentd/chat/controller_loop.py`, `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/chat/models.py`, `services/agentd-py/agentd/api/routes.py`; main wiring belongs to Task 3. Create `services/agentd-py/tests/test_context_usage_integration.py`; extend located compaction/rewind/controller/live tests.

**Interfaces:** Consumes tracker/observer and transport kwarg. Produces typed ThreadLiveState.context_usage and independent process_id plus `context_usage` SSE JSON identical to snapshot.

- [ ] Add failing tests: main dispatch observes while child/team/ping/compaction summarizer does not; next main call replaces input; compaction and rewind/history replacement publish versioned null-input tombstones; reset/swap/auth during await retires callbacks. Route /live and last SSE payload equality, top-level process identity even without observation, retained reopen and process restart unavailable.
- [ ] Run `.venv/bin/pytest -q tests/test_context_usage_integration.py` red. Wire observer only at main loop call, finish for unsupported/error paths, invalidate at actual history/compaction/rewind seams, use tracker snapshot in /live overlay and publish dedicated event to correct thread channel. Actual callback shape:

```python
payload = tracker.snapshot(thread_id).model_dump()
# /live overlay uses this same payload, not an independently calculated counter.
assert payload['scope'] == 'main'
assert payload['process_id'] == tracker.process_id
```

- [ ] Rerun integration and backend focused suites. Assert JSON keys contain no prompt, credential, auth epoch/account ID or token data; existing access-stop/error/usage and compaction tests remain green.

### Task 5: Defensive client contract and ordered host recovery (frontend)

**Files:** Modify `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`, `apps/vscode-extension/src/controller.ts`, `apps/vscode-extension/src/chat-panel.ts`; test `apps/editor-client/test/http-backend-client.test.ts`, `apps/vscode-extension/test/controller.test.ts`. A focused acceptance helper may be created as `apps/vscode-extension/src/context-usage-state.ts` with `apps/vscode-extension/test/context-usage-state.test.ts` to avoid duplicating logic.

**Interfaces:** Consumes locked wire and /live process_id. Produces validated client ContextUsage and host dedicated context view (measurement plus local stale flag); no ThreadUsage changes. Proposed local helper `acceptContextUsage(current, incoming, establishedProcessId)` is a pure ordering gate, not an existing symbol claim.

- [ ] Write failing safe-parse tests: negative/bool/string/fractional/unsafe input, bad status/quality/source combinations and timestamp rejected; valid zero accepted. A malformed context object cannot discard valid gates/teams/provider access. /live top-level process_id is independently decoded; nested mismatches rejected.
- [ ] Run `npm run -w @crucible/editor-client test -- test/http-backend-client.test.ts` red then green after isolated context safe-parse/mapping. SSE bad telemetry cannot throw into unrelated event routing.
- [ ] Write deferred-future host tests: A poll starts → B switch → A switch → old A result resolves; it must not write context/live/transcript. Connection/client replacement has identical retirement. New-process SSE requests hydration; only guarded live establishes process. Older generation/revision rejected, duplicates retain observedAt. Context-only /live update reaches panel despite generic signature equality.
- [ ] Implement ONE acceptance function called by SSE and polling before generic /live dedup. Capture navigation epoch + connection/client/thread identities for fetch/stream entry and recheck after awaits. Handshake process independent of payload validity. Clear local state on navigation only; poll hydrate on reopen, panel replay on reload. Failed poll marks retained value stale; successful unchanged poll clears stale without rewriting measurement timestamp.
- [ ] Run `npm run -w crucible-vscode-extension test -- test/controller.test.ts test/context-usage-state.test.ts` after red/green cycle. If helper is kept inside controller, put all acceptance cases in controller.test.ts and omit nonexistent helper-test argument.

### Task 6: Successful model/settings/auth refresh and catalog-cache guard (frontend)

**Files:** Modify `apps/vscode-extension/src/extension.ts`, `apps/vscode-extension/src/chatgpt-settings.ts`, `apps/vscode-extension/src/settings-data.ts`; extend `apps/vscode-extension/test/composer-model-swap.test.ts` and corresponding located auth/settings tests. Do not create a second authoritative meter denominator in composer state.

**Interfaces:** Consumes existing successful setProvider result, composerModelState, ChatGPT catalog and controller context hydration from Task 5. Produces refresh after successful mutations; rejected swaps neither persist nor refresh. Meter remains backend-authoritative.

- [ ] Add failing tests for known/unknown ChatGPT model windows, API-key Settings update and rejected mutations; preserve existing test requiring unknown catalog metadata leave compaction setting unchanged. Add same-registration sign-in/reconsent/sign-out/account activation composer/live refresh. Delayed catalog fetch after cache invalidation must not repopulate new epoch cache.
- [ ] Run `npm run -w crucible-vscode-extension test -- test/composer-model-swap.test.ts` and auth/settings targeted files selected by owner red. Implement cache epoch + post-await validation, refresh on successful lifecycle callbacks including sign-in, and guarded live hydration after successful provider/settings change. Do not join old count with new composer capacity or fetch ChatGPT catalog for API providers.
- [ ] Rerun focused suites green and host recovery tests. Active-vs-unrelated-account meter invalidation remains backend responsibility; host refresh alone must not manufacture a reset.

### Task 7: Dedicated accessible responsive ContextMeter (frontend)

**Files:** Create `apps/vscode-extension/webview-ui/src/components/ContextMeter.tsx` and `ContextMeter.test.tsx`. Modify `apps/vscode-extension/webview-ui/src/types.ts`, `src/hooks/useAppState.ts`, `src/components/ThreadView.tsx`, `src/components/InputArea.tsx`. Test existing `src/components/UsageChip.test.tsx` unchanged and extend located reducer/InputArea tests.

**Interfaces:** Consumes dedicated host context measurement plus stale boolean. Produces main-composer meter/details only; UsageChip still consumes cumulative ThreadUsage independently. Proposed `ContextMeter({usage, stale})` props are local UI interface, to be grounded against frontend's actual types before wiring.

- [ ] Add table-driven label/ratio tests for awaiting/unavailable/null, known-input unknown-window (no bar), genuine zero, in-flight estimate, last estimate/report, 100%, over-limit raw details/clamped fill and stale. Exact formatting oracle:

```ts
const ratio = inputTokens / contextWindow * 100;
const fill = Math.max(0, Math.min(100, ratio));
const prefix = status === 'in_flight' ? 'Context' : 'Last context';
const approximation = inputQuality === 'estimated' ? '~' : '';
const label = `${prefix} ${approximation}${Math.round(ratio)}%`;
// inputTokens/contextWindow here are validated known numbers; branch before calculation for null.
```

- [ ] Run `npm --prefix apps/vscode-extension/webview-ui test -- src/components/ContextMeter.test.tsx` red. Implement separate dedicated state/reducer messaging and props threading; meter displays visible approximation/current-vs-last, stale modifier and no fabricated zero. Details include count/window/request model/source/time and main-request-input-not-plan-usage explanation.
- [ ] Add keyboard click/Enter/focus and Escape tests for details trigger with expanded semantics, plus lack of per-token announcements. Keep fill decorative or give an accessible percentage with non-color text; over-limit raw ratio not capped in details. Use existing styling without thresholds/analytics.
- [ ] Group wrapping footer with nonshrinking actions. Run `npm --prefix apps/vscode-extension/webview-ui test -- src/components/ContextMeter.test.tsx src/components/UsageChip.test.tsx`, all affected reducer/InputArea suites, then typecheck. DOM tests do not prove layout; narrow-browser gate in Task 8 is separate.

### Task 8: Independent review and fresh integrated verification (all)

**Files:** No unrelated source changes. Reviewer reads actual backend/frontend/doc diffs; owners fix only their area and rerun checks after last edit. Lead updates docs only if contract decisions change.

**Interfaces:** Consumes all completed slices. Produces final command/result/limitation evidence and explicit acceptance disposition; no commit/integration operation performed by team.

- [ ] Compare a backend-produced serialized snapshot with client safe-parse and webview rendering fixture. Verify exact field names/enums/ISO timestamp and top-level process handshake. Independent review examines actual observation placement, auth epoch/cache identity and retirement, not only passing tests.
- [ ] Run fresh backend from `services/agentd-py`: `.venv/bin/pytest -q tests/test_context_usage.py tests/test_context_capacity.py tests/test_context_usage_integration.py tests/test_openai_transport.py tests/test_context_input_transport.py`; adjust new test file arguments to actual delivered files and include relevant existing usage/auth/access/compaction/rewind suites. Run `.venv/bin/ruff check` on changed Python files, `.venv/bin/mypy agentd` and report baseline-vs-new errors honestly. Do not claim full-suite pass if only focused suites ran.
- [ ] Run fresh root `npm test`, `npm run typecheck`, `npm run build`, then `npm --prefix apps/vscode-extension/webview-ui run build`. Expected exit 0; record failing suites/type errors/build limitations verbatim. Builds may generate ignored artifacts; do not change ownership or overwrite unrelated files.
- [ ] When authenticated environment is available, smoke real ChatGPT selection/catalog/unknown/auth/re-sign-in and API Settings change with a main request. Verify fixed input, approximate→reported, model/request details, and no old-model leakage. If credentials/access unavailable, report mock-only coverage and unperformed smoke rather than claim success.
- [ ] Browser check populated narrow composer (e.g. 320 CSS pixels): model/effort/context/UsageChip plus Send/Stop and task-abort controls remain usable; keyboard focus/details/Escape, dark/light theme and stale/reconnect visible. Reload webview, navigate A–B–A, miss SSE then recover via live, restart backend unavailable. Stop temporary servers and redact any evidence.
- [ ] Read-only `git diff --check`; final report lists changed files, commands/results, assumptions/open questions and unverified cases. No VCS mutation.

## Acceptance coverage self-review

| Design requirement | Delivery / review gate |
| --- | --- |
| One main physical INPUT, fixed during generation, replace not sum, no draft/output/reasoning/cache subtraction | Tasks 1, 2, 4 |
| Full instructions/history/native tails/tools/schema estimate | Task 2 |
| completed input independent of output/decode; zero vs invalid; errors/incomplete/cancel/early return | Tasks 1, 2 |
| ChatGPT catalog-only known/unknown; immediate selection awaiting; API Settings configured | Tasks 1, 3, 6 |
| TTL/coalescing/background catalog, private auth epoch, active vs unrelated account, ordinary refresh/no-op/rejected mutation | Tasks 3, 6 |
| Successful change no prior-model input; compaction/rewind/history replacement/versioned tombstone; late callbacks | Tasks 1, 3, 4 |
| Dedicated SSE/live equality/privacy, strict isolated parsing | Tasks 4, 5, 8 |
| Process handshake, old/duplicate order, unknown-process hydration/restart | Task 5 |
| Navigation/connection/A–B–A, stale unchanged reconnect, reopen/reload/missed SSE | Tasks 5, 8 |
| Visible exact wording/quality/current-last; unknown/zero/100/over-limit; details/raw clamped fill/stale | Task 7 |
| Keyboard/Escape/non-color/restrained announcements; narrow Send/Stop/task controls | Tasks 7, 8 |
| UsageChip/account Manage usage/access/compaction regressions | Tasks 2–8 |
| Baseline/TDD/fresh final tests/typechecks/builds and honest live-smoke limits | Tasks 0, 8 |

All approved design sections have a task and independent evidence gate. Cross-task wire keys/enums match the design; optional local helper names above are explicitly proposed, not asserted existing code. This plan intentionally uses owner-read integration seams rather than copying entire large controllers; concrete tests and locked interfaces constrain each slice. No test result is inferred from a plan checkbox or file creation.
