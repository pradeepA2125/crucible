"""Offline recall-judge calibration. Labels are frozen before any score is read."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Label = Literal["needed", "harmless", "harmful"]


@dataclass(frozen=True)
class Rule:
    family: Literal["max", "weighted", "q_gated"]
    threshold: float
    context_threshold: float = .8
    weights: tuple[float, float, float] = (.6, .25, .15)

    def accepts(self, scores: Mapping[str, float]) -> bool:
        q, c, r = (scores.get(key, 0.) for key in ("Q", "C", "R"))
        if self.family == "max":
            return max(q, c, r) >= self.threshold
        if self.family == "weighted":
            return sum(value * weight for value, weight in zip(
                (q, c, r), self.weights, strict=True,
            )) >= self.threshold
        return q >= self.threshold or c >= self.context_threshold and r >= self.context_threshold

    def rank(self, scores: Mapping[str, float]) -> float:
        q, c, r = (scores.get(key, 0.) for key in ("Q", "C", "R"))
        if self.family == "q_gated":
            return max(q, min(c, r))
        if self.family == "weighted":
            return sum(value * weight for value, weight in zip(
                (q, c, r), self.weights, strict=True,
            ))
        return max(q, c, r)


def validate_matrix(cases: Sequence[Mapping[str, object]]) -> None:
    patterns: dict[str, int] = {}
    ids: set[str] = set()
    for case in cases:
        key = str(case["id"])
        if key in ids:
            raise ValueError("Repeated evaluation case")
        ids.add(key)
        pattern = str(case["pattern"])
        if pattern not in {"".join(bits) for bits in itertools.product("01", repeat=3)}:
            raise ValueError("Invalid Q/C/R pattern")
        if case.get("split") not in {"development", "held_out"}:
            raise ValueError("Evaluation cases need a predeclared split")
        candidates = case.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Evaluation case has no candidates")
        candidate_ids = set()
        for candidate in candidates:
            if (not isinstance(candidate, dict) or candidate.get("label") not in {
                "needed", "harmless", "harmful",
            } or not isinstance(candidate.get("id"), str)):
                raise ValueError("Every candidate needs a valid label and ID")
            if candidate["id"] in candidate_ids:
                raise ValueError("Repeated candidate ID")
            candidate_ids.add(candidate["id"])
        patterns[pattern] = patterns.get(pattern, 0) + 1
    if len(patterns) != 8 or min(patterns.values()) < 5:
        raise ValueError("Matrix requires five cases in each of eight patterns")


def metrics(
    cases: Sequence[Mapping[str, object]], chosen: Mapping[str, Sequence[str]],
) -> dict[str, object]:
    injected, needed, hits, harmful, invalid, cases_without = 0, 0, 0, 0, 0, 0
    for case in cases:
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        labels = {item["id"]: item["label"] for item in candidates}
        delivered = list(dict.fromkeys(chosen.get(str(case["id"]), [])))
        if len(delivered) > 4:
            raise ValueError("Judge exceeded the delivery cap")
        invalid += sum(key not in labels for key in delivered)
        delivered = [key for key in delivered if key in labels]
        needed += sum(label == "needed" for label in labels.values())
        injected += len(delivered)
        hits += sum(labels[key] == "needed" for key in delivered)
        harmful += sum(labels[key] == "harmful" for key in delivered)
        cases_without += not delivered
    return {"cases": len(cases), "injected": injected, "needed": needed, "hits": hits,
            "harmful": harmful, "invalid_ids": invalid, "empty_cases": cases_without,
            "precision": hits / injected if injected else None,
            "miss_rate": (needed - hits) / needed if needed else None}


def selected(
    cases: Sequence[Mapping[str, object]], scores: Mapping[str, Mapping[str, Mapping[str, float]]],
    rule: Rule,
) -> dict[str, list[str]]:
    chosen = {}
    for case in cases:
        case_id = str(case["id"])
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        allowed = {item["id"] for item in candidates}
        if set(scores.get(case_id, {})) != allowed:
            raise ValueError("Scores must cover every candidate exactly once")
        if any(set(values) != {"Q", "C", "R"}
               or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values.values())
               for values in scores[case_id].values()):
            raise ValueError("Expected finite sigmoid scores for Q/C/R")
        accepted = [(key, values) for key, values in scores[case_id].items()
                    if rule.accepts(values)]
        accepted.sort(key=lambda pair: (rule.rank(pair[1]), pair[0]), reverse=True)
        chosen[case_id] = [key for key, _values in accepted[:4]]
    return chosen


def compare_rules(
    cases: Sequence[Mapping[str, object]],
    scores: dict[str, dict[str, dict[str, float]]],
) -> dict[str, object]:
    validate_matrix(cases)
    development = [case for case in cases if case["split"] == "development"]
    held_out = [case for case in cases if case["split"] == "held_out"]
    rules = [Rule("max", threshold) for threshold in (.3, .5, .65, .8, .9)]
    rules += [Rule("weighted", threshold, weights=weights)
              for threshold in (.2, .3, .5, .65, .8)
              for weights in ((.6, .25, .15), (.8, .15, .05), (.34, .33, .33))]
    rules += [Rule("q_gated", q, context) for q in (.3, .5, .65, .8, .9)
              for context in (.5, .65, .8, .9)]
    results = [{"rule": asdict(rule), "development": metrics(
        development, selected(development, scores, rule),
    )} for rule in rules]

    def rank(index: int) -> tuple[float, float, float]:
        values = results[index]["development"]
        assert isinstance(values, dict)
        # Predeclared conservative objective: first avoid harmful deliveries,
        # then maximize precision, then reduce misses. Held-out labels never fit.
        return (-float(values["harmful"]), float(values["precision"] or 0),
                -float(values["miss_rate"] if values["miss_rate"] is not None else 1))

    winners = {}
    for family in ("max", "weighted", "q_gated"):
        indices = [i for i, rule in enumerate(rules) if rule.family == family]
        winner = max(indices, key=rank)
        winners[family] = {**results[winner], "held_out": metrics(
            held_out, selected(held_out, scores, rules[winner]),
        )}
    winner = max(range(len(rules)), key=rank)
    return {"selection_objective": "min harmful, max precision, min miss (development only)",
            "selected": asdict(rules[winner]), "families": winners, "development_grid": results,
            "held_out": metrics(held_out, selected(held_out, scores, rules[winner])),
            "provisional": metrics(held_out, selected(held_out, scores, Rule("q_gated", .65))),
            "no_memory": metrics(cases, {})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("scores", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.matrix.read_text())["cases"]
    report = compare_rules(cases, json.loads(args.scores.read_text())["scores"])
    args.output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
