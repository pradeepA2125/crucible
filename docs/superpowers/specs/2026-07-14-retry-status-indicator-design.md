# Unified retry-status indicator — design

**Date:** 2026-07-14
**Status:** Approved (design); implementation pending
**Area:** Provider transports (`ollama_transport.py`, `openrouter_transport.py`) + reactive
chat controller (`CRUCIBLE_CHAT_CONTROLLER=1`) + webview chat UI

## Problem

Two independent retry mechanisms exist today, and both leak their "retrying…" text into
the permanent chat transcript instead of showing as a transient status:

1. **Transport-level backoff** (`ollama_transport.py::_call_with_retry`/
   `_stream_with_thinking`, `openrouter_transport.py`'s three retry loops) — retries real
   network/429/5xx failures with exponential backoff (`min(5.0 * 2**(attempt-1), 60.0)`,
   default 4 retries / 5 attempts). On each retry it calls the existing `on_chunk`/
   `on_thinking` callback with human text like `"⏳ {ExceptionType} — retrying in {delay}s
   (attempt {n}/{max})…"`.
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

Separately, **Finding #18** (rate-limit exhaustion, documented in
`docs/2026-07-12-crucible-builds-kafka-findings.md`): when a turn exhausts all retries, the
`⚠️ The turn failed and had to stop: …` fallback message (`chat/controller.py`'s catch-all
`except Exception`) rendered correctly live via SSE but did not always survive a reload —
i.e. it didn't reliably persist. Root cause is unconfirmed (two direct-path reproductions
both passed); live re-verification is blocked on the provider's rate-limit quota resetting.

## Goal

1. Any retry — transport-level (network/429/5xx) or controller-level (malformed response)
   — surfaces as a distinct, self-overwriting, blinking status shown in place of the
   in-progress assistant response, not as thinking text.
2. The indicator updates in place as attempts progress ("attempt 1" → "attempt 2" → …)
   rather than accumulating as separate lines.
3. On eventual success, the indicator disappears with no trace in the permanent
   transcript — only the real answer remains.
4. On eventual exhaustion, the existing `⚠️ turn failed` message replaces the indicator and
   reliably persists (hardening pass for Finding #18).

## Non-goals

- Root-causing Finding #18's exact live failure mechanism with certainty. This design
  hardens the persist call site; if the flake recurs after this change, it needs its own
  systematic-debugging pass with live reverification against the provider.
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
| Persistence on failure | **Unchanged existing behavior** (the `⚠️ turn failed` message), hardened for reliability. |
| Cross-layer attempt counting | **Not unified** — each layer reports its own attempt/max; the bubble shows whichever is currently active. |
| Wire mechanism | **New dedicated SSE event** (`retry_status`), not an overload of `tool_thinking_chunk`. |

## Design

### 1. Backend: retry classification & event emission

Both transport retry loops and the controller's `consecutive_malformed` loop currently
call `on_chunk`/`on_thinking` with retry text. Each call site instead calls a new
`on_retry(attempt: int, max_attempts: int, reason: str, message: str)` callback, threaded
alongside the existing `on_chunk`/`on_thinking` parameters (same pattern already used for
those).

`reason` is a free label used only for message text/icon selection, not for branching
logic — since both classes are unified in rendering:
- `"rate_limited"` — 429 responses
- `"network_error"` — connection/DNS/timeout failures
- `"server_error"` — 5xx responses
- `"malformed_response"` — controller corrective retry (schema/parse failure)

`controller_loop.py` relays every `on_retry` call as:

```python
{"type": "retry_status", "payload": {"attempt": n, "max_attempts": m, "reason": r, "message": msg}}
```

broadcast via the existing `EventBroadcaster`, on the chat channel — never through
`tool_thinking_chunk`. The controller's own corrective-retry call site
(`controller_loop.py` L372-412, L419-451) is updated to call `on_retry` directly instead of
broadcasting `tool_thinking_chunk` with the "⚠️ Response failed" text.

### 2. Contract & relay

`apps/editor-client/src/contracts/task-contracts.ts`'s `StreamEvent` union gains:

```typescript
{ type: "retry_status", payload: { attempt: number, maxAttempts: number, reason: string, message: string } }
```

`apps/vscode-extension/src/controller.ts` handles `retry_status` by calling a new
`ui.updateRetryStatus(payload)` (parallel to the existing `appendChatThinkingEntry`/
`appendChatThinkingChunk` handlers). `chat-panel.ts` posts a `retryStatus` webview message
to the React app.

### 3. Frontend: ephemeral state & rendering

`useAppState.ts` gains a new state slice `retryStatus: RetryStatus | null` (`RetryStatus =
{attempt, maxAttempts, reason, message}`), set on `retryStatus` events. It is **never**
appended into `thinkingEntries` or any history array — it follows the same
overwrite-in-place discipline as `thinkingStatus` (not the accumulate-then-seal discipline
of `activeThinkingChunk`/`thinkingEntries`).

Cleared when:
- `chat_response` fires (real content starts streaming) — success path, no trace left.
- `chat_done` fires — turn ended (covers the exhaustion/failure path too; the failure
  message itself arrives via the normal `chat_response`/`chat_done` flow and replaces
  whatever was showing in that slot).
- Any gate/status transition that ends the turn.

Rendering: in the in-progress assistant message slot — the same row that shows
`thinkingStatus` ("Thinking…") today — `retryStatus`, when non-null, takes precedence and
renders instead: a small bubble showing `message`, with a CSS opacity-pulse ("blinking")
animation reusing the existing motion tokens from the webview design system (see
`reference_webview_design_system` conventions — `index.css` primitives, not one-off
styles). When `retryStatus` clears, the slot reverts to normal thinking/streaming
rendering.

### 4. Resolution & Finding #18 hardening

Success path: `retryStatus` clears on first real content chunk; nothing about the retry
sequence is persisted or visible afterward.

Failure path: the existing catch-all in `chat/controller.py` (`except Exception as exc: …
ControllerOutcome(kind="answer", text="⚠️ The turn failed and had to stop: …")`) is
unchanged in shape, but its persist call site (`_write_turn_message`, called from
`_finish`) gets:
- An explicit `try/except` with logging around the persist call, so a persistence failure
  is at least observable in logs instead of silently vanishing.
- Verification that call ordering (persist-then-broadcast vs. broadcast-then-persist)
  exactly matches the known-good success path — Finding #18's leading suspects were
  interaction with in-flight-pills bookkeeping or a duplicate-request 409, both ordering-
  sensitive.

This is explicitly a **hardening pass, not a confirmed root-cause fix** — flagged as a risk
below.

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
- **Regression test for Finding #18 hardening**: a scripted-engine test forcing the
  exhaustion path confirms the failure message persists in the same run (does not
  guarantee the live flake is fixed — see Risks).

## Risks

- **Finding #18 may not be fully closed by this change.** The root cause is unconfirmed;
  this design hardens the known persist call site but the actual live failure could be
  elsewhere (e.g. a race with a duplicate `/mode-decision` POST). If it recurs, treat as a
  fresh bug needing systematic-debugging + live reverification, not a regression of this
  work.
- **Duplicated retry logic across transports.** `ollama_transport.py` and
  `openrouter_transport.py` already independently duplicate backoff logic (not factored
  into a shared helper). This design adds `on_retry` to both independently rather than
  extracting a shared retry module — consistent with existing duplication, not a new
  problem, but noted as a candidate for a future consolidation pass (out of scope here).
