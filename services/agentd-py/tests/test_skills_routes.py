from pathlib import Path

from fastapi.testclient import TestClient

from agentd.chat.app_factory import build_app


def _write_skill(ws: Path, name: str, desc: str) -> None:
    d = ws / ".ai-editor" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8"
    )


def test_skills_route_lists_catalog(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_EDITOR_SKILLS_ENABLED", "1")
    _write_skill(tmp_path, "git-commit", "Make a commit.")
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/v1/skills", params={"workspace": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["skills"] == [{"name": "git-commit", "description": "Make a commit."}]


def test_skills_route_gated_empty_when_off(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AI_EDITOR_SKILLS_ENABLED", raising=False)
    _write_skill(tmp_path, "x", "y")
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/v1/skills", params={"workspace": str(tmp_path)})
    assert r.status_code == 200 and r.json()["skills"] == []


def test_config_exposes_skills_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_EDITOR_SKILLS_ENABLED", "1")
    client = TestClient(build_app(str(tmp_path)))
    assert client.get("/v1/config").json()["skills_enabled"] is True
