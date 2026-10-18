from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from discovery_helpers import _RecordingEngine, _workspace

from agentd.chat.controller import ChatController
from agentd.chat.controller_prompts import build_controller_step_payload
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.planning.registry import PlanningToolRegistry
from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.tools.index_search import SEARCH_INDEX, search_index
from agentd.tools.registry import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("planning", [False, True])
async def test_search_tool_runs_hybrid_off_loop_and_returns_only_candidate_metadata(
    tmp_path, planning,
):
    _workspace(tmp_path)
    owner = threading.get_ident()

    class Semantic:
        def is_ready(self):
            return True

        def query(self, query, **kwargs):
            assert threading.get_ident() != owner
            assert query == "where invoices are handled"
            return [SimpleNamespace(score=.9, chunk=SimpleNamespace(
                path="invoice.py", name="invoice", kind="Function", line_start=1, line_end=2,
                text="NEVER RETURN SOURCE"))]

    client = RetrievalArtifactClient(semantic_index=Semantic())
    client._build_planner_evidence = Mock(side_effect=AssertionError("source extraction"))
    registry = (PlanningToolRegistry(tmp_path, retrieval_client=client) if planning else
                ToolRegistry(tmp_path, tmp_path, retrieval_client=client))
    assert any(d.name == "search_index" for d in registry.definitions())
    output = await registry.execute("search_index", {"query": "where invoices are handled"})
    assert not output.is_error and '"invoice.py"' in output.output
    assert '[[1,2]]' in output.output and '"Function",1' in output.output
    assert "NEVER RETURN SOURCE" not in output.output
    assert "symbols[name,kind,line]" in output.output
    assert '"invoice original"' not in output.output


@pytest.mark.asyncio
async def test_missing_or_corrupted_snapshot_never_builds_or_returns_old_candidates(tmp_path):
    _workspace(tmp_path)
    client = RetrievalArtifactClient()
    client._attempt_build_snapshot = Mock(side_effect=AssertionError("tool must not build"))
    assert "auth.py" in (await search_index(tmp_path, client, {"query": "auth"})).output
    snapshot = tmp_path / ".crucible/index-snapshot.json"
    snapshot.write_text("broken")
    corrupted = await search_index(tmp_path, client, {"query": "auth"})
    assert '0|"auth.py"' not in corrupted.output
    snapshot.unlink()
    missing = await search_index(tmp_path, client, {"query": "auth"})
    assert '0|"auth.py"' not in missing.output and "unavailable" in missing.output
    client._attempt_build_snapshot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{"query": ""}, {"query": "  "}, {"query": "x" * 1601},
                                 {"query": "auth", "limit": 0}, {"query": "auth", "limit": True}])
async def test_bad_queries_and_limits_do_not_touch_index(tmp_path, args):
    client = SimpleNamespace(load_context=Mock(side_effect=AssertionError("invalid query")))
    assert (await search_index(tmp_path, client, args)).is_error
    client.load_context.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", [None, "on_demand", "automatic"])
async def test_on_demand_has_no_automatic_lookup_but_agent_can_search_then_read(
    tmp_path, monkeypatch, delivery,
):
    if delivery is None:
        monkeypatch.delenv("CRUCIBLE_CODE_CANDIDATE_DELIVERY", raising=False)
    else:
        monkeypatch.setenv("CRUCIBLE_CODE_CANDIDATE_DELIVERY", delivery)
    # Retired settings from older installs cannot re-enable automatic retrieval.
    monkeypatch.setenv("CRUCIBLE_CODE_CANDIDATE_TRIAL", "1")
    monkeypatch.setenv("CRUCIBLE_CODE_CANDIDATE_SCORER", "qwen")
    _workspace(tmp_path)
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    tid = store.create_thread(str(tmp_path), title="On demand").thread_id
    client = RetrievalArtifactClient()
    original = client.load_context
    client.load_context = Mock(side_effect=original)
    engine = _RecordingEngine()
    engine._controller_step_responses = [
        {"type": "tool_calls", "thought": "locate", "calls": [
            {"id": "s", "tool": "search_index", "args": {"query": "auth"}}]},
        {"type": "tool_calls", "thought": "read", "calls": [
            {"id": "r", "tool": "read_file", "args": {"path": "auth.py"}}]},
        {"type": "answer", "thought": "done", "answer": "ok"},
    ]
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=engine, thread_store=store,
                          orchestrator=None, broadcaster=EventBroadcaster(),
                          retrieval_client=client)
    await ctrl.handle_message(tid, "Where is authentication?", channel_id="c")
    assert len(engine.payloads) == 3 and client.load_context.call_count == 1
    assert not any("code_candidates" in p for p in engine.payloads)
    assert "search_index" in engine.payloads[0]["instruction"]
    assert "Code candidates" in str(engine.payloads[1]["conversation_history"])
    assert "auth original" in str(engine.payloads[2]["conversation_history"])
    client.load_context.reset_mock()
    await ctrl.handle_message(tid, "Thanks", channel_id="c")
    client.load_context.assert_not_called()


def test_discovery_guidance_is_conditional_and_preserves_existing_prompt_without_tool():
    args = ({"goal": "Find relevant code"}, [], [])
    no_tool = build_controller_step_payload(*args, phase="ACTIVE")
    with_tool = build_controller_step_payload(args[0], args[1], [SEARCH_INDEX.model_dump()],
                                              phase="ACTIVE")
    assert "CODE DISCOVERY" not in no_tool["instruction"]
    assert "CODE DISCOVERY" in with_tool["instruction"]
    assert "stale" in with_tool["instruction"]
    assert "skip discovery" in with_tool["instruction"]


@pytest.mark.asyncio
async def test_result_is_not_tokenized_unless_a_profile_is_capturing(tmp_path, monkeypatch):
    # Token counts only feed profile records; counting them on every call is wasted
    # work, and the proxy tokenizer may fetch its encoding over the network.
    _workspace(tmp_path)
    import agentd.tools.index_search as module
    monkeypatch.setattr(module, "text_measurement",
                        Mock(side_effect=AssertionError("tokenized without a capture")))
    output = await search_index(tmp_path, RetrievalArtifactClient(), {"query": "auth"})
    assert not output.is_error and "auth.py" in output.output
