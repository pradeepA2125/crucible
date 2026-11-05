"""The injection eval's summary report and CLI."""
from __future__ import annotations

import io
import re
from dataclasses import replace
from pathlib import Path

from evals.injection import run
from evals.injection.models import Arm, Trial
from evals.injection.report import summarize
from evals.injection.scenarios import load_scenarios
from evals.injection.scoring import TrialRecord, TrialScore

META = {"backend": "scripted", "model": "m", "date": "2026-11-05", "trials": 2}


def _record(scenario_id: str, error: str | None = None) -> TrialRecord:
    return TrialRecord(
        trial_id=f"{scenario_id}:on:ask:0", scenario_id=scenario_id,
        arm=Arm(defense=True, approval="ask"), marker="inj-x-000000", gates=[], commands=[],
        tool_events=[], diffs=[], answer="", sink=[], workspace=Path("."), error=error,
        usage={"total": {"requests": 1, "input": 10, "output": 2, "cached": 0}},
    )


def _scenarios():
    base = next(s for s in load_scenarios() if s.id == "readme_exfil_plain")
    return [replace(base, id="A"), replace(base, id="B")]


def test_summary_rates_exclude_errors_and_flag_uncalibrated():
    records = [_record("A"), _record("A", error="time cap"), _record("B")]
    scores = [TrialScore(True, False, True, False, False, None),
              TrialScore(False, False, False, False, False, "time cap"),
              TrialScore(False, False, True, False, False, None)]
    md = summarize(records, scores, _scenarios(), META)
    assert "attempted 1/1" in md  # the error trial is not in N
    assert "Errors: 1" in md
    assert re.search(r"## Uncalibrated\n.*\bB\b", md, re.S)
    assert not re.search(r"## Uncalibrated\n[^#]*\bA\b", md)
    assert "lower bound" in md


def test_cli_refuses_without_confirmation(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert run.main(["--backend", "chatgpt", "--scenario", "readme_exfil_plain"]) == 1
    assert "trials" in capsys.readouterr().out


class _Resistant:
    async def create_controller_step(self, **_kwargs):  # type: ignore[no-untyped-def]
        return {"type": "answer", "thought": "t", "answer": "Helpers for dates."}


def _resistant(_trial: Trial, _ws: Path) -> _Resistant:
    return _Resistant()


def test_cli_end_to_end_with_scripted_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path)
    code = run.main(["--backend", "scripted", "--scenario", "readme_exfil_plain", "--trials", "1",
                     "--approval", "ask", "--defense", "on", "--yes"], engine_factory=_resistant)
    assert code == 0
    out = next(tmp_path.iterdir())
    assert len((out / "results.jsonl").read_text().splitlines()) == 1
    assert (out / "summary.md").exists()
