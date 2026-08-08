# Exact context accounting — design

**Date:** 2026-08-09
**Status:** approved (design discussed and accepted; not yet planned)
**Depends on:** `31a3b2b` (per-chunk exact token counts), `d52afa7` (cumulative turn counter)

## Problem

Memory compaction decides when to evict conversation history. It fires when

```python
_history_tokens(history) >= window_tokens * trigger_frac      # compactor.py
```

Both sides of that comparison are wrong.

**The left side is a guess, wrong in two directions at once.** `estimate_tokens`
uses `len(text) // 3`. Measured against real responses on the configured provider,
the true ratio is 3.71–4.40 characters per token (three independent samples;
4.35 on a 2000-token generation). So history is over-counted by roughly 45%.
Meanwhile `_history_tokens` sums *message contents only* — the system prompt and
response schema are invisible to it, and on a real controller turn those are
57,086 + 1,122 characters, about **14,552 tokens**, or 17.5% of the trigger
threshold.

Those errors have opposite signs and cross over around 32k history tokens:
below that the trigger under-estimates how full the context is; above it, it
over-estimates and compacts while the window is still less than half used. No
adjustment to the ratio fixes a sign-flipping error — only a real measurement does.

**The right side is a hand-set number.** `CRUCIBLE_MEMORY_WINDOW_TOKENS` defaults
to 128000 and must be edited per deployment, in an env var, far from where the
model is chosen.

**And the consequence of getting it wrong is silent.** Verified against NVIDIA
NIM: a 360,017-token prompt returned HTTP 200 with no error; a 600,058-token
prompt also returned HTTP 200, billed every token, and produced
`completion_tokens: 1` with empty content and `finish_reason: "stop"`. There is
no `context_length_exceeded`, no error field — just a useless answer. Overshooting
the window does not fail loudly, it fails quietly.

## What is now available

`31a3b2b` made the transport read the provider's own usage. Each streamed call
yields exact `prompt_tokens` — the true size of everything sent, system prompt
and schemas included. That is precisely the number the trigger wants, and it is
already arriving.

## Non-goals

Deliberately excluded, each for a measured reason:

- **Deriving the window from a capability lookup.** NIM's `/v1/models` returns
  only `id`, `object`, `created`, `owned_by` for all 100 models. There is nothing
  to look up.
- **Detecting the window from an error.** NIM accepts `max_tokens: 100000000`
  and a 600k-token prompt without complaint. Overflow is not reported.
- **The task tool loop.** `create_tool_step` carries no progress callbacks, and
  `CRUCIBLE_TASK_SUBSYSTEM` is default-OFF. It keeps today's estimate. Wiring it
  is a symmetric follow-on if that path is ever enabled.
- **Passive window learning** (tracking the largest prompt that succeeded).
  Appealing and free, but a second mechanism with its own state; revisit later.

---

## Part 1 — Exact accounting in the compaction trigger

### Trigger basis

The trigger measures **total input** — `prompt_tokens` — not history alone. The
window holds everything, so everything counts against it. The fixed overhead is
real occupancy and stops being invisible:

```
window 128k, trigger 0.65  ->  83,200 tokens of INPUT
  system + schema  ~14,552   (fixed)
  history budget   ~68,648   before compaction fires
```

That is ~17% less history than today's nominal figure, correctly, because that
space is genuinely occupied.

### The feedback path

`prompt_tokens` is known by the transport; the trigger lives in the compactor.
A new callback carries it:

```python
on_usage(prompt_tokens: int, completion_tokens: int) -> None
```

Fired **once**, at stream end, from `_stream_with_finish_reason`. It is separate
from `on_progress`, which is a throttled display callback that fires many times;
this is an accounting signal that must arrive exactly once and unthrottled. It
rides the existing `supports_token_progress` capability gate, so the other eight
transports never see the kwarg.

Rejected alternatives:

- **A `last_prompt_tokens` property on the transport.** One transport instance is
  shared between the orchestrator and the chat controller, so concurrent turns
  would read each other's numbers.
- **Widening `create_controller_step`'s return.** That Protocol is implemented by
  nine transports plus the scripted engine.

### Handling the lag

`prompt_tokens` describes the call just made; compaction decides before the next
one. The observation therefore records which slice of history it measured:

```python
@dataclass(frozen=True)
class ObservedPrompt:
    tokens: int           # exact, from the provider
    message_count: int    # len(history) at the moment that call was built
```

`ControllerLoop` holds the latest observation for the turn and passes it to
`prepare_turn`, which passes it to `maybe_compact`. The compactor computes:

```
input_tokens = observed.tokens + estimate(history[observed.message_count:])
```

Exact for everything measured; estimated only for the few messages appended
since. The observation is discarded when `message_count > len(history)` — which
is exactly what a compaction that rewrote history produces.

