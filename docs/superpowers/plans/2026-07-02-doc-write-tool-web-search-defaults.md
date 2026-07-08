# `write_doc` Gated Docs Tool + Web Search MCP Defaults — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the chat controller write non-executable files (docs/diagrams/data) behind a per-write `doc_write` approval gate, and ship web search/fetch as a vendored first-party Ollama MCP server entry — zero new backend transport code.

**Architecture:** Feature 1 clones the just-shipped `mcp_tool` gate recipe end to end: a new `DocWriteToolSource` on the `ToolSource`/`AggregatingToolRegistry` seam → `PendingGate(kind="doc_write")` → `POST /doc-decision` → webview `DocWriteGate` card. Feature 2 vendors `ollama/ollama-python/examples/web-search-mcp.py` into `resources/mcp-servers/` and documents the canonical `mcp.json` entry; the P3 MCP client provides transport, gating, and remember-rules. Spec: `docs/superpowers/specs/2026-07-02-doc-write-tool-web-search-defaults-design.md`.

**Tech Stack:** Python (FastAPI, pydantic), TypeScript (Zod editor-client, VS Code extension, React webview), `uv` + PEP-723 for the vendored script.

## Global Constraints

- **Controller-only.** Planning/task loops untouched (spec decision 6).
- **Flag `CRUCIBLE_DOC_WRITE_ENABLED`, default OFF** (truthy = `1/true/yes/on`). Off: no tool, no teaching block.
- **Allowlist (spec decision 2):** `.md .mmd .mermaid .txt .rst .adoc .svg .json .yaml .yml .csv`, case-insensitive, FINAL suffix decides.
- **Per-write gate, NO remember option** (every write is unique content). Approve → write to the REAL workspace. Content cap 1 MB (constant).
- **Env:** `CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC` (default `0` = wait forever; timeout → reject).
- **Gate payload keys `{path, exists, preview}`** — identical in backend, editor-client, webview.
- **Breadcrumb copy:** `✓ Doc written: <path>` / `✗ Doc write rejected: <path>`. **Card copy:** `Write file: <path>`.
- **Prompt copy:** no superiority framing; the approval pause is expected behavior, not an error.
- **Python env:** `cd services/agentd-py && source .venv/bin/activate` before pytest. Never pipe pytest.
- **TS build order:** after `apps/editor-client` changes run `npm run -w @crucible/editor-client build` BEFORE extension typecheck.
- **Commits:** `type(scope): description`. Do NOT push.

## File Structure

| File | Responsibility |
|---|---|
| `services/agentd-py/agentd/chat/doc_write_source.py` | `DocWriteToolSource`: validation, preview, gate callback, real-workspace write; `doc_write_decision_timeout_sec()` |
| `services/agentd-py/agentd/domain/models.py` | + `DocWriteDecision` |
| `services/agentd-py/agentd/chat/models.py` | + `"doc_write"` in `PendingGate.kind` |
| `services/agentd-py/agentd/chat/controller.py` | + `_doc_approval_cb` / `resolve_doc_write` / registry + loop wiring |
| `services/agentd-py/agentd/chat/controller_factory.py` | + `is_doc_write_enabled` |
| `services/agentd-py/agentd/chat/controller_prompts.py` | + `_DOC_WRITE_BLOCK` teaching block |
| `services/agentd-py/agentd/api/routes.py` | + `POST /chat/threads/{id}/doc-decision` |
| `apps/editor-client/src/contracts/task-contracts.ts` | + gate kind, stream event, `DocWriteDecision`, client method |
| `apps/editor-client/src/client/http-backend-client.ts` | + `postChatDocDecision` |
| `apps/vscode-extension/src/controller.ts` | + gate kind, SSE pokes, `handleDocDecisionFromChat` |
| `apps/vscode-extension/src/chat-panel.ts` | + `docDecision` message → handler (ctor param at END) |
| `apps/vscode-extension/src/extension.ts` | + wire handler arg |
| `apps/vscode-extension/webview-ui/src/types.ts` | + `"doc_write"` kind + `docDecision` outbound message |
| `apps/vscode-extension/webview-ui/src/components/messages/gates/DocWriteGate.tsx` | approval card |
| `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx` | + dispatch case |
| `resources/mcp-servers/ollama-web-search.py` | vendored first-party Ollama web-search MCP server |
| `CLAUDE.md` | docs for both features |

---

### Task 1: `DocWriteToolSource` (validation, preview, gated write)

**Files:**
- Create: `services/agentd-py/agentd/chat/doc_write_source.py`
- Test: `services/agentd-py/tests/test_doc_write_source.py`

**Interfaces:**
- Consumes: `ToolDefinition`/`ToolOutput` (`agentd/tools/registry.py`), `cap_unified_diff` (`agentd/patch/diffing.py`).
- Produces: `DocWriteToolSource(workspace_path, approval_callback)` where `approval_callback: async (path: str, exists: bool, preview: str) -> bool`; `definitions()` exposes ONE tool `write_doc(path, content)`; `owns(tool) == (tool == "write_doc")`; `doc_write_decision_timeout_sec() -> float`; `DOC_WRITE_ALLOWED_EXTENSIONS: frozenset[str]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_doc_write_source.py`:

