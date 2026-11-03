"""Project-trust routes (prompt-injection Phase 1, spec §4.1)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.project_trust_routes import build_project_trust_router
from agentd.trust.project_trust import ALL_KINDS, ProjectTrust


@pytest.fixture(autouse=True)
def _defense_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def env(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    trust = ProjectTrust(ws, home=tmp_path / "home")
    calls: list[str] = []

    async def on_trusted(kind: str) -> None:
        calls.append(kind)

    app = FastAPI()
    app.include_router(build_project_trust_router(trust, on_trusted))
    return TestClient(app), ws, trust, calls


def test_get_lists_every_kind(env) -> None:
    client, ws, _trust, _calls = env
    (ws / "AGENTS.md").write_text("use pnpm​")
    files = client.get("/v1/project-trust").json()["files"]
    assert [f["kind"] for f in files] == list(ALL_KINDS)
    agents = files[0]
    assert agents["state"] == "untrusted"
    assert agents["content"] == "use pnpm⟦U+200B⟧"
    assert agents["sha256"] == _sha("use pnpm​".encode())
    assert agents["diff_base"] is None
    assert files[2] == {"kind": "mcp", "state": "absent", "sha256": None, "content": None,
                        "diff_base": None}


def test_post_trusts_matching_sha(env) -> None:
    client, ws, trust, _calls = env
    (ws / "AGENTS.md").write_bytes(b"x")
    response = client.post("/v1/project-trust", json={"kind": "agents_md", "sha256": _sha(b"x")})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert trust.state("agents_md") == "trusted"


def test_post_409_on_changed_file(env) -> None:
    client, ws, _trust, calls = env
    (ws / "AGENTS.md").write_bytes(b"new")
    response = client.post("/v1/project-trust", json={"kind": "agents_md", "sha256": _sha(b"old")})
    assert response.status_code == 409
    assert "changed since you reviewed it" in response.json()["detail"]
    assert calls == []
    bad = client.post("/v1/project-trust", json={"kind": "nope", "sha256": "a"})
    assert bad.status_code == 422


def test_saved_trusts_only_from_trusted_before(env) -> None:
    client, ws, trust, _calls = env
    (ws / "AGENTS.md").write_bytes(b"planted")
    before = trust.fingerprint("agents_md")
    (ws / "AGENTS.md").write_bytes(b"planted, edited")
    response = client.post("/v1/project-trust/saved",
                           json={"kind": "agents_md", "before_sha256": before})
    assert response.json() == {"trusted": False}
    (ws / "AGENTS.md").unlink()
    (ws / "AGENTS.md").write_bytes(b"mine")
    response = client.post("/v1/project-trust/saved",
                           json={"kind": "agents_md", "before_sha256": None})
    assert response.json() == {"trusted": True}


def test_trusting_mcp_calls_on_trusted(env) -> None:
    client, ws, _trust, calls = env
    (ws / ".crucible").mkdir()
    (ws / ".crucible" / "mcp.json").write_bytes(b"{}")
    client.post("/v1/project-trust", json={"kind": "mcp", "sha256": _sha(b"{}")})
    assert calls == ["mcp"]


def test_config_reports_injection_defense(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agentd.chat.app_factory import build_app

    client = TestClient(build_app(str(tmp_path)))
    assert client.get("/v1/config").json()["injection_defense"] is True
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "0")
    assert client.get("/v1/config").json()["injection_defense"] is False
