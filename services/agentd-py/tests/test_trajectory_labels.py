"""Hand labels: exported without the judge's verdicts, frozen once scores are read
(observability spec §6.4)."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from agentd.eval.trajectory.episode import Episode
from agentd.eval.trajectory.judge import DIMENSIONS
from evals.trajectory import calibrate
from evals.trajectory.calibration import (
    LabelItem,
    LabelsChangedError,
    LabelsIncompleteError,
    export_labels,
    freeze_and_read_labels,
    hand_pairs,
)


def _episode(answer: str) -> Episode:
    return Episode(thread_id="t", request_id="m", user_message="fix it", turns=[], agents=[],
                   final_answer=answer, diff="")


def _items() -> list[LabelItem]:
    verdicts: dict[Any, Any] = {d: "pass" for d in DIMENSIONS}
    return [LabelItem("a", _episode("fixed"), {**verdicts, "grounded": "fail"}),
            LabelItem("b", _episode("could not"), None)]


def _fill(path: Path, labels: dict[str, dict[str, str]]) -> None:
    data = json.loads(path.read_text())
    for item in data["items"]:
        item["labels"] = labels[item["item_id"]]
    path.write_text(json.dumps(data))


def test_export_hides_judge_verdicts(tmp_path: Path) -> None:
    out = tmp_path / "labels.json"
    assert export_labels(_items(), out) == 2
    text = out.read_text()
    assert "verdict" not in text and "fail" not in text
    data = json.loads(text)
    assert [i["item_id"] for i in data["items"]] == ["a", "b"]
    assert data["items"][0]["labels"] == {d: None for d in DIMENSIONS}
    assert "fixed" in data["items"][0]["summary"]
    sidecar = json.loads(out.with_suffix(".verdicts.json").read_text())
    assert sidecar["a"]["grounded"] == "fail" and sidecar["b"] is None


def test_labels_changed_after_first_read_are_refused(tmp_path: Path) -> None:
    out = tmp_path / "labels.json"
    export_labels(_items(), out)
    _fill(out, {i: {d: "pass" for d in DIMENSIONS} for i in ("a", "b")})
    first = freeze_and_read_labels(out)
    assert first["a"]["grounded"] == "pass"
    assert freeze_and_read_labels(out) == first
    _fill(out, {i: {d: "fail" for d in DIMENSIONS} for i in ("a", "b")})
    with pytest.raises(LabelsChangedError, match="labels.json changed after"):
        freeze_and_read_labels(out)


def test_incomplete_labels_name_the_item_and_dimension(tmp_path: Path) -> None:
    out = tmp_path / "labels.json"
    export_labels(_items(), out)
    labels = {i: {d: "pass" for d in DIMENSIONS} for i in ("a", "b")}
    labels["b"]["efficient"] = None  # type: ignore[assignment]
    _fill(out, labels)
    with pytest.raises(LabelsIncompleteError, match="b.*efficient"):
        freeze_and_read_labels(out)
    assert not out.with_suffix(".json.lock").exists()


def test_hand_pairs_join_labels_with_the_hidden_verdicts(tmp_path: Path) -> None:
    out = tmp_path / "labels.json"
    export_labels(_items(), out)
    _fill(out, {i: {d: "fail" if d == "grounded" else "pass" for d in DIMENSIONS}
                for i in ("a", "b")})
    pairs = {(p.item_id, p.dimension): p for p in hand_pairs(out)}
    assert (pairs["a", "grounded"].truth, pairs["a", "grounded"].judged) == ("fail", "fail")
    assert pairs["b", "safe"].judged is None  # judge errored on b


def test_score_combines_hand_labels_and_injection_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "labels.json"
    export_labels(_items(), out)
    _fill(out, {i: {d: "pass" for d in DIMENSIONS} for i in ("a", "b")})
    code = calibrate.main(["score", "--labels", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "grounded" in printed and "too few to report" in printed
    report = json.loads((tmp_path / "calibration.json").read_text())
    assert report["dimensions"]["grounded"]["fp"] == 1
    assert report["dimensions"]["grounded"]["judge_errors"] == 1


def test_export_cli_needs_a_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit):
        calibrate.main(["export", "--out", str(tmp_path / "l.json")])


def test_items_from_kept_suite_runs_carry_the_runners_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from agentd.telemetry import tracer
    from evals.trajectory import run
    from evals.trajectory.calibration import items_from_results
    from tests.test_trajectory_runner import ANSWER, _Judge

    tracer.reset_for_tests()
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path / "suite")
    assert run.main(["--backend", "scripted", "--case", "explain", "--keep", "--yes"],
                    engine_factory=lambda _c: ScriptedReasoningEngine(
                        None, [], controller_step_responses=[ANSWER]),
                    judge_transport=_Judge()) == 0
    (out,) = (tmp_path / "suite").iterdir()
    (item,) = items_from_results(out / "results.jsonl")
    assert item.item_id.startswith("explain#0@")
    assert item.verdicts == {d: "pass" for d in DIMENSIONS}
    assert item.episode.user_message == "how does pricing work in this repo?"
    assert item.episode.final_answer.startswith("pricing applies")


def test_score_with_injection_asks_before_judging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "labels.json"
    export_labels(_items(), out)
    _fill(out, {i: {d: "pass" for d in DIMENSIONS} for i in ("a", "b")})
    kept = tmp_path / "kept"
    kept.mkdir()
    injection = tmp_path / "inj.jsonl"
    injection.write_text(json.dumps({"record": {"trial_id": "t"}, "kept_root": str(kept),
                                     "score": {"attempted": False, "utility": True,
                                               "error": None}}) + "\n")
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    code = calibrate.main(["score", "--labels", str(out), "--injection", str(injection),
                           "--judge-backend", "nope"])
    assert code == 1
    printed = capsys.readouterr().out
    assert "1 judge call" in printed and "Cancelled" in printed
    assert not (tmp_path / "calibration.json").exists()


def test_export_refuses_to_overwrite_a_label_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text("")
    out = tmp_path / "labels.json"
    out.write_text('{"items": [{"item_id": "a", "labels": {"safe": "pass"}}]}')
    assert calibrate.main(["export", "--results", str(results), "--out", str(out)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert "safe" in out.read_text()
    assert calibrate.main(["export", "--results", str(results), "--out", str(out),
                           "--force"]) == 0
    assert json.loads(out.read_text()) == {"items": []}


def test_errored_runs_are_not_exported_for_labelling(tmp_path: Path) -> None:
    from evals.trajectory.calibration import items_from_results
    kept = tmp_path / "kept"
    kept.mkdir()
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"case_id": "compaction", "repeat": 0,
                                   "episode_dir": str(kept), "error": "time cap",
                                   "judge": None}) + "\n")
    assert items_from_results(results) == []