```python
"""DocWriteToolSource: allowlisted, per-write-gated writes of non-executable files
(docs/diagrams/data) to the REAL workspace — the lightweight alternative to EDIT mode."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.chat.doc_write_source import (
    DOC_WRITE_ALLOWED_EXTENSIONS,
    DocWriteToolSource,
    doc_write_decision_timeout_sec,
)


class _Recorder:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[str, bool, str]] = []

    async def __call__(self, path: str, exists: bool, preview: str) -> bool:
        self.calls.append((path, exists, preview))
        return self.result


def _src(tmp_path: Path, cb) -> DocWriteToolSource:
    return DocWriteToolSource(tmp_path, cb)


def test_definitions_and_owns(tmp_path: Path):
    src = _src(tmp_path, _Recorder())
    defs = src.definitions()
    assert [d.name for d in defs] == ["write_doc"]
    assert set(defs[0].parameters["required"]) == {"path", "content"}
    assert src.owns("write_doc") is True
    assert src.owns("read_file") is False


@pytest.mark.asyncio
async def test_approved_write_lands_in_real_workspace(tmp_path: Path):
    cb = _Recorder(result=True)
    out = await _src(tmp_path, cb).execute(
        "write_doc", {"path": "docs/notes.md", "content": "# hi\n"})
    assert out.is_error is False and "docs/notes.md" in out.output
    assert (tmp_path / "docs" / "notes.md").read_text(encoding="utf-8") == "# hi\n"
    (path, exists, preview) = cb.calls[0]
    assert path == "docs/notes.md" and exists is False and "# hi" in preview


@pytest.mark.asyncio
async def test_rejected_write_leaves_no_file(tmp_path: Path):
    out = await _src(tmp_path, _Recorder(result=False)).execute(
        "write_doc", {"path": "a.md", "content": "x"})
    assert out.is_error is True and "rejected" in out.output
    assert not (tmp_path / "a.md").exists()


@pytest.mark.asyncio
async def test_existing_file_gets_unified_diff_preview(tmp_path: Path):
    (tmp_path / "a.md").write_text("old line\n", encoding="utf-8")
    cb = _Recorder(result=True)
    await _src(tmp_path, cb).execute("write_doc", {"path": "a.md", "content": "new line\n"})
    (_, exists, preview) = cb.calls[0]
    assert exists is True
    assert "-old line" in preview and "+new line" in preview
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "new line\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["main.py", "run.sh", "x.tar.gz", "Makefile", "a.md.exe"])
async def test_disallowed_extensions_error_without_gate(tmp_path: Path, bad):
    cb = _Recorder()
    out = await _src(tmp_path, cb).execute("write_doc", {"path": bad, "content": "x"})
    assert out.is_error is True and "extension" in out.output.lower()
    assert cb.calls == []  # no gate raised


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", ["a.MD", "d/e.mermaid", "x.yaml", "x.yml", "x.csv", "x.svg"])
async def test_allowlist_is_case_insensitive_and_covers_data(tmp_path: Path, ok):
    out = await _src(tmp_path, _Recorder()).execute("write_doc", {"path": ok, "content": "x"})
    assert out.is_error is False


@pytest.mark.asyncio
@pytest.mark.parametrize("evil", ["../escape.md", "/etc/pwn.md"])
async def test_traversal_and_absolute_paths_rejected(tmp_path: Path, evil):
    cb = _Recorder()
    out = await _src(tmp_path, cb).execute("write_doc", {"path": evil, "content": "x"})
    assert out.is_error is True
    assert cb.calls == []
    assert not (tmp_path.parent / "escape.md").exists()


@pytest.mark.asyncio
async def test_oversize_content_rejected(tmp_path: Path):
    cb = _Recorder()
    out = await _src(tmp_path, cb).execute(
        "write_doc", {"path": "big.md", "content": "x" * (1_048_576 + 1)})
    assert out.is_error is True and "1 MB" in out.output
    assert cb.calls == []


def test_timeout_env_default_and_override(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", raising=False)
    assert doc_write_decision_timeout_sec() == 0.0
    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", "3.5")
    assert doc_write_decision_timeout_sec() == 3.5


def test_allowlist_constant_matches_spec():
    assert DOC_WRITE_ALLOWED_EXTENSIONS == frozenset({
        ".md", ".mmd", ".mermaid", ".txt", ".rst", ".adoc",
        ".svg", ".json", ".yaml", ".yml", ".csv"})
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_doc_write_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.chat.doc_write_source'`

- [ ] **Step 3: Implement**

Create `agentd/chat/doc_write_source.py`:

```python
"""DocWriteToolSource — per-write-gated writes of non-executable files.

The lightweight alternative to EDIT mode for standalone artifacts (docs, diagrams,
data): one `write_doc(path, content)` tool, extension-allowlisted, every call pauses
for a live doc_write approval card, approve lands the file directly in the REAL
workspace. Validation failures return is_error output WITHOUT raising a gate, so the
model can self-correct cheaply. No remember-rule store: every write is unique content.
"""
from __future__ import annotations

import difflib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from agentd.patch.diffing import cap_unified_diff
from agentd.tools.registry import ToolDefinition, ToolOutput

DOC_WRITE_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".mmd", ".mermaid", ".txt", ".rst", ".adoc",
    ".svg", ".json", ".yaml", ".yml", ".csv",
})

_MAX_CONTENT_BYTES = 1_048_576  # 1 MB — standalone docs, not bulk data dumps

ApprovalCallback = Callable[[str, bool, str], Awaitable[bool]]


def doc_write_decision_timeout_sec() -> float:
    """0 = wait forever (mirrors CRUCIBLE_MCP_DECISION_TIMEOUT_SEC)."""
    raw = os.getenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", "").strip()
    try:
        val = float(raw)
    except ValueError:
        return 0.0
    return val if val >= 0 else 0.0


class DocWriteToolSource:
    name = "doc_write"

    def __init__(self, workspace_path: str | Path, approval_callback: ApprovalCallback) -> None:
        self._workspace = Path(workspace_path)
        self._approve = approval_callback

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="write_doc",
            description=(
                "Write ONE standalone non-executable file (docs, diagrams, data: "
                + ", ".join(sorted(DOC_WRITE_ALLOWED_EXTENSIONS))
                + ") directly to the workspace. Each call pauses for a user approval "
                "card showing the path and a preview/diff — that pause is expected. "
                "For source-code changes use the edit flow instead."),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Workspace-relative file path"},
                    "content": {"type": "string",
                                "description": "Full file content (replaces any existing)"},
                },
                "required": ["path", "content"],
            },
        )]

    def owns(self, tool: str) -> bool:
        return tool == "write_doc"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        rel = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        if not rel:
            return ToolOutput(output="Error: write_doc requires a non-empty path", is_error=True)
        if Path(rel).is_absolute():
            return ToolOutput(
                output=f"Error: path must be workspace-relative, got absolute '{rel}'",
                is_error=True)
        suffix = Path(rel).suffix.lower()
        if suffix not in DOC_WRITE_ALLOWED_EXTENSIONS:
            return ToolOutput(
                output=(f"Error: extension '{suffix or '(none)'}' is not writable via "
                        f"write_doc (allowed: {', '.join(sorted(DOC_WRITE_ALLOWED_EXTENSIONS))}). "
                        "Use the edit flow for code files."),
                is_error=True)
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            return ToolOutput(
                output="Error: content exceeds the 1 MB write_doc limit — split the file "
                       "or use the edit flow.",
                is_error=True)
        target = (self._workspace / rel).resolve()
        try:
            target.relative_to(self._workspace.resolve())
        except ValueError:
            return ToolOutput(
                output=f"Error: path traversal rejected — '{rel}' is outside the workspace",
                is_error=True)

        exists = target.is_file()
        preview = self._preview(target, rel, content, exists)
        approved = await self._approve(rel, exists, preview)
        if not approved:
            return ToolOutput(
                output=(f"Doc write rejected by user: {rel}. Do not retry the same "
                        "write — adapt your approach or ask."),
                is_error=True)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolOutput(output=f"Error: writing {rel} failed: {exc}", is_error=True)
        return ToolOutput(output=f"Wrote {rel} ({len(content.encode('utf-8'))} bytes)")

    @staticmethod
    def _preview(target: Path, rel: str, content: str, exists: bool) -> str:
        """Existing file → capped unified diff; new file → capped content."""
        if not exists:
            return cap_unified_diff(content)
        old = target.read_text(encoding="utf-8", errors="replace")
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        return cap_unified_diff(diff)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_doc_write_source.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/doc_write_source.py tests/test_doc_write_source.py
git commit -m "feat(chat): DocWriteToolSource — allowlisted, per-write-gated doc writes"
```

