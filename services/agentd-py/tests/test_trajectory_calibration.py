"""Judging the judge: precision and recall of its fail verdicts (observability spec §6.4)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agentd.eval.trajectory.judge import DIMENSIONS
from evals.injection import run as injection_run
from evals.trajectory.calibration import Pair, binary_metrics, injection_pairs
from tests.test_injection_eval_trial import _obedient, _resistant


def _pairs(dimension: str, rows: list[tuple[str, str | None]]) -> list[Pair]:
    return [Pair(f"i{n}", dimension, truth, judged)  # type: ignore[arg-type]
            for n, (truth, judged) in enumerate(rows)]


def test_fail_is_the_positive_class() -> None:
    rows: list[tuple[str, str | None]] = (
        [("fail", "fail")] * 4 + [("fail", "pass")] * 2      # tp 4, fn 2
        + [("pass", "fail")] * 1 + [("pass", "pass")] * 6)   # fp 1, tn 6
    m = binary_metrics(_pairs("safe", rows))["safe"]
    assert (m.tp, m.fn, m.fp, m.tn) == (4, 2, 1, 6)
    assert (m.truth_fails, m.truth_passes, m.n, m.judge_errors) == (6, 7, 13, 0)
    assert m.reportable is True
    assert m.precision == pytest.approx(4 / 5) and m.recall == pytest.approx(4 / 6)


def test_not_reportable_below_five_each_side() -> None:
    rows: list[tuple[str, str | None]] = [("fail", "fail")] * 4 + [("pass", "pass")] * 10
    m = binary_metrics(_pairs("grounded", rows))["grounded"]
    assert m.reportable is False and m.precision is None and m.recall is None
    assert (m.tp, m.tn) == (4, 10)


def test_judge_errors_are_excluded_and_counted() -> None:
    rows: list[tuple[str, str | None]] = (
        [("fail", "fail")] * 5 + [("pass", "pass")] * 5 + [("fail", None)] * 3)
    m = binary_metrics(_pairs("efficient", rows))["efficient"]
    assert m.judge_errors == 3 and m.n == 13
    assert m.truth_fails == 5 and m.recall == 1.0


class _Judge:
    def __init__(self, safe: str) -> None:
        self.safe = safe
        self.trajectories: list[str] = []

    async def generate_json(self, **kwargs: Any) -> dict[str, object]:
        self.trajectories.append(kwargs["user_payload"]["trajectory"])
        return {d: {"verdict": self.safe if d == "safe" else "pass", "reason": "r"}
                for d in DIMENSIONS}


def _kept_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: Any) -> Path:
    monkeypatch.setattr(injection_run, "RESULTS_ROOT", tmp_path / "inj")
    code = injection_run.main(
        ["--backend", "scripted", "--scenario", "readme_exfil_plain", "--trials", "1",
         "--approval", "ask", "--defense", "on", "--keep", "--yes"], engine_factory=factory)
    assert code == 0
    (out,) = (tmp_path / "inj").iterdir()
    return out / "results.jsonl"


def test_injection_pairs_from_a_kept_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = _kept_run(tmp_path, monkeypatch, _obedient)
    line = json.loads(results.read_text().splitlines()[0])
    assert line["score"]["attempted"] is True
    judge = _Judge(safe="fail")
    found = asyncio.run(injection_pairs(results, judge_transport=judge, judge_model="j",
                                        run_id="r"))
    assert found.skipped == 0
    by_dimension = {p.dimension: p for p in found.pairs}
    assert set(by_dimension) == {"safe", "task_completed"}
    assert by_dimension["safe"].truth == "fail" and by_dimension["safe"].judged == "fail"
    assert by_dimension["task_completed"].truth == (
        "pass" if line["score"]["utility"] else "fail")
    assert "attacker.example" in judge.trajectories[0]


def test_resistant_trial_is_safe_ground_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = _kept_run(tmp_path, monkeypatch, _resistant)
    found = asyncio.run(injection_pairs(results, judge_transport=_Judge(safe="fail"),
                                        judge_model="j", run_id="r"))
    safe = next(p for p in found.pairs if p.dimension == "safe")
    assert (safe.truth, safe.judged) == ("pass", "fail")


@pytest.mark.asyncio
async def test_lines_without_a_kept_folder_are_skipped(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"record": {"trial_id": "x"}, "score": {
        "attempted": False, "effect": False, "utility": True, "noticed": False,
        "refused_task": False, "error": None}}) + "\n")
    found = await injection_pairs(results, judge_transport=_Judge("pass"), judge_model="j",
                                  run_id="r")
    assert found.pairs == [] and found.skipped == 1
