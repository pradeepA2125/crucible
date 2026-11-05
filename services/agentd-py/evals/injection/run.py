"""`python -m evals.injection.run` — run the injection scenarios and write the report.

Live runs spend provider or ChatGPT plan usage, so the trial count is shown and confirmed
first unless --yes is passed."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.injection.models import Arm, Scenario, Trial
from evals.injection.report import summarize
from evals.injection.scenarios import load_scenarios, new_marker
from evals.injection.scoring import TrialRecord, TrialScore, score_trial
from evals.injection.trial import EngineFactory, run_trial

RESULTS_ROOT = Path(__file__).resolve().parents[2] / ".tmp" / "injection-evals"
# The scripted backend exists for the harness's own tests; it never loads local models.
_SCRIPTED_ENV = {"CRUCIBLE_LOCAL_MODELS": "0"}


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.injection.run", description=__doc__)
    p.add_argument("--backend", required=True, help="provider backend, e.g. chatgpt, openai")
    p.add_argument("--model", default="", help="model id (default: the backend's)")
    p.add_argument("--defense", choices=("on", "off", "both"), default="both")
    p.add_argument("--approval", default="ask,auto", help="comma list of ask, auto")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--scenario", action="append", default=[], help="scenario id (repeatable)")
    p.add_argument("--attacks-only", action="store_true",
                   help="skip the clean twins (no utility baseline, half the trials)")
    p.add_argument("--max-seconds", type=float, default=300.0)
    p.add_argument("--keep", action="store_true", help="keep each trial's temp folder")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = p.parse_args(argv)
    approvals = [a.strip() for a in args.approval.split(",") if a.strip()]
    if not approvals or any(a not in ("ask", "auto") for a in approvals):
        p.error("--approval takes ask, auto or both (comma-separated)")
    if args.trials < 1:
        p.error("--trials must be at least 1")
    args.approvals = approvals
    return args


def _select(ids: list[str]) -> list[Scenario]:
    scenarios = load_scenarios()
    if not ids:
        return scenarios
    known = {s.id: s for s in scenarios}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(unknown)}")
    return [known[i] for i in ids]


def main(argv: list[str] | None = None, engine_factory: EngineFactory | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.backend == "scripted" and engine_factory is None:
        print("The scripted backend is for the harness's own tests.", file=sys.stderr)
        return 2
    if args.backend != "scripted":
        # Catch a missing key, sign-in or model id now, not as N errored trials later.
        try:
            from agentd.providers.factory import build_transport, resolve_model
            args.model = args.model or resolve_model(args.backend)
            build_transport(args.backend)
        except Exception as exc:  # noqa: BLE001 — reported, nothing has run yet
            print(f"Provider {args.backend} is not ready: {exc}", file=sys.stderr)
            return 2
    scenarios = _select(args.scenario)
    if args.attacks_only:
        scenarios = [s for s in scenarios if s.payload is not None]
    defenses = {"on": [True], "off": [False], "both": [False, True]}[args.defense]
    arms = [Arm(defense=d, approval=a) for a in args.approvals for d in defenses]
    total = len(scenarios) * len(arms) * args.trials
    print(f"{total} trials ({len(scenarios)} scenarios × {len(arms)} arms × "
          f"{args.trials} trials) on {args.backend} {args.model or '(default model)'}")
    if not args.yes:
        print("Live trials spend provider usage. Run them? [y/N] ", end="", flush=True)
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
    return asyncio.run(_run_all(args, scenarios, arms, engine_factory))


async def _run_all(args: argparse.Namespace, scenarios: list[Scenario], arms: list[Arm],
                   engine_factory: EngineFactory | None) -> int:
    started = datetime.now(UTC)
    out = RESULTS_ROOT / started.strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    env = _SCRIPTED_ENV if args.backend == "scripted" else {}
    records: list[TrialRecord] = []
    scores: list[TrialScore] = []
    with (out / "results.jsonl").open("w", encoding="utf-8") as results:
        for scenario in scenarios:
            for arm in arms:
                for index in range(args.trials):
                    trial = Trial(scenario, arm, index, new_marker(scenario.id))
                    root = Path(tempfile.mkdtemp(prefix="crucible-inj-"))
                    record = await run_trial(
                        trial, root, engine_factory, args.max_seconds,
                        backend=args.backend, model=args.model, env_overrides=env)
                    score = score_trial(record, scenario)
                    records.append(record)
                    scores.append(score)
                    line = {"record": _jsonable(record), "score": _jsonable(score)}
                    if args.keep:
                        line["kept_root"] = str(root)
                    else:
                        shutil.rmtree(root, ignore_errors=True)
                    results.write(json.dumps(line, ensure_ascii=False) + "\n")
                    results.flush()
                    status = score.error or (
                        f"attempted={score.attempted} effect={score.effect} "
                        f"utility={score.utility}")
                    print(f"  {record.trial_id}: {status}", flush=True)
    meta = {"backend": args.backend, "model": args.model or "(default)",
            "date": started.strftime("%Y-%m-%d %H:%M UTC"), "trials": args.trials}
    (out / "summary.md").write_text(summarize(records, scores, scenarios, meta), encoding="utf-8")
    print(f"Results: {out / 'results.jsonl'}")
    print(f"Summary: {out / 'summary.md'}")
    return 0


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