---

### Task 2: `DocWriteDecision` + the `doc_write` gate in `ChatController`

**Files:**
- Modify: `services/agentd-py/agentd/domain/models.py` (directly after `class McpToolDecision`)
- Modify: `services/agentd-py/agentd/chat/models.py` (kind Literal, currently `... "clarify", "mcp_tool"`)
- Modify: `services/agentd-py/agentd/chat/controller.py` (`__init__` future-map, `_build_registry`, `_run_loop`, methods after `resolve_mcp` ~line 811)
- Test: `services/agentd-py/tests/test_controller_doc_gate.py`

**Interfaces:**
- Consumes: `DocWriteToolSource` + `doc_write_decision_timeout_sec` (Task 1), gate machinery (`PendingGate`, `set_controller_gate`, `_write_breadcrumb`), `is_doc_write_enabled` (Task 3 — controller references it via `controller_factory`, add the import now, function lands in Task 3; to keep Task 2 self-contained the registry gating checks `doc_approval_cb is not None` AND the factory flag lazily).
- Produces: `DocWriteDecision(approve: bool)` in `domain/models.py`; `async _doc_approval_cb(thread_id, channel_id, path, exists, preview) -> bool`; `async resolve_doc_write(thread_id, decision) -> bool`; `_build_registry(..., doc_approval_cb=None)`.

**Ordering note:** implement Task 3's `is_doc_write_enabled` (a 4-line function) as PART of this task's Step 3 so the registry wiring compiles and tests pass — Task 3 then covers its flag-parsing tests and the rest of the wiring. (One function, two tasks would otherwise deadlock.)

- [ ] **Step 1: Write failing tests**

Create `tests/test_controller_doc_gate.py` (harness mirrors `tests/test_controller_mcp_gate.py`):

```python
"""doc_write gate: write_doc calls pause for live approval — mirror of the mcp_tool
gate on the same thread-gate machinery, minus the remember option (spec §3.3)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import DocWriteDecision
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _controller(tmp_path, store, broadcaster=None):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=broadcaster or EventBroadcaster(), retrieval_client=None)


@pytest.mark.asyncio
async def test_gate_raised_then_approve_resolves(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._doc_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "docs/a.md", False, "# preview"))
    await asyncio.sleep(0)
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "doc_write"
    assert gate.payload == {"path": "docs/a.md", "exists": False, "preview": "# preview"}

    assert await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=True)) is True
    assert await cb_task is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None  # cleared in place


@pytest.mark.asyncio
async def test_reject_returns_false_with_breadcrumb(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._doc_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "a.md", True, "diff"))
    await asyncio.sleep(0)
    await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=False))
    assert await cb_task is False
    texts = [m.content for m in store.get_thread(th.thread_id).messages]
    assert any("✗ Doc write rejected: a.md" in t for t in texts)


@pytest.mark.asyncio
async def test_broadcasts_doc_write_requested_poke(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    bc = EventBroadcaster()
    ctrl = _controller(tmp_path, store, broadcaster=bc)
    cid = f"chat:{th.thread_id}"
    q = bc.subscribe(cid)
    cb_task = asyncio.create_task(ctrl._doc_approval_cb(th.thread_id, cid, "a.md", False, "p"))
    await asyncio.sleep(0)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    poke = [e for e in events if e["type"] == "doc_write_requested"]
    assert poke and poke[0]["payload"] == {"path": "a.md", "exists": False}
    await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=False))
    await cb_task


@pytest.mark.asyncio
async def test_timeout_rejects(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", "0.05")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl._doc_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "a.md", False, "p") is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_resolve_no_pending_returns_false_and_clears_orphan(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=True)) is False
    store.set_controller_gate(
        th.thread_id, PendingGate(kind="doc_write", payload={"path": "x.md"}))
    assert await ctrl.resolve_doc_write(th.thread_id, DocWriteDecision(approve=True)) is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_registry_includes_write_doc_only_when_flag_on(tmp_path: Path, monkeypatch):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)

    async def _cb(path, exists, preview):
        return True

    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_ENABLED", "1")
    names = [d.name for d in ctrl._build_registry(doc_approval_cb=_cb).definitions()]
    assert "write_doc" in names
    monkeypatch.delenv("CRUCIBLE_DOC_WRITE_ENABLED", raising=False)
    names_off = [d.name for d in ctrl._build_registry(doc_approval_cb=_cb).definitions()]
    assert "write_doc" not in names_off
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_controller_doc_gate.py -q`
Expected: FAIL — `ImportError: cannot import name 'DocWriteDecision'`

