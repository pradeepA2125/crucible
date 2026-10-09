# Native keyless web search

## Status

The user approved the research direction: native web search with no user API keys, sign-in, MCP configuration, or installed search process. This specification records that direction and proposes concrete v1 limits for review before implementation planning. Production distribution remains subject to the release gates below.

## Goal and scope

Expose `web_search(query, max_results=5)` as a harness-owned tool, independent of the selected language-model provider. Use Exa's documented keyless hosted MCP service internally. Native means ownership of the tool interface, permissions, limits and errors; it does not require ownership of a search index or avoidance of MCP on the wire.

V1 covers search only. URL fetching remains a distinct capability; implementing native `web_fetch` is a separate follow-up. Existing user-configured MCP tools remain available and are not removed or rewritten.

Non-goals: browser automation, search-page scraping, public SearXNG instance rotation, multiple default search providers, paid-key settings, persistent result caching, and a Crucible-hosted proxy.

## Research and alternatives

- OpenCode exposes native `websearch` and calls Exa or Parallel through built-in HTTPS JSON-RPC requests. Provider selection is not automatic failover.
- Kilo's current dispatcher supports hosted Exa/Parallel and an authenticated Kilo Exa proxy, including an unauthenticated Exa path.
- Zed's inspected `search_web` tool supports Zed Cloud and uses an authenticated cloud `/web_search` request. Its upstream search engine was not established.
- Cursor documents first-party web search and network access to search providers. Its upstream provider and transport were not established.
- Claude Code documents a native `WebSearch` backed by Anthropic, separate from URL fetching. Its backend is not configurable.

Alternatives considered: direct HTML search scraping has fragile parsing and bot-challenge behavior; an owned proxy removes user setup but introduces operating costs, authentication and abuse handling. Exa keyless is the smallest demonstrated starting point, with explicit rate-limit and privacy caveats.

Sources inspected:

- https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/websearch.ts
- https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/mcp-websearch.ts
- https://github.com/Kilo-Org/kilocode/blob/main/packages/opencode/src/tool/websearch.ts
- https://github.com/zed-industries/zed/blob/main/crates/agent/src/tools/web_search_tool.rs
- https://github.com/zed-industries/zed/blob/main/crates/web_search_providers/src/cloud.rs
- https://cursor.com/docs/agent/security
- https://code.claude.com/docs/en/tools-reference#websearch-tool-behavior
- https://exa.ai/docs/reference/exa-mcp
- https://exa.ai/terms
- https://exa.ai/privacy-policy

Upstream branches and documentation are mutable; these are research observations, not guarantees about historical versions.

## Architecture and flow

Implement a focused web tool source behind the existing ToolSource contract, with a separate Exa adapter responsible for transport and protocol decoding. Keep provider-specific arguments and formats out of the model-facing interface. Locate concrete main-agent and subagent registration sites during implementation planning; apply existing allowed-tool filtering and permission policies rather than bypassing them.

Flow: validate arguments → obtain permission → send bounded HTTPS request → decode provider response → classify success/error → format bounded, attributed external content → return normal tool output.

The adapter calls the fixed endpoint `https://mcp.exa.ai/mcp`, using JSON-RPC `tools/call` with tool name `web_search_exa`. Accept JSON and SSE responses. Direct calls without preceding initialization worked in smoke tests; do not describe this as general MCP protocol behavior or rely on it for unrelated servers.

Use a replaceable internal adapter boundary, but implement only Exa in v1. Do not allow the model to select arbitrary endpoints or submit raw provider arguments.

## Proposed tool contract and resource limits

- `query`: required non-whitespace string, at most 2,000 characters; trim surrounding whitespace only.
- `max_results`: integer from 1 through 10, default 5; reject invalid values and booleans rather than silently coercing them.
- Provider search mode: `auto`; no model-facing crawl or search-mode knobs in v1.
- Total request deadline: 25 seconds, covering body reading as well as connection/request work.
- Maximum received response: 1 MiB, enforced while streaming. Abort oversized responses rather than buffering without bounds.
- Maximum model-facing output: 12,000 characters including wrapper and truncation notice, enforced locally.
- No automatic retries or fallback providers in v1; surface actionable errors instead of multiplying keyless traffic.

