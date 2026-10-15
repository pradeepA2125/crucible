import pytest

from agentd.local_models import LocalModels, semantic_ready
from agentd.memory.config import MemoryConfig
from agentd.memory.embedder import Embedder
from agentd.memory.reranker import Reranker
from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.retrieval.semantic_index import SemanticIndex
from agentd.tools.registry import ToolRegistry
from agentd.tools.search import search_semantic


def test_states_are_terminal_after_failure_and_disabled_never_becomes_ready():
    disabled = LocalModels(False)
    disabled.mark_ready("embedder")
    disabled.fail("embedder", "should not change")
    assert disabled.state("embedder").state == "disabled"
    models = LocalModels(True)
    assert models.payload()["embedder"] == {"state": "loading", "error": None}
    models.mark_ready("embedder")
    models.fail("embedder", "runtime failure")
    models.mark_ready("embedder")
    assert models.state("embedder").state == "unavailable"
    assert models.state("embedder").error == "runtime failure"
    assert not models.ready("reranker")
    assert not LocalModels.from_env({"CRUCIBLE_LOCAL_MODELS": "0"}).enabled
    cfg = MemoryConfig.from_env({"CRUCIBLE_LOCAL_MODELS": "0", "CRUCIBLE_MEMORY_RERANKER": "1"})
    assert not cfg.local_models and not cfg.reranker_enabled


def test_disabled_models_do_not_probe_or_load_and_warmup_enables_reads():
    calls = []
    disabled = LocalModels(False)
    embedder = Embedder(encoder=lambda texts: calls.append(texts), local_models=disabled)
    ranker = Reranker(scorer=lambda pairs: calls.append(pairs), local_models=disabled)
    embedder.warmup()
    ranker.warmup()
    assert embedder.embed(["query"]) == [] and calls == []
    assert not embedder.available and not ranker.available
    models = LocalModels(True)
    embedder = Embedder(encoder=lambda texts: [[3.0, 4.0] for text in texts], local_models=models)
    assert not embedder.available and embedder.embed(["query"]) == []
    embedder.warmup()
    assert embedder.available and embedder.embed(["query"]) == [[.6, .8]]


def test_runtime_encoder_failure_disables_all_consumers_without_retries():
    models = LocalModels(True)
    calls = []

    def encoder(texts):
        calls.append(texts)
        if texts != ["warmup"]:
            raise RuntimeError("encode failed")
        return [[1.0]]

    embedder = Embedder(encoder=encoder, local_models=models)
    embedder.warmup()
    assert embedder.embed(["query"]) == []
    assert models.state("embedder").state == "unavailable"
    assert embedder.embed(["later"]) == [] and len(calls) == 2


@pytest.mark.asyncio
async def test_semantic_tool_and_retrieval_gates_follow_shared_state(tmp_path):
    models = LocalModels(True)

    class Index:
        local_models = models

        def query(self, query, **kwargs):
            return []

    index = Index()
    registry = ToolRegistry(tmp_path, tmp_path, semantic_index=index)
    retrieval = RetrievalArtifactClient(semantic_index=index)
    assert not semantic_ready(index) and not retrieval.semantic_enabled()
    assert "search_semantic" not in {tool.name for tool in registry.definitions("explore")}
    assert (await search_semantic(query="q", top_k=8, semantic_index=index)).is_error
    models.mark_ready("embedder")
    assert retrieval.semantic_enabled()
    assert "search_semantic" in {tool.name for tool in registry.definitions("explore")}
    models.fail("embedder", "model unavailable")
    assert not retrieval.semantic_enabled()
    assert "search_semantic" not in {tool.name for tool in registry.definitions("explore")}


def test_semantic_index_warmup_does_not_load_opted_out_model(tmp_path, monkeypatch):
    models = LocalModels(False)
    index = SemanticIndex(tmp_path, local_models=models)
    monkeypatch.setattr(index, "_get_model", lambda: pytest.fail("disabled model loaded"))
    index.warmup()
    assert index.query("q") == []


def test_failed_reranker_reports_unavailable_and_v2_callers_get_an_exception():
    models = LocalModels(True)
    calls = []

    def score(pairs):
        calls.append(pairs)
        return [float("nan")]

    ranker = Reranker(scorer=score, local_models=models)
    ranker.warmup()
    assert models.state("reranker").state == "unavailable"
    with pytest.raises(RuntimeError, match="not ready"):
        ranker.score_pairs([("query", "claim")])
    assert len(calls) == 1