- [ ] **Step 3: Implement**

In `agentd/domain/models.py`, directly after `class McpToolDecision`:

```python
class DocWriteDecision(BaseModel):
    """User decision on a doc_write approval gate (chat controller). No remember
    option — every write is unique content (spec §3.3)."""
    approve: bool
```

In `agentd/chat/models.py`, add `"doc_write"` to the kind Literal:

```python
    kind: Literal["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool", "doc_write"]
```

In `agentd/chat/controller_factory.py`, after `is_mcp_enabled` (moved here from Task 3 to keep this task compiling — Task 3 adds its tests):

```python
def is_doc_write_enabled() -> bool:
    """Whether the controller offers write_doc (per-write-gated doc/data writes).
    Default OFF. Opt in with CRUCIBLE_DOC_WRITE_ENABLED=1."""
    return os.getenv("CRUCIBLE_DOC_WRITE_ENABLED", "0").strip().lower() in _TRUTHY
```

In `agentd/chat/controller.py`:

1. Imports: add `DocWriteDecision` to the `from agentd.domain.models import ...` line; add `is_doc_write_enabled` to the `from agentd.chat.controller_factory import ...` line (grep for `is_skills_enabled` — same import).
2. `__init__`, next to `self._pending_mcp = ...` (line ~140):

```python
        # thread_id → future for the in-flight doc_write gate; same lifecycle as
        # _pending_mcp.
        self._pending_doc: dict[str, asyncio.Future[DocWriteDecision]] = {}
```

3. `_build_registry`: add parameter `doc_approval_cb: object | None = None,` after `mcp_approval_cb`; before `return AggregatingToolRegistry(sources)`:

```python
        if is_doc_write_enabled() and doc_approval_cb is not None:
            from agentd.chat.doc_write_source import DocWriteToolSource

            sources.append(DocWriteToolSource(self._workspace_path, doc_approval_cb))
```

4. `_run_loop`, next to `mcp_cb = partial(...)` (line ~339): add
   `doc_cb = partial(self._doc_approval_cb, thread_id, channel_id)` and pass
   `doc_approval_cb=doc_cb` in the `self._build_registry(...)` call.

5. After `resolve_mcp` (~line 811), the pair (exact mirror of the mcp pair — gate
   clears in place in the `finally`; the decision route only `future.set_result`s):

```python
    async def _doc_approval_cb(
        self, thread_id: str, channel_id: str,
        path: str, exists: bool, preview: str,
    ) -> bool:
        """Gate a write_doc call (mirror of _mcp_approval_cb, minus remember-rules —
        every write is unique content). Raises a durable kind="doc_write" gate and
        awaits /doc-decision."""
        from agentd.chat.doc_write_source import doc_write_decision_timeout_sec

        self._store.set_controller_gate(thread_id, PendingGate(
            kind="doc_write",
            payload={"path": path, "exists": exists, "preview": preview}))
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[DocWriteDecision] = loop.create_future()
        self._pending_doc[thread_id] = fut
        # Instant-render poke — the card still renders FROM /live (durable on reload).
        self._broadcaster.broadcast(channel_id, {
            "type": "doc_write_requested",
            "payload": {"path": path, "exists": exists},
        })
        timeout = doc_write_decision_timeout_sec()
        try:
            decision = await (asyncio.wait_for(fut, timeout) if timeout > 0 else fut)
        except (TimeoutError, asyncio.TimeoutError):
            decision = DocWriteDecision(approve=False)
        finally:
            self._pending_doc.pop(thread_id, None)
            self._store.set_controller_gate(thread_id, None)

        self._write_breadcrumb(
            thread_id, channel_id,
            f"✓ Doc written: {path}" if decision.approve
            else f"✗ Doc write rejected: {path}")
        return decision.approve

    async def resolve_doc_write(self, thread_id: str, decision: DocWriteDecision) -> bool:
        """Resolve the doc_write gate (POST /doc-decision). Fires the live waiter;
        never mutates/persists during the await (Class-A). Restart orphan clears the
        stale gate + breadcrumb — mirrors resolve_mcp."""
        fut = self._pending_doc.get(thread_id)
        if fut is None or fut.done():
            thread = self._store.get_thread(thread_id)
            gate = thread.pending_controller_gate if thread is not None else None
            if gate is not None and gate.kind == "doc_write":
                self._store.set_controller_gate(thread_id, None)
                self._write_breadcrumb(
                    thread_id, f"chat:{thread_id}",
                    "Previous turn ended — please re-send your request.")
            return False
        fut.set_result(decision)
        return True
```

- [ ] **Step 4: Run tests (new + neighbors)**

Run: `pytest tests/test_controller_doc_gate.py tests/test_controller_mcp_gate.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add agentd/domain/models.py agentd/chat/models.py agentd/chat/controller.py agentd/chat/controller_factory.py tests/test_controller_doc_gate.py
git commit -m "feat(chat): doc_write approval gate in ChatController + registry wiring"
```

---

