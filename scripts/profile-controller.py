#!/usr/bin/env python3
"""Enable per-workspace profiling and report the latest completed controller turn."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/agentd-py'))
from agentd.performance import summarize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'report'])
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--all', action='store_true', help='Aggregate every completed turn')
    args = parser.parse_args()
    state = args.workspace.resolve() / '.crucible/state'
    marker = state / 'performance.enabled'
    if args.command == 'start':
        state.mkdir(parents=True, exist_ok=True)
        marker.write_text('enabled\n')
        print('Profiling enabled for subsequent turns. Reload the backend once after installing hooks.')
        return
    if args.command == 'stop':
        marker.unlink(missing_ok=True)
        print('Profiling disabled for subsequent turns; active captures finish normally.')
        return
    source = state / 'performance.jsonl'
    if not source.exists():
        parser.error('No capture yet. Enable profiling and run a chat turn first.')
    records = []
    skipped = 0
    for line in source.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    completed = [r for r in records if r.get('name') == 'controller.turn'
                 and r.get('kind') == 'span']
    if not completed:
        parser.error('No completed turn yet; wait for the active turn to finish.')
    selected = completed if args.all else completed[-1:]
    traces = {r['trace'] for r in selected}
    records = [r for r in records if r['trace'] in traces]
    report = summarize(records)
    report['turns'] = len(selected)
    report['trace_ids'] = sorted(traces)
    report['skipped_lines'] = skipped
    total = sum((r['end_ns'] - r['start_ns']) / 1e6 for r in selected)
    lines = ['# Crucible performance capture', '',
             f'{len(selected)} completed turn(s), {total / 1000:.3f}s total wall time.', '',
             '| Block | Calls | Self total (s) | Inclusive total (s) | p50 (s) | p95 (s) |',
             '|---|---:|---:|---:|---:|---:|']
    for row in report['blocks']:
        lines.append(f"| {row['block']} | {row['calls']} | {row['self_ms']/1000:.3f} | "
                     f"{row['inclusive_ms']/1000:.3f} | {row['p50_ms']/1000:.3f} | "
                     f"{row['p95_ms']/1000:.3f} |")
    lines += ['', 'First-content milestones (included in provider time):', '']
    for name, row in report['milestones'].items():
        lines.append(f"- {name}: {row['calls']} calls, mean {row['mean_ms']/1000:.3f}s, "
                     f"max {row['max_ms']/1000:.3f}s")
    lines += ['', '| Synchronous function | Calls | Self (ms) | Inclusive (ms) |',
              '|---|---:|---:|---:|']
    for row in report['functions'][:25]:
        lines.append(f"| {row['function']} | {row['calls']} | {row['self_ms']:.3f} | "
                     f"{row['inclusive_ms']:.3f} |")
    lines += ['', report['note'], '',
              'Function rows retain the top 40 self-time entries from each profiled call. '
              'p50/p95 describe this capture, not a production benchmark. '
              'Controller time excludes request handling before the controller starts.', '']
    output = state / 'performance-report.md'
    output.write_text('\n'.join(lines))
    (state / 'performance-report.json').write_text(json.dumps(report, indent=2) + '\n')
    # Chrome/Perfetto trace viewer format; each span gets its own lane to expose overlap.
    spans = [r for r in records if r.get('kind') == 'span']
    base = min(r['start_ns'] for r in spans)
    events = [{'name': r['name'], 'ph': 'X', 'pid': sorted(traces).index(r['trace']),
               'tid': i, 'ts': (r['start_ns']-base)/1000,
               'dur': (r['end_ns']-r['start_ns'])/1000,
               'args': {'parent': r['parent'], 'status': r['status']}}
              for i, r in enumerate(spans)]
    (state / 'performance-trace.json').write_text(json.dumps({'traceEvents': events}))
    print('\n'.join(lines))
    print(f'Report: {output}')


if __name__ == '__main__':
    main()
