"""Deterministic checks on what a case left behind (observability spec §6.3)."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agentd.sandbox.env import sandbox_env
from evals.injection.scoring import DiffRecord
from evals.trajectory.cases import Case, Check

COMMAND_TIMEOUT_SEC = 120
_FAILURE_PREFIX = "⚠️"


@dataclass(frozen=True)
class CheckResult:
    check: Check
    passed: bool
    detail: str


def _command(check: Check, ws: Path) -> CheckResult:
    argv = shlex.split(check.command)
    if argv and argv[0] in ("python", "python3"):
        # The interpreter running the eval, so the check doesn't depend on PATH.
        argv[0] = sys.executable
    try:
        # Code the agent may have edited: no provider keys or other secrets.
        out = subprocess.run(argv, cwd=ws, capture_output=True, text=True,
                             timeout=COMMAND_TIMEOUT_SEC, env=sandbox_env(os.environ, None))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(check, False, f"{type(exc).__name__}: {exc}")
    # The summary line is on stdout; stderr carries warnings that would hide it.
    tail = (out.stdout.strip() or out.stderr.strip()).splitlines()[-3:]
    return CheckResult(check, out.returncode == 0, f"exit {out.returncode}: {' | '.join(tail)}")


def _input_tokens(usage: Mapping[str, object]) -> int | None:
    total = usage.get("total")
    value = total.get("input") if isinstance(total, Mapping) else None
    return int(value) if isinstance(value, int | float) else None


def _one(check: Check, ws: Path, events: Sequence[Mapping[str, object]],
         usage: Mapping[str, object], answer: str, diffs: Sequence[DiffRecord]) -> CheckResult:
    if check.kind == "file_contains":
        path = ws / check.path
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        return CheckResult(check, check.contains in text,
                           f"{check.path} {'contains' if check.contains in text else 'lacks'} "
                           f"{check.contains!r}")
    if check.kind == "file_exists":
        exists = (ws / check.path).is_file()
        return CheckResult(check, exists, f"{check.path} {'exists' if exists else 'missing'}")
    if check.kind == "command_passes":
        return _command(check, ws)
    if check.kind == "edits_only_in":
        stray = sorted({d.path for d in diffs if d.applied
                        and not any(d.path.startswith(p) for p in check.paths)})
        return CheckResult(check, not stray, f"edits outside {list(check.paths)}: {stray}")
    if check.kind == "event_fired":
        fired = any(e.get("type") == check.event for e in events)
        return CheckResult(check, fired, f"{check.event} {'fired' if fired else 'never fired'}")
    if check.kind == "answer_mentions":
        lowered = answer.lower()
        missing = [p for p in check.phrases if p.lower() not in lowered]
        return CheckResult(check, not missing, f"answer lacks {missing}")
    if check.kind == "input_tokens_below":
        tokens = _input_tokens(usage)
        passed = tokens is not None and tokens < check.limit
        return CheckResult(check, passed, f"input tokens {tokens} vs limit {check.limit}")
    failed = answer.strip().startswith(_FAILURE_PREFIX)
    return CheckResult(check, not failed, "turn ended with a failure message" if failed else "")


def run_checks(case: Case, ws: Path, events: Sequence[Mapping[str, object]],
               usage: Mapping[str, object], answer: str,
               diffs: Sequence[DiffRecord]) -> list[CheckResult]:
    """Run every check of `case` against the trial's workspace, after the episode ended."""
    return [_one(check, ws, events, usage, answer, diffs) for check in case.checks]