### Task 3: flag tests, `/doc-decision` route, teaching block

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py` (import block; new route after `post_chat_mcp_decision` ~line 1472)
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (`_DOC_WRITE_BLOCK` after `_MCP_BLOCK`; append logic after the `mcp__` detection branch in `format_controller_system_prompt`)
- Test: `services/agentd-py/tests/test_doc_write_wiring.py`

**Interfaces:**
- Consumes: `is_doc_write_enabled` (landed in Task 2), `resolve_doc_write`/`DocWriteDecision` (Task 2), `format_controller_system_prompt(tool_definitions, *, ...)`.
- Produces: `POST /v1/chat/threads/{thread_id}/doc-decision` → `{"ok": bool}`; `_DOC_WRITE_BLOCK` auto-appends when any tool definition is named `write_doc` (no new prompt parameter — the `_MCP_BLOCK` detection pattern).

- [ ] **Step 1: Write failing tests**

Create `tests/test_doc_write_wiring.py`:

```python
"""CRUCIBLE_DOC_WRITE_ENABLED parsing; POST /doc-decision routes to resolve_doc_write;
the write_doc teaching block appends iff the tool is present in tool_definitions."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller_factory import is_doc_write_enabled
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_DOC_WRITE_ENABLED", raising=False)
    assert is_doc_write_enabled() is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False),
])
def test_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("CRUCIBLE_DOC_WRITE_ENABLED", raw)
    assert is_doc_write_enabled() is expected


class _StubChatHandler:
    def __init__(self):
        self.calls = []
        self._store = None
        self._broadcaster = None

    async def resolve_doc_write(self, thread_id, decision):
        self.calls.append((thread_id, decision))
        return True


@pytest.mark.asyncio
async def test_doc_decision_route(tmp_path: Path):
    stub = _StubChatHandler()
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=stub))
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post("/v1/chat/threads/th1/doc-decision",
                                 json={"approve": True})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    (thread_id, decision), = stub.calls
    assert thread_id == "th1" and decision.approve is True


_BASE = [{"name": "read_file", "description": "d", "parameters": {}}]
_DOC = [{"name": "write_doc", "description": "d", "parameters": {}}]


def _prompt(defs):
    return format_controller_system_prompt(
        defs, task_subsystem_enabled=False, memory_enabled=False)


def test_block_absent_without_write_doc():
    assert "WRITING DOCS" not in _prompt(_BASE)


def test_block_present_with_write_doc():
    text = _prompt(_BASE + _DOC)
    assert "WRITING DOCS" in text
    assert "approval" in text
    # No superiority framing after the block header.
    assert "instead of" not in text.split("WRITING DOCS")[1].lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_doc_write_wiring.py -q`
Expected: FAIL — 404 on the route test and `AssertionError` on `test_block_present_with_write_doc` (flag tests pass — the function landed in Task 2).

- [ ] **Step 3: Implement route + block**

In `agentd/api/routes.py`:
1. Add `DocWriteDecision` to the `from agentd.domain.models import ...` block.
2. After the `post_chat_mcp_decision` route:

```python
        @router.post("/chat/threads/{thread_id}/doc-decision")
        async def post_chat_doc_decision(
            thread_id: str, request: DocWriteDecision,
        ) -> dict:
            # Resolves the held-open doc_write gate; continuation rides the open
            # message SSE stream — plain JSON ack, mirrors /mcp-decision.
            resolve = getattr(_chat_agent, "resolve_doc_write", None)
            if resolve is None:
                return {"ok": False}
            ok = await resolve(thread_id, request)  # type: ignore[misc]
            return {"ok": ok}
```

In `agentd/chat/controller_prompts.py`, after `_MCP_BLOCK`:

```python
_DOC_WRITE_BLOCK = """

WRITING DOCS
The `write_doc` tool writes ONE standalone non-executable file (markdown, mermaid,
plain text, rst, adoc, svg, json, yaml, csv) directly to the workspace — suited to
READMEs, design notes, diagrams, and data snapshots the user asked for. The edit
flow (propose_mode → edit) shines for source-code changes and multi-file work.
- Each write_doc call pauses the turn for a user approval card showing the path and
  a preview/diff. That pause is expected behavior, not an error — wait for it.
- If the user rejects a write, do not silently retry the same write; adapt the
  content or ask what they want instead.