### Fallback

No observation — a provider that reports no usage, or the first call of a run —
falls back to today's `_history_tokens`. Eight of nine transports are unaffected.

### Known imprecision

The **trigger** becomes exact. The **eviction floor** does not, fully.
`hot_token_frac` targets retained history, so it needs the fixed overhead
separated out:

```
overhead = max(0, observed.tokens - estimate(history[:observed.message_count]))
```

That subtraction is estimate-contaminated: it inherits the chars-per-token error
on the measured slice. It is still far better than today, and the alternative
(asking the provider to price the system prompt separately) is not offered by any
API. Recorded rather than hidden.

---

## Part 2 — Context window in the settings UI

### Where it lives

The window moves from `CRUCIBLE_MEMORY_WINDOW_TOKENS` to a field in the Settings
panel's Provider section, beside the model and key it belongs with. It travels
the same path as the other provider fields: the webview posts it with
`setProvider`, it is persisted to the extension's `globalState` alongside backend
and model, and it reaches the backend on `PUT /v1/config/provider` — which today
carries `{backend, model}` and gains an optional `context_window`.

Resolution order for the value the compactor uses, most specific first: the
configured provider window, then `CRUCIBLE_MEMORY_WINDOW_TOKENS`, then the
existing 128000 default. Existing deployments that set only the env var keep
working unchanged.

### Defaults

The field is pre-filled from a starter table keyed by model-name substring,
mirroring how `_is_reasoning_model` already matches models, and falling back to
128000 for anything unrecognised. The table starts deliberately small — the
models actually exercised here — because a wrong entry is worse than an obvious
default the user corrects. It is a **declared** value, not a detected one; the
non-goals above establish there is nothing to detect it from.

### Asking for the real number

Because the value is declared, the UI must ask for it properly rather than let a
default ride. The field carries help text telling the user to take the number
from the model's own source — its model card, or the provider's model
documentation — not from memory, and stating what each direction of error costs:

- **Too small** — compaction fires earlier than it needs to. History is evicted
  and a summarization call is paid for while the window is still half empty.
  Wasteful and degrading, but safe and self-correcting once fixed.
- **Too large** — the prompt overruns the real window. On a provider that
  validates, that is an error. On NVIDIA NIM it is worse: measured here, a
  600,058-token prompt returned HTTP 200, billed every token, and answered with
  `completion_tokens: 1` and empty content. No error, no warning — the agent
  simply starts producing nothing useful, and the cause is invisible.

That asymmetry is the argument for the Test button: too-small is merely
inefficient, while too-large can fail silently, so the value is worth confirming
rather than assuming. The help text says exactly that, so the user understands
why they are being asked to look it up.

### The Test button

An **opt-in** button beside the field, never run automatically on save.

It must judge by *capability*, not by HTTP status. The measurements above show a
600k-token prompt returning HTTP 200 with a one-token answer, so a test that
checks for an error would report success at five times the real window. The test
therefore sends a prompt of the declared size with a distinctive passphrase at
the very start and asks the model to repeat it:

- **passphrase recalled** — the model can genuinely use a context that size
- **not recalled, or empty answer** — the declared window is too large; the front
  of the prompt is not reaching the model

The button warns before running, stating the cost explicitly: it consumes
approximately one full window of input tokens (~128,000 for the default) in a
single request, and on a large window it is a multi-megabyte upload that may take
a minute.

### Failure handling

A test that errors reports the provider's message verbatim, in the same style as
the existing provider validate. A test that times out reports that, and does not
change the stored value — the field is whatever the user declared, and the test
is advisory.

---

## Testing

**Pure (the substantive tests).** The accounting is a pure function: given an
`ObservedPrompt` and a history, return the input-token total. That module has no
DOM, no network, no provider. Cases: observation covers the whole history;
observation plus appended tail; stale observation (count exceeds history);
absent observation falls back to the estimate.

**Compactor.** Fires at the right point with an observation, and at the old point
without one. A stale observation does not fire it early.

**Loop.** Records the observation from `on_usage` and forwards it to
`prepare_turn`.

**Transport.** `on_usage` fires exactly once, at stream end, with the provider's
`prompt_tokens`; it does not fire when the endpoint reports no usage.

**Settings.** The field round-trips; the Test button's verdict is derived from
passphrase recall rather than HTTP status.

---

## Decomposition

Two phases, independently useful and independently shippable:

1. **Part 1** — exact accounting. Backend only. Delivers the correctness win with
   no UI change; the window keeps coming from the env var.
2. **Part 2** — the settings field and Test button. Backend config plumbing plus
   the webview. Depends on nothing in Part 1, but is far less useful without it,
   since an exact window against a guessed usage figure still mis-triggers.

Build Part 1 first.
