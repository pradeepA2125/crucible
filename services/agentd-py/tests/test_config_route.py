from fastapi.testclient import TestClient

from agentd.chat.app_factory import build_app


def test_config_reports_flags_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_EDITOR_TASK_SUBSYSTEM", raising=False)
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/v1/config")
    assert r.status_code == 200
    body = r.json()
    assert body["task_subsystem_enabled"] is False
    assert "chat_controller_enabled" in body


def test_config_reports_task_subsystem_on(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_EDITOR_TASK_SUBSYSTEM", "1")
    client = TestClient(build_app(str(tmp_path)))
    assert client.get("/v1/config").json()["task_subsystem_enabled"] is True
