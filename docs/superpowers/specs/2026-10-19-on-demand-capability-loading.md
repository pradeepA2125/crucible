# On-demand capability schemas and guidance

Date: 2026-10-19. Status: draft specification; implementation and rollout are not authorized by this document alone. This specifies workstream 2 after completing on-demand code discovery. No runtime changes accompany this document.

## Purpose

Reduce the first model request by supplying core tools and a compact hierarchical directory of optional capabilities. Let the model request relevant tools within any namespace when its task needs them. Return selected authorized schemas and their required guidance together in that same discovery result, then make those tools callable on the following model step. Do not require a list-tools call followed by a separate schema-fetch call.

The main work is reorganizing prompt assembly and letting the model select what it needs. It also requires a small harness-owned visibility state machine, dynamic validation, and recovery after restart or compaction. Merely shortening the prompt or hiding descriptions while sending every schema would not fulfill the goal.

Use the existing generic `tool_calls` action and tool-result flow. Do not depend on a provider supporting deferred native schemas or native namespaces, and do not introduce a classifier, embedding selector, reranker, or separate model call to choose capabilities.

Browser tools in this specification are supplied by a configured MCP server, not native tools supplied by the model provider. Their names, descriptions and argument schemas enter Crucible through its MCP manager and `McpToolSource`. Loading changes when Crucible shows that metadata to the model. Invocation remains `model → Crucible tool_calls → MCP approval/dispatch → configured browser MCP server`.

Distinguish these browser MCP tools from Crucible's built-in `web_search`/`web_fetch`, and from provider-native functions that encode controller actions. References below to provider-native schemas concern the controller/transport contract, not the browser tool catalog. Since the browser catalog is prompt/tool-result content, Crucible controls its schema-once presentation without needing provider-native browser-tool deferral.

## Scope and decisions

- Pilot with the configured browser MCP capability. Defer its schemas and its dedicated guidance together.
- Preserve the guidance's meaning and wording during the first implementation. Separate relocation from later editorial shortening.
- Use schema-once rows for model-facing tool catalogs, subject to format validation and measured savings. Keep canonical registry definitions and provider-required schemas unchanged.
- Keep the existing code discovery/read/edit flow available from the first request, including `search_index`. Do not put the capability loader in front of ordinary code work.
- Keep core action contracts, permissions, approvals, project instructions, skills requirements, memory behavior, and research/citation obligations available as today.
- Loading controls model visibility; it grants no new permission, creates no connection, installs nothing, and executes no browser operation.
- Keep the feature opt-in until matched evaluations pass. The full existing catalog remains the rollback mode.
- Add per-server `enabled_tools` / `disabled_tools` filters (see "Per-server tool filters"). Unlike deferral, they are always on, independent of the opt-in flag, and also apply to the full-catalog rollback.

Later candidates are execution sessions, optional MCP integrations, team creation and agent dispatch. Each needs its own audited split between optional usage teaching and essential ongoing-work rules. Existing active-team/member duties must not disappear because the creation capability was deferred.

This work does not change Rust retrieval/ranking, the on-demand candidate format, memory selection, source freshness rules, provider authentication, or the task planner's separate retrieval path.

## Current implementation evidence

These are code observations, not proposals:

| Current seam | Behavior and implication |
| --- | --- |
| `agentd/tools/sources.py`: `ToolSource`, `AggregatingToolRegistry` | Sources supply definitions and execution. The registry merges definitions and applies agent allowlists; `prepare` retains execution guards. Capability loading can wrap this seam without replacing tool implementations. |
| `agentd/chat/controller_prompts.py`: `format_controller_system_prompt` | Serializes the supplied definitions into the system prompt and appends named MCP, session, dispatch and team teaching blocks. It already owns the information to package schemas with guidance. |
| `agentd/chat/controller_loop.py`: `ControllerLoop.run`, `_iterate` | Captures tool definitions at run start and validates batches against registry definitions. Visibility changes require fresh per-request snapshots rather than this fixed list. |
| `agentd/reasoning/engine.py`: `create_controller_step` | Builds system instructions and step payload from definitions and render context. It needs distinct core-catalog and callable-tool inputs so loading does not grow the stable system prefix. |
| `agentd/providers/openai_native.py`: `native_actions` | Wraps controller actions, including generic `tool_calls`, in provider functions/namespaces where supported. These are action schemas, not one provider-native function per application tool. Existing namespace usage does not implement capability deferral. |
| `agentd/mcp/tool_source.py`: `McpToolSource.definitions` | Applies an order-based serialized-definition budget before returning tools. A deferred catalog cannot discover tools already dropped here; an explicit authorized inventory is needed below this prompt budget. |
| `agentd/prompting/tagged.py`: `RenderContext` | Renders audience/permission/tool-dependent guidance. Loaded bundles must use the current agent context, not a main-agent rendering shared with children. |
| `agentd/prompting/web_research.py` | Derives instructions for Crucible's built-in web tools from tool availability. Deferral must not misleadingly describe an available-but-unloaded capability as unavailable. General research obligations remain core. |

