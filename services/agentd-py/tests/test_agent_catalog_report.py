"""The catalog report behind Settings › Agents (spec §10.1): warnings are kept, skipped
files are listed, shadowed and inactive definitions stay visible."""
from __future__ import annotations

import hashlib
from pathlib import Path

from agentd.subagents.agent_files import (
    AgentCatalogLoader,
    parse_agent_file,
    trust_clamped_permission,
)
from agentd.subagents.trust import TrustStore


def _md(name: str, extra: str = "", body: str = "Persona.") -> str:
    return f"---\nname: {name}\ndescription: d {name}\n{extra}---\n{body}\n"


def _put(root: Path, name: str, text: str, filename: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / (filename or f"{name}.md")
    path.write_text(text, encoding="utf-8")
    return path


def _loader(tmp_path: Path) -> tuple[AgentCatalogLoader, Path, TrustStore]:
    ws = tmp_path / "ws"
    ws.mkdir()
    store = TrustStore(tmp_path / "trust.json")
    return AgentCatalogLoader(ws, user_agents_dir=tmp_path / "home", trust_store=store), ws, store


def _trust(store: TrustStore, ws: Path, path: Path) -> None:
    store.trust(str(ws), str(path), hashlib.sha256(path.read_bytes()).hexdigest())


def _entry(report, name: str, kind: str):
    return next(e for e in report.entries
                if e.definition.name == name and e.source_kind == kind)


def test_parse_records_warnings(tmp_path: Path) -> None:
    path = _put(tmp_path, "w", _md(
        "w", "tools: Read, WebFetch, frobnicate\nmodel: sonnet\npermissionMode: yolo\nmaxTurns: 500\n"))
    warnings: list[str] = []
    d = parse_agent_file(path, warnings)
    assert d is not None
    assert d.max_turns == 200
    text = " | ".join(d.warnings)
    assert "WebFetch" in text and "frobnicate" in text and "sonnet" in text
    assert "yolo" in text and "clamped to 200" in text
    assert list(d.warnings) == warnings


def test_mcp_and_native_tools_are_not_unknown(tmp_path: Path) -> None:
    path = _put(tmp_path, "m", _md("m", "tools: mcp__github, query_graph, Bash, recall\n"))
    d = parse_agent_file(path)
    assert d is not None and d.warnings == ()


def test_skipped_files_carry_a_reason(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    root = ws / ".crucible" / "agents"
    _put(root, "nofront", "just text\n")
    _put(root, "noname", "---\ndescription: d\n---\nx\n")
    _put(root, "dup", _md("dup"))
    _put(root, "dup", _md("dup"), filename="dup-copy.md")
    report = loader.report()
    reasons = {Path(s.path).name: s.reason for s in report.skipped}
    assert "frontmatter" in reasons["nofront.md"]
    assert "name" in reasons["noname.md"]
    assert reasons["dup.md" if "dup.md" in reasons else "dup-copy.md"].startswith("duplicate of ")


def test_capped_file_shadowing_a_builtin_is_listed_inactive(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    _put(ws / ".claude" / "agents", "explore", _md("explore"))
    report = loader.report()
    file_row = _entry(report, "explore", "claude")
    assert file_row.active is False
    assert any("shadows the built-in" in w for w in file_row.definition.warnings)
    assert _entry(report, "explore", "builtin").active is True
    assert loader.load()["explore"].source == "built-in"


def test_trusted_file_overriding_a_builtin(tmp_path: Path) -> None:
    loader, ws, store = _loader(tmp_path)
    path = _put(ws / ".crucible" / "agents", "explore", _md("explore"))
    _trust(store, ws, path)
    report = loader.report()
    assert _entry(report, "explore", "builtin").shadowed_by == str(path)
    file_row = _entry(report, "explore", "crucible")
    assert file_row.active and any("overrides the built-in" in w for w in file_row.definition.warnings)


def test_lower_precedence_definition_is_shadowed(tmp_path: Path) -> None:
    loader, ws, store = _loader(tmp_path)
    top = _put(ws / ".crucible" / "agents", "helper", _md("helper"))
    _put(ws / ".claude" / "agents", "helper", _md("helper"))
    _trust(store, ws, top)
    report = loader.report()
    low = _entry(report, "helper", "claude")
    assert (low.active, low.shadowed_by) == (False, str(top))


def test_capped_accept_edits_is_clamped_and_warned(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    _put(ws / ".claude" / "agents", "fast", _md("fast", "permissionMode: acceptEdits\n"))
    row = _entry(loader.report(), "fast", "claude")
    assert row.definition.trust == "capped"
    assert trust_clamped_permission(row.definition) == "default"
    assert any("acceptEdits runs as default" in w for w in row.definition.warnings)


def test_content_is_the_exact_hashed_text_and_omitted_when_huge(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    small = _put(ws / ".crucible" / "agents", "small", _md("small"))
    _put(ws / ".crucible" / "agents", "big", _md("big", body="x" * 70000))
    report = loader.report()
    row = _entry(report, "small", "crucible")
    assert row.content == small.read_text(encoding="utf-8")
    assert hashlib.sha256(row.content.encode()).hexdigest() == row.definition.content_sha256
    assert _entry(report, "big", "crucible").content is None


def test_builtins_have_no_path_and_user_files_are_user_claude(tmp_path: Path) -> None:
    loader, _ws, _ = _loader(tmp_path)
    _put(tmp_path / "home", "mine", _md("mine"))
    report = loader.report()
    assert _entry(report, "general-purpose", "builtin").path is None
    assert _entry(report, "mine", "user_claude").definition.trust == "trusted"
