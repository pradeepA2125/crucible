"""ProjectTrust: repository files that change Crucible's behavior count only once the user
trusted their exact content (prompt-injection Phase 1, spec §4.1)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from agentd.trust import project_trust as pt
from agentd.trust.project_trust import (
    ProjectTrust,
    TrustMismatch,
    TrustRefused,
    kind_for_path,
    project_trust_for,
)


@pytest.fixture(autouse=True)
def _defense_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _trust(tmp_path: Path) -> tuple[ProjectTrust, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ProjectTrust(ws, home=tmp_path / "home"), ws


def test_absent_file_reports_absent(tmp_path):
    trust, _ = _trust(tmp_path)
    assert trust.state("agents_md") == "absent"
    assert trust.fingerprint("agents_md") is None


def test_untrusted_until_trust_called(tmp_path):
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"use pnpm")
    assert trust.state("agents_md") == "untrusted"
    assert not trust.is_effective("agents_md")
    trust.trust("agents_md", _sha(b"use pnpm"))
    assert trust.state("agents_md") == "trusted"
    assert trust.is_effective("agents_md")


def test_trust_rejects_stale_sha(tmp_path):
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"new")
    with pytest.raises(TrustMismatch, match="changed since you reviewed it"):
        trust.trust("agents_md", _sha(b"old"))
    assert trust.state("agents_md") == "untrusted"


def test_symlinked_file_is_refused(tmp_path):
    trust, ws = _trust(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"x")
    (ws / "AGENTS.md").symlink_to(outside)
    with pytest.raises(TrustRefused, match="symlink"):
        trust.trust("agents_md", _sha(b"x"))
    assert trust.state("agents_md") == "untrusted"


def test_large_file_is_refused(tmp_path):
    trust, ws = _trust(tmp_path)
    big = b"a" * (65 * 1024)
    (ws / "AGENTS.md").write_bytes(big)
    with pytest.raises(TrustRefused, match="too large to review"):
        trust.trust("agents_md", _sha(big))


def test_every_trusted_fingerprint_is_kept(tmp_path):
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"A")
    trust.trust("agents_md", _sha(b"A"))
    (ws / "AGENTS.md").write_bytes(b"B")
    trust.trust("agents_md", _sha(b"B"))
    (ws / "AGENTS.md").write_bytes(b"A")
    assert trust.state("agents_md") == "trusted"


def test_recreated_file_follows_its_bytes(tmp_path):
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"A")
    trust.trust("agents_md", _sha(b"A"))
    (ws / "AGENTS.md").unlink()
    assert trust.state("agents_md") == "absent"
    (ws / "AGENTS.md").write_bytes(b"A")
    assert trust.state("agents_md") == "trusted"
    (ws / "AGENTS.md").write_bytes(b"C")
    assert trust.state("agents_md") == "untrusted"


def test_record_own_write_needs_trusted_before(tmp_path):
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"planted")
    before = trust.fingerprint("agents_md")
    (ws / "AGENTS.md").write_bytes(b"planted + mine")
    assert trust.record_own_write("agents_md", before) is False
    assert trust.state("agents_md") == "untrusted"
    (ws / "AGENTS.md").unlink()
    (ws / "AGENTS.md").write_bytes(b"created by me")
    assert trust.record_own_write("agents_md", None) is True
    assert trust.state("agents_md") == "trusted"
    before = trust.fingerprint("agents_md")
    (ws / "AGENTS.md").write_bytes(b"edited by me")
    assert trust.record_own_write("agents_md", before) is True
    assert trust.state("agents_md") == "trusted"


def _skill(ws: Path, name: str, body: str) -> Path:
    folder = ws / ".crucible" / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_text(body)
    return folder


def test_skills_fingerprint_covers_every_skill(tmp_path):
    trust, ws = _trust(tmp_path)
    one = _skill(ws, "one", "---\nname: one\ndescription: d\n---\nbody")
    first = trust.fingerprint("skills")
    (one / "scripts").mkdir()
    (one / "scripts" / "x.py").write_text("print(1)")
    assert trust.fingerprint("skills") == first
    _skill(ws, "two", "---\nname: two\ndescription: d\n---\nbody")
    assert trust.fingerprint("skills") != first


def test_workspace_key_uses_realpath(tmp_path):
    ws = tmp_path / "real"
    ws.mkdir()
    (tmp_path / "alias").symlink_to(ws)
    (ws / "AGENTS.md").write_bytes(b"A")
    home = tmp_path / "home"
    ProjectTrust(tmp_path / "alias", home=home).trust("agents_md", _sha(b"A"))
    assert ProjectTrust(ws, home=home).state("agents_md") == "trusted"


def test_concurrent_writers_keep_both_entries(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspaces = []
    for name in ("a", "b"):
        ws = tmp_path / name
        ws.mkdir()
        (ws / "AGENTS.md").write_bytes(name.encode())
        workspaces.append(ws)
    real_read = pt.ProjectTrust._read_all

    def slow_read(self):
        data = real_read(self)
        time.sleep(0.05)
        return data

    monkeypatch.setattr(pt.ProjectTrust, "_read_all", slow_read)
    threads = [threading.Thread(target=ProjectTrust(ws, home=home).trust,
                                args=("agents_md", _sha(ws.name.encode())))
               for ws in workspaces]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for ws in workspaces:
        assert ProjectTrust(ws, home=home).state("agents_md") == "trusted"


def test_agent_entries_survive(tmp_path):
    trust, ws = _trust(tmp_path)
    store = tmp_path / "home" / ".crucible" / "trust.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"/some/ws": {"/some/ws/.crucible/agents/a.md": "abc"}}))
    (ws / "AGENTS.md").write_bytes(b"A")
    trust.trust("agents_md", _sha(b"A"))
    data = json.loads(store.read_text())
    assert data["/some/ws"] == {"/some/ws/.crucible/agents/a.md": "abc"}


def test_flag_off_everything_effective(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "0")
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"planted")
    assert trust.is_effective("agents_md")


def test_diff_base_is_last_trusted_content(tmp_path):
    trust, ws = _trust(tmp_path)
    (ws / "AGENTS.md").write_bytes(b"first")
    trust.trust("agents_md", _sha(b"first"))
    (ws / "AGENTS.md").write_bytes(b"second")
    view = trust.view("agents_md")
    assert view.state == "untrusted"
    assert view.content == "second"
    assert view.diff_base == "first"
    assert view.sha256 == _sha(b"second")


def test_kind_for_path_maps_skill_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert kind_for_path(ws, ws / "AGENTS.md") == "agents_md"
    assert kind_for_path(ws, ws / ".crucible/skills/a/SKILL.md") == "skills"
    assert kind_for_path(ws, ws / ".crucible/skills/a/scripts/x.py") is None
    assert kind_for_path(ws, ws / ".crucible/mcp.json") == "mcp"
    assert kind_for_path(ws, ws / ".crucible/approved-commands.json") == "approved_commands"
    assert kind_for_path(ws, ws / "src/AGENTS.md") is None


def test_project_trust_for_shares_one_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    assert project_trust_for(ws) is project_trust_for(ws)
