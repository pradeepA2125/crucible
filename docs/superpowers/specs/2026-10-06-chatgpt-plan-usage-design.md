# ChatGPT plan usage ("Continue with ChatGPT") — design and phased plan

Status: Phases 1–4 implemented 2026-10-06 on `feat/chatgpt-plan` (uncommitted). Phase 0 spike script and Phase 5 live smoke still need a real sign-in. See "Implementation notes" at the end.
Sources: every page under https://developers.openai.com/siwc (15 pages, fetched raw
2026-10-06), and the code at `f893ffe` on `feat/subagents-v2`.

## 1. What we get

Eligible **ChatGPT Plus and Pro** users sign in with ChatGPT, and Crucible sends its
model calls to `POST https://api.openai.com/v1/responses` with the user's OAuth access
token. Usage comes out of their ChatGPT plan; no API key. OpenAI calls this
"ChatGPT plan usage for open-source apps". It is a **preview**, and it is "available to
all open-source partners": Crucible is public, Apache-2.0 and runs locally, so it
qualifies with no approval step and no client secret.

Limits that matter to us:
- **Plus:** "the five-hour usage limit is shared across all apps where they use their
  ChatGPT plan". Crucible gets no separate allowance. **Pro:** no five-hour limit.
- An app-specific limit can also apply; the user manages it in ChatGPT Settings → Usage.
- If plan usage fails, inference stops. OpenAI never silently falls back to another
  billing path, and neither should we.

## 2. The docs, page by page

| Page | What it requires | Where it lands |
|---|---|---|
| Home / Quickstart | Plus and Pro are eligible. Label the button **Continue with ChatGPT**, use approved branding. Identity scopes alone grant no inference. | §6 UI |
| Request a client ID | Waitlist for *commercial* sign-in. | N/A — the OSS flow needs no client ID request |
| On your website | Confidential/website flow, but its ID-token checks apply to us: discovery, JWKS, `iss` / `aud` / `exp` / nonce, small clock skew, refetch keys on an unknown `kid`. | §4.3 |
| In your ChatGPT plugin | Connector flow, partner-only. | N/A |
| ChatGPT plan usage (overview) | Client vs. agent host. Persist an opaque `ext_agent_host_id` **before** the first sign-in (`urn:uuid:` or JWK-thumbprint URN). | §4.1 |
| UI/UX guidelines | Welcome modal on the first plan sign-in only. Settings entry "Use your ChatGPT plan". **"Using ChatGPT plan · Manage usage"** shown near the composer/model picker. Usage-limit modal/compact message with Manage usage as the primary action. | §6 |
| Registration and sign-in | First registration uses `client_id=dynamic_agent_client` + `agent_name_hint` + `ext_agent_host_id`, then keep the issued `oaiapp_…` id. Loopback `http://127.0.0.1:<port>/auth/callback` (only the port may change). Fresh state / nonce / PKCE (S256) per attempt. Exchange the code with no secret. Check the ID token and the `chatgpt.tokens.use.direct` scope. One 0600 credential file per registration. | §4.2–4.4 |
| Accounts and sessions | Several registrations, kept separate even with the same email. Reauth reuses the issued `client_id` and sends `id_token_hint`/`login_hint`. Sign-out revokes the refresh token (an empty 200 = success; if unconfirmed, tell the user). Refresh with the issued id + `resource`, serialized because refresh tokens rotate. Tokens never in logs or URLs. Link to ChatGPT Settings → Usage. | §4.4–4.6, §6 |
| Models and inference | `GET /v1/models` with the token; show `visibility == "list"` entries by `display_name` in server order, send `slug`. `store:false`, `stream:true`. Success only on `response.completed`. A usage-limit error can arrive as `response.failed` after streaming starts. `response.incomplete` is not success. | §5.1, §5.4 |
| Codex app-server | Drive Codex as a child process. | **Not used** — Crucible has its own agent loop; app-server would replace it |
| Self-hosted VMs | The callback only reaches the machine running the browser, so sign in locally and copy the credential file. | Deferred (§8) — this is also the VS Code Remote-SSH case |
| Token reference | Access token lasts 1 h. Refresh token lasts 30 days and rotates on every refresh. The response has an `earliest_refresh_at` field. | §4.5 |
| Errors and recovery | Codes and recovery for each. Errors before the stream opens can be `{"detail": …}`. Re-enable plan usage after a decline with `prompt=consent` (`force_reconsent` only once OpenAI confirms). OpenAI sends no notice when the user disconnects the app. Refresh-error codes. | §5.4, §4.5 |
| Preview limitations | `input` must be an array. Use `instructions`; a `role:system` input item is rejected. Unsupported fields: `temperature`, `max_output_tokens`, `metadata`, `prompt_cache_retention`, `truncation`, `user`, `previous_response_id`, … Function/custom tools allowed; hosted tools not. | §5.1 |

