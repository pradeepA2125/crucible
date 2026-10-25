import pytest
from test_rewind_routes import _build, _client

from agentd.project_ownership import OWNER


@pytest.mark.asyncio
async def test_owner_capability_and_process_are_required(tmp_path, monkeypatch):
    app, c, t, _ = _build(tmp_path)
    monkeypatch.setattr(OWNER, "active", True)
    monkeypatch.setattr(OWNER, "root", c._workspace_path)
    monkeypatch.setattr(OWNER, "key", "test-owner")
    monkeypatch.setattr(OWNER, "process_id", "test-process")
    async with _client(app) as client:
        url = f"/v1/chat/threads/{t}/file-restore"
        assert (
            await client.post(url, json={"operation": "history", "process_id": "test-process"})
        ).status_code == 409
        assert (
            await client.post(
                url,
                headers={"x-crucible-owner": "test-owner"},
                json={"operation": "history", "process_id": "old"},
            )
        ).status_code == 409
        r = await client.post(
            url,
            headers={"x-crucible-owner": "test-owner"},
            json={"operation": "history", "process_id": "test-process"},
        )
        assert r.status_code == 200 and r.json() == {"kind": "history", "entries": []}
        c._active_turns[t] = object()
        r = await client.post(
            url,
            headers={"x-crucible-owner": "test-owner"},
            json={"operation": "undo", "receipt": "missing", "process_id": "test-process"},
        )
        assert r.status_code == 409 and "Finish" in r.json()["detail"]
