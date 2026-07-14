# Unified retry-status indicator — design

**Date:** 2026-07-14
**Status:** Approved (design); implementation pending
**Area:** Provider transports (`ollama_transport.py`, `openrouter_transport.py`) + reactive
chat controller (`CRUCIBLE_CHAT_CONTROLLER=1`) + webview chat UI

## Problem

Two independent retry mechanisms exist today, and both leak their "retrying…" text into
the permanent chat transcript instead of showing as a transient status:

1. **Transport-level backoff** — four separate retry loops, each duplicating the same
   exponential backoff formula (`min(5.0 * 2**(attempt-1), 60.0)`, default 4 retries / 5
   attempts):
   - `ollama_transport.py::_call_with_retry` (219-265) — Ollama's only retry site (network/
     429/5xx via `_RetryableHttpStatus`/`_RETRYABLE_EXCEPTIONS`); already takes an `on_chunk`
     callback.
   - `openrouter_transport.py::_stream_with_thinking` (391-450) — network/429/5xx; already
     takes `on_thinking`.
   - `openrouter_transport.py::_call_with_retry` (452-479) — network/429/5xx; takes **no
     callback at all**, retries silently.
   - `openrouter_transport.py`'s malformed-JSON fallback loop (325-358) — a **transport-
     level** retry on a JSON-parse failure (distinct from the controller-level one below);
     already takes `on_thinking`.

   On each retry, the sites that already have a callback call it with human text like
   `"⏳ {ExceptionType} — retrying in {delay}s (attempt {n}/{max})…"`.
2. **Controller corrective retry** (`chat/controller_loop.py`'s `consecutive_malformed` /
   `_MAX_MALFORMED = 3` loop) — retries a malformed/unparseable model response by injecting
   a correction into history. It broadcasts the same shape of message
   (`"⚠️ Response failed (n/3): … — retrying…"`) through the same `tool_thinking_chunk`
   event.

Because both piggyback on the thinking-chunk channel, the webview reducer
(`useAppState.ts`) treats this text exactly like real model reasoning: it accumulates into
`streaming.activeThinkingChunk` and gets sealed into the permanent `thinkingEntries` array
the moment real content arrives. Retry noise becomes indistinguishable from genuine
thinking and is baked into history forever.

A related but separate bug surfaced from the same investigation — **Finding #18**
(documented in `docs/2026-07-12-crucible-builds-kafka-findings.md`): when a turn exhausts
all retries, the `⚠️ The turn failed and had to stop: …` fallback message rendered
correctly live via SSE but did not always survive a reload. This design does **not**
address it (see Non-goals) — mentioned here only for context, since both bugs were found in
the same rate-limit-exhaustion scenario.

## Goal

1. Any retry — transport-level (network/429/5xx) or controller-level (malformed response)
   — surfaces as a distinct, self-overwriting, blinking status shown in place of the
   in-progress assistant response, not as thinking text.
2. The indicator updates in place as attempts progress ("attempt 1" → "attempt 2" → …)
   rather than accumulating as separate lines.
3. On eventual success, the indicator disappears with no trace in the permanent
   transcript — only the real answer remains.
