# Claude subscription in Crucible (experimental)

Crucible can connect a Claude subscription and make direct Anthropic Messages requests
while retaining its own prompts, tool execution, controller, subagents and teams.
There is no Claude Code child process or embedded provider agent in this integration.

## Use it locally

Build the editor client, extension and webview from this checkout. Run the extension
with the local backend source (`crucible.devSourcePath` pointing to this checkout), or
attach to a backend running this source. A previously installed release needs the new
extension and backend code; updating only the panel does not add these routes.

In **Crucible Settings → Provider → Claude subscription**, choose **Continue with Claude**.
Complete consent in the system browser. After returning, select the connection,
choose a model from Claude’s catalog, then choose **Use this connection**. Saving
validates one request and switches the active provider without a restart.
The setup wizard also offers **Continue with Claude** before an API key is configured.

**Manage usage** opens Claude's [Usage settings](https://claude.ai/settings/usage).
**Manage plan** opens Claude's [Billing settings](https://claude.ai/settings/billing).
**Disconnect** removes this computer's saved tokens. It does not cancel the subscription
or claim to revoke the server-side grant. Reconnecting creates a new local connection.

Agents share the account's allowance. Crucible never falls back automatically to an
Anthropic API key. Account-level usage credits enabled in Claude may still apply:
see [Claude's usage credit explanation](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans).
The model picker uses Anthropic's [Models API](https://platform.claude.com/docs/en/api/models/list).
The selected model's `max_input_tokens` sets Crucible's context window automatically,
including compaction, the context meter and composer model changes. No manual window
entry or fixed fallback is used for this connection. Models without usable limits are
disabled; retired entries are omitted. Catalog visibility does not guarantee subscription
access: saving still validates one inference request. Remaining subscription balance
is not discovered; Manage usage opens Claude's own settings. The composer shows
Claude's mark and **Using Claude plan · Manage usage** while this backend is active.

Catalog metadata stays in a five-minute, account-scoped backend cache. Requests handle
pagination; **Refresh models** bypasses the cache. Requests verify the connection is still signed in before using or publishing
cached results. The panel ignores late results from a previously selected account.
The cream sign-in button includes Claude's local SVG mark; it is an app-designed control,
not a claim that Anthropic approved a third-party sign-in button.

## Provider-owned effort controls

The composer renders an `EffortControlBuilder` descriptor from the active transport.
The descriptor carries native choices, labels, descriptions, an optional catalog default,
and whether capability details came from a catalog, a transport declaration or a fallback.
No provider-specific option list lives in the composer. Model/provider changes refresh the
control alongside the model list. A model with no effort capability disables the control.

ChatGPT retains `supported_reasoning_levels` and `default_reasoning_level` from the
signed-in model catalog, including native values such as `none`, `minimal` and `xhigh`
when advertised. These fields follow the [OpenAI catalog schema](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs).
Selections reach the Responses request as `reasoning.effort` without collapsing extra
levels into the old five-level ladder. Missing catalog fields use an explicitly unverified
fallback. Existing API adapters expose their own declared choices, constrained to their
wire mappings; adapters without an effort setter disable the control.

Claude retains the Models API's `capabilities.effort` booleans and shows only advertised
levels. Sonnet 5.5 currently offers Low, Medium, High, Extra high and Max; older models can
expose fewer options or none. Effort is sent as `output_config.effort`, following
[Claude's effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort).
There is no Off effort option: thinking mode is a separate parameter. If capability details
are missing, choices remain unverified and the adapter still excludes Off.

**Default** explicitly clears the override and omits the effort parameter. The app does
not invent a default level when a catalog does not provide one. A model switch resolves
an unavailable previous selection against the new model's choices and displays a note.
Validation sends the effective effort before replacing the running provider; failures
leave the existing provider intact. Top-level Claude effort changes can restart prompt
caching, as described in the effort documentation.

A live probe on 2026-10-13 verified the full catalog choice list for Sonnet 5.5, then sent
`output_config.effort: "xhigh"` through the Crucible adapter and received the expected
reply. It reused the explicitly authorized existing test token in memory; it did not
exercise a fresh browser grant or refresh-token rotation.

## Authentication and storage

Browser PKCE uses the public OAuth client/profile used by Pi, with a loopback callback
at `http://localhost:53692/callback` (an ephemeral port if occupied). Each attempt has a
fresh verifier and state, a ten-minute deadline, cancellation and a one-shot exchange.
The extension only receives public status and a connection id. Neither access nor
refresh tokens enter webview messages, provider configuration or environment variables.

Credentials use the backend's existing private-file primitives: atomic owner-only
files and cross-process locks, under `~/.crucible/auth/claude`, separate from ChatGPT,
Pi and Claude Code. This is a private disk store, **not macOS Keychain encryption**.
An optional `CRUCIBLE_CLAUDE_AUTH_DIR` selects another private directory.
Production login never reads or imports Claude Code's Keychain credentials.

Refresh reads and rotates credentials under an account lock. Concurrent agents and
backend processes reread the latest record after acquiring that lock. Cancellation
cannot discard a completed refresh; disconnect is serialized with refresh. Invalid
grants clear tokens; temporary token-server failures retain them for a later attempt.

Backend configuration uses `CRUCIBLE_REASONING_BACKEND=claude_subscription`,
`CRUCIBLE_CLAUDE_REGISTRATION=<saved connection id>` and `CRUCIBLE_CLAUDE_MODEL`.
Optional settings: `CRUCIBLE_CLAUDE_MAX_TOKENS` (16,384) and
`CRUCIBLE_CLAUDE_TIMEOUT_SEC` (120). There is no configurable credential-bearing
upstream URL: subscription requests go to Anthropic only.

## Model adapter and caching

The adapter sends Crucible's action schema as one native `crucible_step` function.
Crucible validates its input and executes the contained host tools itself. Past actions
and matched results become native `tool_use` / `tool_result` blocks. Batch envelopes,
member identifiers, corrections and image origins use the existing history converter.

Requests contain a short compatibility identity block:
`You are Claude Code, Anthropic's official CLI for Claude.`
Crucible's instructions follow in their own system block. This is the Pi request
profile observed to work locally; it is not the full Claude Code system prompt.
The adapter uses Pi's OAuth bearer headers and `tool_choice: auto`; the tested Sonnet
model rejected forced tool selection. Streamed function arguments are assembled,
validated, and rejected if truncated, incomplete, schema-invalid or multiple actions.

Each usage owner has a separate, bounded history cache keyed by its prompt and schema.
Successful requests preserve the preceding request's input prefix when history grows;
compaction or a changed prefix resets that stream. Interleaved agents do not share
conversation history. Four explicit ephemeral cache breakpoints cover tools, the two
system blocks and the last input block. Cache reads and writes are included in reported
input-token totals. Cache availability and savings remain provider-controlled.

A 429 ends the turn with a Claude **rate or usage limit** message; it is not treated as
proof that the subscription quota alone is exhausted. Authentication refusal directs
the user to reconnect. These errors use the existing account-stop path, so controller
correction retries cannot keep spending on an unchanged account failure.

## Support boundary

This is an experimental Pi-style integration, not a verified Crucible OAuth partnership
or generally available third-party subscription API. Pi's existing public client id
and Claude Code compatibility headers are hardcoded. Upstream policy or profile changes
can break it. Do not present a successful request as proof of official authorization.

References:

- [Pinned Pi OAuth implementation](https://github.com/earendil-works/pi/blob/7d75a0e3eb273dff753d49c066ea29fbe7b3b6f6/packages/ai/src/auth/oauth/anthropic.ts)
- [Pinned Pi Messages adapter](https://github.com/earendil-works/pi/blob/7d75a0e3eb273dff753d49c066ea29fbe7b3b6f6/packages/ai/src/api/anthropic-messages.ts)
- [Claude Code credential rules](https://code.claude.com/docs/en/legal-and-compliance)
- [Claude plan / Agent SDK billing note](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)

## Other approaches investigated

| Approach | Local result | Tradeoff |
| --- | --- | --- |
| Unmodified Claude Code print CLI with `--system-prompt`, tools disabled | Real Crucible tool round trip passed | CLI process per call; no cache reads in the short-lived probe |
| Persistent Claude Code stream-json session per agent | Isolated prompts/history and cache reuse passed | Manage a CLI process and transcript for each agent |
| Pi direct Messages adapter | Real Crucible tool round trip and cache reuse passed; selected here | Experimental OAuth client/profile compatibility |
| CLIProxyAPI local translation service | Real Crucible tool round trip and cache reuse passed | Additional proxy process and more request-profile adaptation |
| Stock claude-max-api-proxy converter | Local conversion test failed required history/tool preservation | Flattened system instructions and omitted native tools/results |
| Plain OAuth request with custom system only | 429; restoring Pi identity block passed controlled comparison | Not a working substitute in the tested account/profile |

The CLI methods remain documented alternatives. They are not implemented as an automatic
fallback. [Claude CLI reference](https://code.claude.com/docs/en/cli-reference),
[pinned CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI/tree/bf9341629ef2300ac9d846a2e80fe2756f63a671),
[pinned wrapper converter](https://github.com/wende/claude-max-api-proxy/blob/3e88f2fb98835385bd8d3dcc25ed54e09389a095/src/adapter/openai-to-cli.ts).

## Validation, 2026-10-13

The new Python adapter passed two live Sonnet 5.5 calls using an existing, explicitly
authorized test token held in memory. Crucible's real call validator and `read_file`
executed against a temporary random fixture; the second model action returned the exact
fixture value. There was no Claude Code process. Cached input across these calls was
1,552 tokens. A separate live catalog probe returned 14 active models and verified
Sonnet 5.5’s 1,000,000-token input limit using the same authorized token; it made no
inference request. [Sanitized measurements](claude-subscription-live-result.json).

Automated tests cover browser callback/state/PKCE with a simulated token server,
secret-free route responses, cancellation, timeout, shared refresh rotation, invalid
and temporary grants, cancelled file locks, native batches/images, malformed/truncated
streams, account stops, provider construction and image admission. Client/host/webview
checks cover connection controls, switching, cancellation races, browser URL checks,
plan/usage links, setup readiness, catalog pagination/cache invalidation, automatic
window updates and late account responses. Existing ChatGPT/provider tests, TypeScript checks
and production bundles pass. The panel was inspected at 700px and 430px widths.

The installed extension was then tested through the managed runtime after running
`scripts/dev/install-local.sh`. A fresh Claude browser login and user-approved consent
completed successfully; the app displayed the connected account, loaded Sonnet 5.5's
catalog limits, and validated the provider. The composer offered Default, Low, Medium,
High, Extra high and Max. Selecting Low validated successfully; a new chat returned the
exact requested `CLAUDE_UI_OK` response. The composer displayed the Claude plan indicator,
and context changed from ready to a measured value after the request.
[Sanitized UI measurements](claude-subscription-ui-result.json). The managed runtime
remained enabled; no Claude Code CLI or imported test token was used for this UI flow.

**Not yet tested:** real refresh-token rotation through this new app login. Extra-usage
charges and account balances were not measured.
