# Design — `write_doc` Gated Docs Tool + Web Search via Shipped MCP Defaults

**Date:** 2026-07-02
**Status:** approved (brainstorm 2026-07-02)
**Depends on:** P3 MCP client (`docs/superpowers/specs/2026-07-02-mcp-client-github-integration-design.md`), merged on `feat/mcp-client`.

## 1. Problem

Two gaps in the chat controller's tool surface:

1. **No lightweight write path for non-executable artifacts.** Writing a README, a mermaid
   diagram, or a data file today requires the full `propose_mode → EDIT → shadow →
   EditGate → promote` cycle. That ceremony exists to protect code; for standalone docs it
   is friction with no payoff.
2. **No web access.** The controller cannot search the web or fetch a page, so anything
   requiring current external information is unanswerable.

## 2. Decisions (from brainstorm Q&A)

| # | Decision |
|---|----------|
| 1 | Doc writes use a **lightweight per-write gate** (not ungated direct write, not EditGate reuse). |
| 2 | Extension allowlist = **docs + diagrams + data**: `.md .mmd .mermaid .txt .rst .adoc .svg .json .yaml .yml .csv` (case-insensitive). The per-write gate covers the config-file blurriness of `.json`/`.yaml`. |
| 3 | Web search ships as **MCP config defaults (approach 2B)** — zero new backend code; the P3 MCP client provides transport, gating, and remember-rules. |
| 4 | Web tools include **both `web_search` and `web_fetch`** (the Ollama MCP server exposes both). |
| 5 | Web tool consent = **first-use gate, then remembered** — exactly the existing `mcp_tool` gate + `McpRuleStore` "Approve & remember (this workspace)". |
| 6 | Both features are **controller-only** and **flag/config-gated, default OFF** (`write_doc` behind `CRUCIBLE_DOC_WRITE_ENABLED`; web behind the user's `mcp.json` entry + `CRUCIBLE_MCP_ENABLED`). |

## 3. Feature 1 — `write_doc` tool + `doc_write` gate

### 3.1 Tool source (`agentd/chat/doc_write_source.py`)

`DocWriteToolSource` on the existing `ToolSource`/`AggregatingToolRegistry` seam
(`name = "doc_write"`), registered in `ChatController._build_registry` when
`is_doc_write_enabled()` (new resolver in `chat/controller_factory.py`,
`CRUCIBLE_DOC_WRITE_ENABLED`, truthy = `1/true/yes/on`, default OFF).

One tool:

```
write_doc(path: str, content: str)
```

- One file per call. Multi-file output = multiple calls, each gated (decision 1).
- Constructor takes `workspace_path` and `approval_callback: async (path, exists, preview) -> bool`
  (the controller partial-binds thread/channel ids, mirroring `_mcp_approval_cb`).

### 3.2 Validation (before any gate)

Failures return `ToolOutput(is_error=True)` with an actionable message; no gate is raised.

1. Path is workspace-relative; absolute paths and `..` traversal rejected (reuse the
   normalization discipline of `tools/files.py`).
2. Extension in the allowlist (decision 2). Case-insensitive; the FINAL suffix decides
   (`x.tar.gz` → `.gz` → rejected).
3. Content ≤ 1 MB (constant, no env knob — YAGNI).

### 3.3 Gate (mirror of `mcp_tool`, minus remember)

- `PendingGate(kind="doc_write", payload={path, exists, preview})` on the thread via
  `set_controller_gate` — Class-A: renders from `/live`, survives reload.
- `preview`: for an existing file a **capped unified diff** old→new; for a new file the
  **capped content** (same 400-line/24k-char discipline as `_cap_unified_diff`).
- SSE poke `doc_write_requested {path, exists}` — instant render only; `/live` is the
  durable path.
- Resolved by `POST /v1/chat/threads/{thread_id}/doc-decision` with body
  `DocWriteDecision{approve: bool}` (new model in `domain/models.py`) →
  `ChatController.resolve_doc_write` (exact `resolve_mcp` recipe: fire the in-memory
  future; restart-orphan clears the stale gate + breadcrumb; never mutate during await).
- **No remember option** (every write is unique content) — hence no rule store.
- Timeout: reuses `mcp_decision_timeout_sec()`'s pattern with its own env
  `CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC` (default 0 = wait forever; timeout → reject).
- Approve → write to the **real workspace** (`mkdir -p` parents), return success output
  naming the path and byte count. Reject → `is_error` output: "rejected by user — do not
  retry the same write; adapt or ask."
- Breadcrumbs: `✓ Doc written: <path>` / `✗ Doc write rejected: <path>`.

### 3.4 Phase availability (explicit decision)

`write_doc` is available in **both DECIDE and EDIT**. In EDIT it is gated per write and
writes land immediately on the real workspace, independent of the edit session's shadow
(code edits still await the EditGate). This mixed state is accepted: the write is
user-approved at the moment it happens, and restricting the tool per-phase would need new
phase-filter machinery for no safety gain.

### 3.5 Prompt teaching

Flag-gated block in `controller_prompts.py` (pattern of `_MCP_BLOCK`): `write_doc` is for
standalone non-executable artifacts (docs, diagrams, data snapshots) directly from DECIDE;
each call pauses for a user approval card (expected, not an error); code changes still go
through the edit flow. No superiority framing — state when each path shines.

### 3.6 Frontend (the `mcp_tool` checklist, verbatim)

- `chat/models.py` `PendingGate.kind` + `"doc_write"`; editor-client `PendingGateSchema`
  enum; webview `types.ts` `LiveGateView.kind` (the three-enum footgun).
- editor-client: `StreamEvent` + `doc_write_requested`; `DocWriteDecision` interface;
  `postChatDocDecision(threadId, decision)` on `BackendTaskClient` + `HttpBackendClient`.
- Webview: `DocWriteGate.tsx` — title `Write file: <path>`, subtitle "New file" / "Modifies
  existing file", `<pre>` preview block, **Approve / Reject** (one-shot guard); dispatch
  case in `LiveSlot.tsx`; outbound message `{type: "docDecision", threadId, approve}`.
- Extension host: `chat-panel.ts` ctor param at END + dispatch branch; `extension.ts` arg;
  `controller.ts` gate-kind union + SSE poke (`forwardGateWait("doc_write")`, label
  "Waiting for doc write approval…") + `handleDocDecisionFromChat` (chat route only).

## 4. Feature 2 — web search/fetch via shipped MCP defaults

**Zero backend code.** The P3 MCP client is the delivery vehicle.

### 4.1 Vendored server script

`resources/mcp-servers/ollama-web-search.py` — a pinned copy of the first-party script
`github.com/ollama/ollama-python/blob/main/examples/web-search-mcp.py` (source URL +
upstream commit recorded in the file header), with a **PEP-723 inline metadata block**
added (`dependencies = ["mcp", "ollama"]`) so plain `uv run` self-resolves — no venv or
install step. Exposes `web_search(query, max_results≤10)` and `web_fetch(url)`.

### 4.2 Canonical config entry

Documented in CLAUDE.md and written by the future P4 installer as an installed default
(`enabled: true` — a missing key fails that server's connect with a message naming
`OLLAMA_API_KEY`, which is the designed visible-failure UX; nothing else is affected):

```json
"web": {
  "command": "uv",
  "args": ["run", "<repo>/resources/mcp-servers/ollama-web-search.py"],
  "env": { "OLLAMA_API_KEY": "${OLLAMA_API_KEY}" },
  "enabled": true
}
```

Tools surface as `mcp__web__web_search` / `mcp__web__web_fetch` behind the existing
`mcp_tool` gate; "Approve & remember" yields first-use-gate-then-instant (decision 5).
Key: free account, https://ollama.com/settings/keys, exported in the backend env — never
in the file.

### 4.3 Provider swaps are config

Alternative search backends = swap the entry (community SearXNG / Tavily / Brave MCP
servers); documented as one-liners next to the canonical entry. No Python provider seam.

### 4.4 Example file

`.ai-editor/mcp.json.example` snippets live in CLAUDE.md docs (the P4 installer, not this
feature, owns writing real defaults into user workspaces).

## 5. Error handling

| Failure | Behavior |
|---|---|
| Bad extension / traversal / oversize | `is_error` tool output, no gate, model adapts |
| Doc gate rejected | `is_error` "do not retry; adapt or ask" (known repetition-attractor risk on weak models — same mitigation status as MCP reject; `/stop` is the escape) |
| Doc gate timeout | reject path |
| Write IO error after approve | `is_error` output naming the OS error; gate already cleared |
| `OLLAMA_API_KEY` unset | `web` server connect fails, `McpServerStatus.detail` names the var; other servers unaffected |
| Ollama API quota/network error | rides the MCP result path: `isError` → `ToolOutput(is_error=True)` |

## 6. Testing

**Feature 1 (mirrors the P3 suites):**
- `tests/test_doc_write_source.py` — allowlist accept/reject per extension, traversal
  rejection, oversize rejection, approve→file-on-disk, reject→no-file + error output,
  diff-vs-content preview selection.
- `tests/test_controller_doc_gate.py` — gate raise/approve/reject/timeout/orphan-clear +
  breadcrumbs (harness = `test_controller_mcp_gate.py`).
- Flag wiring + `/doc-decision` route test (harness = `test_mcp_flag_wiring.py`).
- Prompt-block presence/absence test.
- editor-client contracts test (`doc_write` kind parses); webview `DocWriteGate` tests
  (render, approve/reject postMessage, one-shot guard).

**Feature 2:** config + vendored script only — covered by existing P3 suites; verification
is a live smoke on shadow-forge (`uv` present, key exported): search turn → gate →
approve & remember → answer with citations; second turn gate-free.

## 7. Exit criteria

1. `write_doc` from a DECIDE turn writes an approved `.md` to the real workspace with a
   rendered gate card, breadcrumb, and transcript record; reject leaves no file.
2. Flag off → no tool, no teaching block, no route behavior change.
3. `mcp__web__web_search` answers a current-events question end-to-end on shadow-forge
   through the existing MCP gate, with remember working across turns.
4. All three stacks green (`pytest`, editor-client, extension + webview).
