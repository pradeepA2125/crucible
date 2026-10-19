# On-Demand Capability Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep large MCP catalogs out of the first request and let the user trim them: per-server tool filters now, then on-demand loading of MCP schemas and guidance.

**Spec:** `docs/superpowers/specs/2026-10-19-on-demand-capability-loading.md` and its review, `docs/superpowers/specs/2026-10-19-on-demand-capability-loading-review.md`.

| Part | Scope | Status |
| --- | --- | --- |
| A | Per-server `enabled_tools` / `disabled_tools` filters (spec: "Per-server tool filters") | Done 2026-10-19: dry run + fresh review, defects and review fixes folded in (commits 55490d9..f3ac120) |
| B | On-demand capability loading pilot for MCP servers (main agent) | Done 2026-10-19: dry run + fresh review, defects and review fixes folded in (commits 33cc615..7421e08) |

---

# Part A — Per-server tool filters

**Goal:** Let `.crucible/mcp.json` entries limit which of a server's tools reach the agent, with `enabled_tools` / `disabled_tools` lists, and let Settings edit them per tool.

**Architecture:** The connection manager keeps every tool a server lists and derives the visible subset from the entry's two lists when the server connects. Everything downstream (prompt catalog, dispatch, approval) sees only the visible subset. A dedicated admin route writes just the two lists, and the Settings MCP section shows one checkbox per listed tool.

**Tech Stack:** Python 3.12 / FastAPI / pydantic (agentd-py), TypeScript + Zod (editor-client), React + vitest (vscode-extension webview).

**Spec section:** "Per-server tool filters" (and its validation bullet "Per-server filters: …").

## Part A Global Constraints

