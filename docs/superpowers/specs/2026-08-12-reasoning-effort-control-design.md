# Reasoning-effort control — design

**Date:** 2026-08-12
**Status:** approved, not implemented

## Problem

A single chat turn on `nvidia/nemotron-3` (via `openai_compatible` → NVIDIA NIM) spent
**169,490 reasoning tokens against 9,551 output tokens** — a ~17× ratio over a 64-minute
turn. The counters are trustworthy: `WorkBar.tsx`'s `TokenCounter` is cumulative for the
whole turn (`controller_loop.py:834`), and both halves are anchored to the provider's own
`completion_tokens`, split by the reasoning/content character ratio actually seen on the
wire (`_exact_counts`, `openai_compatible_transport.py:99`). So ~179k completion tokens
really were billed, ~95% of them reasoning.

Two things make that structural rather than a fluke:

1. `openai_compatible` sends `extra_body["reasoning"] = {"enabled": True}` with
   `temperature=1.0` for any model matching `_is_reasoning_model` (`nemotron` is in that
   substring list) and **no effort parameter at all**. The model reasons at whatever the
   endpoint's default is, flat out.
2. The ReAct loop re-reasons from scratch on every iteration — prior thinking never
   re-enters history — while each iteration emits one small JSON action. Thinking scales
   with iteration count; output does not.

The dials that *do* exist today are scattered, env-only, and inconsistent:
`CRUCIBLE_GEMINI_THINKING_LEVEL` (which defaults to `high`), `CRUCIBLE_GROQ_REASONING_EFFORT`,
`CRUCIBLE_OLLAMA_THINK`, and TurboQuant's `thinking_budget`. There is no UI for any of them,
and `openai_compatible`, `openrouter`, `anthropic`, `openai`, `watsonx`, and `huggingface`
have nothing.

## Goal

One user-facing effort ladder, settable from the composer, mapped honestly onto each
provider's own wire shape — including telling the user when a rung is not expressible.

## Prior art

### How other products surface it

| Product | Ladder | Where it is set | Notable |
|---|---|---|---|
| Codex CLI | `minimal · low · medium · high · xhigh` (model-dependent; `none`/`max` on some) | `model_reasoning_effort` in `config.toml`, plus `/effort <level>` in the TUI mid-session | `medium` documented as the balanced default; `xhigh` "only when evals justify the latency" |
| Claude Code | keyword-triggered (`think` < `megathink` < `ultrathink`), plus `/effort` | words in the message; `MAX_THINKING_TOKENS` env | began as fixed token budgets (ultrathink = 31,999), now maps to an effort level |
| Zed | effort selector menu in the Agent Panel (`agent::ToggleThinkingEffortMenu`) | UI popover | shown **only when the selected model supports adaptive thinking** — capability-gated, like this design |

Convergent pattern: a named ladder, a switcher next to the model picker, and a config
default. This design follows it.

### How each provider expresses effort

Verified against provider documentation, 2026-08-12. Three incompatible wire shapes —
an effort enum, a token budget, and a boolean/level hybrid.