All paths above are relative to `services/agentd-py/`. Relevant persistence seams also exist in `chat/storage.py`, `chat/rewind.py`, the controller's durable history, and memory history projection. Existing skill loading is a useful implementation reference; its storage and trust rules must not be reused without checking their fit.

## Provider portability is a baseline requirement

The design applies across the transports selected by `providers/factory.py`, not only ChatGPT plan or the OpenAI adapter. The common boundary is `reasoning/engine.py` calling `ModelJsonTransport.generate_json` with system instructions, a user payload/history and the existing controller response schema. Namespace metadata, selected-tool lookup, bundle rendering, visibility state and execution authorization belong above that boundary.

The same logical exchange is used everywhere: the model emits an ordinary controller `tool_calls` action for `load_capabilities`; Crucible appends its result using existing history serialization; the next controller action invokes an allowed returned tool. The transport can use its existing JSON response, grammar, or native action-function encoding. It does not need to implement native dynamic application-tool registration. Catalog rows are input documentation; model output remains ordinary named JSON arguments validated against canonical schemas.

Current transport paths to cover, as observed in repository code:

| Family | Existing route to preserve | Required check |
| --- | --- | --- |
| OpenAI API and ChatGPT plan | `openai_transport.py` and native controller-action codecs | The loader bundle survives action/function history conversion; no dependency on native namespace registration for MCP children. |
| Anthropic API | `anthropic_transport.py` supplies instructions/controller schema and parses JSON responses | Loader and operational calls work through the existing JSON action contract; no assumption of OpenAI function fields or roles. |
| Claude subscription | `claude_subscription.py` wraps controller output in its action tool and translates history | Preserve its action-tool contract, approvals and cache framing; tool implementation remains Crucible/MCP, not a provider-side agent loop. |
| Gemini | `gemini_transport.py` supplies system instructions, contents and a response JSON schema | Preserve contents ordering and existing schema/thinking handling; loader rows must remain normal input content. |
| OpenAI-compatible endpoints and OpenRouter | Their current structured-output, JSON-mode and prompt-only fallback paths | The feature works without native tool calling or strict-schema support; existing fallback/error behavior remains intact. |
| Ollama and TurboQuant/local backends | Existing local JSON/grammar/controller paths | Reuse the existing response grammar; test whether the selected local model understands row catalogs and the loader workflow. |
| Groq, Hugging Face and Watsonx | Their current `generate_json` adapters | Equivalent logical visibility, result replay and argument validation after provider serialization. |

Run contract tests through every adapter, using controlled clients/responses so credentials are not required for baseline coverage. Inspect each serialized request for catalog leakage and loader-result presence, and validate the decoded actions through the real registry. Add representative live/model tests across available transport families; unavailable credentials or models must be recorded as untested rather than reported as compatible.

A portable architecture is not proof that every model reliably selects tools or understands schema-once rows. Keep ordinary minified catalog JSON as a rendering fallback, independently of capability deferral. If a model fails the loader workflow itself, use the explicit full-catalog rollback for that model/configuration until fixed. Do not silently enable an unverified mode globally because it passed on ChatGPT. No alternate permission or execution policy is introduced by these presentation fallbacks.

Token savings are also model-specific: the recorded 2.31% row-format result uses `o200k_base` on one archived catalog. It does not establish the same compression, cache savings or cost reduction for Claude, Gemini or local models. Prefer each provider's reported usage or available tokenizer, keep estimates labeled, and report model, adapter, serialization and cache conditions with each comparison.

## Capability contract

A harness-owned capability definition contains:

| Field | Meaning |
| --- | --- |
| `id` | Stable, explicit identifier, such as `mcp.playwright` for an illustrative configured browser server. |
| `description` | Short purpose and when-to-use description visible before loading. |
| `tool_membership` | Explicit names or registered source membership; resolved against the authorized inventory. Namespace membership is configured, not inferred from a user query. |
| `guidance_blocks` | Existing trusted prompt blocks belonging to this capability. |
| `dependencies` | Explicit shared guidance or capability dependencies, resolved in deterministic order without cycles. |
| `version` / `content_hash` | Identity of the rendered schemas, guidance, dependency versions and relevant render/policy context. |

The definition is application configuration/code, not instructions supplied by a web page or an MCP result. External tool descriptions remain external metadata even when packaged by the harness. Packaging must not elevate their trust.

Each authorized tool belongs to core or one explicit optional leaf group; ancestors summarize their descendants. Reject ambiguous ownership and duplicate names. Unclassified tools stay in core during the pilot; never hide them accidentally. A dependency must not load an unrelated integration or widen the permitted tool set. Track guidance requirements per tool and shared ancestor block so selecting a subset does not lose essential teaching.

For the browser pilot, use the configured browser source/server's explicit membership. Do not classify every `mcp__` tool as browser, and do not hardcode the previously observed count of 21 tools.

### Per-server tool filters

Added 2026-10-19. Today a configured server is all or nothing: every tool of an enabled server reaches
the model, up to `CRUCIBLE_MCP_TOOLS_MAX_CHARS`. Codex's `config.toml` offers per-server
`enabled_tools` and `disabled_tools` lists; Crucible adopts the same two keys so a user can cut a
large server down to the tools they actually use.

```json
{"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"],
  "enabled": true,
  "enabled_tools": ["browser_navigate", "browser_snapshot", "browser_click", "browser_type"],
  "disabled_tools": []}}}
```

- **MCP only.** Filters apply to connector servers, today MCP. Built-in tools keep their
  existing feature switches (`CRUCIBLE_WEB_SEARCH_ENABLED`, `CRUCIBLE_EXEC_SESSIONS_ENABLED` and so
  on); no per-tool filter is planned for them (decided 2026-10-19).
- **Names** are the server's own tool names, unprefixed (`browser_click`, not
  `mcp__playwright__browser_click`).
- **Semantics:** absent `enabled_tools` means every tool; a list means only those; `disabled_tools`
  then removes names. An empty `enabled_tools` list exposes no tools, and the server stays connected.
- **Unknown names** in either list log one warning naming them (servers rename tools between
  versions) and are otherwise ignored. A malformed value (not a list of strings) skips that entry
  with a warning, like any other malformed `mcp.json` entry; it never fails startup.
- **Where they apply:** when the connection manager builds the server's inventory, before the prompt
  budget, the capability directory and the loader. A filtered tool is not authorized: it is not
  shown, not loadable and not callable, and a call naming it is refused at dispatch like any unknown
  tool. A server whose tools are all filtered out has no directory entry.
- **Narrowing only:** sub-agent definition `tools:` patterns and permission modes intersect with the
  filtered inventory and can never restore a filtered tool. Remembered approvals in
  `.crucible/approved-mcp-tools.json` for a filtered tool stay on disk but cannot be used.
- **Changes** take effect on reconnect (`McpConnectionManager.reconcile`/`reconnect`), with no
  backend restart. A filter change is a policy change: it invalidates loaded bundle versions for that
  server, and the callable set drops filtered tools on the next request.
- **Scope:** the lists live in the shared `.crucible/mcp.json`, like `enabled`. The existing
  user-local server toggle (extension `globalState`) stays separate. A user-local per-tool override
  is not part of this workstream.
- **Settings UI:** the MCP servers section lists each connected server's tools with a checkbox per
  tool, writing `enabled_tools`/`disabled_tools` through the existing MCP admin routes (`upsert_server`
  already preserves unknown keys) and then reconnecting.

### MCP namespace and nested tool families

Use `mcp` as the top-level discovery namespace. Its children correspond to configured, permitted MCP servers: `mcp.<server_id>`. Below each server, explicitly configured tool families may nest further. Five or six levels are supported when the inventory benefits from them; do not force every integration into the same depth or create single-child layers without a useful distinction. Use the configured stable server identity; the browser examples below assume a server identified as `playwright`.

