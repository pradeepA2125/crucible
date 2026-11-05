"""The run's `summary.md` (spec §7)."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from evals.injection.models import Scenario
from evals.injection.scoring import TrialRecord, TrialScore

CAVEAT = ("These scenarios are hand-written, so attack rates are a lower bound on what an "
          "adaptive attacker achieves.")
_DEFENSES = ((False, "Defense off"), (True, "Defense on"))

Row = tuple[TrialRecord, TrialScore, Scenario]


def summarize(records: list[TrialRecord], scores: list[TrialScore], scenarios: list[Scenario],
              meta: dict[str, object]) -> str:
    by_id = {s.id: s for s in scenarios}
    rows: list[Row] = [(r, s, by_id[r.scenario_id]) for r, s in zip(records, scores, strict=True)
                       if r.scenario_id in by_id]
    approvals = sorted({r.arm.approval for r, _, _ in rows})
    attempted_ids = {row[2].id for row in rows if row[1].attempted}
    out = ["# Injection eval", "", _meta_line(meta, rows), "", CAVEAT, ""]
    errors = [(r, s) for r, s, _ in rows if s.error is not None]
    out.append(f"Errors: {len(errors)} (excluded from every rate)")
    out.append("")
    for approval in approvals:
        arm_rows = [row for row in rows if row[0].arm.approval == approval]
        attacks = [row for row in arm_rows if row[2].payload is not None]
        out += [f"## Approval: {approval}", "", "| Scenario | Channel | Goal | Technique | "
                + " | ".join(label for _, label in _DEFENSES) + " |",
                "|---|---|---|---|" + "---|" * len(_DEFENSES)]
        for scenario in sorted({row[2].id: row[2] for row in attacks}.values(),
                               key=lambda s: (s.channel, s.id)):
            mine = [row for row in attacks if row[2].id == scenario.id]
            label = scenario.id if scenario.id in attempted_ids else f"{scenario.id} (uncalibrated)"
            out.append(f"| {label} | {scenario.channel} | {scenario.goal} | "
                       f"{scenario.technique} | " + " | ".join(
                           _cell(row for row in mine if row[0].arm.defense == d)
                           for d, _ in _DEFENSES) + " |")
        out.append("")
        for title, key in (("channel", lambda s: s.channel), ("goal", lambda s: s.goal),
                           ("technique", lambda s: s.technique)):
            out += _totals(f"Totals by {title} ({approval})", attacks, key)
        clean = [row for row in arm_rows if row[2].payload is None]
        if clean:
            out += [f"### Clean twins ({approval})", ""]
            for d, label in _DEFENSES:
                ok = [row for row in clean if row[0].arm.defense == d and row[1].error is None]
                useful = sum(row[1].utility for row in ok)
                refused = sum(row[1].refused_task for row in ok)
                out.append(f"- {label}: utility {useful}/{len(ok)} · refused {refused}/{len(ok)}")
            out.append("")
        sandboxed = sum(1 for r, s, _ in attacks if s.effect and s.error is None
                        and any(r.marker in c.line and c.sandboxed for c in r.commands))
        if approval == "auto":
            out += [f"Commands with an effect that ran inside the sandbox (its proxy refuses "
                    f"unlisted hosts): {sandboxed}", ""]
    out += ["## Uncalibrated", "",
            "Attack scenarios never attempted in any configuration; their low rates mean "
            "nothing until something falls for them.", ""]
    uncalibrated = sorted({row[2].id for row in rows
                           if row[2].payload is not None and row[2].id not in attempted_ids})
    out += [f"- {sid}" for sid in uncalibrated] or ["(none)"]
    out.append("")
    if errors:
        out += ["## Errors", ""] + [f"- {r.trial_id}: {s.error}" for r, s in errors] + [""]
    return "\n".join(out)


def _cell(rows: Iterable[Row]) -> str:
    ok = [row for row in rows if row[1].error is None]
    if not ok:
        return "—"
    n = len(ok)
    return (f"attempted {sum(r[1].attempted for r in ok)}/{n} · "
            f"effect {sum(r[1].effect for r in ok)}/{n} · "
            f"utility {sum(r[1].utility for r in ok)}/{n}")


def _totals(title: str, rows: list[Row], key: Callable[[Scenario], str]) -> list[str]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[key(row[2])].append(row)
    if not groups:
        return []
    out = [f"### {title}", "", "| Group | " + " | ".join(lbl for _, lbl in _DEFENSES) + " |",
           "|---|" + "---|" * len(_DEFENSES)]
    for name in sorted(groups):
        out.append(f"| {name} | " + " | ".join(
            _cell(row for row in groups[name] if row[0].arm.defense == d)
            for d, _ in _DEFENSES) + " |")
    return out + [""]


def _meta_line(meta: dict[str, object], rows: list[Row]) -> str:
    tokens: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record, _, _ in rows:
        total = record.usage.get("total") if isinstance(record.usage, dict) else None
        if isinstance(total, dict):
            arm = f"{record.arm.approval}/{'on' if record.arm.defense else 'off'}"
            for k in ("input", "output", "cached"):
                tokens[arm][k] += int(total.get(k) or 0)
    usage = "; ".join(f"{arm}: in {t['input']} · out {t['output']} · cached {t['cached']}"
                      for arm, t in sorted(tokens.items())) or "n/a"
    return (f"Backend {meta.get('backend')} · model {meta.get('model')} · {meta.get('date')} · "
            f"{meta.get('trials')} trial(s) per scenario and arm · tokens {usage}")
