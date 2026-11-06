"""Judging the judge (observability spec §6.4).

  export  write a label file (no judge verdicts in it) from kept suite runs and, optionally,
          real requests from a workspace's chat history (judged at export time)
  score   compare the judge with the frozen hand labels and, optionally, with a kept
          injection run's deterministic scores; prints precision and recall per dimension
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agentd.chat.storage import ChatThreadStore
from agentd.eval.trajectory.judge import DIMENSIONS
from evals.trajectory.calibration import (
    DimensionMetrics,
    Pair,
    binary_metrics,
    export_labels,
    hand_pairs,
    harvest_items,
    injection_pairs,
    items_from_results,
)


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.trajectory.calibrate",
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--results", type=Path, help="a suite results.jsonl run with --keep")
    export.add_argument("--harvest-db", type=Path, help="a workspace's chat.sqlite3")
    export.add_argument("--harvest-workspace", type=Path, help="that workspace's folder")
    export.add_argument("--limit", type=int, default=30, help="most requests to harvest")
    export.add_argument("--out", type=Path, required=True)
    score = sub.add_parser("score")
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--injection", type=Path, help="an injection results.jsonl run with --keep")
    for parser in (export, score):
        parser.add_argument("--judge-backend", default="")
        parser.add_argument("--judge-model", default="")
    args = p.parse_args(argv)
    if args.command == "export":
        if args.results is None and args.harvest_db is None:
            p.error("export needs --results and/or --harvest-db")
        if (args.harvest_db is None) != (args.harvest_workspace is None):
            p.error("--harvest-db and --harvest-workspace go together")
    return args


def _judge(args: argparse.Namespace) -> tuple[Any, str]:
    if not args.judge_backend:
        raise SystemExit("judging needs --judge-backend (and optionally --judge-model)")
    from agentd.providers.factory import build_transport, resolve_model
    return build_transport(args.judge_backend), args.judge_model or resolve_model(
        args.judge_backend)


def _table(metrics: dict[str, DimensionMetrics]) -> str:
    rows = ["| dimension | n | judge errors | truth pass/fail | precision | recall |",
            "|---|---|---|---|---|---|"]
    for dimension in DIMENSIONS:
        m = metrics.get(dimension)
        if m is None:
            continue
        p = f"{m.precision:.2f}" if m.precision is not None else "too few to report"
        r = f"{m.recall:.2f}" if m.recall is not None else "too few to report"
        rows.append(f"| {dimension} | {m.n} | {m.judge_errors} | "
                    f"{m.truth_passes}/{m.truth_fails} | {p} | {r} |")
    return "\n".join(rows)


async def _score(args: argparse.Namespace) -> int:
    pairs: list[Pair] = hand_pairs(args.labels)
    sources: dict[str, dict[str, Any]] = {"hand": {k: asdict(v) for k, v in
                                                   binary_metrics(pairs).items()}}
    print("Hand labels (fail is the positive class):")
    print(_table(binary_metrics(pairs)))  # type: ignore[arg-type]
    if args.injection is not None:
        transport, model = _judge(args)
        found = await injection_pairs(args.injection, judge_transport=transport,
                                      judge_model=model, run_id="calibration")
        injected = binary_metrics(found.pairs)
        sources["injection"] = {k: asdict(v) for k, v in injected.items()}
        print(f"\nInjection trials ({found.skipped} skipped):")
        print(_table(injected))  # type: ignore[arg-type]
        pairs = pairs + found.pairs
    combined = binary_metrics(pairs)
    report = {"dimensions": {k: asdict(v) for k, v in combined.items()}, "sources": sources}
    out = args.labels.parent / "calibration.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWritten: {out}")
    return 0


async def _export(args: argparse.Namespace) -> int:
    items = items_from_results(args.results) if args.results is not None else []
    if args.harvest_db is not None:
        transport, model = _judge(args)
        items += await harvest_items(ChatThreadStore(args.harvest_db), args.harvest_workspace,
                                     args.limit, judge_transport=transport, judge_model=model,
                                     run_id="calibration")
    count = export_labels(items, args.out)
    print(f"{count} items written to {args.out}; fill in every pass/fail label, then run "
          "`score`. The file is frozen the first time it is scored.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_score(args) if args.command == "score" else _export(args))


if __name__ == "__main__":
    raise SystemExit(main())
