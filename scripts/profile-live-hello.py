#!/usr/bin/env python3
"""Measure a real hello against the extension's backend, plus its opt-in spans.

Credentials stay in memory. Event payloads are reduced to type, keys, tool name,
and response text; prompts, reasoning, tool arguments/results are not exported.
This measures HTTP/SSE delivery, not extension/webview rendering.
"""
import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/agentd-py'))
from agentd.performance import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    state = workspace / '.crucible/state'
    state.mkdir(parents=True, exist_ok=True)
    (state / 'performance.enabled').write_text('enabled\n')
    source = state / 'performance.jsonl'
    offset = source.stat().st_size if source.exists() else 0
    token = (Path.home() / f'.crucible/run/agentd-{args.port}.token').read_text()
    base = f'http://127.0.0.1:{args.port}/v1'

    def request(path, payload=None):
        return urllib.request.urlopen(urllib.request.Request(
            base + path, data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}),
            # Fresh retrieval can delay HTTP headers beyond 30 seconds. The
            # independent turn watchdog owns the shorter diagnostic deadline.
            timeout=max(30, args.timeout + 5))

    with request('/chat/threads', {'workspace': str(workspace), 'title': 'Profile hello'}) as response:
        thread = json.load(response)['thread_id']
    report = {'thread_id': thread, 'prompt': 'hello', 'started_utc': time.strftime(
        '%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'events': [], 'response': '',
        'boundary': 'HTTP POST start through SSE chat_done; webview rendering excluded',
        'model_configuration': 'Backend configuration unchanged; see physical context rows'}
    start = time.perf_counter_ns()
    completed = threading.Event()

    def expire():
        if not completed.wait(args.timeout):
            report['timeout_stop_requested'] = True
            with request(f'/chat/threads/{thread}/stop', {}) as response:
                response.read()

    threading.Thread(target=expire, daemon=True).start()
    print(f'Thread {thread}: sending hello', flush=True)
    try:
        with request(f'/chat/threads/{thread}/message', {
            'content': 'hello', 'plan_mode': False, 'step_review': False}) as response:
            report['http_headers_seconds'] = (time.perf_counter_ns() - start) / 1e9
            for line in response:
                if not line.startswith(b'data: '):
                    continue
                now = time.perf_counter_ns()
                event = json.loads(line[6:])
                kind = event.get('type')
                payload = event.get('payload') or {}
                record = {'type': kind, 'at_ns': now,
                          'seconds': (now - start) / 1e9,
                          'payload_keys': sorted(payload) if isinstance(payload, dict) else []}
                if isinstance(payload, dict):
                    for key in ('tool', 'tool_name', 'status', 'step', 'phase'):
                        if isinstance(payload.get(key), (str, int, bool)):
                            record[key] = payload[key]
                report['events'].append(record)
                if kind == 'chat_response':
                    report.setdefault('first_response_seconds', record['seconds'])
                    report['response'] += str(payload.get('chunk', ''))
                if kind not in ('chat_thinking', 'context_usage', 'chat_thinking_delta'):
                    print(f"{record['seconds']:.3f}s {kind}", flush=True)
                if kind in ('chat_done', 'done'):
                    report['done_seconds'] = record['seconds']
                    break
    except Exception as exc:
        report['client_error'] = type(exc).__name__
        # A disconnected SSE client does not necessarily stop the backend task.
        # Stop explicitly before disabling the watchdog in finally.
        try:
            with request(f'/chat/threads/{thread}/stop', {}) as response:
                response.read()
            report['client_error_stop_requested'] = True
        except Exception as stop_exc:
            report['client_error_stop_error'] = type(stop_exc).__name__
        raise
    finally:
        completed.set()
        # Root spans close just after the final SSE event is published.
        for _ in range(20):
            with source.open() if source.exists() else open('/dev/null') as stream:
                stream.seek(offset)
                records = [json.loads(line) for line in stream if line.strip()]
            if any(r.get('name') == 'controller.backend_turn' and r.get('kind') == 'span'
                   for r in records):
                break
            time.sleep(.1)
        report['backend'] = summarize(records)
        report['capture_start_ns'] = start
        report['backend_records'] = records
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(f"Report: {args.output}", flush=True)
        print(json.dumps({k: v for k, v in report.items() if k in (
            'response', 'http_headers_seconds', 'first_response_seconds', 'done_seconds',
            'timeout_stop_requested')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
