"""Memory v2: no unearned "the user said" (prompt-injection Phase 1, spec §4.4).

`remember` takes a model-chosen origin, and user_requested unlocks "user_said" provenance,
user scope and supersedes. Once a turn has read untrusted output, the model can't claim that."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.recall_v2 import catalog
from agentd.memory.tool_source_v2 import DurableMemoryToolSource
from agentd.tools.registry import ToolOutput
from agentd.trust.turn_taint import TurnTaint
from tests.test_memory_durable_store import CTX
from tests.test_tool_result_framing import _run, _Source

REFUSAL = ("This memory can't be saved as yours: this turn has read files or tool output. "
           "Ask the user to confirm, or save it as your own note.")


@pytest.fixture(autouse=True)
def _defense_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")


def _args(**over: object) -> dict[str, object]:
    args: dict[str, object] = {"content": "Use pnpm", "topic": "tooling",
                               "applies_when": "installing", "kind": "procedural",
                               "origin": "user_requested", "scope": "repo"}
    args.update(over)
    return args


def _tools(tmp_path: Path, taint: TurnTaint) -> tuple[DurableMemoryToolSource, DurableMemoryStore]:
    store = DurableMemoryStore(tmp_path / "m.db")
    return DurableMemoryToolSource(store, CTX, source_mseq=lambda: 3, taint=taint), store


@pytest.mark.asyncio
async def test_user_requested_before_untrusted_result_saves_user_said(tmp_path: Path) -> None:
    tools, store = _tools(tmp_path, TurnTaint())
    saved = json.loads((await tools.execute("remember", _args())).output)
    assert store.get(saved["id"], CTX).provenance == "user_said"


@pytest.mark.asyncio
async def test_user_requested_after_untrusted_result_refused(tmp_path: Path) -> None:
    taint = TurnTaint()
    tools, _store = _tools(tmp_path, taint)
    taint.mark()
    out = await tools.execute("remember", _args())
    assert out.is_error and out.output == REFUSAL


@pytest.mark.asyncio
async def test_user_scope_after_untrusted_refused(tmp_path: Path) -> None:
    taint = TurnTaint()
    tools, _store = _tools(tmp_path, taint)
    taint.mark()
    out = await tools.execute("remember", _args(scope="user"))
    assert out.is_error and out.output == REFUSAL


@pytest.mark.asyncio
async def test_supersede_after_untrusted_refused(tmp_path: Path) -> None:
    taint = TurnTaint()
    tools, store = _tools(tmp_path, taint)
    first = json.loads((await tools.execute("remember", _args())).output)
    taint.mark()
    out = await tools.execute("remember", _args(content="Use npm", supersedes=first["id"]))
    assert out.is_error and out.output == REFUSAL
    assert store.get(first["id"], CTX).valid_to is None


@pytest.mark.asyncio
async def test_agent_nomination_after_untrusted_saves(tmp_path: Path) -> None:
    taint = TurnTaint()
    tools, store = _tools(tmp_path, taint)
    taint.mark()
    saved = json.loads((await tools.execute("remember", _args(origin="agent_nomination"))).output)
    assert store.get(saved["id"], CTX).provenance == "agent_inferred"


@pytest.mark.asyncio
async def test_taint_resets_each_turn(tmp_path: Path) -> None:
    taint = TurnTaint()
    taint.mark()
    assert taint.seen
    taint.reset()
    assert not taint.seen
    # The loop marks it for an untrusted result, and only then.
    marked = TurnTaint()
    await _run(tmp_path, [{"id": "a", "tool": "t", "args": {}}],
               _Source({"t": ToolOutput(output="x")}, {"t": "trusted"}), taint=marked)
    assert not marked.seen
    await _run(tmp_path, [{"id": "a", "tool": "t", "args": {}}],
               _Source({"t": ToolOutput(output="x")}, {}), taint=marked)
    assert marked.seen


def test_catalog_shows_from_tool_output(tmp_path: Path) -> None:
    store = DurableMemoryStore(tmp_path / "m.db")
    from tests.test_memory_durable_store import memory

    saved = store.put(memory(provenance="tool_verified"), CTX, origin="agent_nomination")
    assert catalog(saved).endswith("(from tool output)")
    assert store.get(saved.id, CTX).provenance == "tool_verified"


@pytest.mark.asyncio
async def test_flag_off_remember_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "0")
    taint = TurnTaint()
    tools, store = _tools(tmp_path, taint)
    taint.mark()
    saved = json.loads((await tools.execute("remember", _args())).output)
    assert store.get(saved["id"], CTX).provenance == "user_said"
