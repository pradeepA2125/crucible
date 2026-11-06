"""Run the trajectory suite: each case through the real controller, checked, judged, and
(with --trace) exported to Phoenix with the judge's scores on its trace (observability spec
§6.3)."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentd.eval.trajectory.annotations import (
    annotations_for,
    phoenix_base_url,
    post_span_annotations,
)
from agentd.eval.trajectory.judge import DIMENSIONS
from agentd.telemetry import tracer
from agentd.telemetry.config import TracingConfig
from evals.trajectory.cases import Case, load_cases
from evals.trajectory.runner import CaseResult, run_case

RESULTS_ROOT = Path(__file__).resolve().parents[2] / ".tmp" / "trajectory-evals"
# The scripted backend exists for the harness's own tests; it never loads local models.
_SCRIPTED_ENV = {"CRUCIBLE_LOCAL_MODELS": "0"}

EngineFactory = Callable[[Case], Any]


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.trajectory.run", description=__doc__)
    p.add_argument("--backend", required=True, help="provider backend, e.g. chatgpt, openai")
    p.add_argument("--model", default="", help="model id (default: the backend's)")
    p.add_argument("--case", action="append", default=[], help="case id (repeatable)")
    p.add_argument("--repeat", type=int, default=1, help="runs per case")
    p.add_argument("--judge-backend", default="", help="judge provider (default: --backend)")
    p.add_argument("--judge-model", default="", help="judge model (default: the backend's)")
    p.add_argument("--trace", action="store_true", help="export traces to Phoenix")
    p.add_argument("--keep", action="store_true", help="keep each run's temp folder")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = p.parse_args(argv)
    if args.repeat < 1:
        p.error("--repeat must be at least 1")
    return args


def _select(ids: list[str]) -> list[Case]:
    cases = load_cases()
    if not ids:
        return cases
    known = {c.id: c for c in cases}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise SystemExit(f"unknown case(s): {', '.join(unknown)}")
    return [known[i] for i in ids]


def _judge(args: argparse.Namespace) -> tuple[Any, str]:
    from agentd.providers.factory import build_transport, resolve_model
    backend = args.judge_backend or args.backend
    # On the agent's own backend the judge defaults to the agent's model: some backends
    # (the ChatGPT plan) have no default model at all.
    same = backend == args.backend
    model = args.judge_model or (args.model if same and args.model else resolve_model(backend))
    return build_transport(backend), model


def main(argv: list[str] | None = None, engine_factory: EngineFactory | None = None,
         judge_transport: Any = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.backend == "scripted" and engine_factory is None:
        print("The scripted backend is for the harness's own tests.", file=sys.stderr)
        return 2
    judge_model = args.judge_model
    try:
        if args.backend != "scripted":
            # Catch a missing key, sign-in or model id now, not as N errored runs later.
            from agentd.providers.factory import build_transport, resolve_model
            args.model = args.model or resolve_model(args.backend)
            build_transport(args.backend)
        if judge_transport is None and (args.backend != "scripted" or args.judge_backend):
            judge_transport, judge_model = _judge(args)
    except Exception as exc:  # noqa: BLE001 — reported, nothing has run yet
        print(f"Provider is not ready: {exc}", file=sys.stderr)
        return 2
    cases = _select(args.case)
    total = len(cases) * args.repeat
    print(f"{total} runs ({len(cases)} cases × {args.repeat}) on {args.backend} "
          f"{args.model or '(default model)'}, judged by "
          f"{args.judge_backend or args.backend} {judge_model or '(default model)'}")
    if not args.yes:
        print("Live runs spend provider usage. Run them? [y/N] ", end="", flush=True)
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
    return asyncio.run(_run_all(args, cases, engine_factory, judge_transport, judge_model))


async def _run_all(args: argparse.Namespace, cases: list[Case],
                   engine_factory: EngineFactory | None, judge_transport: Any,
                   judge_model: str) -> int:
    started = datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    out = RESULTS_ROOT / started.strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    config = TracingConfig.from_env()
    if args.trace:
        tracer.init_tracing(TracingConfig(True, config.endpoint, config.project,
                                          config.capture_content),
                            version="eval", backend=args.backend)
    env = _SCRIPTED_ENV if args.backend == "scripted" else {}
    results: list[CaseResult] = []
    try:
        with (out / "results.jsonl").open("w", encoding="utf-8") as lines:
            for case in cases:
                for repeat in range(args.repeat):
                    root = Path(tempfile.mkdtemp(prefix="crucible-traj-"))
                    engine = engine_factory(case) if engine_factory is not None else None
                    result = await run_case(
                        case, root, repeat=repeat, run_id=run_id, engine=engine,
                        backend=args.backend, model=args.model,
                        judge_transport=judge_transport, judge_model=judge_model,
                        env_overrides=env, keep=args.keep)
                    if args.trace:
                        tracer.flush()
                        if result.judge is not None and result.span_id:
                            result.annotations_posted = await post_span_annotations(
                                phoenix_base_url(config.endpoint),
                                annotations_for(result.judge, result.span_id))
                    if not args.keep:
                        shutil.rmtree(root, ignore_errors=True)
                    results.append(result)
                    lines.write(json.dumps(_jsonable(result), ensure_ascii=False) + "\n")
                    lines.flush()
                    print(f"  {case.id}#{repeat}: {_status(result)}", flush=True)
    finally:
        if args.trace:
            await tracer.shutdown_tracing()
    meta = {"backend": args.backend, "model": args.model or "(default)", "run_id": run_id,
            "date": started.strftime("%Y-%m-%d %H:%M UTC")}
    (out / "summary.md").write_text(summarize(results, meta, traced=args.trace),
                                    encoding="utf-8")
    print(f"Results: {out / 'results.jsonl'}")
    print(f"Summary: {out / 'summary.md'}")
    return 0


def _status(result: CaseResult) -> str:
    if result.error is not None:
        return f"error: {result.error}"
    passed = sum(c.passed for c in result.checks)
    verdicts = ("judge error" if result.judge is None or result.judge.verdicts is None
                else " ".join(f"{d}={result.judge.verdicts[d].verdict}" for d in DIMENSIONS))
    return f"checks {passed}/{len(result.checks)}, {verdicts}"


def summarize(results: list[CaseResult], meta: dict[str, str], *, traced: bool) -> str:
    header = "| case | checks | " + " | ".join(DIMENSIONS) + " | input tokens | seconds |"
    rows = [f"# Trajectory eval {meta['run_id']}", "",
            f"{meta['backend']} {meta['model']}, {meta['date']}", "", header,
            "|" + "---|" * (len(DIMENSIONS) + 4)]
    for r in results:
        total = r.usage.get("total")
        tokens = total.get("input", "") if isinstance(total, dict) else ""
        if r.error is not None:
            cells = ["error"] * len(DIMENSIONS)
        elif r.judge is None or r.judge.verdicts is None:
            cells = ["—"] * len(DIMENSIONS)
        else:
            cells = [r.judge.verdicts[d].verdict for d in DIMENSIONS]
        checks = f"{sum(c.passed for c in r.checks)}/{len(r.checks)}"
        rows.append(f"| {r.case_id}#{r.repeat} | {checks} | {' | '.join(cells)} | {tokens} | "
                    f"{r.wall_seconds:.0f} |")
    errors = [r for r in results if r.error is not None]
    if errors:
        rows += ["", "## Errors", ""] + [f"- {r.case_id}#{r.repeat}: {r.error}" for r in errors]
    if traced:
        rows += ["", f"Traces: Phoenix project, filter `crucible.eval_run_id == "
                     f"\"{meta['run_id']}\"`."]
    return "\n".join(rows) + "\n"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
