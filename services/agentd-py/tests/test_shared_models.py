"""One instance per local model, warmed off the chat path.

The memory embedder (built once per memory harness) and the semantic index each held their
own bge-small, and nothing warmed the semantic index's copy: the first chat after a restart
loaded it inside the retrieval seed (18.7 s measured), on the event loop.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from agentd.chat.controller_factory import tasks_reachable
from agentd.memory.embedder import Embedder
from agentd.memory.reranker import Reranker
from agentd.retrieval.semantic_index import SemanticIndex


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    constructed: list[str] = []

    class _FakeModel:
        def __init__(self, name: str) -> None:
            constructed.append(name)

        def encode(self, texts: list[str], **_kw: object) -> list[list[float]]:
            return [[1.0] + [0.0] * 383 for _ in texts]

        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            return [0.0 for _ in pairs]

        def get_embedding_dimension(self) -> int:
            return 384

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _FakeModel  # type: ignore[attr-defined]
    fake.CrossEncoder = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return constructed


def test_embedders_and_the_semantic_index_share_one_model(loads: list[str],
                                                          tmp_path: Path) -> None:
    Embedder("fake-shared").embed(["a"])
    Embedder("fake-shared").embed(["b"])  # the second memory harness's embedder
    SemanticIndex(index_path=tmp_path / "idx", model_name="fake-shared").warmup()
    assert loads.count("fake-shared") == 1


def test_rerankers_share_one_model(loads: list[str]) -> None:
    Reranker("fake-rerank").warmup()
    Reranker("fake-rerank").warmup()
    assert loads.count("fake-rerank") == 1


def test_semantic_warmup_loads_the_model_before_any_query(loads: list[str],
                                                         tmp_path: Path) -> None:
    index = SemanticIndex(index_path=tmp_path / "idx", model_name="fake-warm")
    index.warmup()
    assert loads == ["fake-warm"]


def test_semantic_warmup_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken = types.ModuleType("sentence_transformers")

    def _fail(name: str) -> None:
        raise OSError(f"cannot load {name}")

    broken.SentenceTransformer = _fail  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", broken)
    SemanticIndex(index_path=tmp_path / "idx", model_name="fake-broken").warmup()


@pytest.mark.parametrize(("controller", "task_subsystem", "expected"), [
    ("1", "0", False),  # the default: tasks are unreachable, so no task-loop harness
    ("1", "1", True),
    ("0", "0", True),   # the legacy chat path still creates tasks
])
def test_tasks_reachable(monkeypatch: pytest.MonkeyPatch, controller: str, task_subsystem: str,
                         expected: bool) -> None:
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", controller)
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", task_subsystem)
    assert tasks_reachable() is expected