```text
core
  existing code/read/edit tools + load_capabilities
mcp
  playwright: browser inspection and interaction
    pages: locate, inspect and interact with pages
      inspection: inspect page content and state
      interaction: enter text and use controls
        forms: work with form fields
  <other_permitted_server>: its short purpose, when configured
```

This tree is illustrative metadata organization, not a claim about groups already present in that MCP server. Each node has a compact purpose describing the work its descendants support. The top-level MCP description summarizes the configured server families and when they help; server descriptions summarize their tool families. Do not supply every nested description in the initial request.

**Hierarchy is organization, not a required sequence of model calls.** A query at `mcp` searches authorized descendant tools across configured servers; a query at `mcp.playwright` searches within that server; a deeper namespace narrows the same lookup. Every query returns selected full schemas and required guidance directly. The model may start at the broadest useful level, skip levels it already knows, or optionally inspect a directory when the available families are unclear. Never require five or six traversal calls to reach a tool.

The initial compact directory shows MCP's broad purpose and short summaries of its configured, permitted server children. Deeper descriptions are returned only for an explicit directory request or as brief orientation with search results. If a directory is bounded, disclose omitted children and provide bounded continuation; omitted metadata must not exclude their tools from a query over descendants.

A namespace-only request returns its immediate permitted child directory and loads no schemas. A namespace-plus-query request selects a bounded subset; it never imports every descendant just because 50 tools share that namespace. Exact known tool names can request schemas without searching. Cache a metadata lookup over authorized tool names, descriptions and configured family summaries, invalidated on source/schema/policy changes. The initial implementation uses local deterministic lexical retrieval; no separate LLM, embedding selector or reranker is required. Metadata matching can miss tools, so expose a refinement path and evaluate recall rather than promising the first four tools always suffice.

Shared MCP approval/trust teaching is included once, either in core when needed there or with the first selected tool that requires it. Directory discovery grants no authority; loading selected tools does not load siblings. Nested guidance dependencies must be validated without cycles. Namespaces are defined by explicit membership, not invented dynamically by a query.

These are harness discovery IDs. Keep actual execution names such as `mcp__<server>__<tool>` unchanged, with existing server ownership and approval routing. This namespace does not require a model provider's native namespace feature.

## Model-facing flow

### Before optional loading

The initial request contains the existing core rules and core tool definitions, the small `load_capabilities` definition, and a compact capability directory. It omits deferred browser schemas, browser-specific examples, and browser-only teaching.

Illustrative directory entry:

```text
Optional capabilities — call load_capabilities before using their tools.
mcp — connected external tool servers; request relevant tools with a namespace and query:
  mcp.playwright: inspect and interact with web pages when rendering or browser actions are needed.
Loading reveals permitted schemas and guidance; existing approvals still apply.
```

Include entries only for currently authorized, configured capabilities with at least one available tool. Disabled integrations are not loadable. Bounded transient errors can be reported as unavailable; do not imply that enabling access is part of loading.

The model decides from the user request and visible conversation. Repository questions can use core tools immediately. A task requiring browser interaction requests relevant tools under the listed browser namespace, illustrated as `mcp.playwright`. No backend intent classifier runs on every user message. Existing obligations to research external facts still apply even when the relevant optional schemas are deferred.

### Schema-once tool catalogs

Today the catalog is minified JSON containing one `name`/`description`/`parameters` object per tool. The proposed renderer declares those columns once and supplies rows, for both the initial core catalog and each loaded capability catalog:

```json
{
  "format":"tool_catalog_rows_v1",
  "tool_columns":["name","description","parameters"],
  "tools":[
    ["example_tool","Example description.",{"type":"object","properties":{"query":{"type":"string"}},"required":["query"],"additionalProperties":false}]
  ]
}
```

The column declaration explains the row format; tool calls still use ordinary named argument objects. Every `parameters` cell remains the complete original JSON Schema. Preserve nested property names, descriptions, constraints, required fields and composition; do not abbreviate them or infer a shared argument schema across unrelated tools. Guidance stays ordinary text alongside the catalog.

This is a presentation codec, not a new `ToolDefinition` or execution format. Registry validation, ownership, permissions and dispatch continue using canonical definitions. Any provider API field requiring normal tool/function schemas receives that provider's valid schema representation; never send row encoding in its place. The current generic controller-action schemas do not become schema-once rows.

