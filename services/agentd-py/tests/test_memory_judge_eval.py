import copy
import json
from pathlib import Path

import pytest

from agentd.eval.memory_judges import Rule, compare_rules, metrics, selected, validate_matrix

MATRIX = (Path(__file__).parents[3]
          / "docs/superpowers/evals/2026-10-15-memory-recall-matrix-v2.json")


def cases():
    return json.loads(MATRIX.read_text())["cases"]


def scores(data):
    return {case["id"]: {candidate["id"]: {"Q": .95 if candidate["label"] == "needed" else .01,
                                           "C": .01, "R": .01}
                         for candidate in case["candidates"]} for case in data}


def test_matrix_minimum_labels_and_predeclared_split():
    data = cases()
    validate_matrix(data)
    with pytest.raises(ValueError, match="five cases"):
        validate_matrix(data[:-1])
    bad = copy.deepcopy(data)
    bad[0]["candidates"][0]["label"] = "maybe"
    with pytest.raises(ValueError, match="valid label"):
        validate_matrix(bad)


def test_fit_never_uses_held_out_scores_or_labels():
    data = cases()
    values = scores(data)
    before = compare_rules(data, values)
    for case in data:
        if case["split"] == "held_out":
            for candidate in case["candidates"]:
                candidate["label"] = "harmful"
                values[case["id"]][candidate["id"]] = {"Q": .99, "C": .99, "R": .99}
    after = compare_rules(data, values)
    assert before["selected"] == after["selected"]
    assert before["development_grid"] == after["development_grid"]
    assert before["held_out"] != after["held_out"]


def test_invalid_ids_harmful_deliveries_and_misses_are_counted_separately():
    data = cases()
    report = metrics(data, {data[0]["id"]: ["unknown", "source-ids"],
                            data[-1]["id"]: ["file-evidence"]})
    assert report["invalid_ids"] == 1 and report["harmful"] == 1
    assert report["injected"] == 2 and report["hits"] == 1
    assert report["miss_rate"] == 29 / 30
    with pytest.raises(ValueError, match="delivery cap"):
        metrics(data, {data[0]["id"]: ["a", "b", "c", "d", "e"]})


def test_scoring_refuses_missing_or_nonfinite_pairs():
    data = cases()
    values = scores(data)
    del values[data[0]["id"]]["repo-scope"]
    with pytest.raises(ValueError, match="every candidate"):
        selected(data, values, Rule("max", .5))
    values = scores(data)
    values[data[0]["id"]]["repo-scope"]["Q"] = float("nan")
    with pytest.raises(ValueError, match="finite sigmoid"):
        selected(data, values, Rule("max", .5))
