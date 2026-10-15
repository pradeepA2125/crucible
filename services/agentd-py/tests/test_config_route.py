from fastapi.testclient import TestClient

from agentd.chat.app_factory import build_app
from agentd.local_models import LocalModels


def test_config_reports_flags_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUCIBLE_TASK_SUBSYSTEM", raising=False)
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/v1/config")
    assert r.status_code == 200
    body = r.json()
    assert body["task_subsystem_enabled"] is False
    assert "chat_controller_enabled" in body


def test_config_reports_task_subsystem_on(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "1")
    client = TestClient(build_app(str(tmp_path)))
    assert client.get("/v1/config").json()["task_subsystem_enabled"] is True


def test_config_reports_current_local_model_states_and_failure(tmp_path):
    models = LocalModels(True)
    client = TestClient(build_app(str(tmp_path), local_models=models))
    assert client.get("/v1/config").json()["local_models"]["embedder"]["state"] == "loading"
    models.mark_ready("embedder")
    assert client.get("/v1/config").json()["local_models"]["embedder"]["state"] == "ready"
    models.fail("embedder", "model load failed")
    state = client.get("/v1/config").json()["local_models"]["embedder"]
    assert state == {"state": "unavailable", "error": "model load failed"}