Use deterministic ordering and version the rendering format in bundle identity. Require exact round trips to canonical tool definitions and model tests for correct tool/argument selection. Include headers, format explanations, JSON escaping and restored bundles in token counts. Measure minified ordinary JSON versus row encoding independently of deferral; if rows do not save tokens or impair tool use, retain ordinary JSON for that catalog. The main saving remains omitting unused capabilities, not assuming fewer repeated keys always wins.

### Loader

Proposed tool arguments:

```json
{"namespace":"mcp.playwright","query":"Inspect the current page and click a button","limit":4}
```

This replaces the earlier whole-capability loading proposal. `namespace` is a required listed namespace ID. Supply either a natural-language `query` or `tool_names` containing exact known execution names, or neither for a directory-only request. `query` and `tool_names` are mutually exclusive. `limit` defaults to four and accepts 1–8 selected tools; exact-name requests are bounded by the same maximum and must not silently omit requested names. Query text is bounded to 1–1,600 characters. Directory continuation is metadata-only and bounded separately. These arguments do not execute tools or accept executable code, server URLs or arbitrary filesystem paths as commands.

The loader is available to an agent only when it has an optional capability it may discover. Matching and exact-name lookup stay inside the requested namespace and current authorized inventory. A query can return tools from several descendants, with each tool's complete required guidance. It returns schemas directly, rather than returning a shortlist that the model must submit in another call. A later refinement costs another round only when more or different tools are needed.

The result contains the complete permitted schemas and their associated guidance. Illustrative shape; the tool list is abbreviated here only:

```json
{
  "kind":"capability_bundle",
  "capabilities":[{
    "id":"mcp.playwright",
    "version":"<rendered-content-hash>",
    "format":"tool_catalog_rows_v1",
    "tool_columns":["name","description","parameters"],
    "tools":[["<configured-browser-tool>","...",{}]],
    "guidance_blocks":[{"id":"browser_usage","text":"<existing guidance>"}],
    "dependencies":[],
    "scope":"selected_tools"
  }],
  "effective_from":"next_model_request"
}
```

This is an ordinary harness-generated tool result, not a change to system-instruction priority. Core rules explain its purpose and continued subordination to core policy. Do not send a second copy of the same schemas in the system catalog, step payload and tool result.

Load the selected schemas and their guidance dependencies atomically. Deduplicate shared blocks by identity/version. Validate the whole response before publishing it: never truncate a JSON schema or mark a tool loaded if required guidance was omitted. Set a measured bounded output budget before rollout. If a complete selected bundle exceeds it, return `bundle_too_large` without activation and suggest a smaller selection or known narrower namespace; do not silently drop argument constraints, guidance or tools already claimed as delivered. Include only tools whose complete schemas and guidance fit. Required schema dependencies count toward the selected-tool limit and must be disclosed; never expand one selection into all 50 server tools.

Unknown, disabled, disconnected, or no-longer-authorized IDs return bounded errors and do not activate tools. No matches returns a bounded empty result and refinement guidance, not unrelated schemas to fill the limit. Loading the same current tool/guidance versions again is idempotent: when their full content is still visible, a short `already_loaded` receipt suffices. If content is absent, deliver it again. A receipt for one selected tool set does not mark its entire namespace loaded.

### Subsequent calls

The next model request can call newly loaded tools through existing `tool_calls`, using the schemas from the loader result. Execution still routes to the existing source and approval system.

An unloaded browser call is refused; do not silently execute or implicitly activate it. A loader and an unloaded dependent tool in the same model batch are not allowed, even with `depends_on`: the model has not yet seen that tool's schema. Validate the full batch against the pre-request visible set before executing any member, and return a bounded correction asking for a loader-only step. This avoids partially executing a malformed batch.

## Harness state and request assembly

Maintain two distinct sets:

```text
authorized_tools = tools permitted by settings, source state, agent allowlists and phase
callable_tools   = core_tools + authorized tools with current schema/guidance visible
```

`callable_tools` must always be a subset of `authorized_tools`. Build an immutable request snapshot containing the effective tool definitions, rendered bundle versions and policy/source revision. Use that same snapshot for prompt assembly, context accounting and response validation. Recheck execution permissions and source state at dispatch, including after an approval wait; an authorization change can invalidate a previously valid call.

