# Generic OpenAI-compatible provider — design

**Date:** 2026-07-31
**Status:** approved by user, ready for planning
**Motivation:** reach `z-ai/glm-5.2` (and any other OpenAI-compatible endpoint) on NVIDIA NIM
without adding a bespoke transport per vendor.

## Context

Crucible has nine backends, each with a hand-written transport in `agentd/providers/`. Adding a
vendor today means a new file, a new factory branch, and new entries in four mirrored tables. That
cost is paid over and over for endpoints that are all the same wire protocol: OpenAI
`/chat/completions`.

The immediate trigger was NVIDIA NIM (`https://integrate.api.nvidia.com/v1`), which serves
`z-ai/glm-5.2` and ~200 other models. Two facts made the shape of the fix obvious:

- **`openai_transport.py` cannot be repointed.** It builds `AsyncOpenAI(...)` and then takes
  `client.responses` — the Responses API. Verified live: NIM returns `404 page not found` for
  `POST /v1/responses` and `401` (i.e. the route exists) for `POST /v1/chat/completions`. It also
  accepts no `base_url` today.
- **`openrouter_transport.py` is already the generic client.** It is
  `AsyncOpenAI(api_key=…, base_url=…)` → `client.chat.completions`, with retry, streaming,
  a strict-`json_schema`→`json_object` fallback, and the `max_tokens`/`json_max_tokens` split.
  It is vendor-neutral code that happens to be named after one of its users.

So this is less "write a new provider" than "give the generic client that already exists a
configurable endpoint, and stop pretending it belongs to OpenRouter."

### Verified endpoint capabilities (live, 2026-07-31)

Probed directly against NIM with a real key rather than assuming parity from docs. NVIDIA's own
documentation is non-committal — it says `response_format` with `json_schema` is supported but
*recommends* `nvext.guided_json` for LLMs, and warns that structured-output support varies by
model and NIM release. Measured, on `nvidia/nemotron-3-ultra-550b-a55b`:

| Probe | Result |
|---|---|
| Plain `chat/completions`, streamed | PASS — 0.5s TTFB |
| Strict `response_format: json_schema`, flat object | **PASS** — valid JSON, schema honored |
| Strict `json_schema` with a **`oneOf` discriminated union** | **PASS** — discriminator honored |

The third is the controller's tight-schema shape. It works, so `nvext.guided_json` is **not**
needed and strict mode is the correct default. `supports_oneof_grammar`/`supports_anyof_grammar`
are justified as `True` for this endpoint.

**`z-ai/glm-5.2` also passes, but is queue-bound on the free tier.** Measured separately:

| Probe | Result |
|---|---|
| Plain `chat/completions` | PASS — but **226.8s TTFB**, of which only 0.4s was generation |
| Strict `json_schema`, flat object | HTTP 504 after 302s — gateway timeout, **not** a schema rejection |
| Strict `json_schema`, `oneOf` union | **PASS** — 223.6s TTFB, discriminator honored |

The `oneOf` probe is strictly harder than the flat one, so its success establishes strict-schema
support for this model; the flat probe's 504 is free-tier capacity, not capability. On the same
key, URL, and request shape, `meta/llama-3.1-8b-instruct` answered in 0.34s and Nemotron 3 Ultra
in 0.75s, and NIM's own `nvext.scheduler_snapshot` reported `num_waiting_reqs: 0` for the
responsive models — so the wait is NVIDIA-side queueing for this specific model, not a client
problem.

