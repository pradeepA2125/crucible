"""Project words that prime Whisper: the project's most referenced names, from the index."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentd.chat.voice_vocabulary import project_vocabulary, snapshot_path_for


def _snapshot(path: Path, nodes: list[dict[str, object]], edges: list[dict[str, str]],
              root: str = "/ws") -> Path:
    path.write_text(json.dumps({"workspace_root": root, "graph": {"nodes": nodes, "edges": edges}}))
    return path


def _node(node_id: str, name: str, kind: str = "Class",
          path: str = "/ws/src/a.py") -> dict[str, object]:
    return {"id": node_id, "name": name, "kind": kind, "path": path, "line": 1}


def _refs(target: str, count: int) -> list[dict[str, str]]:
    return [{"from": f"x{n}", "to": target, "kind": "Calls"} for n in range(count)]


def test_vocabulary_ranks_most_referenced_names_first(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "s.json", [
        _node("c1", "ControllerLoop"),
        _node("f1", "voice_session.ts", "File", "/ws/src/voice_session.ts"),
        _node("c2", "EchoGate"),
        _node("fn", "build", "Function"),  # a plain word Whisper already knows
        _node("v1", "MAX_TURNS", "Variable"),  # variables are noise
        _node("o1", "OutsideThing", "Class", "/elsewhere/x.py"),
    ], _refs("c1", 5) + _refs("f1", 3) + _refs("c2", 1) + _refs("fn", 9) + _refs("v1", 9)
       + _refs("o1", 9))
    assert project_vocabulary(snapshot, "pradeep-site") == [
        "pradeep-site", "ControllerLoop", "voice_session", "EchoGate"]


def test_vocabulary_skips_private_and_test_names(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "s.json", [
        _node("p", "_privateHelper", "Function"), _node("t", "test_echo_gate", "Function"),
        _node("k", "VoiceAgent")], _refs("p", 5) + _refs("t", 5) + _refs("k", 1))
    assert project_vocabulary(snapshot, "crucible") == ["crucible", "VoiceAgent"]


def test_vocabulary_skips_unresolved_library_names(tmp_path: Path) -> None:
    # The indexer leaves `external:` placeholders for calls it could not resolve
    # (library names like RuntimeError or write_text), with the call site's path.
    snapshot = _snapshot(tmp_path / "s.json", [
        _node("external:call:RuntimeError", "RuntimeError", "Function"),
        _node("external:call:write_text", "write_text", "Function"),
        _node("class:file:/ws/a.py:VoiceAgent", "VoiceAgent")],
        _refs("external:call:RuntimeError", 50) + _refs("external:call:write_text", 50)
        + _refs("class:file:/ws/a.py:VoiceAgent", 1))
    assert project_vocabulary(snapshot, "crucible") == ["crucible", "VoiceAgent"]


def test_vocabulary_ignores_test_files(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "s.json", [
        _node("t1", "toBeNull", "Function", "/ws/src/usage.test.ts"),
        _node("t2", "FakeJson", "Class", "/ws/tests/test_voice_agent.py"),
        _node("c1", "EchoGate", "Class", "/ws/src/echoGate.ts"),
        _node("c2", "VoiceAgent", "Class", "/ws/agentd/voice_agent.py")],
        _refs("t1", 9) + _refs("t2", 9)
        # Tests calling EchoGate a lot must not outrank what the product itself uses.
        + [{"from": "function:file:/ws/tests/test_echo.py:case", "to": "c1", "kind": "Calls"}] * 9
        + _refs("c2", 2))
    assert project_vocabulary(snapshot, "crucible") == ["crucible", "VoiceAgent", "EchoGate"]


def test_vocabulary_respects_the_budget(tmp_path: Path) -> None:
    nodes = [_node(f"c{n}", f"LongClassName{n:03d}") for n in range(200)]
    snapshot = _snapshot(tmp_path / "s.json", nodes, [])
    words = project_vocabulary(snapshot, "crucible", max_chars=120)
    assert words[0] == "crucible"
    assert len(", ".join(words)) <= 120
    assert len(words) > 3


def test_missing_snapshot_gives_no_words(tmp_path: Path) -> None:
    assert project_vocabulary(tmp_path / "absent.json", "crucible") == []


def test_unreadable_snapshot_gives_no_words(tmp_path: Path) -> None:
    broken = tmp_path / "s.json"
    broken.write_text("{not json")
    assert project_vocabulary(broken, "crucible") == []
    broken.write_text(json.dumps({"graph": {"nodes": "nope"}}))
    assert project_vocabulary(broken, "crucible") == []


def test_snapshot_path_follows_the_retrieval_override(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH", raising=False)
    assert snapshot_path_for(tmp_path) == tmp_path / ".crucible" / "index-snapshot.json"
    monkeypatch.setenv("CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH", str(tmp_path / "custom.json"))
    assert snapshot_path_for(tmp_path) == tmp_path / "custom.json"


@pytest.mark.asyncio
async def test_vocabulary_route_returns_the_project_words(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from tests.test_background_dispatch import _setup
    from tests.test_conversation_steering import _app

    ctrl, _store, _tid = _setup(tmp_path, monkeypatch, [], {})
    workspace = Path(ctrl._workspace_path)
    (workspace / ".crucible").mkdir()
    monkeypatch.delenv("CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH", raising=False)
    _snapshot(workspace / ".crucible" / "index-snapshot.json",
              [_node("c1", "ControllerLoop", path=f"{workspace}/a.py")], _refs("c1", 2),
              root=str(workspace))
    transport = ASGITransport(app=_app(ctrl))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/voice/vocabulary")
    assert response.status_code == 200
    assert response.json() == {"words": [workspace.name, "ControllerLoop"]}