The stable system prefix holds core definitions, essential rules and the capability directory. Loading appends a bundle to history; it must not rebuild that prefix by concatenating loaded definitions and their teaching again. Split the formatter's present `tool_definitions` responsibility into core prompt catalog and effective callable definitions. Existing availability-dependent guidance must use the appropriate available/deferred/loaded distinction.

The current provider-native controller action schema can remain unchanged for the pilot. If an adapter independently serializes application tools, it must use exactly the effective callable set, and its resulting schema changes/cache effects must be measured. Never claim token savings while continuing to send the full hidden catalog through another transport field.

Do not overload `ToolSource.definitions()` with incompatible meanings. Add a clear authorized-inventory seam and a model-visible view. For MCP, obtain permitted definitions before the old prompt-list budget, while preserving source configuration, enablement, allowlists and execution approval checks. Replace the old order truncation only for capabilities migrated to this system; unrelated MCP sources keep their present behavior during the pilot.

## Lifetime, recovery and concurrency

Loaded is a statement about a tool schema and all required guidance being present in the actual outgoing request, not a permanent thread or namespace flag. Maintain versions per loaded tool and shared guidance block; a returned bundle records that particular selection. Different subsets can share guidance without repeatedly copying it.

| Event | Required behavior |
| --- | --- |
| Later steps/follow-up turns with an intact current bundle | Reuse visible history; no repeated full bundle injection. |
| Compaction removes an idle bundle | Mark it unloaded. Keep its directory entry; the model may load it when needed again. A summary saying “browser loaded” is insufficient. |
| Compaction removes guidance for an active operation | Restore its full current bundle before the next relevant model request. Determine active work from harness-owned operation state, such as open sessions or pending work, not query similarity. |
| Restart with intact bundle history and matching version | Revalidate current authorization and reuse the content without duplicating it. |
| Restart with missing bundle content | Restore only bundles required for active persisted work; other capabilities remain discoverable and unloaded. |
| Tool schema/guidance/source changes | Invalidate the previous effective version. Deliver the complete replacement when required; old packets remain historical and confer no execution authority. |
| Permission/phase narrowing or source disablement | Remove affected tools immediately from the callable set. Neither an old bundle nor the loader can restore forbidden access. |
| Rewind | Restore capability state consistently with the rewound history/checkpoint and current permissions; discard later activation state. |
| Cancellation/retry during load | Publish only a completed valid delivery. Reconcile durable packet/history state before reuse; no loaded flag for a result the model never received. |
| Parallel threads/children | Separate visibility and loaded state per conversation/agent. A child derives its own authorized subset and render context; loading in main does not grant the child tools. |

Persist versioned bundle receipts and the state needed to reconcile history/checkpoints. They are recovery hints, not proof of visibility. In the pilot, no automatic topic classifier or “unload every user turn” policy is needed. Whole history retention already has a cost: loaded packages may replay until compaction. Do not promise that “loaded once” means “billed once,” or that long sessions cannot accumulate several capabilities after expansion beyond the pilot.

## Guidance split

Always retain core duties: permission and approval rules, source grounding, external-content trust, required skill-loading order, what capability loading means, and how to discover an optional capability. Keep general research/citation obligations visible. A deferred browser capability is available to load, not a reason to claim all web access is absent.

Move detailed browser MCP tool schemas, browser-only examples and usage instructions into its bundle. Keep Crucible's built-in `web_search`/`web_fetch` behavior unchanged in the browser pilot. Preserve existing generic MCP approval/trust guidance where other MCP tools remain core; shared guidance already present in core is not duplicated in bundles. If a core paragraph mixes essential obligations with detailed optional teaching, split it at that boundary and verify preservation of its substantive rules.

Keep skills as skills: loading a capability does not fulfill an applicable `read_skill` requirement. Do not duplicate the full skills catalog in capability results or introduce a second skill-selection workflow.

## Expected benefits and costs

Schema-once-only measurement on the archived 57-tool catalog, retaining every complete argument schema: current minified Python JSON serialization measured 7,972 `o200k_base` tokens; the proposed rows measured 7,764, or 7,788 including the explicit format explanation. Net saving is **184 tokens / 2.31% of the catalog**. The codec round-trips exactly. This is a local size estimate, not a provider or model-comprehension test; the codec is not deployed. Full nested argument schemas dominate this catalog. Do not substitute the larger earlier TRON-codec savings for this proposed row format. Counts and conditions: [schema-once measurement](../../validation/capability-schema-once/catalog-measurement.json).

