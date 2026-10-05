"""Settings › Agents routes (spec §10.1)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.agents_routes import build_agents_router
from agentd.subagents.trust import TrustStore


@pytest.fixture()
def env(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = TrustStore(tmp_path / "trust.json")
    enabled = {"on": True}
    app = FastAPI()
    app.include_router(build_agents_router(
        workspace=str(ws), trust_store=store, user_agents_dir=tmp_path / "home",
        mcp_server_names=lambda: ["github"], enabled=lambda: enabled["on"]))
    return TestClient(app), ws, store, enabled


def _body(**over):
    body = {"description": "Helps.", "persona": "You help.", "tools": None,
            "disallowed_tools": [], "permission": "default", "model": "inherit",
            "max_turns": None, "skills": []}
    body.update(over)
    return body


def _row(client, name: str, source: str | None = None) -> dict:
    agents = client.get("/v1/agents").json()["agents"]
    return next(a for a in agents
                if a["name"] == name and (source is None or a["source"] == source))


def test_list_shape_and_available_tools(env) -> None:
    client, _ws, _store, _ = env
    body = client.get("/v1/agents").json()
    assert set(body) == {"agents", "skipped", "available_tools"}
    gp = _row(client, "general-purpose")
    assert gp["source"] == "builtin" and gp["path"] is None and gp["tools"] is None
    assert "mcp__github__*" in body["available_tools"] and "edit" in body["available_tools"]
    assert "remember" not in body["available_tools"]  # children never get it


def test_put_writes_a_trusted_definition(env) -> None:
    client, ws, _store, _ = env
    resp = client.put("/v1/agents/helper", json=_body(
        tools=["read_file", "edit"], permission="acceptEdits"))
    assert resp.status_code == 200, resp.text
    row = resp.json()["agent"]
    assert row["trust"] == "trusted" and row["active"] and row["permission"] == "acceptEdits"
    assert row["tools"] == ["edit", "read_file"]
    path = ws / ".crucible" / "agents" / "helper.md"
    assert row["path"] == str(path)
    assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert row["content"] == path.read_text()


def test_tools_null_is_distinct_from_empty(env) -> None:
    client, _ws, _store, _ = env
    client.put("/v1/agents/a", json=_body(tools=None))
    client.put("/v1/agents/b", json=_body(tools=[]))
    assert _row(client, "a")["tools"] is None
    assert _row(client, "b")["tools"] == []


@pytest.mark.parametrize("name,over", [
    ("bad name", {}), ("ok", {"max_turns": 0}), ("ok", {"max_turns": 201}),
    ("ok", {"persona": "p" * 20001}), ("ok", {"permission": "yolo"}),
])
def test_put_validation_400(env, name, over) -> None:
    client, _ws, _store, _ = env
    assert client.put(f"/v1/agents/{name}", json=_body(**over)).status_code == 400


def test_reserved_name_trust_is_refused(env) -> None:
    client, _ws, _store, _ = env
    resp = client.put("/v1/agents/trust", json=_body())
    assert resp.status_code in (400, 405)
    assert not (env[1] / ".crucible" / "agents" / "trust.md").exists()


def test_crucible_save_overrides_claude_definition(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    (claude / "helper.md").write_text("---\nname: helper\ndescription: d\n---\nold\n")
    assert _row(client, "helper", "claude")["trust"] == "capped"
    assert client.put("/v1/agents/helper", json=_body()).status_code == 200
    assert _row(client, "helper", "crucible")["active"] is True
    low = _row(client, "helper", "claude")
    assert low["active"] is False and low["shadowed_by"] == str(ws / ".crucible/agents/helper.md")


def test_put_refuses_name_defined_in_subdirectory(env) -> None:
    client, ws, store, _ = env
    sub = ws / ".crucible" / "agents" / "team"
    sub.mkdir(parents=True)
    nested = sub / "x.md"
    nested.write_text("---\nname: x\ndescription: d\n---\nbody\n")
    resp = client.put("/v1/agents/x", json=_body())
    assert resp.status_code == 409 and str(nested) in resp.json()["detail"]
    assert client.delete("/v1/agents/x").status_code == 409
    assert not (ws / ".crucible" / "agents" / "x.md").exists()


def test_rename_moves_the_file_and_trust(env) -> None:
    client, ws, store, _ = env
    client.put("/v1/agents/old", json=_body())
    old = ws / ".crucible" / "agents" / "old.md"
    resp = client.put("/v1/agents/new", json=_body(rename_from="old"))
    assert resp.status_code == 200
    assert not old.exists()
    assert store._read().get(str(ws), {}).get(str(old)) is None
    assert _row(client, "new")["trust"] == "trusted"


def test_rename_onto_existing_name_is_refused(env) -> None:
    client, ws, _store, _ = env
    client.put("/v1/agents/a", json=_body())
    client.put("/v1/agents/b", json=_body(description="B"))
    resp = client.put("/v1/agents/b", json=_body(rename_from="a"))
    assert resp.status_code == 409
    assert (ws / ".crucible" / "agents" / "a.md").exists()
    assert _row(client, "b")["description"] == "B"


def test_delete_removes_file_and_trust(env) -> None:
    client, ws, store, _ = env
    client.put("/v1/agents/gone", json=_body())
    path = ws / ".crucible" / "agents" / "gone.md"
    assert client.delete("/v1/agents/gone").json() == {"ok": True}
    assert not path.exists() and store._read().get(str(ws), {}).get(str(path)) is None
    assert client.delete("/v1/agents/gone").status_code == 404


def test_builtins_and_claude_files_are_read_only(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    (claude / "c.md").write_text("---\nname: c\ndescription: d\n---\nbody\n")
    assert client.delete("/v1/agents/explore").status_code == 409
    assert client.delete("/v1/agents/c").status_code == 409


def test_trust_records_the_reviewed_hash(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    path = claude / "c.md"
    path.write_text("---\nname: c\ndescription: d\npermissionMode: acceptEdits\n---\nbody\n")
    row = _row(client, "c")
    assert row["trust"] == "capped" and row["permission"] == "default"
    assert row["declared_permission"] == "acceptEdits"
    resp = client.post("/v1/agents/trust", json={"path": row["path"], "sha256": row["sha256"]})
    assert resp.json() == {"ok": True}
    assert _row(client, "c")["trust"] == "trusted"
    untrust = client.request("DELETE", "/v1/agents/trust", json={"path": row["path"]})
    assert untrust.json() == {"ok": True}
    assert _row(client, "c")["trust"] == "capped"


def test_trust_rejects_changed_file(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    path = claude / "c.md"
    path.write_text("---\nname: c\ndescription: d\n---\nbody\n")
    row = _row(client, "c")
    path.write_text("---\nname: c\ndescription: d\n---\nswapped\n")
    resp = client.post("/v1/agents/trust", json={"path": row["path"], "sha256": row["sha256"]})
    assert resp.status_code == 409 and "changed since you reviewed it" in resp.json()["detail"]
    assert _row(client, "c")["trust"] == "capped"


def test_trust_refuses_unknown_and_user_files(env, tmp_path) -> None:
    client, _ws, _store, _ = env
    resp = client.post("/v1/agents/trust", json={"path": "/etc/passwd", "sha256": "0" * 64})
    assert resp.status_code == 404


def test_disabled(env) -> None:
    client, _ws, _store, enabled = env
    enabled["on"] = False
    assert client.get("/v1/agents").json() == {"agents": [], "skipped": [], "available_tools": []}
    assert client.put("/v1/agents/helper", json=_body()).status_code == 409
