import pytest

from agentd.memory.tool_source import MemoryToolSource


class _SpyConsolidator:
    def __init__(self):
        self.explicit = []

    async def write_explicit(self, content, kind, entities, scope_kind, scope_id):
        self.explicit.append((content, kind, entities, scope_kind, scope_id))
        return "mem-1"


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