The previous prompt audit estimated about 3,405 tokens for 21 browser schemas in one captured catalog. That is historical context, not a current matched saving or a fixed browser size. The new initial saving is:

```text
deferred schemas + deferred dedicated guidance
  − loader definition − directory − loader protocol guidance
```

Count the actual provider request, including serialization/escaping and any native schema representation. On a browser task, loading adds a model round trip and later history replay. A smaller first request can therefore increase complete-task tokens or latency. On code-only and conversational tasks, the expected benefit is avoiding unused browser material without a loader call.

Measure actual input, cached input, output, number of model requests, loader/result tokens, latency and answer/task quality. Report initial-request savings separately from complete-task and billing savings. No savings target is established by the historical numbers alone.

## Implementation boundaries

Proposed new capability definition/state and loader modules should remain small and use existing sources rather than copying tool implementations. Expected integrations:

1. Add explicit nested namespace membership, concise family summaries, cached local tool-metadata lookup and a deterministic selected-tool bundle builder. Support search from any ancestor without mandatory directory traversal.
2. Expose authorized inventory separately from model-visible definitions in `tools/sources.py` and the MCP source seam.
3. Refactor `chat/controller_prompts.py` and `reasoning/engine.py` to assemble core catalog, directory and loaded bundle context without duplicate schemas/teaching.
4. Refresh the per-request callable snapshot and validate against it in `chat/controller_loop.py`; retain execution guards in source dispatch.
5. Integrate completed bundle deliveries with durable history, compaction, restart and rewind. Handle required active-work restoration before dispatching the model request.
6. Keep role/permission rendering through `RenderContext`, and retain existing provider action codecs and accounting.
7. Add an opt-in backend flag, proposed `CRUCIBLE_CAPABILITY_LOADING_ENABLED`, with legacy full-catalog behavior when disabled. No new user-visible permission approval is added for loading metadata.
8. Parse `enabled_tools`/`disabled_tools` in `mcp/config.py`, apply them where `mcp/client.py` builds each server's tool list, accept them through the MCP admin routes, and add the per-tool checkboxes to the Settings MCP section. This step does not depend on the flag in step 7.

Initial implementation targets main-chat browser discovery. A child may load browser only if its existing policy already permits those tools. Do not claim support for task/child/team paths until the same visibility and persistence contracts are wired and tested there; paths outside the pilot retain their current full authorized catalogs.

## Validation and rollout

Required implementation checks:

- Core-only request contains no deferred browser schemas or browser-only teaching in any serialized field. Directory and loader remain usable.
- The model can load browser, inspect the result, and make a valid browser call on the next step.
- MCP directory discovery lists only permitted children and activates none; selected-tool loading never loads unrelated siblings or changes existing `mcp__<server>__<tool>` execution names.
- Inventories with 50 tools and five/six-level namespaces can return the relevant four schemas plus guidance in one discovery query at an ancestor, without listing each intermediate level. Measure retrieval misses, refinements and returned schema tokens.
- Failed/duplicate/over-budget loads do not partially activate tools or duplicate full packets.
- Every deferred tool carries all required existing guidance; shared dependencies appear once per effective context. Guidance-preservation comparisons make changes reviewable.
- Schema-once catalogs round-trip exactly, preserve every argument constraint, and produce valid model tool calls. Compare full-catalog JSON, full-catalog rows, deferred JSON and deferred rows separately to attribute savings and regressions.
- Hidden, disabled, unauthorized and same-batch speculative calls cannot bypass validation, source guards or approvals.
- Restart, compaction, rewind, schema updates, mid-approval revocation and parallel conversation tests satisfy the lifetime table.
- JSON/schema-driven and native-action transports preserve the same callable behavior; unavailable provider-specific optimizations are not required.
- Adapter contract coverage includes every current factory backend, including JSON-mode/prompt-only fallbacks. Live compatibility and row-format comprehension are reported per tested model; OpenAI-only results cannot establish cross-provider rollout readiness.
- Core code discovery, ordinary source reads/edits, memory and skill handling continue without capability-loader calls.
- Per-server filters: an allowlist, a blocklist, both together, an empty allowlist and unknown names each produce the documented inventory; filtered tools are absent from every serialized field, the directory and loader results, and are refused at dispatch even when named directly or by a sub-agent definition or remembered approval; a filter change applies after reconnect without a restart; the full-catalog rollback honors the filters too.