## 3. Decisions

**D1 — The backend owns credentials and refresh; the extension owns UI and the browser.**
Access tokens expire every hour and the backend is the only consumer. Several backends
can run at once (one per workspace) and must share one rotating refresh token, so
refresh needs a cross-process file lock. The docs model storage as a 0600 local file,
the same pattern `auth_token.py` already uses for our backend token. This also makes
the dev backend (`start-backend.sh`) work with no extension involved.

The alternative (tokens in the extension's SecretStorage, pushed to the backend) leaves
the dev backend without credentials and still needs a cross-window lock that
SecretStorage doesn't provide.

**D2 — One Responses transport, two credential sources.** `openai_transport.py`
becomes a streaming `OpenAIResponsesTransport` that takes a `BearerSource`: a static
API key, or a ChatGPT-plan registration. The plan route's request rules (`stream:true`,
`store:false`, array `input`, no `temperature`) are also valid on the API-key route, so
the request builder doesn't fork. A capability set only gates the fields the plan route
forbids.

**D3 — An OpenAI strict-schema codec at the transport, not in the engine.** The engine
keeps passing our schemas unchanged. The transport encodes them for OpenAI strict mode
and decodes the result back into the dict the engine already expects (§5.2).

**D4 — Plan-access failures are their own error class.** Today
`providers/availability.py` treats any 429 as `ProviderUnavailable`, so
`ControllerLoop` re-raises it and sub-agents/team members end `failed_transient` and
get re-queued after 30 s / 120 s. That's wrong for a plan usage limit, which needs the
user to act, so the requests would just pile up.

**D5 — No static default model for `chatgpt`.** Models come only from the account's
catalog, the way `openai_compatible` has none (`factory.default_model` raises). The
docs' example model name is no guide to what an account has.

**D6 — Phase 0 is a throwaway spike that gates everything else** (§7). The docs never
say whether strict `text.format: json_schema` works on this route, and our controller
depends on it.

## 4. Auth core (backend, new package `agentd/chatgpt_auth/`)

### 4.1 Host id
`~/.crucible/auth/chatgpt/host.json`, written once (atomic, 0600) before the first
authorize. Value: `urn:uuid:<uuid4>`. The JWK-thumbprint form is optional, and per
the docs OpenAI does not check possession of the key, so it adds a keypair for no
benefit. One host = one machine's `~/.crucible`, shared by every workspace's backend.

### 4.2 Authorization attempt (`oauth.py`)
- `start_attempt(registration_id | None)` creates fresh `state`, `nonce` and a PKCE
  verifier, and starts a **one-shot loopback server** on `127.0.0.1` (a free port; try
  1455 first). This is separate from the app socket, so the auth middleware and its
  Host check never see it.
- It builds the authorize URL:
  - first registration: `client_id=dynamic_agent_client`, `agent_name_hint=Crucible`;
  - returning account: the issued `client_id`, `id_token_hint`/`login_hint`, no
    `agent_name_hint`;
  - always: `ext_agent_host_id`, `resource`, the full scope string, `S256`.
- Callback: validate `state`. On `error=access_denied`, stop with no exchange. A new
  registration must return `client_id=oaiapp_…`, or it counts as incomplete. A
  returning account whose callback returns a *different* `client_id` is rejected.
- Exchange the code at the token endpoint with the same `redirect_uri` and `resource`,
  form-encoded, no secret. On `invalid_grant`, start a fresh attempt.
