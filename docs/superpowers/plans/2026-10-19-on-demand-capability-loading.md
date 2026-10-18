# On-Demand Capability Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep large MCP catalogs out of the first request and let the user trim them: per-server tool filters now, then on-demand loading of MCP schemas and guidance.

**Spec:** `docs/superpowers/specs/2026-10-19-on-demand-capability-loading.md` and its review, `docs/superpowers/specs/2026-10-19-on-demand-capability-loading-review.md`.

| Part | Scope | Status |
| --- | --- | --- |
| A | Per-server `enabled_tools` / `disabled_tools` filters (spec: "Per-server tool filters") | Done 2026-10-19: dry run + fresh review, defects and review fixes folded in (commits 55490d9..f3ac120) |
| B | On-demand capability loading pilot for MCP servers | Written after Part A's dry run |

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