4. On eventual exhaustion, the existing `⚠️ turn failed` message replaces the indicator
   (unchanged behavior — its persistence reliability, Finding #18, is separately scoped).

## Non-goals

- **Finding #18** (the exhaustion-failure message not reliably persisting across reload).
  Root cause is unconfirmed and live reverification is blocked on the provider's rate-limit
  quota resetting. Bundling a speculative fix into this spec would make it impossible to
  tell, if the flake recurs, whether this change caused a regression or the original bug
  just wasn't fixed — so it's tracked as a separate follow-up, not part of this
  implementation.
- Unifying attempt counts across the two retry layers into one global counter. If
  transport retries exhaust and the controller's corrective loop takes over, the indicator
  simply switches to that layer's own attempt/max — no cross-layer arithmetic.
- Exposing retry status for the legacy classifier-based `ChatAgent` path (pre-
  `CRUCIBLE_CHAT_CONTROLLER`) or the task/planning pipeline. Controller-chat only, matching
  the existing pattern for controller-only features in this codebase.
- Changing retry counts, backoff timing, or which errors are retryable. Pure surfacing of
  existing retry behavior.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Scope of "retry" | **Both mechanisms unified** — transport backoff (network/429/5xx) AND controller corrective retry (malformed response) both drive the same indicator. |
| Placement | **Inline in the transcript**, in the slot where the in-progress assistant response renders — not the persistent status bar (`WorkBar`). |
| Persistence on success | **None** — indicator vanishes, only the real answer remains in history. |
| Persistence on failure | **Unchanged existing behavior** (the `⚠️ turn failed` message). Its persistence reliability (Finding #18) is separately scoped, not addressed here. |
| Cross-layer attempt counting | **Not unified** — each layer reports its own attempt/max; the bubble shows whichever is currently active. |
| Wire mechanism | **New dedicated SSE event** (`retry_status`), not an overload of `tool_thinking_chunk`. |

## Design

### 1. Backend: retry classification & event emission

**Status-code capture.** `ollama_transport.py::_RetryableHttpStatus` currently swallows the
HTTP status into a message string with no structured field. It gains a `status_code: int`
attribute, set where it's raised (`_stream_chat`, currently line 284), so the catch site in
`_call_with_retry` can classify `reason` without string-parsing.

**`reason` values** (label only, for message text/icon — not branching logic, since both
classes render identically per the "unify both" decision):
- `"rate_limited"` — HTTP 429
- `"server_error"` — other retryable HTTP statuses (500/502/503/504)
- `"network_error"` — connection/DNS/timeout failures (the `httpx` exception tuple)
- `"malformed_response"` — a JSON-parse/schema failure, whether caught at the transport
  layer (OpenRouter's fallback loop) or the controller layer (below) — both are "the
  model's JSON didn't parse," just at different points in the call chain.

**Callback.** Every retry loop gets a new `on_retry(attempt: int, max_attempts: int,
reason: str, message: str) -> None` callback, threaded alongside the existing `on_chunk`/
`on_thinking` parameters. This is **not** a uniform swap — the four transport-level sites
need different treatment:
- `ollama_transport.py::_call_with_retry`, `openrouter_transport.py::_stream_with_thinking`,
  and the malformed-JSON fallback loop already accept a thinking callback — their existing
  `on_chunk(...)`/`on_thinking(...)` retry-text calls are replaced with `on_retry(...)`
  calls carrying the same information structured.
- `openrouter_transport.py::_call_with_retry` (452-479) has **no callback parameter today**
  — it retries silently. It gains one: `on_retry: Callable[[int, int, str, str], None] |
  None = None`.

**Threading through the reasoning layer (the largest piece of this work).** `on_thinking`
is not local to the transports — it's a parameter on all 4 `ReasoningEngine` Protocol
methods (`reasoning/contracts.py`), threaded through the 4 corresponding
`DefaultReasoningEngine` methods (`reasoning/engine.py`), and stubbed as a no-op on the 4
matching `ScriptedReasoningEngine` methods (`orchestrator/scripted_engine.py`) so scripted
tests don't break. `on_retry` needs the identical treatment across all three files/12
methods before it ever reaches `controller_loop.py` — this mirrors the exact
lockstep-breakage pattern noted in project memory for the Phase-3 reranker's
`recall_with_trace` change, and should be sized as such in the implementation plan.

`controller_loop.py` relays every `on_retry` call as:

```python
{"type": "retry_status", "payload": {"attempt": n, "max_attempts": m, "reason": r, "message": msg}}
```

broadcast via the existing `EventBroadcaster`, on the chat channel — never through
`tool_thinking_chunk`. The controller's own corrective-retry call sites
(`controller_loop.py` ~382-412, ~438-452) are updated to call `on_retry` directly (with
`reason="malformed_response"`) instead of broadcasting `tool_thinking_chunk` with the
"⚠️ Response failed"/"⚠️ Invalid response" text.

### 2. Contract & relay

`apps/editor-client/src/contracts/task-contracts.ts`'s `StreamEvent` union gains:

```typescript
{ type: "retry_status", payload: { attempt: number, max_attempts: number, reason: string, message: string } }
```

Payload keys stay snake_case, matching the existing convention for chat SSE events:
`ChatEventSchema` parses the raw wire payload directly with no rename step (e.g.
`memory_compacted`'s `anchor_version` stays snake_case in the Zod schema) — unlike the task
REST endpoints elsewhere in `HttpBackendClient`, which do explicit snake→camel mapping.

`apps/vscode-extension/src/controller.ts` handles `retry_status` by calling a new
`ui.updateRetryStatus(payload)` (parallel to the existing `appendChatThinkingEntry`/
`appendChatThinkingChunk` handlers). `chat-panel.ts` posts a `retryStatus` webview message
to the React app.

### 3. Frontend: ephemeral state & rendering

`useAppState.ts` gains a new state slice `retryStatus: RetryStatus | null` (`RetryStatus =
{attempt, max_attempts, reason, message}`), set on `retryStatus` events. It is **never**
appended into `thinkingEntries` or any history array — it follows the same
overwrite-in-place discipline as `thinkingStatus` (not the accumulate-then-seal discipline
of `activeThinkingChunk`/`thinkingEntries`).

Cleared when:
- `chat_response` fires (real content starts streaming) — success path, no trace left.
- `chat_done` fires — turn ended (covers the exhaustion/failure path too; the failure
  message itself arrives via the normal `chat_response`/`chat_done` flow and replaces
  whatever was showing in that slot).
- **The next `tool_thinking_chunk`/`chat_agent_thinking`/`tool_call` event arrives** — real
  progress resuming means the retry already resolved; don't wait for `chat_response`/
  `chat_done` to clear it, or a stale "retrying…" bubble sits over genuine subsequent
  output for the rest of the turn (`useAppState.ts`'s `appendThinkingChunk`/
  `showThinking`/`appendToolCall` reducers each also set `retryStatus: null`).
- `clearThread` fires — mirrors the existing `thinkingStatus: null` reset at that action
  (`useAppState.ts:122`); without this, switching threads mid-retry leaves the indicator
  showing against the newly selected thread.
- Any live-gate transition surfaced via the `/live` poll (e.g. entering an EditGate) — a
  turn can end without ever emitting `chat_response` (EDIT-phase turns ending at a gate),
  so the poll-driven `liveStatus` reducer case also clears `retryStatus`, not only the
  direct-SSE-event handlers.

Rendering: `retryStatus` is its own independent slot in the in-progress-assistant-row
component — **not** layered onto `thinkingStatus`'s existing row (which nulls itself once
real content starts, per `useAppState.ts:148`, so "same row" is not precise enough to
implement against). Whenever `retryStatus !== null`, it takes full rendering precedence in
that row over `thinkingStatus`/streaming text/tool pills, regardless of their own state: a
small bubble showing `message`, with a CSS opacity-pulse ("blinking") animation reusing the
existing motion tokens from the webview design system (see `reference_webview_design_system`
conventions — `index.css` primitives, not one-off styles). When `retryStatus` clears, the
row reverts to whatever it would otherwise be rendering.

### 4. Resolution

Success path: `retryStatus` clears on first real content chunk (or the next progress
event, per the clearing rules above); nothing about the retry sequence is persisted or
visible afterward.

Failure path: the existing catch-all in `chat/controller.py` (`except Exception as exc: …
ControllerOutcome(kind="answer", text="⚠️ The turn failed and had to stop: …")`) is
**unchanged** — it already flows through the normal `chat_response`/`chat_done` path and
will naturally replace whatever `retryStatus` was showing. Finding #18 (whether that
message reliably *persists* across reload) is out of scope here — see Non-goals.

### 5. Testing

- **Transport unit tests** (per provider): `on_retry` fires with correct
  `attempt`/`max_attempts`/`reason` on each retryable error class; `on_chunk`/`on_thinking`
  no longer carry retry text.
- **Controller loop tests**: `retry_status` events (not `tool_thinking_chunk`) fire from
  both the transport-exhaustion path and the malformed-response corrective path; attempt/
  max numbers match the active layer.
- **Frontend reducer tests**: `retryStatus` never lands in `thinkingEntries`; clears
  correctly on `chat_response` and `chat_done`; is not present after a normal turn with no
  retries.
- **Component test**: the retry bubble renders `message` and the pulse class, and yields to
  normal thinking/streaming rendering once `retryStatus` is null.
- **`ScriptedReasoningEngine` regression check**: confirm all existing scripted-engine
  tests still pass unchanged after `on_retry` is added as a no-op-stubbed parameter across
  its 4 methods (the lockstep-breakage risk called out in section 1).

## Risks

- **Protocol-wide signature change (section 1) is the highest-effort, highest-regression-risk
  part of this work.** `on_retry` must land identically across `reasoning/contracts.py` (4
  methods), `reasoning/engine.py` (4 methods), and `orchestrator/scripted_engine.py` (4
  methods) before `controller_loop.py` ever sees it. A partial rollout (e.g. one provider
  wired, another forgotten) would silently degrade to the current bug for whichever
  provider was missed.
- **Duplicated retry logic across transports.** `ollama_transport.py` and
  `openrouter_transport.py` already independently duplicate backoff logic (not factored
  into a shared helper), and OpenRouter alone has three separate retry loops with slightly
  different callback shapes today (one with no callback at all). This design adds
  `on_retry` to each site independently rather than extracting a shared retry module —
  consistent with existing duplication, not a new problem, but noted as a candidate for a
  future consolidation pass (out of scope here).
- **Finding #18 is explicitly out of scope** (see Non-goals) — do not treat this work as
  having addressed it, even though the two features touch adjacent code.
