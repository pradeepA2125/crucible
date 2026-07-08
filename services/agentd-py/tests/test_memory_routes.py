from fastapi.testclient import TestClient

from agentd.chat.app_factory import build_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "1")
    monkeypatch.setenv("CRUCIBLE_MEMORY_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_MEMORY_DB_PATH", str(tmp_path / "m.sqlite3"))
    monkeypatch.setenv("CRUCIBLE_WORKSPACE_PATH", str(tmp_path))
    return TestClient(build_app(str(tmp_path)))


def test_config_reports_memory_enabled(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/v1/config").json()["memory_enabled"] is True


def test_browse_returns_memories(tmp_path, monkeypatch):
    from agentd.memory.store import MemoryStore
    from tests.test_memory_store_phase2 import _mem
    MemoryStore(tmp_path / "m.sqlite3").insert_memory(
        _mem("a").model_copy(update={"scope_id": str(tmp_path)}), [0.1] * 384)
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/memory", params={"scope_kind": "workspace", "scope_id": str(tmp_path)})
    assert r.status_code == 200 and any(m["id"] == "a" for m in r.json())


def test_chain_route(tmp_path, monkeypatch):
    from agentd.memory.store import MemoryStore
    from tests.test_memory_store_phase2 import _mem
    st = MemoryStore(tmp_path / "m.sqlite3")
    st.insert_memory(_mem("old", content="v1").model_copy(update={"scope_id": str(tmp_path)}),
                     [0.1] * 384)
    st.supersede("old", _mem("new", content="v2").model_copy(update={"scope_id": str(tmp_path)}),
                 [0.2] * 384)
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/memory/new/chain")
    assert r.status_code == 200 and [m["id"] for m in r.json()] == ["old", "new"]


def test_inspect_soft_empty_without_trace(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/memory/inspect", params={"thread_id": "chat-none"})
    assert r.status_code == 200 and r.json().get("entries", []) == []


def test_inspect_serves_persisted_trace(tmp_path, monkeypatch):
    # The trace lands at chat/<thread>/<turn>/memory-recall-NN.json — the route must glob
    # the turn_id level, not the thread level. (A prior .parent over-strip matched nothing.)
    from agentd.runtime.artifacts import chat_turn_artifacts_root

    turn_dir = chat_turn_artifacts_root("chat-trace", "turn-1", str(tmp_path))
    turn_dir.mkdir(parents=True, exist_ok=True)
    (turn_dir / "memory-recall-00.json").write_text(
        '{"query": "what does X do", "entries": [{"memory_id": "a"}]}', encoding="utf-8")

    c = _client(tmp_path, monkeypatch)
    r = c.get("/v1/memory/inspect", params={"thread_id": "chat-trace"})
    body = r.json()
    assert r.status_code == 200
    assert body["query"] == "what does X do"
    assert [e["memory_id"] for e in body["entries"]] == ["a"]
