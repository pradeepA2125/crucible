from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import (
    export_bundle_from_task,
    load_task_from_db,
    replay_bundle,
)
from .corpus import init_manifest
from .scoring import score_bundles_root
from .scoring import build_phase1_gate_report


def _write_json(payload: dict[str, Any], output_path: Path | None = None) -> None:
    serialized = json.dumps(payload, indent=2, default=str)
    if output_path is None:
        print(serialized)  # noqa: T201
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized + "\n", encoding="utf-8")


def _cmd_init_manifest(args: argparse.Namespace) -> int:
    manifest = init_manifest(
        workspace_root=Path(args.workspace_root),
        internal_target=args.internal_target,
        oss_target=args.oss_target,
        frozen=args.freeze,
    )
    payload = manifest.model_dump(mode="json")
    _write_json(payload, Path(args.output))
    return 0


def _cmd_export_bundle(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    task = load_task_from_db(db_path, args.task_id)
    artifacts_root = Path(args.artifacts_root) if args.artifacts_root else None
    bundle = export_bundle_from_task(task, db_path=db_path, artifacts_root=artifacts_root)
    _write_json(bundle.model_dump(mode="json"), Path(args.output))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    result = replay_bundle(Path(args.bundle), expected_fingerprint=args.expect_fingerprint)
    payload = result.model_dump(mode="json")
    _write_json(payload, Path(args.output) if args.output else None)
    return 0 if result.deterministic and result.matches_expected else 1


def _cmd_score(args: argparse.Namespace) -> int:
    summary = score_bundles_root(Path(args.bundles_root))
    payload = summary.model_dump(mode="json")
    _write_json(payload, Path(args.output) if args.output else None)
    return 0


def _cmd_weekly_report(args: argparse.Namespace) -> int:
    summary = score_bundles_root(Path(args.bundles_root))
    phase1_gate = None
    if args.baseline_bundles_root:
        baseline_summary = score_bundles_root(Path(args.baseline_bundles_root))
        phase1_gate = build_phase1_gate_report(
            baseline_summary=baseline_summary,
            current_summary=summary,
        ).model_dump(mode="json")
    output_path: Path
    if args.output:
        output_path = Path(args.output)
    else:
        report_name = f"{date.today().isoformat()}-report.json"
        output_path = Path(".tmp/benchmarks") / report_name

    payload = {
        "schema_version": "benchmark-weekly-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundles_root": str(Path(args.bundles_root).resolve()),
        "summary": summary.model_dump(mode="json"),
        "phase1_gate": phase1_gate,
    }
    _write_json(payload, output_path)
    return 0


def _cmd_phase1_gate_report(args: argparse.Namespace) -> int:
    baseline_summary = score_bundles_root(Path(args.baseline_bundles_root))
    current_summary = score_bundles_root(Path(args.bundles_root))
    gate = build_phase1_gate_report(
        baseline_summary=baseline_summary,
        current_summary=current_summary,
    )
    payload = {
        "schema_version": "phase1-gate-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_bundles_root": str(Path(args.baseline_bundles_root).resolve()),
        "bundles_root": str(Path(args.bundles_root).resolve()),
        "baseline_summary": baseline_summary.model_dump(mode="json"),
        "current_summary": current_summary.model_dump(mode="json"),
        "gate": gate.model_dump(mode="json"),
    }
    if args.output:
        _write_json(payload, Path(args.output))
    else:
        report_name = f"{date.today().isoformat()}-phase1-gate.json"
        _write_json(payload, Path(".tmp/benchmarks") / report_name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-editor-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("init-corpus-manifest")
    manifest.add_argument("--output", required=True)
    manifest.add_argument("--workspace-root", default="workspaces")
    manifest.add_argument("--internal-target", type=int, default=100)
    manifest.add_argument("--oss-target", type=int, default=50)
    manifest.add_argument("--freeze", action="store_true")
    manifest.set_defaults(func=_cmd_init_manifest)

    export = sub.add_parser("export-bundle")
    export.add_argument("--db-path", required=True)
    export.add_argument("--task-id", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--artifacts-root")
    export.set_defaults(func=_cmd_export_bundle)

    replay = sub.add_parser("replay-bundle")
    replay.add_argument("--bundle", required=True)
    replay.add_argument("--expect-fingerprint")
    replay.add_argument("--output")
    replay.set_defaults(func=_cmd_replay)

    score = sub.add_parser("score")
    score.add_argument("--bundles-root", required=True)
    score.add_argument("--output")
    score.set_defaults(func=_cmd_score)

    weekly = sub.add_parser("weekly-report")
    weekly.add_argument("--bundles-root", required=True)
    weekly.add_argument("--baseline-bundles-root")
    weekly.add_argument("--output")
    weekly.set_defaults(func=_cmd_weekly_report)

    gate = sub.add_parser("phase1-gate-report")
    gate.add_argument("--bundles-root", required=True)
    gate.add_argument("--baseline-bundles-root", required=True)
    gate.add_argument("--output")
    gate.set_defaults(func=_cmd_phase1_gate_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
