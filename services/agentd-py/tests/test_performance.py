import asyncio
import json
from pathlib import Path

import pytest

from agentd.performance import (
    Capture,
    _capture,
    interval_union,
    mark,
    profile_controller_run,
    profile_sync,
    span,
    summarize,
)


def test_overlap_is_counted_once_and_milestones_separate():
    def row(identity, parent, start, end, name):
        return dict(kind='span', trace='t', id=identity, parent=parent,
                    start_ns=start*1000000, end_ns=end*1000000, name=name)
    report = summarize([row('root', None, 0, 100, 'turn'),
                        row('a', 'root', 10, 60, 'tool'),
                        row('b', 'root', 40, 80, 'tool'),
                        dict(kind='mark', trace='t', parent='a', name='first', at_ns=20000000)])
    groups = {r['block']: r for r in report['blocks']}
    assert groups['turn']['self_ms'] == 30
    assert groups['tool']['calls'] == 2
    assert groups['tool']['inclusive_ms'] == 90
    assert 'first' not in groups
    assert report['milestones']['first']['mean_ms'] == 10
    assert interval_union([(10, 60), (40, 80)]) == 70


@pytest.mark.asyncio
async def test_opt_in_functions_exception_and_async_context(tmp_path):
    class Loop:
        @profile_controller_run
        async def run(self, plan_context):
            @profile_sync('context.test')
            def build():
                return sum(range(200))
            build()
            async def child():
                with span('child'):
                    mark('first')
                    await asyncio.sleep(0)
            await asyncio.gather(child(), child())
            with span('failure'):
                raise ValueError('request content must not enter trace')
    loop = Loop()
    with pytest.raises(ValueError):
        await loop.run({'workspace_path': str(tmp_path)})
    state = tmp_path / '.crucible/state'
    assert not state.exists()
    state.mkdir(parents=True)
    (state / 'performance.enabled').touch()
    with pytest.raises(ValueError):
        await loop.run({'workspace_path': str(tmp_path)})
    raw = (state / 'performance.jsonl').read_text()
    assert 'request content' not in raw
    assert _capture.get() is None
    records = [json.loads(line) for line in raw.splitlines()]
    roots = [r for r in records if r.get('name') == 'controller.turn']
    assert len(roots) == 1
    children = [r for r in records if r.get('name') == 'child']
    assert len(children) == 2
    assert all(r['parent'] == roots[0]['id'] for r in children)
    assert roots[0]['status'] == 'error_or_cancelled'
    assert summarize(records)['functions']


def test_unavailable_capture_does_not_break_tool(tmp_path):
    blocked = tmp_path / 'file'
    blocked.touch()
    token = _capture.set(Capture(Path(blocked / 'trace.jsonl'), 't'))
    try:
        with span('tool'):
            assert 1 + 1 == 2
    finally:
        _capture.reset(token)


def test_cli_selects_completed_trace_and_exports(tmp_path):
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[3] / 'scripts/profile-controller.py'
    def cli(command):
        return subprocess.run([sys.executable, str(script), command, '--workspace', str(tmp_path)],
                              check=True, capture_output=True, text=True)
    cli('start')
    state = tmp_path / '.crucible/state'
    assert (state / 'performance.enabled').is_file()
    rows = [dict(kind='span', name='controller.turn', trace='complete', id='root',
                 parent=None, status='ok', start_ns=1000000, end_ns=1000000000),
            dict(kind='span', name='tool', trace='inflight', id='tool',
                 parent='unfinished', status='ok', start_ns=2000000, end_ns=3000000)]
    (state / 'performance.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    result = cli('report')
    assert '0.999s total wall time' in result.stdout
    data = json.loads((state / 'performance-report.json').read_text())
    assert data['trace_ids'] == ['complete']
    events = json.loads((state / 'performance-trace.json').read_text())['traceEvents']
    assert len(events) == 1
    cli('stop')
    assert not (state / 'performance.enabled').exists()