| Provider | Parameter | Location | Values | "Off" |
|---|---|---|---|---|
| OpenAI | `reasoning_effort` / `reasoning.effort` | top-level | `none · minimal · low · medium · high · xhigh · max` (model-dependent) | yes — `none` (GPT-5.1's own default) |
| Anthropic | `output_config.effort` | nested | `low · medium · high · xhigh · max` | via `thinking:{type:"disabled"}`; on Opus 5 that **400s above `high` effort** |
| Gemini | `thinking_config.thinking_level` | config | `minimal · medium · high` (Gemini 3) | budget `0` / `thinking_enabled=false` |
| Groq | `reasoning_effort` | top-level | `low · medium · high` (gpt-oss); `none · default` (qwen) | **no — rejects `"none"` with a 400** on most models |
| Ollama | `think` | top-level | bool, or `low · medium · high` | yes — omit the field, or `false` |
| vLLM (what NIM runs) | `reasoning_effort` | top-level | `none · low · medium · high` | yes — `none` auto-injects `enable_thinking:false` |
| NIM / nemotron-3 | `chat_template_kwargs.{enable_thinking, low_effort}`, `reasoning_budget` | `extra_body` | bools + int | yes — `enable_thinking:false` |
| OpenRouter | `reasoning:{effort}` / `{max_tokens}` / `{enabled}` | top-level | `none … max` | yes |
| TurboQuant | `chat_template_kwargs.enable_thinking`, `thinking_budget_tokens` | body | bool + int | yes |

Three findings drive the design:

- **Groq 400s on `reasoning_effort:"none"`.** Concrete proof that blind pass-through is
  wrong; the unsupported rung fails loudly at request time rather than degrading.
- **nemotron-3 has three real rungs**, not five: off / `low_effort:true` / full. Medium,
  High, and Max collapse. Two candidate wire forms exist (NIM's documented
  `chat_template_kwargs`, or vLLM's generic `reasoning_effort`, which NIM inherits since it
  is built on vLLM) — settled by live probe before the mapping is committed.
- **OpenRouter publishes an enum→budget mapping** we can adopt verbatim for budget-shaped
  providers: `max`/`xhigh` ≈95% of available tokens, `high` 80%, `medium` 50%, `low` 20%,
  `minimal` 10%, `none` off.

## Decisions

| Question | Decision | Rejected alternatives |
|---|---|---|
| Surface | Composer chip beside the model picker | Settings-only (3 clicks from the composer); both (two sources of truth); per-message keywords (invisible, can't lower effort for a session) |
| Ladder | `Off · Low · Medium · High · Max` | Codex's exact 4 rungs (no real Off, which several providers do support); 3 rungs (can't express xhigh, forces a choice between extremes) |
| Unsupported rungs | Show all five; disable the unsupported ones with a reason; mark unknown ones unverified | Offer only supported rungs (ladder changes shape on model swap; cannot represent "unknown"); silent clamp (the exact silent-degradation class this repo has been bitten by) |
| Scope | Six transports: `openai_compatible` and `openrouter` (which today hardcode reasoning on/off with no effort control), plus `gemini`, `groq`, `ollama`, `turboquant` (which have an env-only dial to unify) | Only `openai_compatible` (leaves the env sprawl in place); all ten (two new provider integrations riding along with a UI feature) |
| Abstraction | Per-transport `apply_effort` hook, capability resolved async per-model | Central translation table (can't express model-dependent branching, which `openai_compatible` needs since one transport serves NIM/vLLM/LM Studio/Together); token budget as canonical unit (needs a max-output number we cannot detect, and bets against where every provider is heading) |

`anthropic`, `openai`, `watsonx`, and `huggingface` honestly report "no control" until
someone wires them; they are untouched by this work.

## Architecture

### New module: `agentd/providers/reasoning_effort.py`

Holds the ladder and the capability model, and no provider knowledge:

```python
class ReasoningEffort(StrEnum):
    OFF = "off"; LOW = "low"; MEDIUM = "medium"; HIGH = "high"; MAX = "max"

@dataclass(frozen=True)
class EffortSupport:
    supported:   frozenset[ReasoningEffort]
    unsupported: Mapping[ReasoningEffort, str]   # rung -> why
    # A rung in neither set is UNKNOWN, not unsupported.
```

**The tri-state is load-bearing.** A pasted `openai_compatible` endpoint has genuinely
unknown capability, and the UI must be able to say "unverified" rather than claiming
support it cannot vouch for. This mirrors the distinction the context-window verdict
already makes, where `json_mode` absent means "couldn't tell" and `json_mode` present
means "downgraded" — two different states that must not be collapsed.

**Clamping is downward-biased.** An unsupported rung resolves to the nearest supported
rung *below* it, and the substitution is reported to the caller. Never silently spend more
thinking than was asked for — that is the failure mode this feature exists to fix. The one
unavoidable upward case is `OFF` on Groq (which 400s on `"none"`), clamping to `LOW`.

### Transport contract

Two optional members, `getattr`-guarded exactly like the existing `supports_token_progress`,
so no transport is forced to implement them and the four unwired providers keep working:

```python
async def reasoning_effort_support(self, model: str) -> EffortSupport
def  set_reasoning_effort(self, level: ReasoningEffort | None) -> None   # None = send nothing
```

Effort is **instance state, not a per-call argument.** This means zero call-site changes
across the three ReAct loops, the summarizer, and the memory consolidator, and it mirrors
how the provider key and context window already live on the transport. Accepted
consequence: the level applies to *every* call that transport makes, including the memory
harness summarizer, not only the chat turn.

`set_reasoning_effort(None)` sends no effort field at all and is the escape hatch back to
today's behavior.

### Capability resolution

Registry-first, async, per-`(backend, model)` — the shape `_reasoning_config`
(`openai_compatible_transport.py:400`) already has so `OpenRouterJsonTransport` can consult
openrouter.ai's live model registry (`openrouter_transport.py:146`) instead of the
`_is_reasoning_model` substring heuristic. A new model on OpenRouter then gets correct
rungs with no code change.

`openai_compatible` against an unrecognized endpoint resolves every rung to UNKNOWN.
nemotron-3 resolves to `{OFF, LOW, HIGH}` with MEDIUM and MAX carrying the reason
"this model exposes only low_effort and full".

### Per-transport translation

Each in the hook that file already owns (`_build_extra_body` / `_reasoning_config`
for the OpenAI-compatible family):

| Transport | Wire form | OFF |
|---|---|---|
| `openai_compatible` | top-level `reasoning_effort` (vLLM's front door; auto-injects `enable_thinking`) | `"none"` |
| `openrouter` | `reasoning: {effort}` | `{enabled: false}` |
| `groq` | `reasoning_effort` | unsupported → clamps to LOW |
| `ollama` | top-level `think` | `false` |
| `gemini` | `thinking_config.thinking_level`, never alongside `thinking_budget` (sending both is a documented error) | `thinking_enabled=false` |
| `turboquant` | `chat_template_kwargs.enable_thinking` + `thinking_budget_tokens` | `enable_thinking:false` |

### Config plumbing

`PUT /v1/config/provider` gains `reasoning_effort`, applied by `ProviderRuntime.swap` to
every live transport — the same fan-out `context_window` already uses (`runtime.py:74`).
The response carries the **effective** level (post-clamp) and the resolved `EffortSupport`.
`GET /v1/config` reports both.

Env default `CRUCIBLE_REASONING_EFFORT`. The four existing vars
(`CRUCIBLE_GEMINI_THINKING_LEVEL`, `CRUCIBLE_GROQ_REASONING_EFFORT`,
`CRUCIBLE_OLLAMA_THINK`, TurboQuant's `thinking_budget`) become per-provider fallbacks
consulted only when the new one is unset, so no existing setup breaks on upgrade.

**Three env sites, not one.** A new backend env var needs `scripts/stress/start-backend.sh`,
the repo-root `.env`, **and** `buildBackendEnv` in `backend-process.ts` — the managed spawn
reads none of the first two. This repo has been bitten by that gap before.

## Frontend

**Placement.** A chip immediately left of `ModelMenu` in the composer, reusing that
component's upward-popover pattern and the shared `.surface-card` / `.menu-item`
primitives, so it reads as a sibling control. The chip label shows the current rung, and
when the backend clamped it, the substitution is inline — `High · no Max here` — so the
discrepancy is visible without opening the popover.

**Popover rows** — always five, in ladder order:

| State | Rendering |
|---|---|
| supported | normal, selectable |
| unsupported | disabled, reason as sublabel (`Groq rejects "none"`) |
| unknown | selectable, marked `unverified` |

**Data flow**, mirroring the model hot-swap end to end:

```
EffortMenu → postMessage setReasoningEffort
  → host: RuntimeManager.saveReasoningEffort (globalState)
         + PUT /v1/config/provider { reasoning_effort }
  → ProviderRuntime.swap → every live DefaultReasoningEngine transport
  → response: effective level + support map
  → host posts effortState back → chip updates
```

The echo back is required, not decorative — the chip is fully controlled and the effective
level can differ from the requested one after clamping, the same reason the Plan Mode
checkbox needs its `planModeState` round-trip. Swaps apply from the next turn, no restart.

**Capability refresh** rides the existing `listModels` round-trip and re-fetches on model
swap, because capability is per-`(backend, model)`. Accepted limitation: the support map is
only as fresh as the last model-list fetch.

**Contract.** editor-client gains `ReasoningEffortSchema` and `EffortSupportSchema`,
`getConfig()` returns the level plus support, and `setProvider` accepts `reasoningEffort`
(snake↔camel mapped in `http-backend-client.ts` as usual).

**The Settings panel deliberately does not duplicate this.** One write path means no
reconciliation between a per-workspace default and a session override. The Provider
section keeps context-window; effort lives only on the chip.

## Failure behavior

**A 400 on a rung we believed supported:** retry once with the effort field omitted
(provider default), then mark **only that rung** unsupported for the life of the process —
never the whole capability. This is the explicit lesson from the sticky JSON-mode
downgrade documented in `CLAUDE.md`, which conflates "cannot honor *this* schema" with
"cannot honor *any* schema" and permanently degrades an endpoint that would have accepted
the next request. Same trap, same shape; cheap to avoid here because the rungs are
independent.

**Only probative failures teach.** A 429, 5xx, timeout, or connection error must never mark
a rung unsupported — the rule the JSON downgrade already follows. A rate-limit blip that
permanently pinned the session to `LOW` would be the same bug wearing a different hat.

**Everything degrades rather than raises.** A failed capability fetch resolves every rung
to UNKNOWN; a transport without the two optional members is simply never asked.

## Testing

- Table-driven per transport: exact request body for all five rungs × six transports. Two
  are regression guards for verified facts — Groq must never emit `"none"`; Gemini must
  never emit `thinking_level` and `thinking_budget` together.
- Clamp: downward bias; the one upward exception (`OFF`→`LOW` on Groq); UNKNOWN is not
  UNSUPPORTED.
- Learning: one rung 400s → only that rung is marked, the others still send; a 429 marks
  nothing.
- Route: `PUT /v1/config/provider` reaches every live engine — same shape as the existing
  `context_window` swap test.
- TypeScript: editor-client Zod snake↔camel round-trip; `EffortMenu` rendering for all
  three capability states and for the clamped label.

**Live verification, before the mapping is committed.** One real probe against the NIM key
to settle whether nemotron-3 honors vLLM's generic `reasoning_effort` or needs NIM's
`chat_template_kwargs.{enable_thinking, low_effort}`. Unit tests cannot answer this, and
this repo's history says measure it — the NIM `oneOf`/`json_schema` findings and the gopls
unacked-server-request hang were both cases where the reasonable assumption was wrong and
only a live probe found it.

**Acceptance criterion.** Run the same turn at `HIGH` and at `LOW` on nemotron-3 and
compare the WorkBar counters. The instrumentation already exists and is anchored to the
provider's own `completion_tokens`, so the ~17× ratio is directly measurable before and
after. If `LOW` does not move it, the mapping is wrong and we learn immediately rather than
shipping a placebo dial.

## Build order

1. `reasoning_effort.py` — enum, `EffortSupport`, clamp + tests
2. `openai_compatible` mapping, gated on the live NIM probe
3. Remaining five transports
4. `PUT /v1/config/provider` + `ProviderRuntime.swap` fan-out + `GET /v1/config`
5. editor-client contract
6. `EffortMenu` chip + host plumbing + the three env sites

## Out of scope

- Wiring `anthropic`, `openai`, `watsonx`, `huggingface` (each needs its own live
  verification against a real key).
- Per-call effort — e.g. running the memory summarizer at a lower rung than the chat turn.
  The instance-state decision forecloses it for now; revisit if summarizer cost becomes
  the dominant term.
- A token-budget escape hatch (`reasoning_budget` / `thinking_budget_tokens` as a raw
  number). The enum covers the need; a raw budget can be added later behind the same
  transport hook without disturbing the ladder.