- The attempt expires after 10 minutes. The callback page tells the user to return to
  VS Code. The authorize URL (it can contain `id_token_hint`) is never logged.

### 4.3 ID token validation
PyJWT + `PyJWKClient` against `jwks_uri` from
`https://auth.openai.com/.well-known/openid-configuration` (discovery cached, keys
refetched on an unknown `kid`). Check the signature, `iss == discovery.issuer`,
`aud == issued client_id`, `exp`, `nonce`, with about 5 s of clock skew. For a
returning account, `sub` must match the stored subject. Add `pyjwt[crypto]` as a
**direct** dependency; it's only transitive today.

### 4.4 Credential store (`store.py`)
- One file per registration: `~/.crucible/auth/chatgpt/<registration_id>.json`, using
  the record shape from the docs (email, issuer, subject, client_id, host id, id /
  access / refresh tokens, scopes, expiry, saved_at).
- `registration_id` is our own opaque id, never the email, since two registrations can
  share an email.
- Written atomically, 0600, symlink-safe — reuse `auth_token.py`'s write and lock
  helpers rather than writing new ones.
- `plan_enabled = "chatgpt.tokens.use.direct" in scopes`. A sign-in without that scope
  is kept (identity is valid) but marked disabled (§6 re-enable).

### 4.5 Refresh (`refresh.py`)
- `async bearer(registration_id) -> str`:
  - if the access token has more than 5 minutes left, return it;
  - otherwise take an **exclusive flock on that registration's file** (in
    `to_thread`, never blocking the loop), **re-read** the file (another backend may
    have just refreshed), and refresh only if it's still stale;
  - honor `earliest_refresh_at`;
  - write the access token, refresh token, expiry and scopes together.
- A process-local `asyncio.Lock` per registration in front of the flock prevents
  in-process stampedes.
- Unusable-token codes (`invalid_grant`, `refresh_token_expired|invalidated|reused`, …)
  clear the tokens and mark the registration `needs_sign_in`. Network errors and 5xx
  keep the credentials (bounded backoff).
- On a 401 at inference: refresh once and retry once. A second 401 →
  `ChatGPTSessionInvalid`. OpenAI doesn't notify us of a disconnect, so this is the
  only place we find out.

### 4.6 Sign-out (`revoke.py`)
- Stop requests, then POST to the discovered `revocation_endpoint` with
  `token_type_hint=refresh_token` and the issued `client_id`. Retry network errors and
  5xx with backoff.
- Then clear the tokens but **keep** the registration (client_id, subject, email,
  label) and the host id.
- Return `{remote_revoked: bool}` so the UI can say "remote revocation not confirmed —
  disconnect Crucible in ChatGPT Settings".

## 5. Inference (backend)

### 5.1 `OpenAIResponsesTransport` (rewrite of `providers/openai_transport.py`)
Request, for both credential sources:
- `instructions=system_instructions`
- `input=[{"role":"user","content":json.dumps(user_payload)}]`
- `stream:true`, `store:false`
- `text.format` json_schema (via the codec)
- `reasoning:{effort}` when set
- No `temperature`. Today's `temperature=0` is itself suspect against the `gpt-5`
  default; the Phase 0 API-key check covers it.

The API-key source may also send `max_output_tokens`; the plan source never sends any
field on the preview's unsupported list (enforced by a request-body allowlist test).

Stream handling:
- Use the `openai` SDK with `stream=True` and `max_retries=0`; our own retry loop does
  the retrying.
- `response.output_text.delta` → append to a buffer.
- reasoning-summary deltas → `on_thinking`.
- `response.completed` → success, and usage → `on_usage`.
- `response.failed` → map `error.code` (§5.4).
- `response.incomplete` → a truncation error, reported like `finish_reason == "length"`
  is today.
- The stream ending with no terminal event → `TransientTransportError`.

Reuse rather than copy:
- `_within_deadline` / `StreamDeadlineExceeded` and the transient retry/backoff from
  `openai_compatible_transport.py`: move them to a shared `providers/streaming.py`.
- The probative-rejection rule from `reasoning_effort.py`: a 400
  `subscription_sharing_unsupported_capability` with `param` = reasoning marks only that
  rung unsupported.