- Key names are exactly `enabled_tools` and `disabled_tools`, inside a server entry under `mcpServers`.
- Names are the server's own unprefixed tool names (`browser_click`, not `mcp__playwright__browser_click`).
- Absent `enabled_tools` = every tool; a list = only those; then `disabled_tools` removes names. `enabled_tools: []` exposes no tools and the server stays connected.
- Unknown names log one warning naming them and are otherwise ignored. A malformed value (present, not null, and not a list of strings) skips the whole entry with a warning, like any malformed entry; never fails startup. An explicit JSON `null` means unset, the same as an absent key.
- MCP only. Built-in tools are untouched.
- Filters narrow only: nothing (definition `tools:`, permission modes, remembered approvals) can restore a filtered tool.
- Changes apply without a backend restart **and without reconnecting the server**: `McpServerConfig.fingerprint()` excludes the two filter fields, and `reconcile` re-filters a live handle whose config differs only in them (a reconnect would close Playwright's browser and fail in-flight calls — found in review).
- The lists live in the shared `.crucible/mcp.json`. The user-local server toggle (extension `globalState`) stays separate; no user-local per-tool override.
- GET routes stay read-only (`tests/test_get_routes_read_only.py`).
- Full Python suite runs use `pytest --timeout=120`; a fresh worktree needs `npm ci` at the root and in `apps/vscode-extension/webview-ui`.

## Part A Review Focus

1. A filtered tool named directly by the model must be refused **before** the approval gate is raised — the user must never be asked to approve a tool they filtered out. (Task A1, `test_filtered_tool_is_refused_before_approval`.)
2. The Settings checkboxes must show filtered tools too, unchecked, or the user can never re-enable one from the UI. (Task A2 listing test; Task A4 render test.)
3. Saving one checkbox must not rewrite or drop the rest of the entry (`command`, `args`, `${VAR}` references, unknown keys). (Task A2, `test_tools_route_writes_only_the_two_lists`.)
4. A hand-written `enabled_tools` allowlist must stay an allowlist when the user toggles a box (no silent switch to a blocklist that would expose newly added server tools). (Task A4, `nextToolFilters` tests.)
5. A server that is not connected has no tool list; its card must still render and must not offer checkboxes it cannot fill. (Task A4, render test for a failed server.)

---

### Task 1 (A1): Parse the filters and apply them in the connection manager

**Files:**
- Modify: `services/agentd-py/agentd/mcp/models.py`
- Modify: `services/agentd-py/agentd/mcp/config.py` (`McpConfigLoader._parse`)
- Create: `services/agentd-py/agentd/mcp/filters.py`
- Modify: `services/agentd-py/agentd/mcp/client.py` (`_ServerHandle`, `_serve`, `tool_definitions`, `call_tool`, new `tool_inventory`, new `is_exposed`)
- Modify: `services/agentd-py/agentd/mcp/tool_source.py` (`McpToolSource.prepare`)
- Test: `services/agentd-py/tests/test_mcp_config.py`, `services/agentd-py/tests/test_mcp_client.py`, `services/agentd-py/tests/test_mcp_tool_source.py`

**Interfaces:**
- Produces:
  - `McpServerConfig.enabled_tools: list[str] | None = None`, `McpServerConfig.disabled_tools: list[str] = Field(default_factory=list)`
  - `McpToolInfo(BaseModel)` in `models.py`: `name: str`, `description: str`, `enabled: bool`
  - `filters.visible_tool_names(names: list[str], enabled_tools: list[str] | None, disabled_tools: list[str]) -> list[str]` (keeps the server's order)
  - `filters.unknown_filter_names(names: list[str], enabled_tools: list[str] | None, disabled_tools: list[str]) -> list[str]` (sorted, deduplicated)
  - `McpConnectionManager.tool_inventory() -> dict[str, list[McpToolInfo]]` — every connected server, every listed tool, `enabled` = visible
  - `McpConnectionManager.is_exposed(server: str, tool: str) -> bool` (False when the server has no session)
  - `McpConnectionManager.refusal(server: str, tool: str) -> str | None` — `"MCP server 'x' is not connected[ (state: detail)]"` for a down or unknown server, `"unknown tool 'mcp__x__y'"` for a filtered or unlisted tool on a live server, else None. `McpToolSource.prepare` returns `Error: <refusal>` before the approval gate (a down server must not read as a missing tool — found in review)
  - `class McpToolNotExposed(RuntimeError)` in `client.py`; `call_tool` raises it for a listed-but-filtered or unlisted tool
  - `McpServerStatus.tool_count` = visible tool count (unchanged meaning for the UI: tools the agent can use)

- [ ] **Step 1: Write the failing config tests** in `tests/test_mcp_config.py`

```python
def test_tool_filters_parse(tmp_path):
    _write(tmp_path, {"web": {"command": "x", "enabled": True,
                              "enabled_tools": ["a", "b"], "disabled_tools": ["b"]}})
    (cfg,) = McpConfigLoader(tmp_path).load()
    assert cfg.enabled_tools == ["a", "b"] and cfg.disabled_tools == ["b"]

def test_absent_filters_default_to_all(tmp_path):
    _write(tmp_path, {"web": {"command": "x", "enabled": True}})
    (cfg,) = McpConfigLoader(tmp_path).load()
    assert cfg.enabled_tools is None and cfg.disabled_tools == []

@pytest.mark.parametrize("bad", [{"enabled_tools": "a"}, {"disabled_tools": [1]},
                                 {"enabled_tools": [None]}, {"disabled_tools": {"a": 1}}])
def test_malformed_filter_skips_entry_with_warning(tmp_path, caplog, bad):
    _write(tmp_path, {"web": {"command": "x", "enabled": True, **bad},
                      "ok": {"command": "y", "enabled": True}})
    assert [c.name for c in McpConfigLoader(tmp_path).load()] == ["ok"]
    assert "'web'" in caplog.text

def test_null_tool_filters_mean_unset(tmp_path):
    # "enabled_tools": None and "disabled_tools": None -> enabled_tools is None, disabled_tools == []
```

Use the file's existing writer helper if it has one; otherwise add `_write(tmp_path, servers)` writing `{"mcpServers": servers}` to `.crucible/mcp.json`.

- [ ] **Step 2: Write the failing manager and source tests** in `tests/test_mcp_client.py` (reuse `_FakeSession`, `_factory`, `_StaticLoader`; extend `_cfg(name, **kw)` to pass filters through) and `tests/test_mcp_tool_source.py`

```python
@pytest.mark.parametrize("enabled,disabled,visible", [
    (None, [], ["say", "shout", "whisper"]),
    (["say", "shout"], [], ["say", "shout"]),
    (None, ["shout"], ["say", "whisper"]),
    (["say", "shout"], ["shout"], ["say"]),
    ([], [], []),
])
async def test_filters_decide_visible_tools(enabled, disabled, visible): ...
    # tool_definitions() names == [f"mcp__echo__{t}" for t in visible]
    # statuses()["echo"].tool_count == len(visible)
    # tool_inventory()["echo"] lists all three names in server order, enabled == (name in visible)

async def test_unknown_filter_names_warn_once(caplog): ...
    # enabled_tools=["say", "gone"], disabled_tools=["nope"] -> one WARNING naming "gone" and "nope"

async def test_call_tool_refuses_filtered_tool(): ...
    # disabled_tools=["shout"]; await mgr.call_tool("echo", "shout", {}) raises McpToolNotExposed;
    # the fake session recorded no call

async def test_filter_change_reconnects_on_reconcile(): ...
    # reconcile([_cfg("echo", disabled_tools=["shout"])]) after start -> shout gone from tool_definitions()
```

In `tests/test_mcp_tool_source.py`:

```python
async def test_filtered_tool_is_refused_before_approval(): ...
    # manager.is_exposed returns False for ("echo", "shout"); the approval callback is never called;
    # prepare("mcp__echo__shout", {}) returns ToolOutput with is_error=True and
    # output == "Error: unknown tool 'mcp__echo__shout'"
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_mcp_config.py tests/test_mcp_client.py tests/test_mcp_tool_source.py`
Expected: FAIL (unknown fields / missing `tool_inventory`, `McpToolNotExposed`, `is_exposed`).

- [ ] **Step 4: Implement**

- `models.py`: the two fields and `McpToolInfo`.
- `config.py`: a module-level `_tool_filters(entry) -> dict[str, list[str]] | None` (None = malformed, null = unset) used by `_parse`; a malformed value logs `"[mcp] server %r: enabled_tools/disabled_tools must be lists of tool names — skipped"` and `continue`s.
- `filters.py`: the two pure functions.
- `client.py`: `_ServerHandle.all_tools` holds `listed.tools`; `.tools` holds the visible ones; `_serve` computes both and logs unknown names once (`"[mcp] server %s: filter names not offered by the server: %s"`); `tool_definitions` reads `.tools` (unchanged otherwise); `call_tool` checks `is_exposed` first.
- `tool_source.py` `prepare`: after `parse_tool_name`, if `not self._manager.is_exposed(server, tool_name)` return `ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)` — before `_approve`. Use `getattr(self._manager, "is_exposed", None)` and treat a missing method as exposed, so fakes without it keep working.

- [ ] **Step 5: Run them to verify they pass, then the whole MCP suite**

Run: `cd services/agentd-py && pytest tests/test_mcp_config.py tests/test_mcp_client.py tests/test_mcp_tool_source.py tests/test_mcp_routes.py tests/test_controller_mcp_gate.py tests/test_mcp_flag_wiring.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/mcp services/agentd-py/tests/test_mcp_config.py services/agentd-py/tests/test_mcp_client.py services/agentd-py/tests/test_mcp_tool_source.py
git commit -m "feat(mcp): per-server enabled_tools/disabled_tools filters"
```

---

### Task 2 (A2): Admin route and listing

**Files:**
- Modify: `services/agentd-py/agentd/mcp/admin.py`
- Modify: `services/agentd-py/agentd/api/routes.py` (`_mcp_server_listing`, new route, new request model)
- Test: `services/agentd-py/tests/test_mcp_admin.py`, `services/agentd-py/tests/test_mcp_routes.py`

**Interfaces:**
- Consumes: `McpConnectionManager.tool_inventory()` (Task A1).
- Produces:
  - `admin.set_tool_filters(path: Path, name: str, enabled_tools: list[str] | None, disabled_tools: list[str]) -> bool` — read-modify-write of one existing entry; sets `enabled_tools` (removes the key when `None`) and `disabled_tools` (removes the key when empty); returns `False` when the server is not in the file. Every other key is preserved verbatim.
  - `class McpToolFiltersRequest(BaseModel)`: `enabled_tools: list[str] | None = None`, `disabled_tools: list[str] = []`, `disabled: list[str] = []`
  - `PUT /v1/mcp/servers/{name}/tools` → 409 when MCP is off, 404 when the server is not in the file, else writes, then `reconcile(loader.load(), disabled=frozenset(body.disabled))`, returns the listing.
  - Each listed server gains `"tools": [{"name", "description", "enabled"}]` (empty when not connected), `"enabled_tools": list[str] | None` and `"disabled_tools": list[str]`, parsed with `config.parse_tool_filters`: a malformed entry lists `null`/`[]` with `detail` = `TOOL_FILTERS_MALFORMED`, never raw values (the client's Zod parse would reject the whole listing and blank Settings — found in review).

- [ ] **Step 1: Write the failing tests**

```python
# test_mcp_admin.py
def test_tools_route_writes_only_the_two_lists(tmp_path):
    # entry has command, args, env {"K": "${K}"}, an unknown key "note"
    # set_tool_filters(path, "web", None, ["b"]) -> entry keeps command/args/env/note verbatim,
    #   disabled_tools == ["b"], no "enabled_tools" key
    # set_tool_filters(path, "web", ["a"], []) -> enabled_tools == ["a"], no "disabled_tools" key
    # set_tool_filters(path, "missing", None, []) is False and the file is unchanged

# test_mcp_routes.py — give _client an optional session factory and add a two-tool stub (a, b);
# the shared one-tool stub is asserted elsewhere. A test making several requests must use
# `with _client(...) as client:` — separate requests run separate event loops, and the PUT's
# reconcile awaits serve tasks an earlier request started (see test_delete_and_reconnect).
def test_put_tools_filters_and_lists_every_tool(tmp_path): ...
    # PUT /v1/mcp/servers/web/tools {"disabled_tools": ["b"]} -> 200;
    # web["tools"] == [{"name": "a", ..., "enabled": True}, {"name": "b", ..., "enabled": False}]
    # web["tool_count"] == 1, web["disabled_tools"] == ["b"], web["enabled_tools"] is None
def test_put_tools_unknown_server_404(tmp_path): ...
    # assert detail == "no MCP server named 'nope'" — a bare 404 also passes with no route at all
def test_put_tools_when_mcp_disabled_409(tmp_path): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_mcp_admin.py tests/test_mcp_routes.py`
Expected: FAIL.

- [ ] **Step 3: Implement** `set_tool_filters`, the request model, the route, and the listing fields.

- [ ] **Step 4: Run them to verify they pass, plus the GET read-only pin**

Run: `cd services/agentd-py && pytest tests/test_mcp_admin.py tests/test_mcp_routes.py tests/test_get_routes_read_only.py`
Expected: all pass (the new route is a PUT, so the read-only pin needs no change).

- [ ] **Step 5: Commit** — `feat(mcp): route to set a server's tool filters`

---

### Task 3 (A3): editor-client contract

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`McpServerViewSchema`, `BackendTaskClient`)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`mapMcpList`, new method)
- Test: `apps/editor-client/test/settings-client.test.ts`

**Interfaces:**
- Consumes: Task A2's wire shape.
- Produces:
  - `McpToolViewSchema = z.object({ name: z.string(), description: z.string(), enabled: z.boolean() })`
  - `McpServerViewSchema` gains `tools: z.array(McpToolViewSchema)`, `enabledTools: z.array(z.string()).nullable()`, `disabledTools: z.array(z.string())`
  - `BackendTaskClient.setMcpServerTools(name: string, enabledTools: string[] | null, disabledTools: string[], disabled: string[]): Promise<McpServerList>` → `PUT /v1/mcp/servers/{name}/tools` with body `{enabled_tools, disabled_tools, disabled}`

- [ ] **Step 1: Write the failing tests**: `listMcpServers` maps `tools`, `enabled_tools` → `enabledTools` (null stays null), `disabled_tools` → `disabledTools`, defaulting to `[]`/`null`/`[]` when absent (older backend); `setMcpServerTools` sends the right URL, method and snake_case body.
- [ ] **Step 2: Run them to verify they fail** — `npm run -w @crucible/editor-client test -- settings-client`
- [ ] **Step 3: Implement** the schema fields, mapping and method. Every other `BackendTaskClient` implementation in the repo (stubs in tests) must compile: run `npm run typecheck` and add the method to any stub that fails.
- [ ] **Step 4: Run tests and build** — `npm run -w @crucible/editor-client test && npm run -w @crucible/editor-client build`
- [ ] **Step 5: Commit** — `feat(editor-client): MCP tool filter contract`

---

### Task 4 (A4): Settings MCP section — per-tool checkboxes

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/mcpToolFilters.ts`
- Modify: `apps/vscode-extension/webview-ui/src/settings/types.ts` (`McpServerRow`)
- Modify: `apps/vscode-extension/webview-ui/src/settings/sections/McpSection.tsx`
- Modify: `apps/vscode-extension/src/settings-data.ts` (message union, handler, `SettingsDeps.client`)
- Modify: `apps/vscode-extension/src/settings-deps.ts` (wires the settings client method by method; add `setMcpServerTools`)
- Modify: `apps/vscode-extension/webview-ui/src/settings/types.ts` also holds the webview's copy of the message union — add `settings/mcpTools` there too
- Modify: `apps/vscode-extension/webview-ui/src/settings/sections/OverviewSection.test.tsx` (its `McpServerRow` fixture needs the new fields; the root typecheck covers webview tests)
- Test: `apps/vscode-extension/webview-ui/src/settings/mcpToolFilters.test.ts`, `.../sections/McpSection.test.tsx`, `apps/vscode-extension/test/settings-data.test.ts`

**Interfaces:**
- Consumes: `setMcpServerTools` (Task A3).
- Produces:
  - `McpServerRow` gains `tools: { name: string; description: string; enabled: boolean }[]`, `enabledTools: string[] | null`, `disabledTools: string[]`
  - `nextToolFilters(row: { enabledTools: string[] | null; disabledTools: string[] }, tool: string, checked: boolean): { enabledTools: string[] | null; disabledTools: string[] }` — with an allowlist present, edit the allowlist (add/remove `tool`, and drop it from `disabledTools` when checked); with no allowlist, edit `disabledTools` only. Result lists are sorted and deduplicated.
  - Webview message `{ type: "settings/mcpTools"; name: string; enabledTools: string[] | null; disabledTools: string[] }`; the host calls `client.setMcpServerTools(name, enabledTools, disabledTools, deps.mcpDisabled())` and rebuilds state.

- [ ] **Step 1: Write the failing tests**
  - `mcpToolFilters.test.ts`: unchecking `b` with no allowlist → `{enabledTools: null, disabledTools: ["b"]}`; re-checking → `{null, []}`; with allowlist `["a","b"]` unchecking `b` → `{["a"], []}`; with allowlist `["a"]` checking `c` → `{["a","c"], []}`; checking a tool that is both outside the allowlist and in `disabledTools` → added to the allowlist and removed from `disabledTools`.
  - `McpSection.test.tsx`: a connected server with tools `a` (enabled) and `b` (disabled) renders a "Tools" disclosure showing `2 of 2 · 1 hidden` (or similar exact copy you pick and assert); expanding shows two checkboxes, `b` unchecked; clicking `b` sends `{type: "settings/mcpTools", name: "web", enabledTools: null, disabledTools: []}`; a failed server with `tools: []` renders no disclosure. Update `makeState` rows with `tools`, `enabledTools`, `disabledTools`.
  - `settings-data.test.ts`: `settings/mcpTools` calls `setMcpServerTools("web", null, ["b"], <current disabled>)` and posts refreshed state.
- [ ] **Step 2: Run them to verify they fail** — `npm run -w crucible-vscode-extension test -- mcpToolFilters McpSection settings-data`
- [ ] **Step 3: Implement.** Checkboxes sit inside the server's `CardShell` body behind a collapsed disclosure (servers can list 25+ tools); disable them while `busy`. Follow the webview design system (existing `--color-*` tokens and primitives already used in `McpSection.tsx`). The card subtitle keeps `${transport} · ${toolCount} tools`, which is now the visible count.
- [ ] **Step 4: Run tests, typecheck, build** — `npm run -w @crucible/editor-client build && npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck && npm run build`
- [ ] **Step 5: Commit** — `feat(settings): per-tool checkboxes for MCP servers`

---

### Task 5 (A5): Documentation

**Files:**
- Modify: `CLAUDE.md` — MCP client (P3) section: one bullet "Per-server tool filters" with the semantics from Global Constraints, the route, and "refused before the approval gate". Keep it to the facts a future reader needs.

- [ ] **Step 1: Edit, then run the full suites** — `cd services/agentd-py && pytest` (summary line must show 0 failed) and `npm run test`.
- [ ] **Step 2: Commit** — `docs: per-server MCP tool filters`

---

# Part B — On-demand capability loading pilot

**Goal:** With the flag on, keep MCP tool schemas and MCP guidance out of the main agent's first request. The agent sees a one-line directory entry per connected server and calls `load_capabilities` when its task needs one; the selected schemas and guidance arrive as that call's result and are callable from the next step.

**Architecture:** A small `agentd/capabilities/` package does the pure work: group MCP definitions by server, render the directory, select tools, build a bundle, and read which tools are loaded from the conversation history. "Loaded" is derived from history every step: a tool is loaded when a bundle containing its current schema version is in the history being sent. So restart, rewind and compaction need no extra state — a compaction that evicts a bundle simply unloads its tools, and the directory still lists the server. The controller loop gives the engine the core catalog (every non-MCP tool plus `load_capabilities`) and the directory, and refuses a call to an unloaded MCP tool before any member of the batch runs.

**Tech Stack:** Python 3.12 (agentd-py); TypeScript (one extension setting).

**Spec sections:** Purpose, Capability contract, MCP namespace (one level only), Model-facing flow (Before optional loading, Loader, Subsequent calls), Harness state and request assembly, Lifetime (as simplified below), Guidance split, Implementation boundaries, Validation.

## Part B Global Constraints

Decisions from the review (`…-review.md`, agreed by Codex) that narrow the spec for this pilot:

- **No schema-once rows.** Catalogs and bundles use ordinary JSON tool definitions (the spec's own measurement: 184 tokens, 2.31%).
- **One namespace level:** `mcp.<server>` for each connected MCP server. No families, no nested directories, no directory continuation.
- **Whole small servers.** A request for a namespace with no `query` and no `tool_names` returns every tool of that server when the bundle fits `MAX_BUNDLE_CHARS = 32_000`; otherwise it returns an error asking for a `query` or `tool_names`. `query` selects up to `limit` tools (1–8, default 4) by deterministic lexical overlap; `tool_names` selects exact unprefixed names (all must exist; bounded by 8).
- **Loaded = visible in history**, versioned per tool (`sha256` of the canonical definition JSON, first 12 hex chars), **and only while a bundle carrying the MCP guidance is also visible** (the spec: loaded means schema and required guidance in the request). No separate loaded-state persistence. Compaction that evicts a bundle unloads its tools. Restoring bundles for active work after compaction is deferred (spec "Lifetime" row 3).
- **The loader runs alone in its step** and its result is delivered whole (`ToolOutput.whole`), never cut by the batch result budget. A bundle over `MAX_BUNDLE_CHARS` is refused (`bundle_too_large`) and activates nothing.
- **Pilot scope:** main chat agent only (`RenderContext.is_main` and no `AgentContext`). Children, team members and the task path keep their full catalogs.
- **Flag:** `CRUCIBLE_CAPABILITY_LOADING_ENABLED`, default OFF (truthy `1/true/yes/on`). Off → byte-identical prompts (`tests/goldens/*` unchanged). Extension setting `crucible.performance.mcpOnDemand` (boolean, default false) maps to it.
- **Every MCP server is deferred when the flag is on.** Built-in tools, including `web_search`/`web_fetch`, stay core. Part A's filters apply first: a filtered tool is never in the directory, a bundle or the callable set. The main agent's `McpToolSource` skips the `CRUCIBLE_MCP_TOOLS_MAX_CHARS` order budget while the flag is on (`budgeted=False`), so deferral lifts the reachability ceiling; children keep the budget.
- Loading grants nothing: execution still goes through `McpToolSource` (refusal, approval gate, dispatch) unchanged.

## Part B Review Focus

1. A model that calls an MCP tool it saw in an earlier turn's bundle, after compaction evicted that bundle, gets a refusal telling it to load the namespace again — never a silent execution against a schema it can no longer see. (Task 9, `test_unloaded_after_eviction_is_refused`.)
2. A server whose tool schema changes on reconnect invalidates the old bundle: the old version must not count as loaded. (Task 7, `test_changed_schema_is_not_loaded`.)
3. The loader batched with any other call is refused with nothing executed, including when the other call is a read. (Task 9, `test_loader_must_run_alone`.)
4. Flag off, or a child agent with the flag on: the request is byte-identical to today's. (Task 9 goldens and `test_child_keeps_full_catalog`.)
5. A bundle exactly at the size limit is delivered whole even when the adaptive result budget is smaller. (Task 6 and Task 8, `test_bundle_survives_small_result_budget`.)

---

### Task 6 (B1): Deliver a tool result whole

**Files:**
- Modify: `services/agentd-py/agentd/tools/registry.py` (`ToolOutput`)
- Modify: `services/agentd-py/agentd/tools/batching.py` (`run_batch`, the aggregate-budget slice)
- Test: `services/agentd-py/tests/test_tool_batching.py` (or the file holding `run_batch` tests — `grep -l "run_batch" tests/`)

**Interfaces:**
- Produces: `ToolOutput.whole: bool = False` — when True, `run_batch` never slices the output to the batch share.

- [ ] **Step 1: Failing test** `test_whole_output_is_not_sliced`: one call whose prepared run returns `ToolOutput("x" * 5000, whole=True)` with `budget=1024` → the outcome's output is the full 5000 characters; the same call without `whole` is sliced and ends with the existing "[Result omitted beyond aggregate budget" notice.
- [ ] **Step 2: Run it, expect FAIL** (unknown keyword `whole`).
- [ ] **Step 3: Implement** the field and the `and not result.whole` condition.
- [ ] **Step 4: Run** `pytest tests/<batching test file>` → PASS.
- [ ] **Step 5: Commit** `feat(tools): let a tool result opt out of the batch budget slice`

---

### Task 7 (B2): Capability catalog, bundles and history state (pure)

**Files:**
- Create: `services/agentd-py/agentd/capabilities/__init__.py` (empty), `catalog.py`, `bundle.py`, `history.py`
- Test: `services/agentd-py/tests/test_capabilities.py`

**Interfaces:**
- Produces (`catalog.py`):
  - `LOADER_TOOL = "load_capabilities"`
  - `tool_version(definition: dict[str, object]) -> str` — first 12 hex of `sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")))`
  - `@dataclass(frozen=True) class CapabilitySet`: `core: list[dict[str, object]]`, `namespaces: dict[str, list[dict[str, object]]]` (key `mcp.<server>`, value that server's definitions in registry order)
  - `split_definitions(definitions: list[dict[str, object]]) -> CapabilitySet` — every `mcp__<server>__<tool>` definition goes to `mcp.<server>`; everything else to `core`
  - `render_directory(namespaces: dict[str, list[dict[str, object]]]) -> str` — one line per namespace, sorted: `- mcp.<server> (<n> tools): <first 10 unprefixed names, comma-separated>[, +<k> more]` (`(1 tool)` when n is 1); empty string when there are none
- Produces (`bundle.py`):
  - `MAX_BUNDLE_CHARS = 32_000`, `DEFAULT_LIMIT = 4`, `MAX_LIMIT = 8`
  - `select_tools(definitions: list[dict[str, object]], *, query: str | None, tool_names: list[str] | None, limit: int) -> list[dict[str, object]]` — no query and no names → all; names → exactly those, in the order given, each either unprefixed or the full `mcp__<server>__<tool>` name (raises `KeyError` naming the missing ones); query → score each tool by the count of query words (lowercased, split on non-alphanumerics, words of 3+ chars) found in its unprefixed name split on `_` plus its description words, drop zero scores, sort by score descending then registry order, take `limit`
  - `render_bundle(namespace: str, definitions: list[dict[str, object]], guidance: str | None) -> str` — compact JSON `{"kind": "capability_bundle", "namespace", "tools": [<definitions>], "versions": {name: tool_version}, "guidance": guidance or null, "effective_from": "next_model_request"}`
- Produces (`history.py`):
  - `loaded_versions(history: list[dict[str, object]]) -> dict[str, str]` — every tool name → version from `tool_result` entries with `tool == "load_capabilities"`, not `is_error`, whose `content` parses as a `capability_bundle`; later bundles override earlier ones; `{}` when no visible bundle carries guidance (`guidance_visible` False)
  - `guidance_visible(history: list[dict[str, object]]) -> bool` — True when a visible bundle carries non-null `guidance`
  - `unloaded_reason(tool: str, definitions_by_name: dict[str, dict[str, object]], loaded: dict[str, str]) -> str | None` — None for a non-MCP tool, for an MCP tool absent from `definitions_by_name` (filtered or unknown: `McpToolSource` refuses it with its own message), or for a loaded one at its current version; else `"`<tool>` is not loaded. Call load_capabilities with namespace \"mcp.<server>\" first, alone in its step."` (a changed version says "its schema changed since it was loaded")

- [ ] **Step 1: Failing tests** in `tests/test_capabilities.py`: `split_definitions` routes `mcp__web__fetch` to `mcp.web` and `read_file` to core; `render_directory` matches the exact line format, including the `+k more` suffix at 12 tools and sorted namespaces; `select_tools` returns all with no query, the exact named tools in given order, raises `KeyError` mentioning a missing name, ranks `"click a button"` → `browser_click` first among `browser_click/browser_type/browser_snapshot` (with descriptions), returns `[]` when nothing matches, and respects `limit`; `render_bundle` round-trips through `json.loads` with the documented keys; `loaded_versions` reads a bundle result, ignores an error result, and lets a later bundle win; `test_changed_schema_is_not_loaded`: a bundle recorded at version A and a current definition at version B → `unloaded_reason` mentions "schema changed"; a core tool → None.
- [ ] **Step 2: Run** `pytest tests/test_capabilities.py` → FAIL (module missing).
- [ ] **Step 3: Implement** the three modules.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(capabilities): catalog split, bundles and loaded state from history`

---

### Task 8 (B3): The `load_capabilities` tool

**Files:**
- Create: `services/agentd-py/agentd/capabilities/tool_source.py`
- Test: `services/agentd-py/tests/test_capabilities.py` (append)

**Interfaces:**
- Consumes: Task 6 `ToolOutput.whole`; Task 7 functions.
- Produces:
  - `class CapabilitySession` — shared by the loop and the source, like `active_skills`: attributes `namespaces: dict[str, list[dict]]` (set by the loop at run start), `history: list[dict]` (the loop's live history list), `guidance: str` (the rendered MCP guidance for this agent).
  - `LOAD_CAPABILITIES: ToolDefinition` — parameters `{"namespace": string (required), "query": string (1–1600 chars), "tool_names": array of strings (≤ 8), "limit": integer 1–8}`, `additionalProperties: false`. Description: what it does, that results are schemas (not executions) usable from the next step, that it must be called alone, and that existing approvals still apply.
  - `class CapabilityToolSource` (ToolSource): `definitions() -> [LOAD_CAPABILITIES]`, `owns(tool) -> tool == "load_capabilities"`, `async execute(tool, args) -> ToolOutput`.
  - Outcomes, each a `ToolOutput`:
    - unknown namespace → error listing the known namespaces;
    - both `query` and `tool_names` → error;
    - missing exact names → error naming them;
    - no query matches → non-error `"No tools in <ns> match that query; try other words or tool_names."`;
    - every selected tool already loaded at its current version → non-error receipt `"Already loaded: <names>. Their schemas are earlier in this conversation."` (no bundle repeated);
    - bundle over `MAX_BUNDLE_CHARS` → error `bundle_too_large` asking for a `query` or fewer `tool_names` (nothing activates);
    - else the bundle JSON with `whole=True`; guidance included only when `guidance_visible(session.history)` is False.

- [ ] **Step 1: Failing tests:** each outcome above; `test_bundle_survives_small_result_budget`: a 30,000-char bundle run through `run_batch(..., budget=16_000)` reaches the outcome whole.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_capabilities.py tests/<batching test file>` → PASS.
- [ ] **Step 5: Commit** `feat(capabilities): load_capabilities tool`

---

### Task 9 (B4): Wire it into the main controller loop and prompt

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_factory.py` (`is_capability_loading_enabled() -> bool`, env `CRUCIBLE_CAPABILITY_LOADING_ENABLED`, default OFF)
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (`_CAPABILITY_BLOCK`; `format_controller_system_prompt(..., capability_directory: str | None = None)`)
- Modify: `services/agentd-py/agentd/reasoning/engine.py` (`create_controller_step(..., capability_directory: str | None = None)` passed through)
- Modify: `services/agentd-py/agentd/chat/controller_loop.py` (`ControllerLoop(..., capabilities: CapabilitySession | None = None)`; per run: split the registry's definitions, set `session.namespaces`/`session.history`/`session.guidance`; engine catalog = `core + [LOAD_CAPABILITIES]`; pass `capability_directory` only through `accepts_kwarg`; `fixed_estimate` counts the core catalog; in `batch_guard`, refuse an unloaded MCP tool with `unloaded_reason(...)` and refuse `load_capabilities` in a batch of more than one call)
- Modify: `services/agentd-py/agentd/chat/controller.py` (`_run_loop`: when the flag is on and an MCP manager exists, create a `CapabilitySession(guidance=mcp_bundle_guidance(RenderContext.main()))`, add `CapabilityToolSource(session)` to `extra_sources`, pass `capabilities=session` to the loop. The loop splits the catalog per run and drops `load_capabilities` when no namespace is connected. `_build_registry` constructs the main agent's `McpToolSource(..., budgeted=not is_capability_loading_enabled())`)
- Modify: `services/agentd-py/agentd/mcp/tool_source.py` (`McpToolSource(..., budgeted: bool = True)`; False skips the order budget)
- Modify: `services/agentd-py/agentd/chat/controller_factory.py` (`warn_if_incoherent_flags`: warn when the flag is on and `CRUCIBLE_MCP_ENABLED` is off)
- Test: `services/agentd-py/tests/test_capability_loading_controller.py`

**Interfaces:**
- Consumes: Tasks 6–8.
- Produces: `_CAPABILITY_BLOCK` text (core teaching): MCP tools are deferred; the directory lists connected servers; call `load_capabilities` alone with a namespace (and optionally `query` or `tool_names`) when the task needs one; schemas arrive as the result and are callable from the next step; loading grants no permission and approvals still apply; a refusal saying a tool is not loaded means load it again. Then `OPTIONAL CAPABILITIES` and the directory lines.
  - The `_MCP_BLOCK` is not appended to the system prompt when no MCP tool is in the core catalog (already true: it keys off `mcp__` names); the bundle carries it instead, through `mcp_bundle_guidance(render_ctx)`: the block with its "parameter schemas are in TOOLS" sentence rewritten to "in this bundle" (raises if that sentence changes, so a drift fails `test_bundle_guidance_points_at_the_bundle`).
  - The loop guards in `batch_guard`: `unloaded_reason(tool, deferred_defs, loaded_versions(history))`, read once per batch against the history being sent; `load_capabilities` in a batch of more than one call raises before `validate_calls`. A rejected batch keeps the loop's existing per-member `blocked` slots, so tests assert `status == "blocked"`, not absence.

- [ ] **Step 1: Failing tests** (use the scripted engine and a stub MCP manager; record the `tool_definitions` and system prompt each step sees):
  - `test_first_request_has_directory_not_schemas`: flag on, a stub manager with `mcp__web__fetch` → the step's catalog has no `mcp__` names and includes `load_capabilities`; the system prompt contains `mcp.web` and no `EXTERNAL MCP TOOLS`.
  - `test_load_then_call_next_step`: script `load_capabilities {"namespace": "mcp.web"}` → next step calls `mcp__web__fetch` → it executes through `McpToolSource` (approval callback called once).
  - `test_unloaded_call_is_refused_before_execution`: a batch of `read_file` + `mcp__web__fetch` without loading → correction containing "not loaded", neither member executed.
  - `test_unloaded_after_eviction_is_refused`: history whose bundle was removed (simulate compaction by seeding history without it) → refusal.
  - `test_loader_must_run_alone`: `load_capabilities` + `read_file` in one batch → correction, nothing executed.
  - `test_flag_off_is_unchanged`: flag off → the catalog includes the `mcp__` tools and the prompt matches the existing goldens (`tests/goldens/controller_prompt_main.json` stays untouched).
  - `test_child_keeps_full_catalog`: flag on, a dispatched child → its catalog includes `mcp__` tools and no `load_capabilities`.
  - `test_loaded_in_earlier_turn_is_callable` (a seeded bundle with guidance), `test_engine_puts_directory_in_system_prompt` (the real `DefaultReasoningEngine` with a capturing transport), `test_bundle_guidance_points_at_the_bundle`, a guidance-evicted history (a guidance-less bundle alone is not loaded), every MCP tool listed with a tiny `CRUCIBLE_MCP_TOOLS_MAX_CHARS`, and a >16,000-char bundle reaching history whole through the real loop with a small result budget.
  - The recording engine needs `CRUCIBLE_TOOL_BATCHING_ENABLED=1` for multi-call batches; pre-approve the stub tool with `McpRuleStore(ws).add(...)` so no gate waits.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_capability_loading_controller.py tests/test_controller_payload.py tests/test_prompt_leak_lint.py tests/test_controller_mcp_gate.py tests/test_mcp_*.py` and the goldens test (`grep -l goldens tests/`) → PASS.
- [ ] **Step 5: Commit** `feat(chat): load MCP capabilities on demand behind a flag`

---

### Task 10 (B5): Extension setting

**Files:**
- Modify: `apps/vscode-extension/package.json` (`crucible.performance.mcpOnDemand`: boolean, default false, `scope: "machine-overridable"`, description "Keep MCP tool schemas out of the first request; the agent loads a server's tools when it needs them. Experimental. Requires a backend restart.")
- Modify: `apps/vscode-extension/src/runtime/vscode-runtime.ts` (`SETTING_ENV_MAP`: `"crucible.performance.mcpOnDemand": "CRUCIBLE_CAPABILITY_LOADING_ENABLED"`)
- Test: `apps/vscode-extension/test/runtime-performance-settings.test.ts`

- [ ] **Step 1: Failing test:** the manifest declares the setting with default false; a user-set `true` maps to `CRUCIBLE_CAPABILITY_LOADING_ENABLED=true` and an unset setting maps to nothing (follow the file's existing pattern for `toolBatching`).
- [ ] **Step 2: Run** `npm run -w crucible-vscode-extension test -- runtime-performance-settings` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; `npm run -s build`.
- [ ] **Step 5: Commit** `feat(extension): setting for on-demand MCP loading`

---

### Task 11 (B6): Measure, document, full suites

**Files:**
- Create: `docs/validation/capability-loading/first-request.json` and a short note in `docs/on-demand-capability-loading-pilot.md`
- Modify: `CLAUDE.md` (MCP client section: one bullet for on-demand loading — flag, directory, loader, loaded-from-history rule, what is deferred; plus a `CRUCIBLE_CAPABILITY_LOADING_ENABLED` line in Key Configuration)
- Create: `docs/validation/capability-loading/measure_first_request.py` (rebuilds the prompt from a recorded turn's catalog; sets compact context and batching on, the managed defaults)

- [ ] **Step 1: Measure** the first-request system prompt with the flag off and on for this workspace's connected MCP inventory (take the tool definitions from the latest `controller-turn-00.json` or the live `/v1/mcp/servers` listing): `o200k_base` tokens of the catalog, the MCP block, the capability block and directory. Record both numbers and the difference; label them as one workspace's estimate, not a billing figure.
- [ ] **Step 2: Docs** — CLAUDE.md bullet; the pilot note states what is measured, what is not (no model-behavior evaluation yet), and the deferred items: active-work restoration after compaction, children/teams/task path, weaker-model evaluation before any default change.
- [ ] **Step 3: Full suites** — `cd services/agentd-py && pytest --timeout=120` (0 failed) and `npm run test`.
- [ ] **Step 4: Commit** `docs: on-demand MCP loading pilot`

