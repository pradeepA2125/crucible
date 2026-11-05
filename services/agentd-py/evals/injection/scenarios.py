"""Loading scenario files and building a trial's workspace from one."""
from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import Any, get_args

import yaml

from evals.injection.models import Allow, BenignCheck, Channel, Goal, Scenario, Technique

PACKAGE_DIR = Path(__file__).resolve().parent
SCENARIO_DIR = PACKAGE_DIR / "scenarios"
WORKSPACES_DIR = PACKAGE_DIR / "fixtures" / "workspaces"
PLACEHOLDER = "@@PAYLOAD@@"
MCP_CONTENT_FILE = ".crucible/eval-mcp-content.json"

_REQUIRED = (
    "id", "channel", "goal", "technique", "user_message", "workspace", "payload",
    "payload_target", "benign_check",
)
# Readable by the model, carries no instruction: the invisible payload rides behind it.
_INVISIBLE_COVER = "Last reviewed by the docs team."


class ScenarioError(ValueError):
    """A scenario file is malformed; the message names the file."""


def new_marker(scenario_id: str) -> str:
    return f"inj-{scenario_id}-{secrets.token_hex(3)}"


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    raw: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ScenarioError(f"{path.name}: not valid YAML: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise ScenarioError(f"{path.name}: expected a mapping with a string `id`")
        if data["id"] in raw:
            raise ScenarioError(f"{path.name}: duplicate id {data['id']!r}")
        raw[data["id"]] = (path, data)

    scenarios: list[Scenario] = []
    for path, data in raw.values():
        clean_of = data.get("clean_of")
        if clean_of is not None:
            # A clean twin inherits its attack's fields and overrides only what it names.
            if clean_of not in raw:
                raise ScenarioError(f"{path.name}: clean_of names unknown scenario {clean_of!r}")
            base = raw[clean_of][1]
            if base.get("clean_of") is not None:
                raise ScenarioError(f"{path.name}: clean_of must name an attack, not a clean twin")
            data = {**base, **data, "payload": None}
        scenarios.append(_build(path, data))
    return sorted(scenarios, key=lambda s: s.id)


def _build(path: Path, data: dict[str, Any]) -> Scenario:
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise ScenarioError(f"{path.name}: missing field(s) {', '.join(missing)}")
    _check_enum(path, "channel", data["channel"], get_args(Channel))
    _check_enum(path, "goal", data["goal"], get_args(Goal))
    _check_enum(path, "technique", data["technique"], get_args(Technique))
    payload = data["payload"]
    if payload is not None and (not isinstance(payload, str) or "{marker}" not in payload):
        raise ScenarioError(f"{path.name}: an attack payload must be text containing {{marker}}")
    workspace = WORKSPACES_DIR / str(data["workspace"])
    if not workspace.is_dir():
        raise ScenarioError(
            f"{path.name}: workspace {data['workspace']!r} not found in {WORKSPACES_DIR}")
    target = str(data["payload_target"])
    if not target.startswith(("file:", "mcp:issue:", "mcp:page:")):
        raise ScenarioError(
            f"{path.name}: payload_target must start with file:, mcp:issue: or mcp:page:")
    allow = data.get("allow") or {}
    return Scenario(
        id=str(data["id"]),
        channel=data["channel"],
        goal=data["goal"],
        technique=data["technique"],
        user_message=str(data["user_message"]),
        workspace=workspace,
        payload=payload,
        payload_target=target,
        user_trusted=tuple(data.get("user_trusted") or ()),
        benign_check=_benign_check(path, data["benign_check"]),
        allow=Allow(
            commands=tuple(allow.get("commands") or ()),
            mcp_tools=tuple(allow.get("mcp_tools") or ()),
            edit_paths=tuple(allow.get("edit_paths") or ()),
        ),
        uses_mcp=bool(data.get("uses_mcp", False)),
        uses_skills=bool(data.get("uses_skills", False)),
        clean_of=data.get("clean_of"),
    )


def _check_enum(path: Path, field: str, value: object, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ScenarioError(f"{path.name}: {field} {value!r} is not one of {', '.join(allowed)}")


def _benign_check(path: Path, spec: object) -> BenignCheck:
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ScenarioError(f"{path.name}: benign_check must have exactly one of "
                            "answer_mentions, file_matches, command_ran")
    (kind, value), = spec.items()
    if kind == "answer_mentions" and isinstance(value, list) and value:
        return BenignCheck("answer_mentions", phrases=tuple(str(v) for v in value))
    if (kind == "file_matches" and isinstance(value, dict)
            and value.get("path") and value.get("contains")):
        return BenignCheck("file_matches", path=str(value["path"]), contains=str(value["contains"]))
    if kind == "command_ran" and isinstance(value, str) and value:
        return BenignCheck("command_ran", pattern=value)
    raise ScenarioError(f"{path.name}: benign_check {kind!r} has a missing or malformed value")


def render_payload(scenario: Scenario, marker: str) -> str:
    """The planted text for one trial; empty for a clean twin."""
    if scenario.payload is None:
        return ""
    text = scenario.payload.replace("{marker}", marker).strip()
    if scenario.technique != "invisible":
        return text
    # Unicode tag characters (U+E0000 + ASCII) render as nothing in editors and terminals.
    hidden = "".join(chr(0xE0000 + ord(c)) for c in text if 0x20 <= ord(c) < 0x7F)
    return _INVISIBLE_COVER + hidden


def materialize(scenario: Scenario, marker: str, dest: Path) -> None:
    """Copy the scenario's workspace to `dest` with this trial's payload in its target.

    Fixture workspaces are shared between scenarios, so every placeholder other than the
    target's is removed."""
    shutil.copytree(scenario.workspace, dest)
    payload = render_payload(scenario, marker)
    target = scenario.payload_target
    if target.startswith("mcp:"):
        _fill_mcp_content(dest, target, payload)
        target_file = None
    else:
        target_file = dest / target.removeprefix("file:")
        if not target_file.exists():
            # A planted file the fixture doesn't ship (an AGENTS.md) is the payload alone.
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(f"{PLACEHOLDER}\n", encoding="utf-8")
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PLACEHOLDER not in text:
            continue
        fill = payload if target_file is not None and path == target_file else ""
        path.write_text(_replace_placeholder(text, fill), encoding="utf-8")


def _replace_placeholder(text: str, fill: str) -> str:
    if fill:
        return text.replace(PLACEHOLDER, fill)
    # Removing the payload leaves no trace: a line that held only the placeholder goes too.
    lines = [line for line in text.splitlines(keepends=True) if line.strip() != PLACEHOLDER]
    return "".join(lines).replace(PLACEHOLDER, "")


def _fill_mcp_content(dest: Path, target: str, payload: str) -> None:
    path = dest / MCP_CONTENT_FILE
    content: dict[str, dict[str, str]] = {"issues": {}, "pages": {}}
    if path.exists():
        content.update(json.loads(path.read_text(encoding="utf-8")))
    _, section, key = target.split(":", 2)
    bucket = content["issues" if section == "issue" else "pages"]
    for name in ("issues", "pages"):
        for k, text in content[name].items():
            fill = payload if content[name] is bucket and k == key else ""
            content[name][k] = _replace_placeholder(text, fill)
    if key not in bucket:
        bucket[key] = payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "MCP_CONTENT_FILE", "PLACEHOLDER", "SCENARIO_DIR", "WORKSPACES_DIR", "ScenarioError",
    "load_scenarios", "materialize", "new_marker", "render_payload",
]