**Practical consequence:** GLM-5.2 on NVIDIA's *free* tier is not viable for interactive use here.
A single Crucible turn issues many LLM calls, so a ~225s wait per call compounds past usability
even though each call is fast once served. Point the provider at Nemotron 3 Ultra on NIM, or at a
paid GLM-5.2 endpoint (Z.ai's own API is OpenAI-compatible) — which is a base-URL change under
this design, not code.

None of this affects the design: the transport is model-agnostic, and the sticky downgrade (§4)
covers any endpoint whose capabilities differ from those measured here.

## Scope

One configurable `openai_compatible` slot — a single endpoint at a time, matching Crucible's
existing one-provider-at-a-time model. Explicitly **not** a list of named saved endpoints.

## 1. Architecture

```
settings / setup UI  ──extraCredentials──►  build_transport(backend, credentials)
   Base URL                                   │  credentials overlay os.environ
   Model                                      ▼
   API key (optional)                 OpenAICompatibleTransport(base_url=…)
                                                ▲
                                       OpenRouterJsonTransport  (subclass)
```

No new configuration mechanism is required. `build_transport(backend, credentials)` already
overlays a request-supplied dict onto the process environment, and the UI already ships arbitrary
`ProviderInfo.extraFields` through SecretStorage into that dict. **watsonx already sends a URL
this way** (`WATSONX_URL`), so base-URL-as-a-field is an established pattern here, not new
construction.

## 2. Transport extraction

`providers/openai_compatible_transport.py` receives the vendor-neutral logic currently in
`openrouter_transport.py`: `generate_json` (strict → fallback), `generate_text`,
`_call_with_retry`, `_stream_with_thinking`, `_extract_text`, `_parse_output_object`, and the
`max_tokens` / `json_max_tokens` split.

Three protected hooks keep the base vendor-free:

| Hook | Base implementation | OpenRouter override |
|---|---|---|
| `_default_headers()` | `None` | `HTTP-Referer` / `X-Title` |
| `_build_extra_body(model)` | `{}` plus reasoning params | adds `provider.require_parameters` |
| `_reasoning_config(model)` | name-substring heuristic | live model-caps registry |

`_ModelCapabilityCache` stays in `openrouter_transport.py`. It is an HTTP client for
openrouter.ai's model registry — vendor-specific, not shared behavior.

### Why extract rather than clone

A clone means every future fix lands twice by hand. This codebase already has that failure mode
documented: three parallel ReAct loops that duplicate mitigations manually. The
`json_max_tokens` split exists precisely because an under-provisioned budget silently truncated
real file writes into invalid JSON (Finding #11) — exactly the class of fix that a forgotten twin
would miss.

The extraction is the **riskiest part of this work**, because it touches a live-validated path.
The mitigation is the 21 existing tests in `test_openrouter_transport.py`, which must pass
unchanged. If they prove too shallow to protect the refactor, falling back to a clone is the
correct retreat — that call gets made during implementation, on evidence.

## 3. Configuration

```
CRUCIBLE_OPENAI_COMPAT_BASE_URL   required — no default
CRUCIBLE_OPENAI_COMPAT_MODEL      required — no default
CRUCIBLE_OPENAI_COMPAT_API_KEY    optional — local vLLM / LM Studio need none
CRUCIBLE_OPENAI_COMPAT_MAX_TOKENS         default 4096
CRUCIBLE_OPENAI_COMPAT_JSON_MAX_TOKENS    default 16384
CRUCIBLE_OPENAI_COMPAT_TIMEOUT_SEC        default 120
CRUCIBLE_OPENAI_COMPAT_MAX_RETRIES        default 4
```

Backend id: `openai_compatible`.

**No `_DEFAULT_MODEL` entry.** `default_model()` raises for this backend with a message naming the
env var; `validate.py`'s deliberately broad catch already surfaces construction errors verbatim to
the UI. A guessed default would produce a confusing failure at the endpoint instead of a clear one
at the boundary.

**Base URL normalization** mirrors `anthropic_transport.normalize_endpoint_to_base_url`: strip a
trailing `/`, and strip a pasted `/chat/completions` suffix (the predictable copy-paste error, since
that is the URL vendors document).

**Optional API key is the one deliberate asymmetry.** Every existing cloud transport raises when
its key is missing. Local endpoints legitimately have none, so this transport sends an
`Authorization` header only when a key is present.

## 4. Sticky JSON-mode downgrade

The base class carries `self._json_mode`, starting at `"strict"`. On the first strict failure it
logs a warning naming the endpoint, falls back to `json_object` with the schema injected into the
system prompt, and sets `_json_mode = "json_object"`. Every later call in that process skips the
strict attempt.

The same flag drops `supports_oneof_grammar` / `supports_anyof_grammar` to `False` on downgrade, so
the controller stops emitting a tight `oneOf` schema to an endpoint that just proved it cannot
honor schemas.

State is **process-lifetime only** — a restart re-probes, so an endpoint that gains support
recovers with no user action and no cache to invalidate.

This corrects a real inefficiency inherited from the source: the current OpenRouter fallback
re-attempts strict on **every** call, as its own comment concedes ("forces a guaranteed 404 →
fallback every turn"). On an endpoint that never supports strict, that is one wasted request per
LLM call, and this system makes many per turn. OpenRouter inherits the fix.

## 5. Validate probe

`ping_provider` currently returns `str`. It becomes a small result object
`{model, json_mode, warning?}`, and the route returns `{ok, model, json_mode, warning?}`. Only the
validate route calls `ping_provider` — `runtime.py` uses `ping_transport` directly — so the blast
radius is one call site.

For `openai_compatible` only, validate performs the normal ping **plus** one tiny strict
`json_schema` call, and reports which mode the endpoint supports:

```
✓ Connected — nvidia/nemotron-3-ultra-550b-a55b
✓ Strict JSON schema supported

✓ Connected — some-model
⚠ No strict JSON schema; will use json_object + schema-in-prompt. Expect lower reliability.
```

The probe is **informational only**. It does not persist a mode or configure the transport; the
transport rediscovers independently via §4 at a cost of at most one failed call per process.
Persisting a probed capability would introduce a stale-cache bug class for a fact that is already
cheap to learn at runtime.

Known providers keep the plain ping — their capabilities are not in question, and first-run setup
should not pay an extra request to re-confirm them.

## 6. Frontend

One entry in `setup-data.ts`'s `PROVIDERS`, carried automatically by the existing `extraFields`
machinery into both the setup wizard and the settings panel:

```ts
{
  id: "openai_compatible",
  label: "OpenAI-compatible",
  local: false,
  keyEnvVar: "CRUCIBLE_OPENAI_COMPAT_API_KEY",
  defaultModel: "",
  extraFields: [
    { envVar: "CRUCIBLE_OPENAI_COMPAT_BASE_URL", label: "Base URL",
      placeholder: "https://integrate.api.nvidia.com/v1" },
  ],
}
```

Mirror entries go in `backend-process.ts`'s `MODEL_ENV_VAR` and `vscode-runtime.ts`'s
`PROVIDER_KEY_ENV` (both are hand-maintained copies of the Python tables and already documented as
such).

Base URL gets a **placeholder and a short hint** listing known-good services (NVIDIA NIM, vLLM,
LM Studio, Together, DeepInfra) — no preset dropdown. A preset table is a hardcoded list that goes
stale as vendors change URLs and default model ids, for a field the user pastes once.

### What already works (verified by reading the components)

`ProviderSection.tsx` (settings) needs **no structural change**: it already renders
`provider.extraFields` as labeled inputs with `placeholder` support, already treats the API key as
optional on save (`provider.local || !apiKey ? {} : { apiKey }`), and already gates only on
`!model`. Base URL therefore becomes editable in settings purely from the `PROVIDERS` entry above.

`defaultModel: ""` also needs no new validation — both surfaces already block save on an empty
model.

### The one required change

`SetupApp.tsx:266` gates the wizard's save on:

```ts
disabled={busy || !model || (!provider.local && !apiKey) || !extraFieldsFilled}
```

`openai_compatible` is `local: false` but has an **optional** key, so this would wrongly block the
local-vLLM / LM-Studio case. `ProviderInfo` gains `keyOptional?: boolean`, set for this provider,
and the gate becomes:

```ts
(!provider.local && !provider.keyOptional && !apiKey)
```

`ExtraField.optional` already exists and is honored by `extraFieldsFilled`, so leaving Base URL
non-optional correctly makes it a required field.

### Validate result rendering

The validate result gains optional `jsonMode` / `warning`, rendered under the existing validate
status line in both `SetupApp.tsx` and `ProviderSection.tsx`.

## 7. Error handling

| Failure | Behavior |
|---|---|
| Base URL missing | Construction raises naming the field; UI renders it verbatim |
| Model missing | `default_model()` raises naming the env var |
| Unreachable / wrong URL | Existing retry, then the provider's own message surfaces |
| No strict schema support | Warn once, downgrade, keep running (§4) |
| Both modes fail | Existing "fallback also failed" error |
| Endpoint queues the request | Surfaces as the existing timeout; `CRUCIBLE_OPENAI_COMPAT_TIMEOUT_SEC` is the knob |

## 8. Testing

New `tests/test_openai_compatible_transport.py`, using the `completions_client` injection seam the
other transports use so no test makes a network call:

- base URL normalization (trailing slash, `/chat/completions` suffix)
- optional API key — header present with a key, absent without
- strict path success
- downgrade fires once on strict failure and **sticks** for later calls
- `supports_oneof_grammar` / `supports_anyof_grammar` flip to `False` on downgrade
- missing base URL and missing model produce messages naming the field

Regression contract for the extraction: the **21 existing tests in
`test_openrouter_transport.py` must pass unchanged**, with one deliberate exception — any test
asserting that strict is re-attempted per call must be updated to the sticky behavior.

Also: `test_provider_factory.py` entries for the new backend, a `setup-data.test.ts` case for the
new provider entry, and a `ProviderSection.test.tsx` case covering save with an empty API key
(the `keyOptional` path).

## 9. Out of scope

- Named multi-endpoint list (single slot by decision)
- URL preset dropdown
- Per-endpoint manual JSON-mode override
- `nvext.guided_json` support — measured unnecessary (§Context); revisit only if a real endpoint
  needs it
- The memory-harness summarizer's transport, which remains restart-scoped (pre-existing v1
  limitation of the hot-swap path, unchanged here)
