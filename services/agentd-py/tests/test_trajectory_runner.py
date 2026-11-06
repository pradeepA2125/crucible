"""The offline runner: a case through the real controller, checked and judged (observability
spec §6.3)."""
from __future__ import annotations

import asyncio
import io
import json
import socket
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentd.eval.trajectory.judge import DIMENSIONS
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.telemetry import tracer
from evals.trajectory import run
from evals.trajectory.cases import Case, load_cases
from evals.trajectory.runner import run_case

_SCRIPTED = {"CRUCIBLE_LOCAL_MODELS": "0"}
ANSWER: dict[str, object] = {"type": "answer", "thought": "t",
                             "answer": "pricing applies discount codes to the cart total"}


class _Judge:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, **_kwargs: Any) -> dict[str, object]:
        self.calls += 1
        return {d: {"verdict": "pass", "reason": "ok"} for d in DIMENSIONS}


class _Blocking:
    async def create_controller_step(self, **_kwargs: Any) -> dict[str, object]:
        await asyncio.Event().wait()
        return ANSWER


def _case(case_id: str) -> Case:
    return next(c for c in load_cases() if c.id == case_id)


@pytest.fixture(autouse=True)
def _no_tracer() -> Iterator[None]:
    tracer.reset_for_tests()
    yield
    tracer.reset_for_tests()


@pytest.mark.asyncio
async def test_run_case_with_scripted_engine_records_checks_and_judge(tmp_path: Path) -> None:
    judge = _Judge()
    result = await run_case(
        _case("explain"), tmp_path / "r", repeat=0, run_id="r1",
        engine=ScriptedReasoningEngine(None, [], controller_step_responses=[ANSWER]),
        backend="scripted", model="m", judge_transport=judge, judge_model="j",
        env_overrides=_SCRIPTED)
    assert result.error is None, result.error
    assert [r.passed for r in result.checks] == [True, True]
    assert judge.calls == 1
    assert result.judge is not None and result.judge.verdicts is not None
    assert result.answer.startswith("pricing applies")
    assert result.wall_seconds > 0


@pytest.mark.asyncio
async def test_timeout_is_an_error_not_a_pass(tmp_path: Path) -> None:
    case = replace(_case("explain"), max_seconds=0.5)
    result = await run_case(
        case, tmp_path / "r", repeat=0, run_id="r1", engine=_Blocking(), backend="scripted",
        model="m", judge_transport=None, judge_model="", env_overrides=_SCRIPTED)
    assert result.error is not None and "time cap" in result.error
    assert result.judge is None


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_runner_works_with_phoenix_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path)
    monkeypatch.setenv("CRUCIBLE_TRACING_ENDPOINT",
                       f"http://127.0.0.1:{_closed_port()}/v1/traces")
    code = run.main(
        ["--backend", "scripted", "--case", "explain", "--trace", "--yes"],
        engine_factory=lambda _case: ScriptedReasoningEngine(
            None, [], controller_step_responses=[ANSWER]),
        judge_transport=_Judge())
    assert code == 0
    (out,) = tmp_path.iterdir()
    (line,) = (out / "results.jsonl").read_text().splitlines()
    record = json.loads(line)
    assert record["case_id"] == "explain" and record["error"] is None
    assert record["annotations_posted"] == 0
    assert record["trace_id"]
    assert "explain" in (out / "summary.md").read_text()


def test_cli_asks_before_spending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    code = run.main(["--backend", "scripted", "--case", "explain"],
                    engine_factory=lambda _case: ScriptedReasoningEngine(None, []),
                    judge_transport=_Judge())
    assert code == 1 and list(tmp_path.iterdir()) == []


def test_unknown_case_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path)
    with pytest.raises(SystemExit, match="nope"):
        run.main(["--backend", "scripted", "--case", "nope", "--yes"],
                 engine_factory=lambda _case: ScriptedReasoningEngine(None, []))


@pytest.mark.asyncio
async def test_full_access_case_applies_the_fix_and_its_test_command_passes(
    tmp_path: Path,
) -> None:
    fix: dict[str, object] = {"type": "edit", "thought": "t", "patch_ops": [
        {"op": "search_replace", "file": "shop/pricing.py",
         "search": "return amount * DISCOUNTS[code]",
         "replace": "return amount * (1 - DISCOUNTS[code])", "reason": "discount"}]}
    done: dict[str, object] = {"type": "submit_changes", "thought": "d",
                               "summary": "fixed the discount"}
    result = await run_case(
        _case("fix-discount"), tmp_path / "r", repeat=0, run_id="r1",
        engine=ScriptedReasoningEngine(None, [], controller_step_responses=[fix, done]),
        backend="scripted", model="m", judge_transport=None, judge_model="",
        env_overrides=_SCRIPTED, keep=True)
    assert result.error is None, result.error
    assert [(r.check.kind, r.passed) for r in result.checks] == [
        ("command_passes", True), ("edits_only_in", True)], [r.detail for r in result.checks]
    assert result.episode_dir == tmp_path / "r"