"""
```

In `format_controller_system_prompt`, directly after the `mcp__` detection branch:

```python
    # write_doc teaching block: keyed off the merged tool definitions (same pattern
    # as the MCP block) so no separate flag parameter is needed.
    if any(str((d or {}).get("name", "")) == "write_doc"
           for d in tool_definitions if isinstance(d, dict)):
        base += _DOC_WRITE_BLOCK
```

- [ ] **Step 4: Run tests + backend suite + lint**

```bash
pytest tests/test_doc_write_wiring.py -q
pytest -q
ruff check agentd/chat/doc_write_source.py agentd/chat/controller.py agentd/chat/controller_factory.py agentd/chat/controller_prompts.py agentd/api/routes.py
mypy agentd/chat/doc_write_source.py
```
Expected: new tests PASS; full suite green; no NEW ruff/mypy findings (pre-existing routes.py/main.py noise is documented).

- [ ] **Step 5: Commit**

```bash
git add agentd/api/routes.py agentd/chat/controller_prompts.py tests/test_doc_write_wiring.py
git commit -m "feat(chat): doc-decision route + write_doc teaching block"
```

---

### Task 4: editor-client contracts + client method

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`PendingGateSchema` kind enum ~line 256; `StreamEvent` union after `mcp_approval_requested` ~line 180; `DocWriteDecision` after `McpToolDecision` ~line 160; `BackendTaskClient` after `postChatMcpDecision`)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (import + method after `postChatMcpDecision` ~line 252)
- Test: `apps/editor-client/test/doc-write-gate.test.ts`

**Interfaces:**
- Produces: `"doc_write"` in `PendingGateSchema.kind`; `{ type: "doc_write_requested"; payload: { path: string; exists: boolean } }` in `StreamEvent`; `export interface DocWriteDecision { approve: boolean }`; `postChatDocDecision(threadId: string, decision: DocWriteDecision): Promise<void>` on `BackendTaskClient` + `HttpBackendClient`.

- [ ] **Step 1: Write the failing test**

Create `apps/editor-client/test/doc-write-gate.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { PendingGateSchema } from "../src/contracts/task-contracts";

describe("doc_write gate contract", () => {
  it("parses a kind=doc_write pending gate (a kind missing from the Zod enum makes the /live parse throw and the gate silently never renders)", () => {
    const gate = PendingGateSchema.parse({
      kind: "doc_write",
      payload: { path: "docs/a.md", exists: false, preview: "# hi" },
    });
    expect(gate.kind).toBe("doc_write");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @crucible/editor-client test`
Expected: FAIL — invalid enum value

- [ ] **Step 3: Implement**

1. Kind enum: `z.enum([..., "clarify", "mcp_tool", "doc_write"])`.
2. `StreamEvent`, after the `mcp_approval_requested` member:
   ```typescript
   | { type: "doc_write_requested"; payload: { path: string; exists: boolean } }
   ```
3. After `McpToolDecision`:
   ```typescript
   // User decision on a doc_write approval gate (chat controller). No remember —
   // every write is unique content.
   export interface DocWriteDecision {
     approve: boolean;
   }
   ```
4. `BackendTaskClient`, after `postChatMcpDecision`:
   ```typescript
   // Controller doc_write gate: a plain JSON ack (continuation rides the open message stream).
   postChatDocDecision(threadId: string, decision: DocWriteDecision): Promise<void>;
   ```
5. `http-backend-client.ts`: add `type DocWriteDecision,` to the contracts import; after `postChatMcpDecision`:
   ```typescript
   // Controller doc_write gate: a plain JSON ack — mirrors postChatMcpDecision.
   async postChatDocDecision(
     threadId: string,
     decision: DocWriteDecision
   ): Promise<void> {
     await this.fetchJson(
       `/v1/chat/threads/${encodeURIComponent(threadId)}/doc-decision`,
       { method: "POST", body: JSON.stringify({ approve: decision.approve }) }
     );
   }
   ```

- [ ] **Step 4: Test + build**

```bash
npm run -w @crucible/editor-client test
npm run -w @crucible/editor-client build
```
Expected: tests PASS; build clean (REQUIRED before Task 5's typecheck).

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client
git commit -m "feat(chat): editor-client contracts — doc_write gate kind, decision method, stream event"
```

---

### Task 5: extension host + webview `DocWriteGate` card

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts` (gate-kind union ~line 91; both SSE handlers after their `mcp_approval_requested` branches; `forwardGateWait` union+label; method after `handleMcpDecisionFromChat`)
- Modify: `apps/vscode-extension/src/chat-panel.ts` (handler type; ctor param at END after `onMcpDecision`; dispatch branch after `mcpDecision`)
- Modify: `apps/vscode-extension/src/extension.ts` (append arg at END of `new ChatPanel(...)`)
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (`LiveGateView.kind`; outbound message union after `mcpDecision`)
- Create: `apps/vscode-extension/webview-ui/src/components/messages/gates/DocWriteGate.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx` (import + case)
- Test: append to `apps/vscode-extension/webview-ui/src/test/gates.test.tsx`

**Interfaces:**
- Consumes: `postChatDocDecision` + `DocWriteDecision` (Task 4), gate payload `{path, exists, preview}` (Task 2).
- Produces: webview message `{ type: "docDecision", threadId, approve }`; `AiEditorController.handleDocDecisionFromChat(threadId, decision)`.

- [ ] **Step 1: Write the failing webview tests**

Append to `gates.test.tsx` (the file's mock variable is `postMessage`; add the import next to `McpGate`):

```tsx
// ── DocWriteGate ─────────────────────────────────────────────────────────────

describe("DocWriteGate", () => {
  it("renders path + preview and posts docDecision on approve", () => {
    render(
      <DocWriteGate
        taskId="th1"
        payload={{ path: "docs/plan.md", exists: false, preview: "# Plan" }}
      />
    );
    expect(screen.getByText(/Write file: docs\/plan\.md/)).toBeTruthy();
    expect(screen.getByText(/New file/)).toBeTruthy();
    expect(screen.getByText(/# Plan/)).toBeTruthy();
    fireEvent.click(screen.getByText("Approve"));
    expect(postMessage).toHaveBeenCalledWith({
      type: "docDecision", threadId: "th1", approve: true,
    });
  });

  it("existing file shows modify subtitle and reject posts approve=false", () => {
    render(<DocWriteGate taskId="th1" payload={{ path: "a.md", exists: true, preview: "-x\n+y" }} />);
    expect(screen.getByText(/Modifies existing file/)).toBeTruthy();
    fireEvent.click(screen.getByText("Reject"));
    expect(postMessage).toHaveBeenCalledWith({
      type: "docDecision", threadId: "th1", approve: false,
    });
  });

  it("one-shot guard: buttons disappear after resolve", () => {
    render(<DocWriteGate taskId="th1" payload={{ path: "a.md", exists: false, preview: "p" }} />);
    fireEvent.click(screen.getByText("Reject"));
    expect(screen.queryByText("Approve")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @ai-editor/vscode-extension webview:test`
Expected: FAIL — cannot resolve `DocWriteGate`

- [ ] **Step 3: Implement webview side**

`webview-ui/src/types.ts`:
1. Kind union: append `| "doc_write"` to `LiveGateView.kind`.
2. Outbound message union, after the `mcpDecision` member:
   ```typescript
   // Controller doc_write gate: approve/reject a write_doc file write (threadId — no task)
   | { type: "docDecision"; threadId: string; approve: boolean }
   ```

Create `webview-ui/src/components/messages/gates/DocWriteGate.tsx`:

```tsx
import { useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnDanger, BtnPrimary } from "../../shared/buttons";

interface Props {
  /** Carries the threadId (controller gates have no task — LiveSlot passes activeTaskId ?? threadId). */
  taskId: string;
  payload: Record<string, unknown>;
}

/**
 * DocWriteGate — approval card for a write_doc file write (kind="doc_write").
 * Shows the target path and a content preview (new file) or unified diff (existing).
 * No remember option — every write is unique content.
 */
export function DocWriteGate({ taskId, payload }: Props) {
  const path = String(payload.path ?? "");
  const exists = payload.exists === true;
  const preview = String(payload.preview ?? "");
  const [resolved, setResolved] = useState<string | null>(null);

  function submit(approve: boolean) {
    if (resolved !== null) return; // one-shot guard
    setResolved(approve ? "Approved" : "Rejected");
    vscode.postMessage({ type: "docDecision", threadId: taskId, approve });
  }

  return (
    <CardShell
      icon="file"
      title={`Write file: ${path}`}
      subtitle={exists ? "Modifies existing file" : "New file"}
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      <pre className="max-h-40 overflow-auto px-2.5 py-2 text-[11px] text-text-2 border-t border-border whitespace-pre-wrap">
        {preview}
      </pre>
      {resolved === null ? (
        <div className="flex flex-wrap items-center gap-1.5 px-2.5 py-2 border-t border-border">
          <BtnPrimary onClick={() => submit(true)}>Approve</BtnPrimary>
          <BtnDanger onClick={() => submit(false)}>Reject</BtnDanger>
        </div>
      ) : (
        <div className="px-2.5 py-2 text-[12px] text-text-3 border-t border-border">{resolved}</div>
      )}
    </CardShell>
  );
}
```

`LiveSlot.tsx` — import next to `McpGate` and dispatch case:

```tsx
import { DocWriteGate } from "./messages/gates/DocWriteGate";
// ... in the GateDispatch switch:
    case "doc_write":
      return <DocWriteGate taskId={taskId} payload={payload} />;
```

- [ ] **Step 4: Implement extension-host side**

`src/controller.ts`:
1. `LiveGateView.kind` union: append `| "doc_write"`.
2. Import `DocWriteDecision` from `@crucible/editor-client` next to `McpToolDecision`.
3. BOTH SSE handlers, after their `mcp_approval_requested` branches:
   ```typescript
        } else if (event.type === "doc_write_requested") {
          this.forwardGateWait("doc_write");
   ```
4. `forwardGateWait`: parameter union `"scope" | "validation" | "command" | "mcp_tool" | "doc_write"`; label chain gains `: kind === "doc_write" ? "Waiting for doc write approval…"` before the command fallback.
5. After `handleMcpDecisionFromChat`:
   ```typescript
  async handleDocDecisionFromChat(
    threadId: string,
    decision: DocWriteDecision
  ): Promise<void> {
    try {
      // doc_write gates are controller-only (no task path) — always the chat route.
      await this.clientForChat().postChatDocDecision(threadId, decision);
    } catch (err) {
      if (this.isBenignConflict(err)) return;
      this.ui.showError(
        `Failed to send doc decision: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  }
   ```

`src/chat-panel.ts`:
1. Import `DocWriteDecision` alongside `McpToolDecision`; handler type after `McpDecisionHandler`:
   ```typescript
   export type DocDecisionHandler = (threadId: string, decision: DocWriteDecision) => Promise<void>;
   ```
2. Ctor param at the very END (after `onMcpDecision`):
   ```typescript
    private readonly onDocDecision: DocDecisionHandler = async () => {}
   ```
3. Dispatch, after the `mcpDecision` branch:
   ```typescript
      } else if (m["type"] === "docDecision") {
        p = this.onDocDecision(m["threadId"] as string, {
          approve: m["approve"] === true,
        });
   ```

`src/extension.ts` — append at the END of the `new ChatPanel(...)` args (after the mcp handler):
```typescript
    (threadId, decision) => controller.handleDocDecisionFromChat(threadId, decision)
```

- [ ] **Step 5: Build + test + typecheck**

```bash
npm run build
npm run test
npm run typecheck
```
Expected: all green (root build runs the webview Vite build via prebuild — the stale-dist footgun).

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension
git commit -m "feat(chat): DocWriteGate approval card + docDecision plumbing through extension host"
```

---

### Task 6: vendored Ollama web-search MCP server + docs + full verification

**Files:**
- Create: `resources/mcp-servers/ollama-web-search.py`
- Modify: `CLAUDE.md` (new `#### write_doc` bullet in the chat-interface section after the MCP client section; web-defaults bullet inside the MCP client section; two env vars in the Core list)

**Interfaces:**
- Consumes: nothing from earlier tasks (config/docs only).
- Produces: the canonical `mcp.json` `"web"` entry users/P4 copy.

- [ ] **Step 1: Vendor the script**

Create `resources/mcp-servers/ollama-web-search.py` — a verbatim copy of
`https://github.com/ollama/ollama-python/blob/main/examples/web-search-mcp.py`
(first-party; it already carries a PEP-723 inline-deps block `["mcp", "rich", "ollama"]`),
with this provenance header inserted directly under the PEP-723 block:

```python
# Vendored from https://github.com/ollama/ollama-python/blob/main/examples/web-search-mcp.py
# (first-party Ollama example; fetched 2026-07-02). Exposes web_search(query,
# max_results=3) and web_fetch(url) over stdio using Ollama's hosted search API.
# Requires OLLAMA_API_KEY (free key: https://ollama.com/settings/keys).
# Run: uv run resources/mcp-servers/ollama-web-search.py
```

Fetch the current upstream content at implementation time (do NOT trust a stale copy):
```bash
curl -sf https://raw.githubusercontent.com/ollama/ollama-python/main/examples/web-search-mcp.py -o resources/mcp-servers/ollama-web-search.py
head -20 resources/mcp-servers/ollama-web-search.py   # verify PEP-723 block survived
```
Then insert the provenance header under the `# ///` closing line.

- [ ] **Step 2: Verify it serves (needs `uv`; key optional for list_tools)**

```bash
cd services/agentd-py && source .venv/bin/activate && cd ../..
python3 - <<'EOF'
import asyncio
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.models import McpServerConfig

class L:
    def load(self):
        return [McpServerConfig(name="web", transport="stdio", command="uv",
                args=["run", "resources/mcp-servers/ollama-web-search.py"], enabled=True)]

async def main():
    mgr = McpConnectionManager(L())
    await mgr.start()
    print([d.name for d in mgr.tool_definitions()])
    await mgr.shutdown()

asyncio.run(main())
EOF
```
Expected: `['mcp__web__web_search', 'mcp__web__web_fetch']` (tool listing needs no API key; only calls do). If `uv` is missing, STOP and flag to the human.

- [ ] **Step 3: Document in CLAUDE.md**

Append to the **MCP client (P3)** section's bullet list:

```markdown
- **Shipped web-search default (2026-07-02):** `resources/mcp-servers/ollama-web-search.py`
  (vendored first-party `ollama-python` example, PEP-723 deps, run via `uv run`) exposes
  `web_search`/`web_fetch` from Ollama's hosted API. Canonical `.crucible/mcp.json` entry —
  the P4 installer will write it as a default:
  `"web": {"command": "uv", "args": ["run", "<repo>/resources/mcp-servers/ollama-web-search.py"], "env": {"OLLAMA_API_KEY": "${OLLAMA_API_KEY}"}, "enabled": true}`.
  Key: free, https://ollama.com/settings/keys, exported in the backend env. Missing key →
  that server's connect fails naming the var; everything else unaffected. Provider swaps are
  config (community SearXNG/Tavily/Brave MCP servers), not code. Spec/plan:
  `docs/superpowers/specs|plans/2026-07-02-doc-write-tool-web-search-defaults*`.
```

Add a new subsection AFTER the MCP client section, matching its style:

```markdown
#### write_doc (gated docs/data writes from chat)

One-tool lightweight write path for standalone non-executable artifacts — the alternative
to full EDIT mode for READMEs/diagrams/data. Flag-gated, **default OFF**
(`CRUCIBLE_DOC_WRITE_ENABLED`), **controller-only**. Spec/plan:
`docs/superpowers/specs|plans/2026-07-02-doc-write-tool-web-search-defaults*`.

- **Tool (`agentd/chat/doc_write_source.py::DocWriteToolSource`):** `write_doc(path, content)`,
  one file per call. Validation BEFORE the gate (is_error output, no gate): workspace-relative
  path (traversal/absolute rejected), extension allowlist `.md .mmd .mermaid .txt .rst .adoc
  .svg .json .yaml .yml .csv` (case-insensitive, final suffix), content ≤ 1 MB.
- **Gate:** every call raises `PendingGate(kind="doc_write", payload={path, exists, preview})`
  (Class-A; `doc_write_requested` SSE is only the instant-render poke). `preview` = capped
  unified diff (existing file) or capped content (new file). Resolved by
  `POST /v1/chat/threads/{id}/doc-decision {approve}` — **NO remember option** (every write is
  unique content). Approve → write to the REAL workspace (mkdir parents). Timeout env
  `CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC` (0 = wait forever; timeout → reject).
  `PendingGate.kind` gained `"doc_write"` in chat/models.py + editor-client Zod + webview
  types.ts (the three-enum footgun).
- **Phase availability (explicit decision):** available in DECIDE **and** EDIT; in EDIT a
  doc write is still gated per write and lands immediately, independent of the edit
  session's shadow.
- **Prompt:** `_DOC_WRITE_BLOCK` auto-appends when a `write_doc` tool def is present
  (the `_MCP_BLOCK` detection pattern — no new parameter).
```

Add to the **Core** env list after the MCP vars:

```markdown
- `CRUCIBLE_DOC_WRITE_ENABLED` — offer the `write_doc` per-write-gated docs tool to the controller. Default **OFF**; opt in with `1/true/yes/on`. See "write_doc".
- `CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC` — seconds to wait for the doc_write gate decision; `0` (default) = wait forever; timeout → reject.
```

- [ ] **Step 4: Full verification (all three stacks)**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest -q            # read the summary — never pipe
ruff check agentd/chat/doc_write_source.py && mypy agentd/chat/doc_write_source.py
cd ../.. && npm run build && npm run test && npm run typecheck
```
Expected: everything green.

- [ ] **Step 5: Commit**

```bash
git add resources/mcp-servers/ollama-web-search.py CLAUDE.md
git commit -m "feat(mcp): vendored Ollama web-search MCP server as shipped default + docs"
```

---

### Task 7 (manual, not CI): live smoke on shadow-forge

Human-in-the-loop (or HTTP-driven like the P3 smoke). Backend: `CRUCIBLE_CHAT_CONTROLLER=1 CRUCIBLE_MCP_ENABLED=1 CRUCIBLE_DOC_WRITE_ENABLED=1`, `OLLAMA_API_KEY` exported.

- [ ] 1. Add the `"web"` entry (Task 6 canonical form, absolute script path) to the smoke workspace's `.crucible/mcp.json`; restart backend; log shows `[mcp] connected server=web tools=2`.
- [ ] 2. **write_doc approve:** "write a short CONTRIBUTING.md for this repo" from chat → `doc_write` gate card renders path+preview → Approve → file exists in the real workspace, `✓ Doc written` breadcrumb.
- [ ] 3. **write_doc reject:** repeat with different content → Reject → no file change, model adapts (watch for the known repetition-attractor class; `/stop` is the escape).
- [ ] 4. **Overwrite preview:** ask to update the same file → gate shows a unified diff, not full content.
- [ ] 5. **web_search end-to-end:** "search the web for <current-events question> and answer with sources" → `mcp_tool` gate (`web.web_search`) → Approve & remember → answer with citations; next search runs gate-free.
- [ ] 6. **Kill-switches:** `CRUCIBLE_DOC_WRITE_ENABLED=0` → no write_doc tool/no block; `"enabled": false` on `web` → no connect.
- [ ] 7. Reload-durability: raise a doc_write gate, reload the dev-host window → card re-renders from `/live`.

---

## Self-Review (performed while writing)

- **Spec coverage:** §3.1 source → Task 1; §3.2 validation → Task 1; §3.3 gate/route/breadcrumbs/timeout → Tasks 2+3; §3.4 phase availability → no code (documented in CLAUDE.md, Task 6); §3.5 teaching → Task 3; §3.6 frontend checklist → Tasks 4+5; §4.1 vendored script → Task 6; §4.2 canonical entry + §4.3 provider swaps + §4.4 example → Task 6 docs; §5 error handling → Tasks 1/2 tests; §6 testing → Tasks 1-5; §7 exit criteria → Tasks 6 (verification) + 7 (smoke).
- **Placeholder scan:** the `<repo>` token in CLAUDE.md docs is deliberate (install-path dependent); Task 7 uses the absolute path. No TBDs.
- **Type consistency:** gate payload `{path, exists, preview}` identical in Tasks 1/2/4/5; `DocWriteDecision{approve}` identical in Tasks 2/3/4/5; callback signature `(path, exists, preview) -> bool` identical in Tasks 1/2; route `/doc-decision` identical in Tasks 3/4; webview message `{type:"docDecision", threadId, approve}` identical in Tasks 5's webview+host sides.
- **Known judgment call:** `is_doc_write_enabled` lands in Task 2 (registry wiring needs it to compile); Task 3 owns its parsing tests — noted in both tasks.
