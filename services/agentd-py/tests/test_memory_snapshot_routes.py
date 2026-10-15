from types import SimpleNamespace
from unittest.mock import Mock

from tests.test_memory_routes import _client


def test_snapshot_scopes_existing_store_and_never_constructs_one(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    agent = client.app.state.chat_agent
    thread = client.post('/v1/chat/threads', json={'workspace': str(tmp_path)}).json()['thread_id']
    context = object()
    memory = Mock()
    memory.model_dump.return_value = {'id': 'visible'}
    store = Mock()
    store.list_memories.return_value = [memory]
    agent._memory_harness = SimpleNamespace(recall_v2=True, durable_store=store,
                                           scope_context=Mock(return_value=context))
    response = client.get('/v1/memory/snapshot', params={'thread_id': thread})
    assert response.json() == {'pipeline': 'durable', 'status': 'ready', 'memories': [{'id': 'visible'}]}
    store.list_memories.assert_called_once_with(context, include_retired=True)
    agent._memory_harness.scope_context.assert_called_once_with(thread)
    assert client.get('/v1/memory/snapshot', params={'thread_id': '../foreign'}).status_code == 404
    foreign = client.post('/v1/chat/threads', json={'workspace': '/foreign'}).json()['thread_id']
    assert client.get('/v1/memory/snapshot', params={'thread_id': foreign}).status_code == 404
    assert store.list_memories.call_count == 1


def test_snapshot_distinguishes_legacy_unavailable_disabled(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    agent = client.app.state.chat_agent
    thread = client.post('/v1/chat/threads', json={'workspace': str(tmp_path)}).json()['thread_id']
    agent._memory_harness = SimpleNamespace(recall_v2=False)
    assert client.get('/v1/memory/snapshot', params={'thread_id': thread}).json()['pipeline'] == 'legacy'
    agent._memory_harness = SimpleNamespace(recall_v2=True, durable_store=None)
    assert client.get('/v1/memory/snapshot', params={'thread_id': thread}).json() == {
        'pipeline': 'durable', 'status': 'unavailable', 'memories': []}
    monkeypatch.setenv('CRUCIBLE_MEMORY_ENABLED', '0')
    assert client.get('/v1/memory/snapshot', params={'thread_id': thread}).json()['status'] == 'disabled'