Pass the requested result count to Exa. Provider text may not be structured, so do not promise an exact locally enforced number of entries. Preserve provider titles, URLs and highlights where present; do not fabricate metadata or imply that result pages were independently fetched. Prefer truncation at complete result boundaries when recognizable, otherwise truncate with an explicit notice. A malformed or empty successful envelope is a protocol error unless the provider explicitly represents no results.

## Permissions, privacy and security

Make the native tool discoverable without setup. Network execution requires permission under the harness's existing approval system. The approval must identify Exa and show the query, and support the existing remembered-approval behavior. Provide a persistent disable control that prevents execution and excludes the tool from advertised definitions. Reuse the current permission/configuration conventions; the implementation plan must establish their exact integration points.

Send only the query and necessary search controls. Do not attach workspace paths, files, conversation history, session identifiers, model names, or credentials. An agent-authored query can itself contain sensitive information; instruct the agent not to include secrets, personal information or confidential code and let the user review the query. Do not claim this is a perfect data-loss-prevention boundary.

Exa's public privacy policy says query data can be used to improve its technology, including model training and fine-tuning. Do not claim anonymous keyless requests have zero retention or enterprise contractual protections. Document this beside the feature and in the approval disclosure.

Mark returned material as untrusted external content: it is evidence, not instructions or authorization. Require source links when the agent uses results in an answer. Use normal escaped text rendering, never execute provider content or render it as trusted HTML. Use verified TLS, a fixed endpoint, and no cross-origin redirects.

## Errors and cancellation

Distinguish invalid local arguments, denied permission, cancellation, timeout, transport failure, HTTP error, JSON-RPC error, MCP `result.isError`, malformed envelope and oversized response. All failures produce error tool outputs, not successful search text or 'no results'. Check application-level errors even when HTTP status is 200. Rate-limit responses explain that the free service is limited and that the user can try later; do not suggest credential creation as a prerequisite for this feature.

Cancellation must stop active network work and release resources. Provider outage must not prevent unrelated harness tools from operating.

## Evaluation evidence

On 2026-10-09, direct keyless HTTPS smoke requests succeeded for 'Python asyncio TaskGroup official documentation', returning official Python documentation and CPython source. Responses used HTTP 200 with SSE-wrapped JSON-RPC results. A request with `contextMaxCharacters: 1000` returned substantially more text, demonstrating why local limits are mandatory. A missing-query request returned HTTP 200 with `result.isError: true` and an input-validation error.

These were narrow smoke tests, not a general quality benchmark. No deliberate quota-exhaustion test was performed, and the exact anonymous quota was not established.

## Verification requirements

Use mocked network tests for JSON/SSE decoding, chunk boundaries, multiple text content items, JSON-RPC errors, MCP errors under HTTP 200, empty/malformed envelopes, transport failures, 429/5xx responses, deadlines, cancellation and streaming byte limits. Test argument validation and local output truncation separately.

Integration tests must prove tool discovery, allowed-tool filtering, permission denial preventing all network calls, remembered approval, disable behavior, and absence of unrelated context/credentials in outbound requests. Confirm search works with different model providers because transport belongs to the harness, not an LLM provider.

A manual opt-in live smoke test may verify the production endpoint without consuming public quota during ordinary CI. Do not require a search API key to run that smoke test. Never intentionally exhaust provider limits.

## Production release gates

Prototype implementation may proceed after specification review. Public production-default distribution must wait for:

1. Confirmation of permissible third-party product integration under Exa's documentation/terms; existing competitors' code is not legal permission.
2. Confirmation of applicable keyless limits and acceptable operational expectations; do not advertise unlimited or guaranteed service.
3. Reviewed user-facing privacy disclosure and network permission behavior.

Exa's terms include resale/sublicensing and other restrictions. This research is not a legal conclusion that our distribution is permitted or prohibited. Obtain clarification before making a production commitment. Do not silently ship the default broadly while these gates remain unresolved.
