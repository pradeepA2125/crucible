"""Trajectory cases: one user prompt against a fixture repo, with deterministic checks
(observability spec §6.3)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args

import yaml

from agentd.eval.trajectory.judge import DIMENSIONS, Dimension

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
FIXTURES_DIR = HERE / "fixtures"

CheckKind = Literal["file_contains", "file_exists", "command_passes", "edits_only_in",
                    "event_fired", "answer_mentions", "input_tokens_below",
                    "no_failure_message"]
# ask: every edit and command raises a card the careful user answers; full: Full access,
# edits apply and commands run without a card (the command recorder still decides what runs).
Approval = Literal["ask", "full"]
_CASE_KEYS = frozenset({"id", "prompt", "workspace", "approval", "flags", "allow_commands",
                        "checks", "expected", "max_seconds"})
_CHECK_KEYS = frozenset({"kind", "path", "contains", "command", "paths", "event", "phrases",
                         "limit"})


class CaseError(ValueError):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path.name}: {reason}")


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    path: str = ""
    contains: str = ""
    command: str = ""
    paths: tuple[str, ...] = ()
    event: str = ""
    phrases: tuple[str, ...] = ()
    limit: int = 0


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    workspace: Path
    approval: Approval
    flags: Mapping[str, str]
    allow_commands: tuple[str, ...]
    checks: tuple[Check, ...]
    expected: Mapping[Dimension, Literal["pass", "fail"]]
    max_seconds: float


def _check(path: Path, raw: object) -> Check:
    if not isinstance(raw, dict):
        raise CaseError(path, f"a check must be a mapping, got {raw!r}")
    unknown = set(raw) - _CHECK_KEYS
    if unknown:
        raise CaseError(path, f"unknown check field(s) {sorted(unknown)}")
    kind = raw.get("kind")
    if kind not in get_args(CheckKind):
        raise CaseError(path, f"unknown check kind {kind!r}")
    return Check(kind=cast(CheckKind, kind), path=str(raw.get("path", "")),
                 contains=str(raw.get("contains", "")), command=str(raw.get("command", "")),
                 paths=tuple(str(p) for p in raw.get("paths") or ()),
                 event=str(raw.get("event", "")),
                 phrases=tuple(str(p) for p in raw.get("phrases") or ()),
                 limit=int(raw.get("limit", 0)))


def _case(path: Path, data: dict[str, Any]) -> Case:
    unknown = set(data) - _CASE_KEYS
    if unknown:
        raise CaseError(path, f"unknown field(s) {sorted(unknown)}")
    missing = [k for k in ("id", "prompt", "workspace", "approval") if k not in data]
    if missing:
        raise CaseError(path, f"missing field(s) {', '.join(missing)}")
    workspace = FIXTURES_DIR / str(data["workspace"])
    if not workspace.is_dir():
        raise CaseError(path, f"workspace {data['workspace']!r} not found in {FIXTURES_DIR}")
    if data["approval"] not in get_args(Approval):
        raise CaseError(path, f"approval must be one of {get_args(Approval)}")
    expected = data.get("expected") or {}
    if not isinstance(expected, dict) or any(
            k not in DIMENSIONS or v not in ("pass", "fail") for k, v in expected.items()):
        raise CaseError(path, f"expected must map rubric dimensions to pass/fail: {expected}")
    return Case(
        id=str(data["id"]), prompt=str(data["prompt"]), workspace=workspace,
        approval=cast(Approval, data["approval"]),
        flags={str(k): str(v) for k, v in (data.get("flags") or {}).items()},
        allow_commands=tuple(str(c) for c in data.get("allow_commands") or ()),
        checks=tuple(_check(path, c) for c in data.get("checks") or ()),
        expected=cast(Mapping[Dimension, Literal["pass", "fail"]], expected),
        max_seconds=float(data.get("max_seconds", 600)))


def load_cases(directory: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise CaseError(path, "a case file must be a mapping")
        cases.append(_case(path, data))
    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise CaseError(directory, f"duplicate case id(s) {duplicates}")
    return cases