### 5.2 Strict-schema codec (`providers/openai_strict_schema.py`, pure)
`encode(schema) -> (strict_schema, decoder)`. The decoder undoes the encoding on the
returned dict.

| Our schema | OpenAI strict needs | Encode | Decode |
|---|---|---|---|
| Root `anyOf`/`oneOf` (controller with `anyof=True`) | Root must be an object | Wrap: `{action: <union>}` | Unwrap `action` |
| `oneOf` (patch ops, tight variants) | Only `anyOf` (to confirm in Phase 0) | `oneOf` → `anyOf`. Branches are already disjoint by `op`/`type` const, so it means the same | — |
| Free-form `{"type":"object"}` — `tool_call.args`, stance `evidence` | Every object needs listed `properties` + `additionalProperties:false` | `{"type":"string","description":"JSON object, encoded"}` | `json.loads` at those paths. If the result isn't an object, raise the error the loop already turns into a correction |
| Optional properties | Every property required | Required + `null` added to the type | Drop `None` at those paths, so "absent" stays absent for `result.get(f)` checks |
| Objects without `additionalProperties:false` (Pydantic-derived, narrative, consolidator) | Must be false | Add it | — |

A test feeds **every schema the codebase passes to `generate_json`** (controller per
phase/variant/team_member, planning, patch, narrative, classifier, consolidator,
validate probe) through `encode` and checks it against the OpenAI strict rules: root
object, no free-form objects, all required, `additionalProperties:false`, no `oneOf`,
depth and size limits. It also round-trips sample outputs through the decoder.

This replaces the brief's "envelope only" idea. The envelope alone doesn't fix
`tool_call.args`, which strict mode can't express at all.

### 5.3 Factory and runtime
- New backend id **`chatgpt`** (not a mode of `openai`, so model lists, keys, env and
  validation stay one-to-one per backend).
- `_build_raw_transport("chatgpt")` reads `CRUCIBLE_CHATGPT_REGISTRATION` and builds
  `OpenAIResponsesTransport(bearer=ChatGPTPlanBearer(reg_id), plan_route=True)`.
- `PROVIDER_KEY_ENV` gets no entry: there is no key, and the access token never enters
  any environment.
- If the registration is missing or needs sign-in, use `UnconfiguredProviderTransport`
  with an actionable reason, so the backend still starts and Settings can repair it.
- `ProviderRuntime.swap` accepts `chatgpt_registration`. Known v1 limitation: the
  memory summarizer keeps its startup transport until restart, so swapping *to*
  `chatgpt` leaves the summarizer on the old provider until a restart.

### 5.4 Errors (`providers/plan_access.py`)
`ProviderAccessStopped(RuntimeError)` with subclasses:

| Signal | Class | Behavior |
|---|---|---|
| 429 `subscription_sharing_usage_limit_exceeded` | `PlanUsageLimitReached` | No retry. The turn ends with the usage-limit card. Teams **pause** (they don't re-queue). |
| 403 `…_user_not_eligible` | `PlanNotEligible` | No retry, no OAuth loop. Offer "use an API key instead". |
| 401 `…_invalid_user`, or 401 after refresh | `PlanSessionInvalid` | Mark `needs_sign_in`. Offer "Sign in again". |
| 400 `…_unsupported_capability` (not effort) | `PlanUnsupportedCapability(param)` | Show the param verbatim. No retry. |
| 403 `…_route_not_supported`, `chatpass_v2_*` | `PlanAccessMisconfigured` | A bug on our side; surface the request id. |
| 503 `…_usage_unavailable` / `…_user_unavailable` / admission 503 | `TransientTransportError` | The existing bounded retry, then `ProviderUnavailable`. |

- `is_provider_unavailable` returns **False** for `ProviderAccessStopped`; that is
  checked before its 429 rule.
- `ControllerLoop` re-raises `ProviderAccessStopped` immediately: it's not malformed
  output, so it must not use up correction attempts.
- `ChatController._run_loop` ends the turn and publishes `/live`
  `provider_access: {kind, message, request_id}` for the cards.
- Admission bodies shaped `{"detail": …}` are kept as diagnostic text.
- Every error keeps the status, code and request id.

### 5.5 Routes (all behind the existing bearer middleware)
| Route | Purpose |
|---|---|
| `POST /v1/auth/chatgpt/attempts {registration_id?, reconsent?}` | Start sign-in; returns `{attempt_id, authorize_url}`. `reconsent` adds `prompt=consent` (re-enable plan usage after a decline). |
| `GET /v1/auth/chatgpt/attempts/{id}` | `pending \| succeeded{registration_id, plan_enabled} \| failed{reason}`. Read-only. |
| `GET /v1/auth/chatgpt/registrations` | Label, email, `plan_enabled`, `needs_sign_in`. Never tokens. |
| `POST /v1/auth/chatgpt/registrations/{id}/sign-out` | `{remote_revoked}`. |
| `POST /v1/auth/chatgpt/registrations/{id}/models` | The account's `/v1/models`, filtered. POST because it may refresh (a credential write), which keeps `test_get_routes_read_only` honest. |

`/v1/config` gains `provider.billing: "api_key" | "chatgpt_plan"` and the active
registration's label.

## 6. Extension and webview

- **Setup wizard:** a provider card "Use your ChatGPT plan" with a **Continue with
  ChatGPT** button. This is the only new provider that can be configured before
  install finishes, because sign-in needs the backend: install → start backend → sign
  in → pick a model → validate.
- **Settings › Provider:**
  - ChatGPT account picker: saved accounts with labels, the active one marked, "Add
    another account", Sign out showing the revocation result.
  - Plan status: enabled / disabled-by-consent ("Enable ChatGPT plan usage" →
    reconsent) / needs sign-in.
  - "Use an API key instead" stays a separate billing path (UI/UX guidelines; errors
    page).
- **Browser:** `vscode.env.openExternal`. Smoke-test that the authorize query string
  survives `Uri.parse` without double-encoding.
- **Composer:**
  - When `billing == "chatgpt_plan"`: **"Using ChatGPT plan · Manage usage"** next to
    the model chip.
  - The model menu lists the account catalog (`display_name`), refreshed after an
    account switch. `buildModelOptions` gains a catalog source; it no longer offers a
    `defaultModel` for `chatgpt`.
- **First plan sign-in only:** a "You're using your ChatGPT plan" modal with a Got it
  button (a `globalState` flag per registration).
- **Cards from `/live.provider_access`:**
  - usage limit: Manage usage as the primary action, no "buy credits";
  - not eligible; sign in again; unsupported capability.

  `provider_access` must be in `lastLiveSignature` (the dedup invariant).
- **Persisted state:** `globalState` keeps `{backend: "chatgpt", registration_id,
  model}`. Spawn env: `CRUCIBLE_REASONING_BACKEND=chatgpt`,
  `CRUCIBLE_CHATGPT_REGISTRATION`, `CRUCIBLE_CHATGPT_MODEL`. Because this flag is
  default-off, it needs all three opt-in sites (`start-backend.sh`, `.env`,
  `buildBackendEnv`).
- **Contracts:** editor-client Zod schemas for registrations, attempts and
  `provider_access`, mirrored in webview `types.ts`.
- **Usage page:** we have no usage page, so the composer and Settings link "Manage
  usage" to ChatGPT Settings → Usage. **The exact deep-link URL is not in the docs.
  Find it in Phase 0; don't guess.**
- **Branding:** the UI/UX page says "approved OpenAI branding" for the button. Take the
  asset and its rules from that page before building the button.

## 7. Phases

Each phase gets its own TDD plan (`docs/superpowers/plans/…`) when it starts. Rough
sizes are new/changed lines.

**Phase 0 — spike (script in scratch, no product code; about half a day).**
Hand-run OAuth once with a Plus account, then answer, against the real route:
1. Is strict `text.format: json_schema` accepted? Does `anyOf` nested under an
   envelope work? Is `oneOf` rejected?
2. Do `reasoning.effort` and reasoning summaries work? Is `prompt_cache_key` accepted
   (it's not on the unsupported list)?
3. Does `response.completed` carry `usage`?
4. What fields does `/v1/models` return? Is there a context-window field? What are the
   real slugs?
5. The error body shapes for a bad field, and for a 401 after revocation.
6. The ChatGPT Settings → Usage URL.

Also, with an **API key**, run today's `openai` backend through one controller turn.
It is expected to fail on the root `anyOf`, and possibly on `temperature` with `gpt-5`.

**Exit:** a one-page findings note. If strict json_schema is rejected on the plan
route, stop and redesign §5.2 around schema-in-prompt (the `json_object` fallback
pattern) before continuing.

**Phase 1 — Responses transport + strict codec, API-key path only (about 600).**
Shippable alone: it fixes the existing `openai` backend. Includes the shared
`streaming.py` extraction, the all-schemas codec test, and a live API-key smoke test.

**Phase 2 — auth core (about 900).** Host id, OAuth attempts, ID-token validation,
the store, refresh with a cross-process lock, revocation. Tests run against a fake
authorization server (an in-process HTTP server with real JWKS signing). Required
tests:
- two processes racing a refresh rotate exactly once;
- a returning account whose callback has a different `client_id` is rejected;
- a missing direct scope gives `plan_enabled=false`;
- no token in logs (caplog scan).

**Phase 3 — `chatgpt` backend wiring (about 450).** Factory, routes, `/v1/config`,
`ProviderAccessStopped` through the controller loop, sub-agents and team pause, and
`/live.provider_access`.

**Phase 4 — extension and webview (about 900 TS).** Wizard, Settings account picker,
composer indicator and catalog model menu, welcome modal, access cards, contracts,
spawn env.

**Phase 5 — live smoke and docs.** A real Plus account in the dev host:
- sign in, run a chat turn, an edit, sub-agents and a short team run;
- switch accounts;
- sign out (revocation shown);
- re-enable after a decline;
- a forced usage-limit path, if one can be reached safely.

Then add a CLAUDE.md section.

## 8. Deferred and out of scope
- **Remote-SSH / VM hosts:** the loopback callback reaches the machine running the
  browser, not the remote backend. Later: "sign in locally, import the credential file"
  per the Self-hosted VMs page. Until then, Settings says sign-in needs a local
  workspace.
- Codex app-server, WebSocket continuation, and native Responses function tools (our
  tools stay prompt-described and run locally).
- A per-registration choice of keychain instead of a 0600 file. The docs accept the
  file, and it matches `auth_token.py`.

## 9. Risks
- **The preview can change.** Field rules and error codes are preview-documented.
  Keep the request allowlist and error map in one module each.
- **Plus five-hour limit vs. fan-out.** Sub-agents (up to 8 concurrent) and teams can
  use up a shared five-hour allowance quickly. The access-stop pause (D4) prevents
  retry storms; the Settings copy should say this.
- **No output cap on the plan route** (`max_output_tokens` is unsupported). Runaway
  output ends as `response.incomplete` or runs into the stream deadline; both already
  map to errors the loop corrects or stops on.
- **Context window unknown** unless `/v1/models` exposes it (Phase 0). Otherwise the
  existing user-declared Context window field applies.

## 10. Implementation notes (where the build differs from the plan above)

- The class name `OpenAIJsonTransport` was kept (no rename) to avoid churn; it is now the streaming Responses transport for both credential kinds.
- §4.5's ID-token check on refresh: a refreshed ID token is kept only if it verifies as the same subject; otherwise the previous one stays (it is only a sign-in hint).
- §5.4 adds `PlanUsageDisabled` (signed in without the direct scope) as its own kind, `plan_disabled`.
- §5.5's `/v1/config` field is `uses_chatgpt_plan: bool` rather than a `billing` enum.
- §6: the registration id is persisted in `globalState` (`crucible.chatgpt.registration`), not SecretStorage — it is an id, not a secret. ChatGPT is not in the API-key provider dropdowns; it has its own card in Settings and Setup.
- `main.py`'s chat-model fallback is now empty for `chatgpt` (it was a generic `gpt-4o`).

## 11. Phase 0 findings (live, 2026-10-06, ChatGPT Go account)

| Question | Answer |
|---|---|
| Eligibility | A **Go** account signed in with `chatgpt.tokens.use.direct` and completed inference, although the docs name Plus/Pro. |
| Token response | `access_token, earliest_refresh_at, expires_in (3600), id_token, refresh_token, scope, token_type`. `earliest_refresh_at` is an absolute Unix time. |
| ID token claims | `aud, auth_time, email, email_verified, exp, iat, iss, jti, name, nonce, sid, sub, at_hash, https://api.openai.com/auth`. |
| Discovery | Issuer `https://auth.openai.com`; revocation at `/api/accounts/oauth/revoke`. |
| `/v1/models` | `{"models": [...]}`; two listed models (`gpt-5.6-terra`, `gpt-5.6-luna`), each with `context_window` 272000, `max_context_window` 872000, `supported_reasoning_levels`, `default_reasoning_level`, plus Codex-specific fields (`prefer_websockets`, `use_responses_lite`, …). |
| Strict `json_schema` | Works. Root `anyOf` → 400 "schema must be … type object"; `oneOf` → 400 "not permitted"; free-form object → 400 "additionalProperties is required … false"; our raw controller schema → 400 (`const` without `type`). **The codec-encoded controller schema succeeds.** |
| Roles | `system` and `developer` input items were both accepted (the docs say `system` is rejected). We use `instructions`. |
| Unsupported fields | `temperature` → 400 `{"detail":"Unsupported parameter: temperature"}`; string `input` → `{"detail":"Input must be a list"}`; `store:true` / `stream:false` refused the same way. `max_output_tokens` was **accepted** (docs list it unsupported); we still omit it. |
| Reasoning | `none/low/medium/high/xhigh/max` accepted; `minimal` → 400 `unsupported_value`, `param: reasoning.effort`. Live through the transport: reasoning tokens 0→52 from off→max. |
| `prompt_cache_key`, `text.verbosity` | Accepted. |
| Unknown model | 400 `{"detail":"The 'x' model is not supported when using Codex with a ChatGPT account."}` → now `PlanUnsupportedCapability`. |
| Usage | `response.completed` carries `usage` (input/output/cached/reasoning tokens, plus an `attribution` breakdown). |
| Multiple messages | A response can contain a `commentary` message and then a `final_answer` message, both with the full answer; `response.completed.output` is empty. Fixed by per-item selection. |
| Request ids | No `x-request-id` header was observed on these responses. |

## 12. How output arrives, and native function calling (2026-10-07)

**Research.** A streamed response is a sequence of output items — reasoning (optional
summary), messages, function calls — each with added/delta/done events, closed by
`response.completed`. Messages carry `phase`: `commentary` for preambles and status
updates, `final_answer` for the message that ends the turn ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)).
Actions are meant to be `function_call` items; after them the model "stop[s] and wait[s]"
for `function_call_output` ([function calling](https://developers.openai.com/api/docs/guides/function-calling)).
`tool_choice: "required"` + `parallel_tool_calls: false` guarantees exactly one call.
Prompting can change how many preambles appear but no parameter does
([Codex prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide));
`max_tool_calls` is unsupported on this route. Other harnesses hit the same text-mode
failure ([camunda #9285](https://github.com/camunda/connectors/issues/9285)).

**Live findings (Plus account).** With actions as schema-constrained text, `gpt-5.6-terra`
put several actions in one response (commentary messages = invented steps, final message =
fabricated results); `gpt-6-astra` and `gpt-5.6-sol` did not. Function tools alone gave one
call per response but `terra` still looped (3/3) while history was role-labelled JSON in
one user message; with history as native items it progressed (3/3 `create_team`), even
without reasoning-item replay.

**Cache.** Exact-prefix caching; measured `cached_tokens` over 4–6-step loops: native with
reasoning replay, native without, and the old JSON payload all cache the same ~8.8k-token
instructions+tools prefix; misses were scattered across formats (per-machine routing).
Reasoning replay matters for quality, not caching — a later step if long runs need it
(it needs reasoning items stored with the controller's history, outside the provider).

**Design.** `providers/openai_native.py`: actions → function tools (union → namespace
`crucible`, one function per action type; flat → one forced function), history →
`function_call` / `function_call_output` / messages, payload fields before the history
open the input and later ones close it. Lossless by construction and by test
(`from_native_input` round trip, synthetic shapes + real payloads).