Run matched baseline/full-catalog versus deferred-capability tasks on the same workspace/model/effort and comparable cache conditions. Include greetings, narrow code questions, vague multi-file work, public URL research, browser interaction, follow-up browser tasks, restart/compaction recovery, and unavailable/denied browser cases. Do not tell the experimental model which capability to select in the user question. Keep optional metadata inventory constant between runs.

Publish first-request component counts and actual provider counts when available; whole-task input/output/cached input, loader and operational tool calls, end-to-end latency and judged task correctness. Failure to discover or use required browser tools blocks rollout. Require lower initial payload on non-browser tasks and no observed loss of required permissions, guidance or task correctness in the evaluation set. Whole-task regressions must be visible and assessed before making the feature default; lower initial tokens alone do not qualify it.

Roll out the browser pilot first. Expand to another capability only after its guidance ownership, active-work recovery and matched evaluation pass. Keep rollback available until the final implementation is proven, then perform a separate cleanup rather than carrying indefinite experimental branches in runtime code.

## Deferred items: tool permissions

Added 2026-10-19 from review. These follow this workstream; capability loading must not depend on
them, and loading still grants no permission. Namespaces organize visibility; permission stays a
per-tool property, because one server mixes kinds (Playwright has tools that only read a page and
tools that click and type).

- **One shared tool-effect classification.** Sub-agent `plan` and main-agent Plan Mode use two
  separate blocklists that already disagree: `subagents/permissions.py::_READ_ONLY_EXCLUDED`
  removes `run_command`, `web_search`, `web_fetch` and every `mcp__` tool, while
  `chat/controller_loop.py::_STATE_CHANGING_TOOLS` refuses only `run_command` in Plan Mode.
  Replace both with one classification of what each tool does: read the workspace, write the
  workspace, run commands, read from the network, act on an external system. MCP tools carry
  `readOnlyHint`, `destructiveHint` and `openWorldHint` (present in the installed SDK, ignored
  today). The server sets those hints, so they need a per-server user override.
- **Plan Mode dispatch gap (fix first).** A dispatch from the main agent passes no read-only flag
  (`ChatController._dispatch`: `inherited` is empty when there is no dispatching agent), so a
  `general-purpose` agent dispatched from Plan Mode can edit files. Children dispatched in Plan
  Mode must inherit read-only. Teams are already limited to deliberation there.
- **Plan Mode leaves other effects open.** Terminal sessions (`start_session`, `write_stdin`) stay
  available on purpose (pinned by `test_session_tools_allowed_in_plan_phase`), and a session runs
  any shell command. MCP tools, `web_search`, `web_fetch` and `remember` also stay available.
  Decide each against the classification instead of extending the blocklist.
- **A main-agent read-only mode.** There is none today: approval modes decide whether to ask, not
  whether a tool can run. Fixing Plan Mode (the dispatch gap and the effects above) is intended to
  make it that mode, reusing the sub-agent `plan` machinery (tool filtering, no `edit` action,
  read-only inherited by dispatches). No separate disable mechanism is planned.
- **Web tools in read-only agents.** `web_search` and `web_fetch` change nothing locally, but each
  call sends data out: the query, or a URL that could carry text the agent read. Today read-only
  sub-agents lose both, so the `explore` agent cannot research documentation. Decide whether
  "read-only" means "changes nothing" (keep them, behind the existing approval gate) or "sends
  nothing out" (drop them), and name the two separately.
- **Namespace-scoped agent tool sets.** Agent definitions can already allow a whole server with
  `mcp__<server>__*` (`tool_matches`). Letting definitions and settings name capability namespaces
  such as `mcp.playwright` would reuse that, keeping authorization separate from visibility.

## Completion criteria

This workstream is complete when unused optional browser schemas and dedicated teaching are absent from the initial request, the model selects loading naturally when needed, loaded tools execute under unchanged authorization, recovery restores real schemas/guidance when necessary, and a matched report quantifies both first-request savings and whole-task tradeoffs. A reformatted prompt alone is not completion.
