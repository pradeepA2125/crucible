import pytest

from agentd.memory.store import MemoryStore
from agentd.memory.tool_source import MemoryToolSource
from tests.test_memory_store_phase2 import _mem


class _SpyConsolidator:
    def __init__(self):
        self.explicit = []

    async def write_explicit(self, content, kind, entities, scope_kind, scope_id):
        self.explicit.append((content, kind, entities, scope_kind, scope_id))
        return "mem-1"


class _SpyRecall:
    def __init__(self, mems):
        self._mems = mems

    async def recall(self, query, scope_kind, scope_id, k):  # async (FIX #3)
        return self._mems


@pytest.mark.asyncio
async def test_remember_tool_writes_and_reports():
    spy = _SpyConsolidator()
    src = MemoryToolSource(spy, "workspace", "/ws")
    assert src.owns("remember")
    out = await src.execute("remember", {"content": "quote --workspace",
                                         "kind": "procedural", "entities": ["start-backend.sh"]})
    assert not out.is_error and "mem-1" in out.output
    assert spy.explicit[0][1] == "procedural" and spy.explicit[0][2] == ["start-backend.sh"]


@pytest.mark.asyncio
async def test_remember_rejects_bad_kind():
    src = MemoryToolSource(_SpyConsolidator(), "workspace", "/ws")
    out = await src.execute("remember", {"content": "x", "kind": "nonsense"})
    assert out.is_error


@pytest.mark.asyncio
async def test_remember_rejects_empty_content():
    src = MemoryToolSource(_SpyConsolidator(), "workspace", "/ws")
    out = await src.execute("remember", {"content": "  ", "kind": "semantic"})
    assert out.is_error


@pytest.mark.asyncio
async def test_remember_thread_scope_overrides():
    spy = _SpyConsolidator()
    src = MemoryToolSource(spy, "workspace", "/ws")
    await src.execute("remember", {"content": "local note", "kind": "episodic",
                                   "scope": "thread"})
    assert spy.explicit[0][3] == "thread"  # scope_kind overridden


@pytest.mark.asyncio
async def test_recall_tool_lists_memories(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    src = MemoryToolSource(_SpyConsolidator(), "workspace", "/ws",
                           recall_engine=_SpyRecall([_mem("a", content="patch ops here")]),
                           store=store)
    assert src.owns("recall")
    out = await src.execute("recall", {"query": "patch"})
    assert not out.is_error and "patch ops here" in out.output


@pytest.mark.asyncio
async def test_recall_tool_no_engine_errors():
    src = MemoryToolSource(_SpyConsolidator(), "workspace", "/ws")  # no recall_engine
    out = await src.execute("recall", {"query": "x"})
    assert out.is_error


@pytest.mark.asyncio
async def test_recall_tool_empty_is_clean(tmp_path):
    src = MemoryToolSource(_SpyConsolidator(), "workspace", "/ws",
                           recall_engine=_SpyRecall([]), store=MemoryStore(tmp_path / "m.sqlite3"))
    out = await src.execute("recall", {"query": "nothing"})
    assert not out.is_error and "no relevant" in out.output.lower()
